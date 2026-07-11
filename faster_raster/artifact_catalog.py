from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from faster_raster.adapter_contract import stable_json
from faster_raster.artifact_receipts import normalize_artifact_contract
from faster_raster.artifact_store import sha256_file
from faster_raster.run_receipts import write_json, write_jsonl


CATALOG_ROOT = Path("reports/artifacts")
CATALOG_VERSION = 1


class ArtifactCatalogError(ValueError):
    pass


def _catalog_hash(snapshot: dict[str, Any]) -> str:
    import hashlib

    contract = {key: normalize_artifact_contract(value, Path.cwd()) for key, value in snapshot.items() if key != "catalog_contract_sha256"}
    return hashlib.sha256(stable_json(contract).encode("utf-8")).hexdigest()


def _entry_from_receipt(receipt: dict[str, Any], receipt_path: Path, now: str) -> dict[str, Any]:
    return {
        "artifact_id": receipt["artifact_id"],
        "whole_object_sha256": receipt["whole_object_sha256"],
        "object_size_bytes": receipt["object_size_bytes"],
        "artifact_path": receipt["artifact_path"],
        "artifact_extension": receipt["artifact_extension"],
        "source_ids": [receipt["source_id"]],
        "request_ids": [receipt["request_id"]],
        "task_ids": [receipt["task_id"]],
        "temporal_keys": [receipt.get("temporal_key")],
        "content_family": receipt["detected_content_family"],
        "detected_magic": receipt["detected_magic"],
        "container_validation_status": receipt["container_validation_status"],
        "first_materialization_run_id": receipt["materialization_run_id"],
        "latest_verified_run_id": receipt["materialization_run_id"],
        "artifact_receipt_paths": [str(receipt_path)],
        "verified": True,
        "created_at_utc": now,
        "last_verified_at_utc": now,
    }


def load_catalog(catalog_root: Path = CATALOG_ROOT) -> dict[str, Any]:
    path = catalog_root / "artifact_catalog.json"
    if not path.exists():
        return {
            "catalog_version": CATALOG_VERSION,
            "artifact_count": 0,
            "total_materialized_bytes": 0,
            "unique_content_hash_count": 0,
            "entries": [],
            "catalog_contract_sha256": "",
            "updated_at_utc": None,
        }
    return json.loads(path.read_text(encoding="utf-8"))


def update_catalog(artifact_receipts: list[dict[str, Any]], receipt_paths: list[Path], *, catalog_root: Path = CATALOG_ROOT, now: str) -> dict[str, Any]:
    catalog_root.mkdir(parents=True, exist_ok=True)
    lock_path = catalog_root / "artifact_catalog.lock"
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError as exc:
        raise ArtifactCatalogError("artifact_catalog_conflict") from exc
    try:
        snapshot = load_catalog(catalog_root)
        by_id = {entry["artifact_id"]: entry for entry in snapshot.get("entries", [])}
        events: list[dict[str, Any]] = []
        for receipt, receipt_path in zip(artifact_receipts, receipt_paths, strict=True):
            artifact_id = receipt["artifact_id"]
            if artifact_id not in by_id:
                by_id[artifact_id] = _entry_from_receipt(receipt, receipt_path, now)
                events.append({"event_type": "artifact_committed", "artifact_id": artifact_id, "materialization_run_id": receipt["materialization_run_id"], "timestamp_utc": now})
            else:
                entry = by_id[artifact_id]
                if entry["whole_object_sha256"] != receipt["whole_object_sha256"]:
                    raise ArtifactCatalogError("conflicting catalog entry")
                for key, value in {
                    "source_ids": receipt["source_id"],
                    "request_ids": receipt["request_id"],
                    "task_ids": receipt["task_id"],
                    "temporal_keys": receipt.get("temporal_key"),
                    "artifact_receipt_paths": str(receipt_path),
                }.items():
                    if value not in entry[key]:
                        entry[key].append(value)
                entry["latest_verified_run_id"] = receipt["materialization_run_id"]
                entry["last_verified_at_utc"] = now
                events.append({"event_type": "provenance_reference_added", "artifact_id": artifact_id, "materialization_run_id": receipt["materialization_run_id"], "timestamp_utc": now})
        entries = sorted(by_id.values(), key=lambda item: item["artifact_id"])
        snapshot = {
            "catalog_version": CATALOG_VERSION,
            "artifact_count": len(entries),
            "total_materialized_bytes": sum(int(entry["object_size_bytes"]) for entry in entries),
            "unique_content_hash_count": len({entry["whole_object_sha256"] for entry in entries}),
            "entries": entries,
            "catalog_contract_sha256": "",
            "updated_at_utc": now,
        }
        snapshot["catalog_contract_sha256"] = _catalog_hash(snapshot)
        write_json(catalog_root / "artifact_catalog.json", snapshot)
        journal = catalog_root / "artifact_catalog.jsonl"
        previous = journal.read_text(encoding="utf-8") if journal.exists() else ""
        journal.write_text(previous + "".join(json.dumps(event, sort_keys=True) + "\n" for event in events), encoding="utf-8")
        verification = verify_artifact_catalog(snapshot, catalog_root=catalog_root)
        write_json(catalog_root / "artifact_catalog_verification.json", verification)
        return snapshot
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def verify_artifact_catalog(snapshot: dict[str, Any] | None = None, *, catalog_root: Path = CATALOG_ROOT, claimed_update: bool = False) -> dict[str, Any]:
    catalog_path = catalog_root / "artifact_catalog.json"
    if snapshot is None and not catalog_path.exists():
        if claimed_update:
            return {
                "catalog_status": "missing_after_claimed_update",
                "verification_status": "FAIL",
                "blocking": True,
                "artifact_count": 0,
                "check_count": 1,
                "passed_check_count": 0,
                "failed_check_count": 1,
                "warnings": [],
                "failures": ["catalog_missing_after_claimed_update"],
                "checks": [{"name": "catalog_present_after_claimed_update", "status": "FAIL", "details": None}],
            }
        return {
            "catalog_status": "not_initialized",
            "verification_status": "NOT_APPLICABLE",
            "blocking": False,
            "artifact_count": 0,
            "reason": "no committed artifacts",
            "check_count": 0,
            "passed_check_count": 0,
            "failed_check_count": 0,
            "warnings": [],
            "failures": [],
            "checks": [],
        }
    snapshot = snapshot or load_catalog(catalog_root)
    if snapshot.get("artifact_count") == 0 and not snapshot.get("catalog_contract_sha256"):
        return {
            "catalog_status": "not_initialized",
            "verification_status": "NOT_APPLICABLE",
            "blocking": False,
            "artifact_count": 0,
            "reason": "no committed artifacts",
            "check_count": 0,
            "passed_check_count": 0,
            "failed_check_count": 0,
            "warnings": [],
            "failures": [],
            "checks": [],
        }
    checks: list[dict[str, Any]] = []
    failures: list[str] = []
    computed = _catalog_hash(snapshot)
    checks.append({"name": "catalog_hash", "status": "PASS" if computed == snapshot.get("catalog_contract_sha256") else "FAIL", "details": None})
    if computed != snapshot.get("catalog_contract_sha256"):
        failures.append("catalog hash mismatch")
    for entry in snapshot.get("entries", []):
        path = Path(entry["artifact_path"])
        if not path.is_file():
            failures.append(f"artifact missing: {entry['artifact_id']}")
        elif path.is_symlink():
            failures.append(f"artifact symlink: {entry['artifact_id']}")
        elif sha256_file(path) != entry["whole_object_sha256"]:
            failures.append(f"artifact tampered: {entry['artifact_id']}")
        elif path.stat().st_size != entry["object_size_bytes"]:
            failures.append(f"artifact size mismatch: {entry['artifact_id']}")
    checks.append({"name": "artifact_entries", "status": "PASS" if not any("artifact" in item for item in failures) else "FAIL", "details": None})
    return {
        "catalog_status": "verified" if not failures else "invalid",
        "verification_status": "PASS" if not failures else "FAIL",
        "blocking": bool(failures),
        "artifact_count": snapshot.get("artifact_count", 0),
        "check_count": len(checks),
        "passed_check_count": sum(1 for check in checks if check["status"] == "PASS"),
        "failed_check_count": sum(1 for check in checks if check["status"] == "FAIL"),
        "warnings": [],
        "failures": failures,
        "checks": checks,
    }
