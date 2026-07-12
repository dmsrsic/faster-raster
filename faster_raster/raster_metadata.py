from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from faster_raster.adapter_contract import stable_json
from faster_raster.artifact_receipts import normalize_artifact_contract
from faster_raster.artifact_store import sha256_file
from faster_raster.derived_artifacts import derived_content_addressed_path, read_geotiff_info, repo_root
from faster_raster.run_receipts import write_json

METADATA_ROOT = Path("reports/metadata")


def _hash(value: dict[str, Any], *, root: Path | None = None) -> str:
    contract = {k: normalize_artifact_contract(v, root or Path.cwd()) for k, v in value.items() if k != "metadata_contract_sha256"}
    return hashlib.sha256(stable_json(contract).encode("utf-8")).hexdigest()


def compute_metadata_contract_sha256(metadata: dict[str, Any], *, root: Path | None = None) -> str:
    return _hash(metadata, root=root)


def _status(value: Any, confirmed: bool = True) -> str:
    if value is None or value == []:
        return "missing"
    return "confirmed" if confirmed else "declared_only"


def extract_raster_metadata(receipt: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    root = repo_root(root)
    derived_sha = receipt["output_sha256"]
    path = derived_content_addressed_path(derived_sha, receipt.get("output_extension") or ".tif", root=root)
    info = read_geotiff_info(path)
    bounds = info["bounds"]
    transform = info["transform"]
    block_shapes = info["block_shapes"]
    band_metadata = []
    for band in info["bands"]:
        band_metadata.append({
            **band,
            "status": {
                "dtype": _status(band.get("dtype")),
                "nodata": _status(band.get("nodata")),
                "units": _status(band.get("units")),
            },
        })
    metadata = {
        "metadata_schema_version": 1,
        "metadata_contract_sha256": "",
        "source_id": "chirps_daily_precipitation",
        "source_artifact_sha256": receipt["source_artifact_sha256"],
        "derived_artifact_sha256": derived_sha,
        "derived_artifact_receipt_sha256": receipt["derived_artifact_receipt_contract_sha256"],
        "derivation_plan_sha256": receipt["derivation_plan_contract_sha256"],
        "container": {
            "container_format": "geotiff",
            "driver": info["driver"],
            "file_extension": receipt.get("output_extension") or ".tif",
            "size_bytes": path.stat().st_size,
            "byte_order": receipt.get("validation", {}).get("byte_order"),
            "compression": info.get("compression"),
            "tiled": info.get("tiled"),
            "block_shapes": block_shapes,
            "interleave": info.get("interleave"),
            "overview_levels": info.get("overview_levels"),
            "status": {"driver": "confirmed", "compression": _status(info.get("compression")), "byte_order": _status(receipt.get("validation", {}).get("byte_order"))},
        },
        "raster_shape": {
            "width": info["width"],
            "height": info["height"],
            "band_count": info["band_count"],
            "dtypes": info["dtypes"],
            "color_interpretations": info.get("color_interpretations"),
            "status": {"width": "confirmed", "height": "confirmed", "band_count": "confirmed", "dtypes": "confirmed"},
        },
        "spatial_reference": {
            "crs_present": info.get("crs_present"),
            "crs_authority": info.get("crs_authority"),
            "crs_code": info.get("crs_code"),
            "crs_wkt": info.get("crs_wkt"),
            "crs_projjson": info.get("crs_projjson"),
            "axis_order": None,
            "coordinate_units": "degree" if info.get("crs") == "EPSG:4326" else None,
            "status": {"crs": _status(info.get("crs")), "axis_order": "unsupported", "coordinate_units": "inferred" if info.get("crs") == "EPSG:4326" else "missing"},
        },
        "grid_geometry": {
            "affine_transform": transform,
            "pixel_width": transform[0],
            "pixel_height": transform[4],
            "rotation_present": bool(transform[1] or transform[3]),
            "bounds": bounds,
            "upper_left": [bounds[0], bounds[3]],
            "lower_right": [bounds[2], bounds[1]],
            "row_direction": "north_to_south" if transform[4] < 0 else "south_to_north",
            "column_direction": "west_to_east" if transform[0] > 0 else "east_to_west",
            "pixel_registration": "area",
            "status": {"affine_transform": "confirmed", "bounds": "confirmed", "pixel_registration": "inferred"},
        },
        "band_metadata": band_metadata,
        "semantic_declarations": {
            "variable_name": "precipitation",
            "semantic_type": "daily_precipitation",
            "canonical_units": "mm/day",
            "temporal_key": "20230101",
            "temporal_support": "daily",
            "status": {
                "variable_name": "declared_only",
                "semantic_type": "declared_only",
                "canonical_units": "declared_only",
                "temporal_key": "declared_only",
                "temporal_support": "declared_only",
            },
        },
        "metadata_provenance": {
            "embedded_file_metadata": info["reader"],
            "source_registry": "declared_source_id_without_registry_mutation",
            "task_contract": "example_wave1_climate_stack",
            "filename_or_temporal_key": "artifact_catalog_temporal_key_20230101",
            "derived_artifact_receipt": receipt["derived_artifact_receipt_contract_sha256"],
        },
        "provenance_status": {
            "embedded_file_metadata": "confirmed",
            "source_registry": "declared_only",
            "task_contract": "declared_only",
            "filename_or_temporal_key": "declared_only",
            "derived_artifact_receipt": "confirmed",
        },
    }
    metadata["metadata_contract_sha256"] = compute_metadata_contract_sha256(metadata, root=root)
    return metadata


def write_metadata_reports(metadata: dict[str, Any], verification: dict[str, Any] | None = None, *, root: Path | None = None) -> Path:
    root = repo_root(root)
    out_dir = root / METADATA_ROOT / metadata["source_id"] / metadata["derived_artifact_sha256"]
    write_json(out_dir / "raster_metadata.json", metadata)
    lines = [
        "# Raster Metadata",
        "",
        f"- Source ID: `{metadata['source_id']}`",
        f"- Derived SHA256: `{metadata['derived_artifact_sha256']}`",
        f"- Metadata contract SHA256: `{metadata['metadata_contract_sha256']}`",
        f"- Driver: `{metadata['container']['driver']}`",
        f"- Shape: `{metadata['raster_shape']['width']} x {metadata['raster_shape']['height']} x {metadata['raster_shape']['band_count']}`",
        f"- CRS: `{metadata['spatial_reference']['crs_authority']}:{metadata['spatial_reference']['crs_code']}`",
        f"- Verification: `{(verification or {}).get('verification_status', 'NOT_RUN')}`",
    ]
    (out_dir / "raster_metadata.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if verification is not None:
        write_json(out_dir / "metadata_verification.json", verification)
    return out_dir / "raster_metadata.json"


def latest_metadata_path(*, root: Path | None = None) -> Path:
    root = repo_root(root)
    latest = root / METADATA_ROOT / "latest_metadata.json"
    if latest.exists():
        pointer = json.loads(latest.read_text(encoding="utf-8"))
        return root / pointer["metadata_path"]
    catalog = json.loads((root / METADATA_ROOT / "metadata_catalog.json").read_text(encoding="utf-8"))
    entry = catalog["entries"][-1]
    return root / METADATA_ROOT / entry["source_id"] / entry["derived_artifact_sha256"] / "raster_metadata.json"


def inspect_plain(metadata: dict[str, Any], verification_status: str = "NOT_RUN") -> str:
    return "\n".join([
        f"source_id: {metadata['source_id']}",
        f"derived_artifact_sha256: {metadata['derived_artifact_sha256']}",
        f"width: {metadata['raster_shape']['width']}",
        f"height: {metadata['raster_shape']['height']}",
        f"band_count: {metadata['raster_shape']['band_count']}",
        f"driver: {metadata['container']['driver']}",
        f"CRS: {metadata['spatial_reference']['crs_authority']}:{metadata['spatial_reference']['crs_code']}",
        f"transform: {metadata['grid_geometry']['affine_transform']}",
        f"bounds: {metadata['grid_geometry']['bounds']}",
        f"dtype: {metadata['raster_shape']['dtypes']}",
        f"nodata: {[band['nodata'] for band in metadata['band_metadata']]}",
        f"metadata_contract_sha256: {metadata['metadata_contract_sha256']}",
        f"verification_status: {verification_status}",
        "",
    ])
