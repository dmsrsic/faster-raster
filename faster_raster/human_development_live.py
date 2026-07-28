from __future__ import annotations

import copy
import hashlib
import json
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
from faster_raster.cdl_acquisition import (
    CDL_ENDPOINT,
    CDL_SOURCE_ID,
    NAIP_SOURCE_ID,
    ArcGISClient,
    acquire_cdl_epoch,
    discover_cdl_coverage,
    find_cached_cdl_asset,
    find_cached_naip_context,
)
from faster_raster.development_sources import USDA_CDL_MAPPING
from faster_raster.human_development import (
    HumanDevelopmentError,
    analyze_common_all_epoch_footprint,
    analyze_interval,
    build_service_target_grid,
    harmonize_epoch,
    methodology_receipt,
)
from faster_raster.human_development_cdl_preview import render_cdl_proxy_preview
from faster_raster.local_paths import LocalPaths
from faster_raster.source_capabilities import write_profile_atomic
from faster_raster.workfiles import HumanDevelopmentWorkfileSpec, Workfile


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(value: str) -> str:
    result = "".join(character if character.isalnum() or character in "-_" else "_" for character in value).strip("_-")
    if not result:
        raise HumanDevelopmentError("study name must contain a letter or number")
    return result


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _spec(workfile: Workfile) -> HumanDevelopmentWorkfileSpec:
    if not isinstance(workfile.spec, HumanDevelopmentWorkfileSpec):
        raise HumanDevelopmentError("workfile is not a human_development_change v2 workfile")
    return workfile.spec


def _public_asset_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    public = copy.deepcopy(dict(plan))
    for epoch in public.get("epochs", []):
        cache = epoch.get("cache")
        if isinstance(cache, dict):
            cache.pop("path", None)
    context = public.get("context_imagery")
    if isinstance(context, dict) and isinstance(context.get("cache"), dict):
        context["cache"].pop("path", None)
    return public

def _public_resolved_config(resolved: Mapping[str, Any]) -> dict[str, Any]:
    public = copy.deepcopy(dict(resolved))
    temporary_root = public.get("values", {}).get("temporary_root")
    if isinstance(temporary_root, dict):
        temporary_root["value"] = None
        temporary_root["publication_policy"] = "runtime_only_path_not_published"
    return public



def compile_live_cdl_plan(
    repository_root: Path,
    workfile: Workfile,
    paths: LocalPaths,
    *,
    cli_overrides: Mapping[str, Any] | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    from faster_raster.human_development_workflow import _resolved_configuration

    spec = _spec(workfile)
    if spec.sources.policy != "service_discovered":
        raise HumanDevelopmentError("live CDL planner requires sources.policy: service_discovered")
    resolved, _ = _resolved_configuration(workfile, paths, cli_overrides=cli_overrides)
    reuse_mode = str(resolved["values"]["reuse_mode"]["value"])
    maximum_bytes = int(float(resolved["values"]["maximum_download_mb"]["value"]) * 1_000_000)
    offline_requested = bool((cli_overrides or {}).get("offline"))
    allow_network = bool(
        spec.data.allow_network
        and reuse_mode != "only"
        and not offline_requested
    )
    resolved["values"].update({
        "allow_network": {"value": allow_network, "origin": "workfile", "key": "data.allow_network"},
        "source_id": {"value": spec.sources.source_id, "origin": "workfile", "key": "sources.source_id"},
        "mapping_id": {"value": spec.sources.mapping_id, "origin": "workfile", "key": "sources.mapping_id"},
        "service_tile_size": {"value": spec.processing.service_tile_size, "origin": "workfile", "key": "processing.service_tile_size"},
        "offline": {
            "value": offline_requested or reuse_mode == "only",
            "origin": "cli_override" if offline_requested else "workflow_contract",
            "key": "offline" if offline_requested else "strict_reuse_only",
        },
    })
    grid = build_service_target_grid(spec.area.bbox, resolution_m=spec.processing.resolution_m)
    handoff_root = configured_handoff_root(repository_root)
    cache_by_year = {
        epoch.year: (None if reuse_mode == "never" else find_cached_cdl_asset(handoff_root, spec.area.bbox, epoch.year, grid))
        for epoch in spec.epochs
    }
    context_cache = None
    if spec.outputs.include_context_imagery and spec.sources.context_year is not None and reuse_mode != "never":
        context_cache = find_cached_naip_context(handoff_root, spec.area.bbox, spec.sources.context_year)

    missing_years = [year for year, asset in cache_by_year.items() if asset is None]
    blocking_reasons: list[str] = []
    discovery: dict[str, Any]
    if reuse_mode == "only":
        discovery = {
            "schema_version": "fasterraster.cdl-source-discovery/v1",
            "source_id": CDL_SOURCE_ID,
            "status": "SKIPPED_COMPLETE_VERIFIED_CACHE" if not missing_years else "SKIPPED_NETWORK_DISABLED",
            "metadata_network_bytes": 0,
            "requests": [],
            "epochs": [],
        }
        if missing_years:
            blocking_reasons.append(f"strict reuse-only cache is missing CDL years: {missing_years}")
    elif offline_requested:
        discovery = {
            "schema_version": "fasterraster.cdl-source-discovery/v1",
            "source_id": CDL_SOURCE_ID,
            "status": "SKIPPED_OFFLINE",
            "metadata_network_bytes": 0,
            "requests": [],
            "epochs": [
                {
                    "requested_year": epoch.year,
                    "exact_coverage_status": "NOT_CHECKED",
                    "catalog_record_ids": [],
                }
                for epoch in spec.epochs
            ],
        }
    elif not allow_network:
        discovery = {"status": "BLOCKED_NETWORK_PERMISSION", "metadata_network_bytes": 0, "requests": [], "epochs": []}
        blocking_reasons.append("service_discovered CDL requires explicit network permission")
    else:
        client = ArcGISClient(byte_ceiling=min(maximum_bytes, 20_000_000), allow_network=True)
        try:
            discovery = discover_cdl_coverage(client, spec.area.bbox, [epoch.year for epoch in spec.epochs])
        except Exception as exc:
            evidence = getattr(exc, "evidence", None)
            discovery = {
                "schema_version": "fasterraster.cdl-source-discovery/v1",
                "source_id": CDL_SOURCE_ID,
                "status": "FAIL",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "evidence": evidence or {},
                "metadata_network_bytes": client.total_bytes,
                "requests": client.requests,
            }
            blocking_reasons.append(str(exc))

    coverage_by_year = {
        int(item["requested_year"]): item
        for item in discovery.get("epochs", [])
        if isinstance(item, dict) and "requested_year" in item
    }
    epoch_plans = []
    rows = []
    estimated_cumulative = 0
    estimated_epoch_bytes = int(grid.width * grid.height * 1.25 + 65_536)
    for epoch in spec.epochs:
        cache = cache_by_year[epoch.year]
        coverage = coverage_by_year.get(epoch.year)
        if cache is not None:
            action = cache.action
            cache_state = "verified_finalized_cache"
            reason = f"compatible immutable source asset from handoff {cache.source_handoff_id}"
            estimated = 0
        elif offline_requested:
            action = "metadata_discovery_required"
            cache_state = "missing"
            reason = (
                "offline plan did not check exact-year coverage; rerun planning "
                "without --offline before execution"
            )
            estimated = 0
        elif blocking_reasons:
            action = "blocked"
            cache_state = "missing"
            reason = blocking_reasons[-1]
            estimated = 0
        else:
            action = "export_remote"
            cache_state = "missing"
            reason = "exact-year AOI catalog coverage confirmed; bounded raw-class export required"
            estimated = estimated_epoch_bytes
            estimated_cumulative += estimated
        record_ids = list(coverage.get("catalog_record_ids", [])) if coverage else []
        exact_status = coverage.get("exact_coverage_status") if coverage else (
            "VERIFIED_CACHE" if cache is not None and reuse_mode == "only" else "NOT_CHECKED"
        )
        epoch_plan = {
            "year": epoch.year,
            "requested_year": epoch.year,
            "exact_coverage_status": exact_status,
            "catalog_record_ids": record_ids,
            "selected_source": CDL_SOURCE_ID,
            "mapping_id": USDA_CDL_MAPPING.mapping_id,
            "source_semantic_type": USDA_CDL_MAPPING.source_semantic_type,
            "source_crs": "EPSG:3857",
            "expected_export_crs": grid.crs,
            "expected_resolution_m": grid.resolution_m,
            "expected_width": grid.width,
            "expected_height": grid.height,
            "expected_resampling": "nearest",
            "cache_state": cache_state,
            "planned_action": action,
            "reason": reason,
            "estimated_maximum_bytes": estimated,
            "cumulative_estimated_raster_bytes": estimated_cumulative,
            "cache": cache.as_dict() if cache else None,
        }
        epoch_plans.append(epoch_plan)
        rows.append({
            "data": f"CDL raw classes {epoch.year}", "logical_asset": "land_cover",
            "source": CDL_SOURCE_ID, "local_asset_readiness": "ready_exact" if action == "reuse_exact" else "ready_requires_crop_reprojection" if action == "reuse_crop" else "missing",
            "remote_source_status": (
                "available"
                if exact_status in {"PASS", "VERIFIED_CACHE"}
                else "unknown"
                if exact_status == "NOT_CHECKED"
                else "unreachable"
            ),
            "remote_source_required": action == "export_remote", "remote_source_blocking": action == "blocked",
            "action": action, "reason": reason, "provisional": False,
            "reused": action.startswith("reuse_"), "acquired": action == "export_remote",
            "bytes": cache.bytes if cache else estimated,
        })
    if estimated_cumulative > maximum_bytes:
        blocking_reasons.append(
            f"estimated CDL raster bytes {estimated_cumulative:,} exceed configured ceiling {maximum_bytes:,}"
        )
    requires_coverage_validation = any(
        item["planned_action"] == "metadata_discovery_required"
        for item in epoch_plans
    )
    context_plan = {
        "requested": spec.outputs.include_context_imagery,
        "year": spec.sources.context_year,
        "source_id": spec.sources.context_imagery_source_id,
        "role": "endpoint_geographic_context_only",
        "planned_action": context_cache.action if context_cache else "context_unavailable",
        "cache": context_cache.as_dict() if context_cache else None,
        "blocking": False,
    }
    asset_plan = {
        "schema_version": "fasterraster.human-development-asset-plan/v2",
        "comparison_mode": spec.comparison_mode,
        "source_id": CDL_SOURCE_ID,
        "mapping_id": USDA_CDL_MAPPING.mapping_id,
        "mapping_contract_sha256": USDA_CDL_MAPPING.sha256,
        "epochs": epoch_plans,
        "adjacent_intervals": [{"before_year": a.year, "after_year": b.year} for a, b in zip(spec.epochs, spec.epochs[1:])],
        "endpoint_comparison": {"before_year": spec.epochs[0].year, "after_year": spec.epochs[-1].year},
        "target_grid": grid.as_dict(),
        "context_imagery": context_plan,
        "reuse_mode": reuse_mode,
        "allow_network": allow_network,
        "maximum_download_bytes": maximum_bytes,
        "estimated_raster_network_bytes": estimated_cumulative,
        "metadata_network_bytes": int(discovery.get("metadata_network_bytes", 0)),
    }
    source_gate = {
        "schema_version": "fasterraster.human-development-source-gate/v2",
        "result": (
            "BLOCKED"
            if blocking_reasons
            else "PROVISIONAL_OFFLINE"
            if requires_coverage_validation
            else "AVAILABLE"
        ),
        "remote_live_acquisition_implemented": True,
        "source_id": CDL_SOURCE_ID,
        "mapping_id": USDA_CDL_MAPPING.mapping_id,
        "mapping_contract_sha256": USDA_CDL_MAPPING.sha256,
        "scientific_claim": USDA_CDL_MAPPING.scientific_claim,
        "qualification": "CDL is crop-focused; non-agricultural classes 121-124 are a mapped-development proxy.",
        "network_bytes": int(discovery.get("metadata_network_bytes", 0)),
    }
    source_resolution = {
        "schema_version": "fasterraster.source-resolution/v1", "workfile": str(workfile.path),
        "network_requests": len(discovery.get("requests", [])), "blocking": bool(blocking_reasons),
        "decisions": [{
            "logical_asset": "land_cover", "selected_source": CDL_SOURCE_ID,
            "selected_capability_status": (
                "unreachable"
                if blocking_reasons
                else "verification_required"
                if requires_coverage_validation
                else "available"
            ),
            "mapping_id": USDA_CDL_MAPPING.mapping_id, "provisional": False,
            "live_execution_must_revalidate": requires_coverage_validation,
        }],
    }
    explanation = {
        "source_selection": "USDA CDL was selected because its public raw categorical ImageServer already has a proven bounded acquisition path.",
        "scientific_claim": USDA_CDL_MAPPING.scientific_claim,
        "mapping": "CDL 121, 122, 123, and 124 map to ordered developed open, low, medium, and high proxy states; other declared valid classes map to non-developed.",
        "invalid_values": "Service background 0, Clouds/No Data 81, transparent 255, and undeclared values are invalid.",
        "year_acceptance": "Every selected year requires an intersecting catalog record whose Year equals the requested year; no substitution is permitted.",
        "resampling": "Categorical values use nearest-neighbor only.",
        "grid": "AOI bounds are transformed to EPSG:5070 and snapped outward to the global 30 m origin.",
        "context": "2021 NAIP is endpoint geographic context only and is not temporal change evidence.",
        "outputs": "Epoch state, endpoint and adjacent change, trend, transitions, statistics, methodology, source evidence, and checksums.",
        "unsupported": ["population growth", "economic activity", "confirmed construction dates", "occupancy", "causal urban expansion", "cadastral approval", "authoritative non-agricultural land-cover change"],
    }
    plan = {
        "schema_version": "fasterraster.study-plan/v1", "created_at": datetime.now(timezone.utc).isoformat(),
        "workfile": str(workfile.path), "study_name": spec.name, "workflow": spec.workflow_id,
        "comparison_mode": spec.comparison_mode,
        "offline_planning": offline_requested or reuse_mode == "only",
        "network_requests": len(discovery.get("requests", [])), "blocking": bool(blocking_reasons),
        "requires_coverage_validation": requires_coverage_validation,
        "blocking_reasons": blocking_reasons, "rows": rows, "asset_plan": asset_plan,
        "maximum_download_bytes": maximum_bytes, "source_gate": source_gate,
        "source_discovery": discovery, "source_resolution": source_resolution,
        "resolved_config": resolved, "explanation": explanation,
    }
    destination = output_dir or Path(resolved["values"]["state_root"]["value"]) / "plans" / spec.name
    destination.mkdir(parents=True, exist_ok=True)
    for name, value in (
        ("resolved_config.json", resolved), ("source_resolution.json", source_resolution),
        ("source_gate_report.json", source_gate), ("source_discovery.json", discovery),
        ("asset_plan.json", _public_asset_plan(asset_plan)), ("plan.json", {**plan, "asset_plan": _public_asset_plan(asset_plan)}),
    ):
        write_profile_atomic(destination / name, value)
    plan["artifacts"] = {"directory": str(destination), "plan": str(destination / "plan.json"), "source_discovery": str(destination / "source_discovery.json")}
    return plan


def execute_live_cdl(
    repository_root: Path,
    *,
    workfile: Workfile,
    plan: Mapping[str, Any],
    open_preview: bool,
) -> Path:
    spec = _spec(workfile)
    if plan.get("blocking"):
        raise HumanDevelopmentError("human-development live CDL study plan is blocked")
    if plan.get("requires_coverage_validation"):
        raise HumanDevelopmentError(
            "offline plan requires exact-year coverage validation; rerun "
            "planning without --offline before execution"
        )
    grid = build_service_target_grid(spec.area.bbox, resolution_m=spec.processing.resolution_m)
    if grid.fingerprint != plan["asset_plan"]["target_grid"]["fingerprint_sha256"]:
        raise HumanDevelopmentError("target-grid fingerprint changed between plan and cook")
    handoff_root = configured_handoff_root(repository_root)
    final = handoff_root / f"{_safe_name(spec.name)}_{_stamp()}"
    maximum_bytes = int(plan["asset_plan"]["maximum_download_bytes"])
    total_network = 0
    total_reused = 0
    with handoff_transaction(final) as staging:
        _write_json(staging / "resolved_config.json", _public_resolved_config(plan["resolved_config"]))
        _write_json(staging / "source_resolution.json", plan["source_resolution"])
        _write_json(staging / "source_gate_report.json", plan["source_gate"])
        _write_json(staging / "source_discovery.json", plan["source_discovery"])
        _write_json(staging / "asset_plan.json", _public_asset_plan(plan["asset_plan"]))
        _write_json(staging / "source_mapping_contract.json", {**USDA_CDL_MAPPING.as_dict(), "sha256": USDA_CDL_MAPPING.sha256})
        epoch_results = []
        epoch_receipts = []
        for epoch_plan in plan["asset_plan"]["epochs"]:
            year = int(epoch_plan["year"])
            action = str(epoch_plan["planned_action"])
            source_path: Path
            if action.startswith("reuse_"):
                cache = epoch_plan.get("cache") or {}
                source_path = Path(str(cache["path"]))
                if _sha256(source_path) != cache["source_checksum"]:
                    raise HumanDevelopmentError(f"cached CDL checksum changed for {year}")
                total_reused += source_path.stat().st_size
                acquisition = {
                    "schema_version": "fasterraster.cdl-acquisition-receipt/v1",
                    "status": "REUSED", "source_id": CDL_SOURCE_ID, "year": year,
                    "mapping_id": USDA_CDL_MAPPING.mapping_id,
                    "source_handoff_id": cache["source_handoff_id"],
                    "source_checksum_sha256": cache["source_checksum"],
                    "planned_action": action, "export_request_count": 0,
                    "total_network_bytes": 0, "reused_bytes": source_path.stat().st_size,
                }
            else:
                source_path = staging / "source" / f"cdl_{year}_raw.tif"
                acquisition = acquire_cdl_epoch(
                    bbox=spec.area.bbox, year=year, grid=grid,
                    request_tile_ceiling=spec.processing.service_tile_size,
                    byte_ceiling=maximum_bytes - total_network,
                    destination=source_path, allow_network=True,
                    catalog_record_ids=epoch_plan["catalog_record_ids"],
                )
                total_network += int(acquisition["total_network_bytes"])
                acquisition["output"] = f"source/cdl_{year}_raw.tif"
                acquisition["mapping_id"] = USDA_CDL_MAPPING.mapping_id
                acquisition["mapping_contract_sha256"] = USDA_CDL_MAPPING.sha256
                acquisition["reused_bytes"] = 0
            receipt_relative = f"metadata/acquisition/cdl_{year}.json"
            _write_json(staging / receipt_relative, acquisition)
            result = harmonize_epoch(
                year=year, land_cover_path=source_path, imperviousness_path=None,
                destination=staging / "data" / "epochs" / str(year),
                grid=grid, window_size=spec.processing.window_size,
                mapping=USDA_CDL_MAPPING,
            )
            result["acquisition_receipt"] = receipt_relative
            epoch_results.append(result)
            epoch_receipts.append({
                "year": year, "planned_action": action,
                "acquisition_receipt": receipt_relative,
                "harmonized_land_cover": f"data/epochs/{year}/land_cover.tif",
                "valid_mask": f"data/epochs/{year}/valid_mask.tif",
                "source_handoff_id": acquisition.get("source_handoff_id"),
                "source_checksum_sha256": acquisition["source_checksum_sha256"],
                "network_bytes": acquisition["total_network_bytes"],
                "reused_bytes": acquisition["reused_bytes"],
            })

        common_result = analyze_common_all_epoch_footprint(
            epoch_results=epoch_results,
            destination=staging / "analysis" / "common_all_epoch",
            grid=grid,
            window_size=spec.processing.window_size,
            mapping=USDA_CDL_MAPPING,
        )
        interval_results = []
        for before, after in zip(epoch_results, epoch_results[1:]):
            interval_results.append(analyze_interval(
                before_year=int(before["year"]), after_year=int(after["year"]),
                before_land_cover=Path(before["land_cover"]), after_land_cover=Path(after["land_cover"]),
                before_imperviousness=None, after_imperviousness=None,
                destination=staging / "analysis" / "intervals" / f"{before['year']}_{after['year']}",
                grid=grid, window_size=spec.processing.window_size, mapping=USDA_CDL_MAPPING,
            ))
        if len(epoch_results) == 2:
            endpoint_result = interval_results[0]
        else:
            before, after = epoch_results[0], epoch_results[-1]
            endpoint_result = analyze_interval(
                before_year=int(before["year"]), after_year=int(after["year"]),
                before_land_cover=Path(before["land_cover"]), after_land_cover=Path(after["land_cover"]),
                before_imperviousness=None, after_imperviousness=None,
                destination=staging / "analysis" / "endpoint" / f"{before['year']}_{after['year']}",
                grid=grid, window_size=spec.processing.window_size, mapping=USDA_CDL_MAPPING,
            )

        context_plan = plan["asset_plan"].get("context_imagery") or {}
        context_receipt = {
            "schema_version": "fasterraster.context-imagery-receipt/v1",
            "status": "UNAVAILABLE", "role": "endpoint_geographic_context_only",
            "source_id": NAIP_SOURCE_ID, "year": context_plan.get("year"),
            "network_bytes": 0, "reused_bytes": 0,
            "limitation": "NAIP is visual context only and is not historical change evidence.",
        }
        context_result = None
        context_cache = context_plan.get("cache")
        if isinstance(context_cache, dict) and context_cache.get("path"):
            source_context = Path(str(context_cache["path"]))
            if _sha256(source_context) != context_cache["source_checksum"]:
                raise HumanDevelopmentError("cached NAIP context checksum changed")
            context_destination = staging / "data" / "context" / f"naip_{context_plan['year']}_natural_color.tif"
            context_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_context, context_destination)
            total_reused += source_context.stat().st_size
            context_receipt.update({
                "status": "REUSED", "source_handoff_id": context_cache["source_handoff_id"],
                "source_checksum_sha256": context_cache["source_checksum"],
                "output": context_destination.relative_to(staging).as_posix(),
                "reused_bytes": source_context.stat().st_size,
            })
            context_result = {"path": context_destination, "year": context_plan["year"], "receipt": context_receipt}
        _write_json(staging / "context_imagery_receipt.json", context_receipt)
        methodology = methodology_receipt(grid, USDA_CDL_MAPPING)
        _write_json(staging / "methodology_receipt.json", methodology)
        preview = render_cdl_proxy_preview(
            staging / "preview" / "human_development_change_4k.png",
            study_name=spec.name, comparison_mode=spec.comparison_mode,
            bbox=spec.area.bbox,
            epoch_results=epoch_results, endpoint_result=endpoint_result,
            source_contract={**spec.sources.model_dump(mode="json"), "mapping_contract_sha256": USDA_CDL_MAPPING.sha256},
            grid=grid.as_dict(), context_result=context_result,
            interval_results=interval_results, network_bytes=total_network, reused_bytes=total_reused,
            preview_emphasis=spec.outputs.preview_emphasis,
        )
        receipt = {
            "schema_version": "fasterraster.human-development-workflow-receipt/v2",
            "workflow_version": "human_development_change/cdl-proxy-v1",
            "final_status": "PASS", "workflow": spec.workflow_id,
            "comparison_mode": spec.comparison_mode, "published_handoff_id": final.name,
            "requested_name": spec.name, "requested_bbox_epsg_4326": list(spec.area.bbox),
            "source_id": CDL_SOURCE_ID, "source_endpoint": CDL_ENDPOINT,
            "mapping_id": USDA_CDL_MAPPING.mapping_id,
            "mapping_contract_sha256": USDA_CDL_MAPPING.sha256,
            "scientific_claim": USDA_CDL_MAPPING.scientific_claim,
            "epochs": [epoch.year for epoch in spec.epochs], "epoch_assets": epoch_receipts,
            "per_epoch_valid_footprint": [
                item["per_epoch_valid_footprint"] for item in common_result["epoch_statistics"]
            ],
            "common_all_epoch_footprint": {
                "mask": Path(common_result["mask"]).relative_to(staging).as_posix(),
                "statistics": Path(common_result["statistics_path"]).relative_to(staging).as_posix(),
                "epoch_statistics": common_result["epoch_statistics"],
            },
            "preview_emphasis": spec.outputs.preview_emphasis,
            "adjacent_intervals": [{
                "before_year": item["before_year"], "after_year": item["after_year"],
                "statistics": Path(item["statistics_path"]).relative_to(staging).as_posix(),
            } for item in interval_results],
            "endpoint_comparison": {
                "before_year": endpoint_result["before_year"], "after_year": endpoint_result["after_year"],
                "statistics": Path(endpoint_result["statistics_path"]).relative_to(staging).as_posix(),
            },
            "target_grid": grid.as_dict(), "total_network_bytes": total_network,
            "raster_network_bytes": total_network, "total_reused_bytes": total_reused,
            "context_imagery_receipt": "context_imagery_receipt.json",
            "methodology_receipt": "methodology_receipt.json",
            "source_mapping_contract": "source_mapping_contract.json",
            "preview": preview.relative_to(staging).as_posix(), "preview_sha256": _sha256(preview),
            "transition_reconciliation": all(item["statistics"]["transition_reconciliation"]["reconciles"] for item in interval_results),
            "limitations": methodology["source_qualification"],
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        _write_json(staging / "workflow_receipt.json", receipt)
        manifest = {
            "schema_version": 3, "operation_status": "completed", "verification_status": "PASS",
            "workflow": spec.workflow_id, "source_id": CDL_SOURCE_ID,
            "mapping_id": USDA_CDL_MAPPING.mapping_id, "network_bytes": total_network,
            "reused_bytes": total_reused, "workflow_receipt": "workflow_receipt.json",
            "methodology_receipt": "methodology_receipt.json", "preview": receipt["preview"],
            "warnings": [
                "USDA CDL-derived mapped development proxy change; not authoritative Annual NLCD change.",
                "CDL is crop-focused and non-agricultural changes may include classification or production differences.",
                "No population, economic, construction-date, occupancy, cadastral, or causal claim is supported.",
            ],
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        _write_json(staging / "manifest.json", manifest)
        _regenerate_checksums(staging)
        _assert_no_staging_provenance(staging)
        preview_relative = preview.relative_to(staging)
    final_preview = final / preview_relative
    if open_preview:
        _open_final_preview(final_preview)
    return final_preview
