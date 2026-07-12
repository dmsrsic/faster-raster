from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from faster_raster.artifact_store import sha256_file
from faster_raster.derived_artifacts import derived_content_addressed_path, read_geotiff_info, repo_root
from faster_raster.raster_metadata import compute_metadata_contract_sha256

SECRET_MARKERS = ("/tmp/pytest-", "/home/dmsrsic", "authorization", "credential", "token", "password", "secret")


def _status(failures: list[str], prefix: str) -> str:
    return "FAIL" if any(item.startswith(prefix) for item in failures) else "PASS"


def verify_metadata(metadata: dict[str, Any], receipt: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    root = repo_root(root)
    failures: list[str] = []
    warnings: list[str] = []
    derived_sha = metadata.get("derived_artifact_sha256")
    path = derived_content_addressed_path(derived_sha, metadata.get("container", {}).get("file_extension") or ".tif", root=root)
    if not path.exists():
        failures.append("identity: missing derived artifact")
    elif sha256_file(path) != derived_sha:
        failures.append("identity: derived artifact SHA256 mismatch")
    elif path.stat().st_size != metadata.get("container", {}).get("size_bytes"):
        failures.append("identity: derived artifact size mismatch")
    if receipt.get("derived_artifact_receipt_contract_sha256") != metadata.get("derived_artifact_receipt_sha256"):
        failures.append("lineage: receipt hash mismatch")
    if compute_metadata_contract_sha256(metadata, root=root) != metadata.get("metadata_contract_sha256"):
        failures.append("identity: metadata contract hash mismatch")
    try:
        info = read_geotiff_info(path)
        if info["driver"] != metadata["container"]["driver"]:
            failures.append("container: driver mismatch")
        if info["width"] != metadata["raster_shape"]["width"] or info["height"] != metadata["raster_shape"]["height"]:
            failures.append("spatial: shape mismatch")
        if info["band_count"] != metadata["raster_shape"]["band_count"]:
            failures.append("band: band count mismatch")
        if info["dtypes"] != metadata["raster_shape"]["dtypes"]:
            failures.append("band: dtype mismatch")
        expected = None
        if metadata["spatial_reference"].get("crs_authority") and metadata["spatial_reference"].get("crs_code"):
            expected = f"{metadata['spatial_reference']['crs_authority']}:{metadata['spatial_reference']['crs_code']}"
        if expected and info.get("crs") != expected:
            failures.append("spatial: CRS mismatch")
        if info["transform"] != metadata["grid_geometry"]["affine_transform"]:
            failures.append("spatial: transform mismatch")
        if info["bounds"] != metadata["grid_geometry"]["bounds"]:
            failures.append("spatial: bounds mismatch")
        if info["nodata"] != [band.get("nodata") for band in metadata.get("band_metadata", [])]:
            failures.append("band: nodata mismatch")
        if info["block_shapes"] != metadata["container"]["block_shapes"]:
            failures.append("container: block shapes mismatch")
    except Exception as exc:
        failures.append(f"container: raster reopen failed {type(exc).__name__}")
    lineage_ok = (
        metadata.get("source_artifact_sha256") == receipt.get("source_artifact_sha256")
        and metadata.get("derivation_plan_sha256") == receipt.get("derivation_plan_contract_sha256")
        and metadata.get("derived_artifact_sha256") == receipt.get("output_sha256")
    )
    if not lineage_ok:
        failures.append("lineage: source lineage break")
    serialized = json.dumps(metadata, sort_keys=True)
    for marker in SECRET_MARKERS:
        if marker in serialized:
            failures.append(f"identity: forbidden marker present {marker}")
    semantic_statuses = metadata.get("semantic_declarations", {}).get("status", {})
    if any(status == "declared_only" for status in semantic_statuses.values()):
        warnings.append("semantic metadata contains declared-only fields")
    return {
        "identity_verification_status": _status(failures, "identity"),
        "container_verification_status": _status(failures, "container"),
        "spatial_verification_status": _status(failures, "spatial"),
        "band_verification_status": _status(failures, "band"),
        "semantic_verification_status": "WARN" if warnings and not failures else ("FAIL" if any(item.startswith("semantic") for item in failures) else "PASS"),
        "lineage_verification_status": _status(failures, "lineage"),
        "verification_status": "PASS" if not failures else "FAIL",
        "warnings": warnings,
        "failures": failures,
        "check_count": 7,
        "failed_check_count": len({item.split(":", 1)[0] for item in failures}),
    }
