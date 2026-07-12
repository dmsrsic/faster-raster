from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from faster_raster.adapter_contract import stable_json
from faster_raster.artifact_receipts import normalize_artifact_contract
from faster_raster.derived_artifacts import repo_root
from faster_raster.run_receipts import write_json, write_jsonl

CATALOG_ROOT = Path("reports/metadata")


class MetadataCatalogError(ValueError):
    pass


def catalog_hash(catalog: dict[str, Any], *, root: Path | None = None) -> str:
    contract = {k: normalize_artifact_contract(v, root or Path.cwd()) for k, v in catalog.items() if k != "metadata_catalog_contract_sha256"}
    return hashlib.sha256(stable_json(contract).encode("utf-8")).hexdigest()


def load_catalog(*, root: Path | None = None) -> dict[str, Any]:
    root = repo_root(root)
    path = root / CATALOG_ROOT / "metadata_catalog.json"
    if not path.exists():
        return {"schema_version": 1, "artifact_count": 0, "entries": [], "metadata_catalog_contract_sha256": ""}
    return json.loads(path.read_text(encoding="utf-8"))


def entry_from_metadata(metadata: dict[str, Any], verification: dict[str, Any]) -> dict[str, Any]:
    semantic = metadata["semantic_declarations"]
    spatial = metadata["spatial_reference"]
    return {
        "derived_artifact_sha256": metadata["derived_artifact_sha256"],
        "metadata_contract_sha256": metadata["metadata_contract_sha256"],
        "metadata_verification_status": verification["verification_status"],
        "metadata_verification_sha256": hashlib.sha256(stable_json(verification).encode("utf-8")).hexdigest(),
        "source_artifact_sha256": metadata["source_artifact_sha256"],
        "source_id": metadata["source_id"],
        "format": metadata["container"]["container_format"],
        "width": metadata["raster_shape"]["width"],
        "height": metadata["raster_shape"]["height"],
        "band_count": metadata["raster_shape"]["band_count"],
        "crs_summary": f"{spatial.get('crs_authority')}:{spatial.get('crs_code')}" if spatial.get("crs_authority") else None,
        "bounds": metadata["grid_geometry"]["bounds"],
        "temporal_key": semantic.get("temporal_key"),
        "semantic_type": semantic.get("semantic_type"),
        "units_status": semantic.get("status", {}).get("canonical_units"),
        "verification_status": verification["verification_status"],
    }


def update_catalog(metadata: dict[str, Any], verification: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    root = repo_root(root)
    catalog = load_catalog(root=root)
    entry = entry_from_metadata(metadata, verification)
    by_sha = {item["derived_artifact_sha256"]: item for item in catalog.get("entries", [])}
    existing = by_sha.get(entry["derived_artifact_sha256"])
    if existing is not None and existing != entry:
        raise MetadataCatalogError("conflicting metadata for derived artifact")
    by_sha[entry["derived_artifact_sha256"]] = entry
    entries = [by_sha[key] for key in sorted(by_sha)]
    catalog = {"schema_version": 1, "artifact_count": len(entries), "entries": entries, "metadata_catalog_contract_sha256": ""}
    catalog["metadata_catalog_contract_sha256"] = catalog_hash(catalog, root=root)
    out_root = root / CATALOG_ROOT
    write_json(out_root / "metadata_catalog.json", catalog)
    write_jsonl(out_root / "metadata_catalog.jsonl", entries)
    verify = verify_catalog(catalog, root=root)
    write_json(out_root / "metadata_catalog_verification.json", verify)
    metadata_path = CATALOG_ROOT / metadata["source_id"] / metadata["derived_artifact_sha256"] / "raster_metadata.json"
    write_json(out_root / "latest_metadata.json", {"metadata_path": str(metadata_path), "derived_artifact_sha256": metadata["derived_artifact_sha256"]})
    return catalog


def verify_catalog(catalog: dict[str, Any] | None = None, *, root: Path | None = None) -> dict[str, Any]:
    root = repo_root(root)
    catalog = catalog or load_catalog(root=root)
    failures: list[str] = []
    if catalog_hash(catalog, root=root) != catalog.get("metadata_catalog_contract_sha256"):
        failures.append("catalog tampering detected")
    seen: set[str] = set()
    for entry in catalog.get("entries", []):
        sha = entry.get("derived_artifact_sha256")
        if sha in seen:
            failures.append(f"duplicate derived artifact entry: {sha}")
        seen.add(sha)
        metadata_path = root / CATALOG_ROOT / entry["source_id"] / sha / "raster_metadata.json"
        if not metadata_path.exists():
            failures.append(f"metadata missing: {sha}")
        else:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("metadata_contract_sha256") != entry.get("metadata_contract_sha256"):
                failures.append(f"metadata contract mismatch: {sha}")
    return {
        "catalog_status": "PASS" if not failures else "FAIL",
        "verification_status": "PASS" if not failures else "FAIL",
        "artifact_count": catalog.get("artifact_count", 0),
        "failures": failures,
        "warnings": [],
    }
