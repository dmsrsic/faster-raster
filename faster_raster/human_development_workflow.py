from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from faster_raster.ag_execution import (
    _assert_no_staging_provenance,
    _open_final_preview,
    _regenerate_checksums,
    configured_handoff_root,
    handoff_transaction,
)
from faster_raster.human_development import (
    HumanDevelopmentError,
    analyze_common_all_epoch_footprint,
    analyze_interval,
    build_target_grid,
    harmonize_epoch,
    inspect_raster,
    methodology_receipt,
    resolve_local_path,
)
from faster_raster.human_development_preview import render_human_development_preview
from faster_raster.local_config import normalized_config_paths, resolved_config_document
from faster_raster.local_paths import LocalPaths
from faster_raster.source_capabilities import write_profile_atomic
from faster_raster.workfiles import HumanDevelopmentWorkfileSpec, Workfile


def source_gate_report() -> dict[str, Any]:
    return {
        "schema_version": "fasterraster.human-development-source-gate/v1",
        "evaluated_at": "2026-07-18",
        "result": "BLOCKED",
        "remote_live_acquisition_implemented": False,
        "local_pinned_acquisition_supported": True,
        "official_contract": {
            "provider": "U.S. Geological Survey",
            "product_suite": "Annual NLCD Conterminous U.S.",
            "collection": 1,
            "version": 2,
            "supported_years": {"first": 1985, "last": 2025, "cadence": "annual"},
            "categorical_product_code": "LndCov",
            "optional_continuous_product_code": "FctImp",
            "format": "GeoTIFF / cloud-optimized GeoTIFF",
            "crs": "Albers Equal Area Conic on WGS84 (EPSG:5070 semantics)",
            "resolution_m": 30,
            "land_cover_nodata": 250,
            "developed_classes": [21, 22, 23, 24],
        },
        "official_evidence": [
            {
                "title": "USGS EROS Archive – Annual NLCD Collection 1.2 Land Cover",
                "url": "https://www.usgs.gov/centers/eros/science/usgs-eros-archive-land-cover-annual-nlcd-collection-12-land-cover",
                "supports": "Collection 1.2, 1985-2025, LndCov GeoTIFFs, 30 m common Albers grid, official legend",
            },
            {
                "title": "USGS Annual NLCD Product Suite",
                "url": "https://www.usgs.gov/centers/eros/science/nlcd-product-suite",
                "supports": "six products including Land Cover and Fractional Impervious Surface for 1985-2025",
            },
            {
                "title": "USGS Annual NLCD Data Access",
                "url": "https://www.usgs.gov/centers/eros/science/annual-nlcd-data-access",
                "supports": "cloud access is requester-pays S3; other access routes are packaged, interactive, or services",
            },
            {
                "title": "USGS Annual NLCD Collection 1 Science Product User Guide",
                "url": "https://www.usgs.gov/media/files/annual-nlcd-collection-1-science-product-user-guide",
                "supports": "Collection 1.2 product semantics, legend, and nodata",
            },
        ],
        "existing_repository_evidence": {
            "annual_nlcd_aws_tile": "Collection 1.0 FctImp fixed-tile URL template; continuous, not categorical LndCov",
            "annual_nlcd_aws_mosaic": "Collection 1.0 whole-CONUS FctImp URL template; continuous and not AOI-bounded",
            "stac_adapter": "not implemented",
            "authenticated_requester_pays": "explicitly outside the existing unauthenticated transport contract",
        },
        "failed_gate_conditions": [
            "no deterministic Collection 1.2 categorical LndCov object catalog is implemented",
            "official AWS access is requester-pays and requires signed authentication plus charge acknowledgement",
            "existing static HTTP evidence identifies FctImp Collection 1.0, not compatible LndCov Collection 1.2 assets",
            "EarthExplorer packages all years per tile and the MRLC viewer uses asynchronous email delivery",
            "WMS is rendered imagery; WCS would require a new service adapter",
        ],
        "exact_blocker": (
            "A bounded live proof requires a credential-aware requester-pays S3/STAC object-resolution adapter "
            "or a bounded raw-value WCS adapter. That is a separate authentication/catalog sprint, not a small "
            "extension of the current static HTTP range adapter."
        ),
        "network_bytes": 0,
        "reused_bytes": 0,
    }


def _human_spec(workfile: Workfile) -> HumanDevelopmentWorkfileSpec:
    if not isinstance(workfile.spec, HumanDevelopmentWorkfileSpec):
        raise HumanDevelopmentError("workfile is not a human_development_change v2 workfile")
    return workfile.spec


def _resolved_configuration(
    workfile: Workfile,
    paths: LocalPaths,
    *,
    cli_overrides: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], Any]:
    spec = _human_spec(workfile)
    config, config_files = resolved_config_document(paths)
    normalized = normalized_config_paths(config, paths)
    overrides = dict(cli_overrides or {})
    reuse_mode = overrides.get("reuse_mode") or spec.data.reuse
    maximum_download_mb = overrides.get("maximum_download_mb") or spec.limits.maximum_download_mb
    open_when_complete = (
        overrides["open_when_complete"]
        if overrides.get("open_when_complete") is not None
        else spec.outputs.open_when_complete
    )
    values = {
        "reuse_mode": {"value": reuse_mode, "origin": "cli_override" if "reuse_mode" in overrides else "workfile", "key": "data.reuse"},
        "maximum_download_mb": {
            "value": maximum_download_mb,
            "origin": "cli_override" if "maximum_download_mb" in overrides else "workfile",
            "key": "limits.maximum_download_mb",
        },
        "open_when_complete": {
            "value": open_when_complete,
            "origin": "cli_override" if "open_when_complete" in overrides else "workfile",
            "key": "outputs.open_when_complete",
        },
        "preview": {"value": spec.outputs.preview, "origin": "workfile", "key": "outputs.preview"},
        "resolution_m": {"value": spec.processing.resolution_m, "origin": "workfile", "key": "processing.resolution_m"},
        "window_size": {"value": spec.processing.window_size, "origin": "workfile", "key": "processing.window_size"},
        "cache_root": {"value": str(normalized["cache_root"]), "origin": "configuration", "key": "paths.cache_root"},
        "state_root": {"value": str(normalized["state_root"]), "origin": "configuration", "key": "paths.state_root"},
        "temporary_root": {"value": str(normalized["temporary_root"]), "origin": "configuration", "key": "paths.temporary_root"},
        "offline": {"value": True, "origin": "workflow_contract", "key": "local_pinned_only"},
    }
    return {
        "schema_version": "fasterraster.resolved-config/v1",
        "workfile": str(workfile.path),
        "workflow": spec.workflow_id,
        "configuration_files": [str(path) for path in config_files],
        "precedence": ["cli_override", "workfile", "project_configuration", "user_configuration", "workflow_defaults"],
        "values": values,
    }, config


def compile_human_development_plan(
    repository_root: Path,
    workfile: Workfile,
    paths: LocalPaths,
    *,
    cli_overrides: Mapping[str, Any] | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    spec = _human_spec(workfile)
    if spec.sources.policy == "service_discovered":
        from faster_raster.human_development_live import compile_live_cdl_plan
        return compile_live_cdl_plan(
            repository_root,
            workfile,
            paths,
            cli_overrides=cli_overrides,
            output_dir=output_dir,
        )
    del repository_root
    resolved, _ = _resolved_configuration(workfile, paths, cli_overrides=cli_overrides)
    epoch_assets = []
    rows = []
    for epoch in spec.epochs:
        land_path = resolve_local_path(workfile.path, epoch.land_cover_path)
        land_metadata = inspect_raster(land_path)
        impervious_path = (
            resolve_local_path(workfile.path, epoch.imperviousness_path)
            if epoch.imperviousness_path
            else None
        )
        impervious_metadata = inspect_raster(impervious_path) if impervious_path else None
        epoch_assets.append(
            {
                "year": epoch.year,
                "land_cover_path": str(land_path),
                "land_cover": land_metadata,
                "imperviousness_path": str(impervious_path) if impervious_path else None,
                "imperviousness": impervious_metadata,
            }
        )
        for label, metadata in (
            ("Land cover", land_metadata),
            ("Fractional imperviousness", impervious_metadata),
        ):
            if metadata is None:
                continue
            rows.append(
                {
                    "data": f"{label} {epoch.year}",
                    "logical_asset": "land_cover" if label == "Land cover" else "fractional_imperviousness",
                    "source": "usgs_annual_nlcd_local_pinned",
                    "local_asset_readiness": "ready_exact",
                    "remote_source_status": "credential_missing",
                    "remote_source_required": False,
                    "remote_source_blocking": False,
                    "action": "reuse_direct",
                    "reason": "validated local pinned GeoTIFF; no network acquisition requested",
                    "provisional": False,
                    "reused": True,
                    "acquired": False,
                    "bytes": metadata["bytes"],
                }
            )
    grid = build_target_grid(
        spec.area.bbox,
        Path(epoch_assets[0]["land_cover_path"]),
        resolution_m=spec.processing.resolution_m,
    )
    blocking_reasons = []
    if resolved["values"]["reuse_mode"]["value"] == "never":
        blocking_reasons.append("human_development_change requires local pinned inputs; data.reuse: never is incompatible")
    source_gate = source_gate_report()
    source_resolution = {
        "schema_version": "fasterraster.source-resolution/v1",
        "workfile": str(workfile.path),
        "network_requests": 0,
        "blocking": False,
        "remote_live_result": source_gate["result"],
        "decisions": [
            {
                "logical_asset": "land_cover",
                "selected_source": "usgs_annual_nlcd_local_pinned",
                "selected_capability_status": "available",
                "provisional": False,
                "live_execution_must_revalidate": False,
                "remote_live_blocker": source_gate["exact_blocker"],
            },
            {
                "logical_asset": "fractional_imperviousness",
                "selected_source": "usgs_annual_nlcd_local_pinned"
                if any(item["imperviousness_path"] for item in epoch_assets)
                else None,
                "selected_capability_status": "available"
                if any(item["imperviousness_path"] for item in epoch_assets)
                else "optional_unavailable",
                "provisional": False,
                "live_execution_must_revalidate": False,
                "remote_live_blocker": source_gate["exact_blocker"],
            },
        ],
    }
    asset_plan = {
        "schema_version": "fasterraster.human-development-asset-plan/v1",
        "comparison_mode": spec.comparison_mode,
        "epochs": epoch_assets,
        "adjacent_intervals": [
            {"before_year": before.year, "after_year": after.year}
            for before, after in zip(spec.epochs, spec.epochs[1:])
        ],
        "endpoint_comparison": {"before_year": spec.epochs[0].year, "after_year": spec.epochs[-1].year},
        "target_grid": grid.as_dict(),
        "reuse_mode": resolved["values"]["reuse_mode"]["value"],
        "network_bytes_planned": 0,
        "reused_bytes_planned": sum(
            item["land_cover"]["bytes"] + (item["imperviousness"]["bytes"] if item["imperviousness"] else 0)
            for item in epoch_assets
        ),
    }
    plan = {
        "schema_version": "fasterraster.study-plan/v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "workfile": str(workfile.path),
        "study_name": spec.name,
        "workflow": spec.workflow_id,
        "comparison_mode": spec.comparison_mode,
        "offline_planning": True,
        "network_requests": 0,
        "blocking": bool(blocking_reasons),
        "blocking_reasons": blocking_reasons,
        "rows": rows,
        "asset_plan": asset_plan,
        "maximum_download_bytes": int(resolved["values"]["maximum_download_mb"]["value"] * 1_000_000),
        "source_gate": source_gate,
    }
    destination = output_dir or Path(resolved["values"]["state_root"]["value"]) / "plans" / spec.name
    destination.mkdir(parents=True, exist_ok=True)
    write_profile_atomic(destination / "resolved_config.json", resolved)
    write_profile_atomic(destination / "source_resolution.json", source_resolution)
    write_profile_atomic(destination / "source_gate_report.json", source_gate)
    write_profile_atomic(destination / "plan.json", plan)
    plan["artifacts"] = {
        "directory": str(destination),
        "resolved_config": str(destination / "resolved_config.json"),
        "source_resolution": str(destination / "source_resolution.json"),
        "source_gate_report": str(destination / "source_gate_report.json"),
        "plan": str(destination / "plan.json"),
    }
    plan["resolved_config"] = resolved
    plan["source_resolution"] = source_resolution
    return plan


def _safe_name(value: str) -> str:
    result = "".join(character if character.isalnum() or character in "-_" else "_" for character in value).strip("_-")
    if not result:
        raise HumanDevelopmentError("study name must contain a letter or number")
    return result


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_paths(staging: Path) -> list[str]:
    return [
        path.relative_to(staging).as_posix()
        for path in sorted(staging.rglob("*"))
        if path.is_file() and "_work" not in path.parts
    ]


def execute_human_development(
    repository_root: Path,
    *,
    workfile: Workfile,
    plan: Mapping[str, Any],
    open_preview: bool,
) -> Path:
    spec = _human_spec(workfile)
    if spec.sources.policy == "service_discovered":
        from faster_raster.human_development_live import execute_live_cdl
        return execute_live_cdl(
            repository_root, workfile=workfile, plan=plan, open_preview=open_preview
        )
    if plan.get("blocking"):
        raise HumanDevelopmentError("human-development study plan is blocked")
    grid_info = plan["asset_plan"]["target_grid"]
    first_path = Path(plan["asset_plan"]["epochs"][0]["land_cover_path"])
    grid = build_target_grid(spec.area.bbox, first_path, resolution_m=spec.processing.resolution_m)
    if grid.fingerprint != grid_info["fingerprint_sha256"]:
        raise HumanDevelopmentError("target-grid fingerprint changed between plan and cook")
    handoff_root = configured_handoff_root(repository_root)
    final = handoff_root / f"{_safe_name(spec.name)}_{_stamp()}"
    with handoff_transaction(final) as staging:
        _write_json(staging / "resolved_config.json", plan["resolved_config"])
        _write_json(staging / "source_resolution.json", plan["source_resolution"])
        _write_json(staging / "source_gate_report.json", plan["source_gate"])
        _write_json(staging / "asset_plan.json", plan["asset_plan"])
        epoch_results = []
        for epoch in plan["asset_plan"]["epochs"]:
            epoch_results.append(
                harmonize_epoch(
                    year=int(epoch["year"]),
                    land_cover_path=Path(epoch["land_cover_path"]),
                    imperviousness_path=Path(epoch["imperviousness_path"]) if epoch["imperviousness_path"] else None,
                    destination=staging / "data" / "epochs" / str(epoch["year"]),
                    grid=grid,
                    window_size=spec.processing.window_size,
                )
            )
        common_result = analyze_common_all_epoch_footprint(
            epoch_results=epoch_results,
            destination=staging / "analysis" / "common_all_epoch",
            grid=grid,
            window_size=spec.processing.window_size,
        )
        interval_results = []
        for before, after in zip(epoch_results, epoch_results[1:]):
            interval_results.append(
                analyze_interval(
                    before_year=int(before["year"]),
                    after_year=int(after["year"]),
                    before_land_cover=Path(before["land_cover"]),
                    after_land_cover=Path(after["land_cover"]),
                    before_imperviousness=Path(before["imperviousness"]) if before["imperviousness"] else None,
                    after_imperviousness=Path(after["imperviousness"]) if after["imperviousness"] else None,
                    destination=staging / "analysis" / "intervals" / f"{before['year']}_{after['year']}",
                    grid=grid,
                    window_size=spec.processing.window_size,
                )
            )
        if len(epoch_results) == 2:
            endpoint_result = interval_results[0]
        else:
            before = epoch_results[0]
            after = epoch_results[-1]
            endpoint_result = analyze_interval(
                before_year=int(before["year"]),
                after_year=int(after["year"]),
                before_land_cover=Path(before["land_cover"]),
                after_land_cover=Path(after["land_cover"]),
                before_imperviousness=Path(before["imperviousness"]) if before["imperviousness"] else None,
                after_imperviousness=Path(after["imperviousness"]) if after["imperviousness"] else None,
                destination=staging / "analysis" / "endpoint" / f"{before['year']}_{after['year']}",
                grid=grid,
                window_size=spec.processing.window_size,
            )
        methodology = methodology_receipt(grid)
        _write_json(staging / "methodology_receipt.json", methodology)
        preview = render_human_development_preview(
            staging / "preview" / "human_development_change_4k.png",
            study_name=spec.name,
            comparison_mode=spec.comparison_mode,
            epoch_results=epoch_results,
            endpoint_result=endpoint_result,
            source_contract=spec.sources.model_dump(mode="json"),
            grid=grid.as_dict(),
            preview_emphasis=spec.outputs.preview_emphasis,
        )
        reused_paths = {
            Path(item[key])
            for item in plan["asset_plan"]["epochs"]
            for key in ("land_cover_path", "imperviousness_path")
            if item.get(key)
        }
        reused_bytes = sum(path.stat().st_size for path in reused_paths)
        generated = _relative_paths(staging)
        workflow_receipt = {
            "schema_version": "fasterraster.human-development-workflow-receipt/v1",
            "final_status": "PASS",
            "workflow": spec.workflow_id,
            "comparison_mode": spec.comparison_mode,
            "published_handoff_id": final.name,
            "requested_name": spec.name,
            "requested_bbox_epsg_4326": list(spec.area.bbox),
            "epochs": [epoch.year for epoch in spec.epochs],
            "per_epoch_valid_footprint": [
                item["per_epoch_valid_footprint"] for item in common_result["epoch_statistics"]
            ],
            "common_all_epoch_footprint": {
                "mask": Path(common_result["mask"]).relative_to(staging).as_posix(),
                "statistics": Path(common_result["statistics_path"]).relative_to(staging).as_posix(),
                "epoch_statistics": common_result["epoch_statistics"],
            },
            "preview_emphasis": spec.outputs.preview_emphasis,
            "adjacent_intervals": [
                {
                    "before_year": item["before_year"],
                    "after_year": item["after_year"],
                    "statistics": Path(item["statistics_path"]).relative_to(staging).as_posix(),
                }
                for item in interval_results
            ],
            "endpoint_comparison": {
                "before_year": endpoint_result["before_year"],
                "after_year": endpoint_result["after_year"],
                "statistics": Path(endpoint_result["statistics_path"]).relative_to(staging).as_posix(),
            },
            "target_grid": grid.as_dict(),
            "total_network_bytes": 0,
            "total_reused_bytes": reused_bytes,
            "source_gate_result": plan["source_gate"]["result"],
            "methodology_receipt": "methodology_receipt.json",
            "preview": preview.relative_to(staging).as_posix(),
            "preview_sha256": _sha256(preview),
            "generated_output_paths": [
                *generated,
                "workflow_receipt.json",
                "manifest.json",
                "checksums.sha256",
            ],
            "transition_reconciliation": all(
                item["statistics"]["transition_reconciliation"]["reconciles"]
                for item in interval_results
            ),
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        _write_json(staging / "workflow_receipt.json", workflow_receipt)
        manifest = {
            "schema_version": 3,
            "operation_status": "completed",
            "verification_status": "PASS",
            "workflow": spec.workflow_id,
            "comparison_mode": spec.comparison_mode,
            "network_bytes": 0,
            "reused_bytes": reused_bytes,
            "source_gate_result": plan["source_gate"]["result"],
            "workflow_receipt": "workflow_receipt.json",
            "methodology_receipt": "methodology_receipt.json",
            "preview": preview.relative_to(staging).as_posix(),
            "warnings": [
                "Remote live Annual NLCD acquisition is blocked; inputs were local pinned GeoTIFFs.",
                "Results describe mapped land-cover development-state change, not population, economics, construction date, causality, or occupancy.",
            ],
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        _write_json(staging / "manifest.json", manifest)
        shutil.rmtree(staging / "_work", ignore_errors=True)
        _regenerate_checksums(staging)
        _assert_no_staging_provenance(staging)
        preview_relative = preview.relative_to(staging)
    final_preview = final / preview_relative
    if open_preview:
        _open_final_preview(final_preview)
    return final_preview
