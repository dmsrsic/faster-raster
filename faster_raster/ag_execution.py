from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

from faster_raster.ag_assets import (
    ASSET_PATTERNS,
    AssetDecision,
    AssetRecord,
    asset_plan_document,
    compile_asset_plan,
    discover_cached_assets,
    inspect_asset,
    spatial_relationship,
)
from faster_raster.ag_geography import BBoxValidationError, validate_bbox_text
from faster_raster.ag_recipes import (
    AgriculturalRecipe,
    AgriculturalRecipeV3,
    RecipeLoadError,
    load_named_recipe,
)


ASSET_FILENAMES = {
    "natural": "naip_{year}_natural_color.cog.tif",
    "naip_multispectral": "naip_{year}_multispectral.cog.tif",
    "ndvi": "naip_{year}_ndvi_color.cog.tif",
    "cdl_classes": "cdl_{year}_classes.cog.tif",
    "cdl_color": "cdl_{year}_color.cog.tif",
    "hillshade": "three_dep_hillshade.cog.tif",
}
SERVICE_ENDPOINTS = {
    "natural": "USGS NAIP ImageServer/exportImage (NaturalColor)",
    "naip_multispectral": "USGS NAIP ImageServer/exportImage (raw bands 0,1,2,3)",
    "ndvi": "USGS NAIP ImageServer/exportImage (NDVI_Color)",
    "cdl_classes": "USDA CDL ImageServer/exportImage (raw classes)",
    "cdl_color": "USDA CDL ImageServer/exportImage (croptypes)",
    "hillshade": "USGS 3DEP ImageServer/exportImage (Hillshade Gray)",
}
REQUEST_PREFIXES = {
    "natural": "naip_natural_",
    "naip_multispectral": "naip_multispectral_",
    "ndvi": "naip_ndvi_",
    "cdl_classes": "cdl_raw_",
    "cdl_color": "cdl_color_",
    "hillshade": "three_dep_hillshade_",
}


class RecipeExecutionError(RuntimeError):
    pass


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def configured_handoff_root(root: Path) -> Path:
    return Path(
        os.environ.get("FASTERRASTER_HANDOFF_ROOT", str(root / "outputs" / "handoffs"))
    )


def _safe_name(value: str) -> str:
    allowed = "".join(character if character.isalnum() or character in "-_" else "_" for character in value)
    result = allowed.strip("_-")
    if not result:
        raise RecipeExecutionError("--name must contain a letter or number")
    return result


def _parse_bbox(value: str) -> tuple[float, float, float, float]:
    try:
        return validate_bbox_text(value)
    except BBoxValidationError as exc:
        raise RecipeExecutionError(f"--bbox {exc}") from exc


def _validate_dates(start: str, end: str, year: int) -> None:
    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
    except ValueError as exc:
        raise RecipeExecutionError("--start and --end must be ISO dates") from exc
    if start_date >= end_date:
        raise RecipeExecutionError("timeframe start must precede end")
    if start_date.year != year or end_date.year != year:
        raise RecipeExecutionError("growing-season dates must match --cdl-year")


@contextlib.contextmanager
def handoff_transaction(final_path: Path) -> Iterator[Path]:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    staging = final_path.parent / f".{final_path.name}.staging-{uuid.uuid4().hex[:8]}"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        yield staging
        if final_path.exists():
            raise RecipeExecutionError(f"final handoff already exists: {final_path}")
        os.replace(staging, final_path)
    except BaseException as exc:
        _write_json(
            staging / "failure_report.json",
            {
                "schema_version": 1,
                "final_status": "FAIL",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "completed_handoff_created": False,
                "failed_at_utc": datetime.now(timezone.utc).isoformat(),
            },
        )
        shutil.rmtree(staging / "_work", ignore_errors=True)
        failed = final_path.parent / f".failed-{final_path.name}-{uuid.uuid4().hex[:6]}"
        try:
            os.replace(staging, failed)
        except OSError:
            pass
        raise


def _link_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def _warp_reused_asset(
    source: Path,
    destination: Path,
    bbox: tuple[float, float, float, float],
    resampling: str,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".working.tif")
    subprocess.run(
        [
            "gdalwarp",
            "-q",
            "-overwrite",
            "-of",
            "GTiff",
            "-t_srs",
            "EPSG:3857",
            "-te_srs",
            "EPSG:4326",
            "-te",
            *(str(value) for value in bbox),
            "-r",
            resampling,
            str(source),
            str(temporary),
        ],
        check=True,
    )
    subprocess.run(
        [
            "gdal_translate",
            "-q",
            "-of",
            "COG",
            "-co",
            "COMPRESS=DEFLATE",
            "-co",
            "BLOCKSIZE=512",
            str(temporary),
            str(destination),
        ],
        check=True,
    )
    temporary.unlink(missing_ok=True)


def _resolve_reused(
    decisions: Sequence[AssetDecision],
    staging: Path,
    bbox: tuple[float, float, float, float],
    year: int,
) -> dict[str, Path]:
    resolved: dict[str, Path] = {}
    for decision in decisions:
        if not decision.action.startswith("reuse_") or decision.candidate is None:
            continue
        source = Path(decision.candidate.local_path)
        destination = staging / "data" / ASSET_FILENAMES[decision.asset_name].format(year=year)
        if decision.action == "reuse_direct":
            _link_or_copy(source, destination)
        else:
            _warp_reused_asset(source, destination, bbox, decision.resampling)
        resolved[decision.asset_name] = destination
    return resolved


def _run_selective_acquisition(
    root: Path,
    staging: Path,
    assets: Sequence[str],
    *,
    name: str,
    bbox: tuple[float, float, float, float],
    start: str,
    end: str,
    year: int,
    recipe: AgriculturalRecipe,
    max_total_bytes: int,
    service_tile_size: int,
    naip_resolution_m: float | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    if not assets:
        return {"network_bytes": 0, "requests": [], "layers": []}
    command = [
        sys.executable,
        str(root / "scripts" / "fr-cook-ag"),
        "--asset-only",
        "--assets",
        ",".join(assets),
        "--output-dir",
        str(staging),
        "--name",
        name,
        "--bbox=" + ",".join(str(value) for value in bbox),
        "--start",
        start,
        "--end",
        end,
        "--cdl-year",
        str(year),
        "--portion",
        recipe.defaults.portion,
        "--naip-resolution",
        str(
            naip_resolution_m
            if naip_resolution_m is not None
            else recipe.defaults.naip_resolution_meters
        ),
        "--service-tile-size",
        str(service_tile_size),
        "--max-total-bytes",
        str(max_total_bytes),
        "--preview-width",
        str(recipe.defaults.preview_width),
    ]
    result = runner(command, cwd=root, check=False, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout, end="")
    if result.returncode != 0:
        raise RecipeExecutionError(
            "selective acquisition failed: " + (result.stderr or result.stdout or "unknown error").strip()
        )
    manifest_path = staging / "manifest.json"
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecipeExecutionError("selective acquisition did not write a valid manifest") from exc


def _find_resolved_paths(staging: Path, year: int, names: Sequence[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for name in names:
        expected = staging / "data" / ASSET_FILENAMES[name].format(year=year)
        if expected.is_file():
            result[name] = expected
            continue
        for pattern in ASSET_PATTERNS[name]:
            matches = sorted((staging / "data").glob(pattern))
            if matches:
                result[name] = matches[0]
                break
    return result


def _verify_resolved(
    staging: Path,
    recipe: AgriculturalRecipe,
    bbox: tuple[float, float, float, float],
    year: int,
) -> dict[str, AssetRecord]:
    paths = _find_resolved_paths(staging, year, list(recipe.required_assets))
    missing = sorted(set(recipe.required_assets) - set(paths))
    if missing:
        raise RecipeExecutionError(f"resolved asset verification missing: {', '.join(missing)}")
    records: dict[str, AssetRecord] = {}
    for name, path in paths.items():
        record = inspect_asset(path, name, staging)
        relationship = spatial_relationship(record.bbox_epsg_4326, bbox)
        if record.validation_state != "valid" or relationship not in {"exact", "contains"}:
            raise RecipeExecutionError(
                f"resolved {name} failed validation: {record.validation_errors}; spatial={relationship}"
            )
        if name in {
            "natural",
            "naip_multispectral",
            "ndvi",
            "cdl_classes",
            "cdl_color",
        } and record.temporal_key != year:
            raise RecipeExecutionError(f"resolved {name} has wrong year {record.temporal_key}")
        if name in {"natural", "naip_multispectral", "ndvi"} and (
            record.pixel_size_m is None
            or record.pixel_size_m > recipe.maximum_naip_pixel_size_m + 0.01
        ):
            raise RecipeExecutionError(f"resolved {name} does not meet recipe resolution")
        records[name] = record
    return records


def _validate_recipe_outputs(preview: Path) -> None:
    inventory = preview.parent / "class_inventory.csv"
    required = [preview, inventory]
    missing = [str(path) for path in required if not path.is_file() or path.stat().st_size == 0]
    if missing:
        raise RecipeExecutionError(
            "recipe output validation failed: " + ", ".join(missing)
        )


def _validate_classification_recipe_outputs(
    staging: Path,
    preview: Path,
) -> None:
    required = [
        preview,
        preview.parent / "classification_legend.json",
        staging / "analysis/classification/class_area_inventory.csv",
        *sorted((staging / "data").glob("naip_*_surface_classification.cog.tif")),
        *sorted((staging / "data").glob("naip_*_classification_confidence.cog.tif")),
        *sorted((staging / "data").glob("naip_*_cdl_agreement_state.cog.tif")),
    ]
    if len(required) < 6 or any(
        not path.is_file() or path.stat().st_size == 0 for path in required
    ):
        raise RecipeExecutionError("classification output validation failed")


def _validate_required_artifacts(
    recipe: AgriculturalRecipe,
    staging: Path,
    preview: Path,
) -> None:
    known = {
        "asset_plan.json": staging / "asset_plan.json",
        "recipe_receipt.json": preview.parent / "recipe_receipt.json",
        "class_inventory.csv": preview.parent / "class_inventory.csv",
        "preview_4k_png": preview,
        "checksums.sha256": staging / "checksums.sha256",
    }
    if isinstance(recipe, AgriculturalRecipeV3):
        analysis = staging / "analysis" / "classification"
        data = staging / "data"
        first = lambda pattern: next(iter(sorted(data.glob(pattern))), data / "missing")
        known.update(
            {
                "classification_legend.json": preview.parent / "classification_legend.json",
                "classification_cog": first("naip_*_surface_classification.cog.tif"),
                "confidence_cog": first("naip_*_classification_confidence.cog.tif"),
                "agreement_cog": first("naip_*_cdl_agreement_state.cog.tif"),
                "cdl_superclasses_cog": data / "cdl_superclasses.cog.tif",
                "cdl_training_cores_cog": data / "cdl_training_cores.cog.tif",
                "holdout_confusion_matrix.csv": analysis / "holdout_confusion_matrix.csv",
                "holdout_confusion_matrix.json": analysis / "holdout_confusion_matrix.json",
                "weak_label_metrics.json": analysis / "weak_label_metrics.json",
                "training_receipt.json": analysis / "training_receipt.json",
                "model_receipt.json": analysis / "model_receipt.json",
                "feature_contract.json": analysis / "feature_contract.json",
                "class_agreement_matrix.csv": analysis / "class_agreement_matrix.csv",
                "class_agreement_matrix.json": analysis / "class_agreement_matrix.json",
                "disagreement_summary.json": analysis / "disagreement_summary.json",
                "class_area_inventory.csv": analysis / "class_area_inventory.csv",
            }
        )
    unsupported = sorted(set(recipe.required_output_artifacts) - set(known))
    if unsupported:
        raise RecipeExecutionError(
            "recipe declares unsupported output artifacts: " + ", ".join(unsupported)
        )
    missing = [
        name
        for name in recipe.required_output_artifacts
        if not known[name].is_file() or known[name].stat().st_size == 0
    ]
    if missing:
        raise RecipeExecutionError(
            "required output artifact validation failed: " + ", ".join(missing)
        )


def _open_final_preview(path: Path) -> None:
    if shutil.which("explorer.exe") and shutil.which("wslpath"):
        windows_path = subprocess.run(
            ["wslpath", "-w", str(path.resolve())],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.Popen(
            ["explorer.exe", windows_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    elif shutil.which("xdg-open"):
        subprocess.Popen(
            ["xdg-open", str(path.resolve())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def _regenerate_checksums(directory: Path) -> Path:
    destination = directory / "checksums.sha256"
    with destination.open("w", encoding="utf-8") as stream:
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path != destination and "_work" not in path.parts:
                stream.write(f"{_sha256(path)}  {path.relative_to(directory)}\n")
    return destination


def _assert_no_staging_provenance(staging: Path) -> None:
    transient = (".staging-", str(staging.resolve()))
    suffixes = {".json", ".md", ".txt", ".csv", ".html", ".yaml", ".yml"}
    leaks: list[str] = []
    for path in sorted(staging.rglob("*")):
        if not path.is_file() or (path.suffix.lower() not in suffixes and path.name != "checksums.sha256"):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(value in content for value in transient):
            leaks.append(path.relative_to(staging).as_posix())
    if leaks:
        raise RecipeExecutionError(
            "transient staging provenance would be published in: " + ", ".join(leaks)
        )


def _asset_network_bytes(manifest: dict[str, Any], name: str) -> int:
    prefix = REQUEST_PREFIXES[name]
    return sum(
        int(request.get("bytes", 0))
        for request in manifest.get("requests", [])
        if str(request.get("label", "")).startswith(prefix)
    )


def execute_recipe(
    root: Path,
    *,
    recipe: AgriculturalRecipe,
    recipe_raw: dict[str, Any],
    name: str,
    bbox: tuple[float, float, float, float],
    start: str,
    end: str,
    year: int,
    reuse_mode: str,
    open_preview: bool,
    max_total_bytes: int,
    service_tile_size: int,
    renderer: Callable[..., Path],
    naip_resolution_m: float | None = None,
) -> Path:
    if isinstance(recipe, AgriculturalRecipeV3):
        from faster_raster.ag_classification import classification_dependency_status

        if not classification_dependency_status()["available"]:
            raise RecipeExecutionError(
                "This recipe requires the FasterRaster classification extra: "
                "pip install -e '.[classification]'"
            )
    cache_root = Path(
        os.environ.get(
            "FASTERRASTER_AG_CACHE_ROOT",
            str(root / "outputs" / "handoffs"),
        )
    )
    inventory = (
        []
        if reuse_mode == "never"
        else discover_cached_assets(cache_root)
    )
    decisions = compile_asset_plan(
        recipe,
        inventory,
        bbox,
        year,
        reuse_mode,  # type: ignore[arg-type]
    )
    plan = asset_plan_document(
        recipe,
        decisions,
        bbox=bbox,
        start=start,
        end=end,
        year=year,
        reuse_mode=reuse_mode,
        requested_resolution_m=naip_resolution_m,
    )
    handoff_root = configured_handoff_root(root)
    final = handoff_root / f"{_safe_name(name)}_{_stamp()}"
    plan["published_handoff_id"] = final.name
    plan["published_handoff_relative_path"] = final.name
    plan["cache_inventory_policy"] = "configured_local_cache"
    acquired_names = [
        decision.asset_name
        for decision in decisions
        if decision.action in {"acquire", "acquire_and_mosaic"}
    ]
    blocking = [decision.asset_name for decision in decisions if decision.action == "reject"]

    with handoff_transaction(final) as staging:
        _write_json(staging / "asset_plan.json", plan)
        if blocking:
            raise RecipeExecutionError(
                "REUSE_ONLY: network prohibited; unavailable or incompatible assets: "
                + ", ".join(blocking)
            )
        _resolve_reused(decisions, staging, bbox, year)
        acquisition_manifest = (
            _run_selective_acquisition(
                root,
                staging,
                acquired_names,
                name=name,
                bbox=bbox,
                start=start,
                end=end,
                year=year,
                recipe=recipe,
                max_total_bytes=max_total_bytes,
                service_tile_size=service_tile_size,
                naip_resolution_m=naip_resolution_m,
            )
            if acquired_names
            else {"network_bytes": 0, "requests": [], "layers": []}
        )
        raw_acquisition_evidence: dict[str, Any] | None = None
        if isinstance(recipe, AgriculturalRecipeV3) and "naip_multispectral" in acquired_names:
            from faster_raster.ag_classification_acquisition import (
                RawNaipEvidenceError,
                validate_raw_naip_acquisition_evidence,
            )

            try:
                raw_acquisition_evidence = validate_raw_naip_acquisition_evidence(
                    acquisition_manifest,
                    requested_year=year,
                )
            except RawNaipEvidenceError as exc:
                raise RecipeExecutionError(
                    f"acquired naip_multispectral failed evidence validation: {exc}"
                ) from exc
        resolved = _verify_resolved(staging, recipe, bbox, year)
        compatibility_assets = {
            key: str(_find_resolved_paths(staging, year, [key]).get(key))
            if key in recipe.required_assets
            else None
            for key in ASSET_PATTERNS
        }
        spectral = (
            resolved["naip_multispectral"]
            if isinstance(recipe, AgriculturalRecipeV3)
            else resolved["natural"]
        )
        compatibility = {
            "compatible": True,
            "assets": compatibility_assets,
            "published_handoff_id": final.name,
            "published_handoff_relative_path": final.name,
            "natural_pixel_size_m": spectral.pixel_size_m,
            "spectral_pixel_size_m": spectral.pixel_size_m,
            "effective_naip_resolution_m": (
                naip_resolution_m
                if naip_resolution_m is not None
                else recipe.defaults.naip_resolution_meters
            ),
            "maximum_naip_pixel_size_m": recipe.maximum_naip_pixel_size_m,
            "checks": {decision.asset_name: decision.action for decision in decisions},
            "network_bytes": int(acquisition_manifest.get("network_bytes", 0)),
        }
        classification_result: dict[str, Any] | None = None
        publication_receipt: dict[str, Any] | None = None
        if isinstance(recipe, AgriculturalRecipeV3):
            from faster_raster.ag_classification import execute_classification
            from faster_raster.ag_classification_publication import (
                render_classification_audit,
            )

            classification_result = execute_classification(
                Path(resolved["naip_multispectral"].local_path),
                Path(resolved["cdl_classes"].local_path),
                staging,
                recipe,
                year=year,
            )
            output_dir = staging / "preview" / recipe.recipe_id
            preview_path = (
                output_dir
                / f"{recipe.recipe_id}_4k.png"
            )
            catalog_features = (
                acquisition_manifest.get("naip", {}).get(
                    "catalog_features",
                    [],
                )
                if isinstance(acquisition_manifest.get("naip"), dict)
                else []
            )
            acquisition_dates = sorted(
                {
                    item.get("attributes", {}).get("acquisition_date")
                    for item in catalog_features
                    if item.get("attributes", {}).get("acquisition_date")
                    is not None
                },
                key=lambda value: (type(value).__name__, str(value)),
            )
            preview, publication_receipt = render_classification_audit(
                preview_path,
                naip_path=Path(resolved["naip_multispectral"].local_path),
                classification_result=classification_result,
                recipe=recipe,
                year=year,
                acquisition_evidence={
                    "acquisition_date_evidence": acquisition_dates or None,
                },
                network_bytes=int(acquisition_manifest.get("network_bytes", 0)),
                reused_bytes=sum(
                    Path(decision.candidate.local_path).stat().st_size
                    for decision in decisions
                    if decision.candidate is not None
                ),
            )
            _validate_classification_recipe_outputs(staging, preview)
        else:
            preview = renderer(
                root,
                staging,
                recipe_raw,
                compatibility,
                name,
                bbox,
                start,
                end,
                year,
                False,
            )
            output_dir = preview.parent
            _validate_recipe_outputs(preview)
        per_asset: list[dict[str, Any]] = []
        for decision in decisions:
            output_record = resolved[decision.asset_name]
            acquired = decision.action in {"acquire", "acquire_and_mosaic"}
            source_record = decision.candidate
            source_reference: dict[str, Any] = {
                "source_handoff_id": None,
                "source_relative_path": None,
            }
            if source_record:
                source_reference.update(source_record.stable_source_reference())
            per_asset.append(
                {
                    "asset_name": decision.asset_name,
                    "action": decision.action,
                    "reason": decision.reason,
                    **source_reference,
                    "source_contract": SERVICE_ENDPOINTS[decision.asset_name] if acquired else None,
                    "output_path": str(Path(output_record.local_path).relative_to(staging)),
                    "source_crs": source_record.crs if source_record else output_record.crs,
                    "output_crs": output_record.crs,
                    "source_resolution_m": source_record.pixel_size_m if source_record else output_record.pixel_size_m,
                    "output_resolution_m": output_record.pixel_size_m,
                    "resampling": decision.resampling,
                    "cropped": "crop" in decision.action,
                    "reprojected": "reproject" in decision.action,
                    "bytes_downloaded": _asset_network_bytes(acquisition_manifest, decision.asset_name) if acquired else 0,
                    "bytes_reused": Path(source_record.local_path).stat().st_size if source_record else 0,
                    "sha256": _sha256(Path(output_record.local_path)),
                    "validation_result": "PASS",
                }
            )
        generated_output_paths = sorted(
            {
                path.relative_to(staging).as_posix()
                for path in staging.rglob("*")
                if path.is_file() and "_work" not in path.parts
            }
            | {
                (output_dir / "recipe_receipt.json")
                .relative_to(staging)
                .as_posix(),
                "checksums.sha256",
                "manifest.json",
            }
        )
        receipt = {
            "schema_version": 3,
            "final_status": "PASS",
            "published_handoff_id": final.name,
            "recipe_id": recipe.recipe_id,
            "recipe_schema_version": recipe.schema_version,
            "requested_name": name,
            "requested_bbox_epsg_4326": list(bbox),
            "requested_timeframe": {"start": start, "end": end},
            "requested_cdl_year": year,
            "effective_naip_resolution_m": (
                naip_resolution_m
                if naip_resolution_m is not None
                else recipe.defaults.naip_resolution_meters
            ),
            "reuse_mode": reuse_mode,
            "assets": per_asset,
            "total_network_bytes": int(acquisition_manifest.get("network_bytes", 0)),
            "total_reused_bytes": sum(item["bytes_reused"] for item in per_asset),
            "asset_plan": str((staging / "asset_plan.json").relative_to(staging)),
            "generated_output_paths": generated_output_paths,
            "blocking_failures": [],
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        if classification_result is not None:
            receipt.update(
                {
                    "scientific_claim": recipe.scientific_claim,
                    "unsupported_claims": recipe.unsupported_claims,
                    "mapping": {
                        "mapping_id": classification_result["mapping"]["mapping_id"],
                        "contract_version": classification_result["mapping"][
                            "contract_version"
                        ],
                        "sha256": classification_result["mapping_sha256"],
                    },
                    "four_band_source_verification": {
                        **classification_result["source_validation"],
                        "acquisition_request_evidence": (
                            raw_acquisition_evidence
                            or {"status": "REUSED", "network_request_verified": False}
                        ),
                    },
                    "classification": {
                        "model": classification_result["model_receipt"],
                        "training": classification_result["training_receipt"],
                        "weak_label_spatial_holdout_agreement": (
                            classification_result["metrics"]
                        ),
                        "agreement_audit": classification_result["agreement"],
                        "publication": publication_receipt,
                    },
                    "artifact_accounting": {
                        "downloaded_bytes": int(
                            acquisition_manifest.get("network_bytes", 0)
                        ),
                        "reused_bytes": sum(
                            item["bytes_reused"] for item in per_asset
                        ),
                        "locally_derived_artifacts": [
                            "data/cdl_superclasses.cog.tif",
                            "data/cdl_training_cores.cog.tif",
                        ],
                        "model_products": [
                            f"data/naip_{year}_surface_classification.cog.tif",
                            f"data/naip_{year}_classification_confidence.cog.tif",
                            "analysis/classification/model_receipt.json",
                        ],
                        "qa_products": [
                            f"data/naip_{year}_cdl_agreement_state.cog.tif",
                            "analysis/classification/holdout_confusion_matrix.csv",
                            "analysis/classification/holdout_confusion_matrix.json",
                            "analysis/classification/weak_label_metrics.json",
                            "analysis/classification/training_receipt.json",
                            "analysis/classification/feature_contract.json",
                            "analysis/classification/class_agreement_matrix.csv",
                            "analysis/classification/class_agreement_matrix.json",
                            "analysis/classification/disagreement_summary.json",
                            "analysis/classification/class_area_inventory.csv",
                        ],
                        "preview_products": [
                            path
                            for path in generated_output_paths
                            if path.startswith("preview/")
                        ],
                    },
                }
            )
        _write_json(output_dir / "recipe_receipt.json", receipt)
        _regenerate_checksums(output_dir)
        manifest = {
            "schema_version": 2,
            "operation_status": "completed",
            "verification_status": "PASS",
            "order": {
                "name": name,
                "bbox_epsg_4326": list(bbox),
                "time_start": start,
                "time_end": end,
                "cdl_year": year,
                "reuse_mode": reuse_mode,
                "recipe_id": recipe.recipe_id,
                "effective_naip_resolution_m": (
                    naip_resolution_m
                    if naip_resolution_m is not None
                    else recipe.defaults.naip_resolution_meters
                ),
                "network_ceiling_bytes": max_total_bytes,
            },
            "layers": [
                {
                    "name": item["asset_name"],
                    "output": item["output_path"],
                    "output_bytes": (staging / item["output_path"]).stat().st_size,
                    "output_sha256": item["sha256"],
                    "resolution_meters": item["output_resolution_m"],
                    "crs": item["output_crs"],
                    "semantic_type": resolved[item["asset_name"]].semantic_type,
                    "resolution_action": item["action"],
                    "source_handoff_id": item["source_handoff_id"],
                }
                for item in per_asset
            ],
            "network_bytes": int(acquisition_manifest.get("network_bytes", 0)),
            "reused_bytes": sum(item["bytes_reused"] for item in per_asset),
            "requests": acquisition_manifest.get("requests", []),
            "asset_plan": "asset_plan.json",
            "recipe_receipt": str((output_dir / "recipe_receipt.json").relative_to(staging)),
            "blocking_failures": [],
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        if classification_result is not None:
            manifest["classification"] = {
                "mapping_id": classification_result["mapping"]["mapping_id"],
                "mapping_version": classification_result["mapping"][
                    "contract_version"
                ],
                "mapping_sha256": classification_result["mapping_sha256"],
                "scientific_claim": recipe.scientific_claim,
                "weak_label_spatial_holdout_agreement": (
                    classification_result["metrics"]
                ),
                "artifact_accounting": receipt["artifact_accounting"],
                "four_band_source_verification": receipt["four_band_source_verification"],
            }
        _write_json(staging / "manifest.json", manifest)
        _regenerate_checksums(staging)
        _validate_required_artifacts(recipe, staging, preview)
        shutil.rmtree(staging / "_work", ignore_errors=True)
        _regenerate_checksums(staging)
        _assert_no_staging_provenance(staging)
        preview_relative = preview.relative_to(staging)

    final_preview = final / preview_relative
    if open_preview:
        _open_final_preview(final_preview)
    print("===== FASTERRASTER AG RECIPE: PASS =====")
    print(f"recipe: {recipe.recipe_id}")
    print(f"handoff: {final}")
    print(f"network_bytes: {acquisition_manifest.get('network_bytes', 0)}")
    print(f"preview: {final_preview}")
    print(f"asset_plan: {final / 'asset_plan.json'}")
    print(f"receipt: {final / output_dir.relative_to(staging) / 'recipe_receipt.json'}")
    return final_preview


def _recipe_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--recipe", required=True)
    parser.add_argument("--reuse", choices=["auto", "only", "never"], default="auto")
    parser.add_argument("--name")
    parser.add_argument("--bbox", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--cdl-year", required=True, type=int)
    parser.add_argument("--max-total-bytes", type=int)
    parser.add_argument("--service-tile-size", type=int)
    parser.add_argument("--open", action="store_true")
    return parser


def run_recipe_cli(
    root: Path,
    argv: list[str],
    *,
    renderer: Callable[..., Path],
) -> int | None:
    if not any(value == "--recipe" or value.startswith("--recipe=") for value in argv[1:]):
        return None
    try:
        args, _ = _recipe_parser().parse_known_args(argv[1:])
        recipe = load_named_recipe(root, args.recipe)
        recipe_path = root / "recipes" / "ag" / f"{args.recipe}.json"
        recipe_raw = json.loads(recipe_path.read_text(encoding="utf-8"))
        bbox = _parse_bbox(args.bbox)
        _validate_dates(args.start, args.end, args.cdl_year)
        max_total_bytes = args.max_total_bytes or recipe.defaults.max_total_bytes
        service_tile_size = args.service_tile_size or recipe.defaults.service_tile_size
        if max_total_bytes <= 0 or max_total_bytes > 20_000_000_000:
            raise RecipeExecutionError("--max-total-bytes is outside the supported range")
        if service_tile_size < 64 or service_tile_size > 10_000:
            raise RecipeExecutionError("--service-tile-size is outside the supported range")
        execute_recipe(
            root,
            recipe=recipe,
            recipe_raw=recipe_raw,
            name=args.name or f"{recipe.recipe_id}_{args.cdl_year}",
            bbox=bbox,
            start=args.start,
            end=args.end,
            year=args.cdl_year,
            reuse_mode=args.reuse,
            open_preview=args.open,
            max_total_bytes=max_total_bytes,
            service_tile_size=service_tile_size,
            renderer=renderer,
        )
        return 0
    except (RecipeLoadError, RecipeExecutionError, subprocess.SubprocessError) as exc:
        print(f"FASTERRASTER AG RECIPE: FAILED: {exc}", file=sys.stderr)
        return 2
