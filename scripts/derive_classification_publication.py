from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from faster_raster.ag_classification_contracts import (
    CDL_SURFACE_SUPERCLASSES,
)
from faster_raster.ag_classification_publication import (
    render_classification_audit,
)
from faster_raster.ag_execution import (
    _assert_no_staging_provenance,
    _regenerate_checksums,
    _validate_required_artifacts,
)
from faster_raster.ag_recipes import AgriculturalRecipeV3, load_named_recipe
from faster_raster.preview_open import inspect_handoff, is_finalized_handoff


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_path(value: Any) -> PurePosixPath:
    if not isinstance(value, str):
        raise ValueError("artifact path must be handoff-relative text")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"artifact path is not handoff-relative: {value}")
    return path


def _analytical_hashes(handoff: Path) -> dict[str, str]:
    paths = [
        *sorted((handoff / "data").glob("*.tif")),
        *sorted((handoff / "analysis" / "classification").glob("*")),
    ]
    return {
        path.relative_to(handoff).as_posix(): _sha256(path)
        for path in paths
        if path.is_file()
    }


def _raw_naip_evidence(handoff: Path, year: int) -> list[Any] | None:
    catalog_path = handoff / "metadata" / f"naip_{year}_catalog.json"
    if not catalog_path.is_file():
        return None
    catalog = _read_json(catalog_path)
    encoded: dict[str, Any] = {}
    for feature in catalog.get("features", []):
        value = feature.get("attributes", {}).get("acquisition_date")
        if value is not None:
            encoded[json.dumps(value, sort_keys=True)] = value
    return [encoded[key] for key in sorted(encoded)] or None


def _classification_result(
    handoff: Path,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    analysis = handoff / "analysis" / "classification"
    agreement = _read_json(analysis / "disagreement_summary.json")
    counts = agreement.get("post_sieve_class_counts", {})
    valid_source_pixels = sum(int(value) for value in counts.values())
    return {
        "paths": {
            "classification": handoff
            / "data"
            / next(
                path.name
                for path in sorted((handoff / "data").glob(
                    "naip_*_surface_classification.cog.tif"
                ))
            ),
            "confidence": handoff
            / "data"
            / next(
                path.name
                for path in sorted((handoff / "data").glob(
                    "naip_*_classification_confidence.cog.tif"
                ))
            ),
            "agreement": handoff
            / "data"
            / next(
                path.name
                for path in sorted((handoff / "data").glob(
                    "naip_*_cdl_agreement_state.cog.tif"
                ))
            ),
        },
        "metrics": _read_json(analysis / "weak_label_metrics.json"),
        "training_receipt": _read_json(
            analysis / "training_receipt.json"
        ),
        "agreement": agreement,
        "source_validation": receipt["four_band_source_verification"],
        "inference": {
            "post_sieve_class_counts": counts,
            "valid_source_pixels": valid_source_pixels,
        },
        "mapping": CDL_SURFACE_SUPERCLASSES.as_dict(),
        "mapping_sha256": CDL_SURFACE_SUPERCLASSES.sha256,
    }


def derive_publication(
    source: Path,
    output_root: Path,
    *,
    name: str,
) -> Path:
    source = source.resolve()
    output_root = output_root.resolve()
    if not is_finalized_handoff(source):
        raise ValueError(f"source is not a finalized handoff: {source}")
    if not name or name.startswith((".", "_")) or any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
        for character in name
    ):
        raise ValueError("derived handoff name contains unsupported characters")
    final = output_root / name
    if final.exists():
        raise FileExistsError(f"derived handoff already exists: {final}")
    output_root.mkdir(parents=True, exist_ok=True)
    staging = output_root / f".{name}.staging-{uuid.uuid4().hex[:10]}"
    source_hashes = _analytical_hashes(source)
    source_manifest_hash = _sha256(source / "manifest.json")
    try:
        shutil.copytree(source, staging, copy_function=shutil.copy2)
        receipt_path = next(
            iter(
                sorted(
                    staging.glob(
                        "preview/naip_cdl_classification_audit/recipe_receipt.json"
                    )
                )
            )
        )
        receipt = _read_json(receipt_path)
        cdl_year = int(receipt["requested_cdl_year"])
        actual_imagery = receipt.get("actual_imagery")
        actual_imagery = (
            actual_imagery
            if isinstance(actual_imagery, dict)
            else {}
        )
        imagery_year = int(actual_imagery.get("year", cdl_year))
        resolved_location = receipt.get("resolved_location")
        resolved_location = (
            resolved_location
            if isinstance(resolved_location, dict)
            else {}
        )
        analysis_aoi = resolved_location.get(
            "analysis_aoi_epsg_4326"
        )
        contract_repair = receipt.get("contract_repair")
        contract_repair = (
            contract_repair
            if isinstance(contract_repair, dict)
            else None
        )
        recipe = load_named_recipe(
            Path(__file__).resolve().parent.parent,
            "naip_cdl_classification_audit",
        )
        if not isinstance(recipe, AgriculturalRecipeV3):
            raise TypeError("classification publication requires recipe v3")
        classification_result = _classification_result(staging, receipt)
        source_assets: list[dict[str, Any]] = []
        reused_bytes = 0
        for asset in receipt.get("assets", []):
            updated = dict(asset)
            relative = _relative_path(updated["output_path"])
            size = (staging / relative).stat().st_size
            reused_bytes += size
            updated.update(
                {
                    "action": "reuse_direct",
                    "bytes_downloaded": 0,
                    "bytes_reused": size,
                    "reason": (
                        "checksummed analytical asset reused unchanged from "
                        f"{source.name} for publication-only derivation"
                    ),
                    "source_handoff_id": source.name,
                    "source_relative_path": relative.as_posix(),
                }
            )
            source_assets.append(updated)
        preview = (
            staging
            / "preview"
            / recipe.recipe_id
            / f"{recipe.recipe_id}_4k.png"
        )
        naip_asset = next(
            (
                asset
                for asset in receipt.get("assets", [])
                if asset.get("asset_name") == "naip_multispectral"
            ),
            None,
        )
        naip_relative = (
            _relative_path(naip_asset["output_path"])
            if isinstance(naip_asset, dict)
            else PurePosixPath(
                f"data/naip_{imagery_year}_multispectral.cog.tif"
            )
        )
        acquisition_date_evidence = _raw_naip_evidence(
            staging,
            imagery_year,
        )
        if acquisition_date_evidence is None:
            catalog_dates = actual_imagery.get(
                "catalog_acquisition_dates"
            )
            acquisition_date_evidence = (
                catalog_dates
                if isinstance(catalog_dates, list) and catalog_dates
                else None
            )
        preview, publication = render_classification_audit(
            preview,
            naip_path=staging / naip_relative,
            classification_result=classification_result,
            recipe=recipe,
            year=imagery_year,
            cdl_year=cdl_year,
            analysis_aoi_epsg_4326=analysis_aoi,
            contract_repair=contract_repair,
            acquisition_evidence={
                "acquisition_date_evidence": acquisition_date_evidence
            },
            network_bytes=0,
            reused_bytes=reused_bytes,
        )
        now = datetime.now(timezone.utc).isoformat()
        receipt["assets"] = source_assets
        receipt["completed_at_utc"] = now
        receipt["total_network_bytes"] = 0
        receipt["total_reused_bytes"] = reused_bytes
        receipt["classification"]["publication"] = publication
        receipt["artifact_accounting"]["downloaded_bytes"] = 0
        receipt["artifact_accounting"]["reused_bytes"] = reused_bytes
        receipt["derived_publication"] = {
            "operation": "publication_only",
            "source_handoff_id": source.name,
            "source_manifest_sha256": source_manifest_hash,
            "network_bytes": 0,
            "analytical_rasters_modified": False,
            "analytical_artifact_hashes": source_hashes,
        }
        _write_json(receipt_path, receipt)
        _regenerate_checksums(preview.parent)

        asset_plan = _read_json(staging / "asset_plan.json")
        asset_plan["assets"] = [
            {
                "action": "reuse_direct",
                "asset_name": asset["asset_name"],
                "candidate": {
                    "source_handoff_id": source.name,
                    "source_relative_path": asset["output_path"],
                    "sha256": asset["sha256"],
                },
                "reason": "publication-only derivation from verified source handoff",
                "resampling": asset["resampling"],
                "spatial_relationship": "exact",
                "tolerance_degrees": 0.0,
            }
            for asset in source_assets
        ]
        asset_plan["network_required_assets"] = []
        asset_plan["reuse_mode"] = "only"
        asset_plan["published_handoff_id"] = name
        asset_plan["published_handoff_relative_path"] = name
        asset_plan["derived_from_handoff_id"] = source.name
        asset_plan["source_asset_plan_sha256"] = _sha256(
            source / "asset_plan.json"
        )
        _write_json(staging / "asset_plan.json", asset_plan)

        manifest = _read_json(staging / "manifest.json")
        manifest["operation_status"] = "completed"
        manifest["verification_status"] = "PASS"
        manifest["completed_at_utc"] = now
        manifest["network_bytes"] = 0
        manifest["reused_bytes"] = reused_bytes
        manifest["requests"] = []
        manifest["asset_plan"] = "asset_plan.json"
        manifest["recipe_receipt"] = receipt_path.relative_to(staging).as_posix()
        manifest["order"]["name"] = name
        manifest["order"]["reuse_mode"] = "only"
        for layer in manifest.get("layers", []):
            layer["resolution_action"] = "reuse_direct"
            layer["source_handoff_id"] = source.name
        manifest["classification"]["artifact_accounting"] = receipt[
            "artifact_accounting"
        ]
        manifest["derived_publication"] = receipt["derived_publication"]
        _write_json(staging / "manifest.json", manifest)

        resolved_path = staging / "resolved_config.json"
        resolved = (
            _read_json(resolved_path)
            if resolved_path.is_file()
            else {
                "compatibility": {
                    "source_resolved_config_present": False,
                    "note": (
                        "publication-only derivation synthesized this "
                        "metadata wrapper for an older finalized handoff"
                    ),
                }
            }
        )
        resolved["derived_publication"] = {
            "source_handoff_id": source.name,
            "network_allowed": False,
            "reuse_mode": "only",
        }
        _write_json(resolved_path, resolved)

        if _analytical_hashes(staging) != source_hashes:
            raise RuntimeError(
                "publication derivation modified an analytical artifact"
            )
        _validate_required_artifacts(recipe, staging, preview)
        _regenerate_checksums(staging)
        _assert_no_staging_provenance(staging)
        staging.replace(final)
    except Exception:
        failed = output_root / f".failed-{name}-{uuid.uuid4().hex[:8]}"
        if staging.exists():
            staging.replace(failed)
        raise
    report = inspect_handoff(final)
    if report["status"] != "completed" or report["network_bytes"] != 0:
        raise RuntimeError(f"derived handoff inspection failed: {report}")
    return final


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create a zero-network classification publication from a finalized "
            "classification handoff without rerunning the model."
        )
    )
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "outputs" / "handoffs",
    )
    parser.add_argument("--name")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = args.name or f"{args.source.name}_publication_{stamp}"
    final = derive_publication(
        args.source,
        args.output_root,
        name=name,
    )
    report = inspect_handoff(final)
    print(
        json.dumps(
            {
                "status": "PASS",
                "handoff": str(final),
                "preview": report["preview"],
                "network_bytes": report["network_bytes"],
                "reused_bytes": report["reused_bytes"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
