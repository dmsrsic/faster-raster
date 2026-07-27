from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from faster_raster.adapter_contract import stable_json
from faster_raster.run_receipts import sha256_file
from faster_raster.prism_product import PRISM_SOURCE_ID, PrismProductError, inspect_prism_archive


VOLATILE_ARTIFACT_FIELDS = {
    "generated_at_utc",
    "started_at_utc",
    "finished_at_utc",
    "duration_ms",
    "updated_at_utc",
    "materialization_run_receipt_contract_sha256",
    "artifact_receipt_contract_sha256",
    "catalog_contract_sha256",
}


def normalize_artifact_contract(value: Any, repo_root: Path | None = None) -> Any:
    if isinstance(value, dict):
        return {
            key: normalize_artifact_contract(item, repo_root)
            for key, item in value.items()
            if key not in VOLATILE_ARTIFACT_FIELDS
        }
    if isinstance(value, list):
        return [normalize_artifact_contract(item, repo_root) for item in value]
    if isinstance(value, str) and repo_root is not None:
        try:
            path = Path(value)
            if path.is_absolute():
                return str(path.relative_to(repo_root))
        except (OSError, ValueError):
            return value
    return value


def _hash_contract(value: dict[str, Any], repo_root: Path | None = None) -> str:
    return hashlib.sha256(stable_json(normalize_artifact_contract(value, repo_root)).encode("utf-8")).hexdigest()


def build_materialization_plan_contract(plan: dict[str, Any]) -> dict[str, Any]:
    excluded = {"materialization_plan_contract_sha256", "generated_at_utc", "current_free_disk_bytes"}
    return {key: normalize_artifact_contract(value, Path.cwd()) for key, value in plan.items() if key not in excluded}


def compute_materialization_plan_sha256(plan: dict[str, Any]) -> str:
    return hashlib.sha256(stable_json(build_materialization_plan_contract(plan)).encode("utf-8")).hexdigest()


def build_artifact_receipt_contract(receipt: dict[str, Any], repo_root: Path | None = None) -> dict[str, Any]:
    return normalize_artifact_contract(receipt, repo_root)


def compute_artifact_receipt_sha256(receipt: dict[str, Any], repo_root: Path | None = None) -> str:
    return _hash_contract(build_artifact_receipt_contract(receipt, repo_root), repo_root)


def build_materialization_run_receipt_contract(receipt: dict[str, Any], repo_root: Path | None = None) -> dict[str, Any]:
    return normalize_artifact_contract(receipt, repo_root)


def compute_materialization_run_receipt_sha256(receipt: dict[str, Any], repo_root: Path | None = None) -> str:
    return _hash_contract(build_materialization_run_receipt_contract(receipt, repo_root), repo_root)


def _check(name: str, passed: bool, details: str | None = None) -> dict[str, Any]:
    return {"name": name, "status": "PASS" if passed else "FAIL", "details": details}


def verify_artifact_receipt(receipt: dict[str, Any], *, repo_root: Path | None = None) -> dict[str, Any]:
    repo_root = repo_root or Path.cwd()
    checks: list[dict[str, Any]] = []
    failures: list[str] = []
    computed = compute_artifact_receipt_sha256(receipt, repo_root)
    checks.append(_check("artifact_receipt_hash", computed == receipt.get("artifact_receipt_contract_sha256")))
    if computed != receipt.get("artifact_receipt_contract_sha256"):
        failures.append("artifact receipt hash mismatch")
    artifact_path = Path(receipt.get("artifact_path") or "")
    checks.append(_check("artifact_exists", artifact_path.is_file()))
    if not artifact_path.is_file():
        failures.append("artifact missing")
    elif artifact_path.is_symlink():
        failures.append("artifact path is symlink")
    else:
        digest = sha256_file(artifact_path)
        size = artifact_path.stat().st_size
        if digest != receipt.get("whole_object_sha256"):
            failures.append("artifact checksum mismatch")
        if size != receipt.get("object_size_bytes"):
            failures.append("artifact size mismatch")
        if digest not in artifact_path.name:
            failures.append("artifact path does not contain whole-object SHA256")
    if receipt.get("complete_object") is not True or receipt.get("bounded_probe_only") is not False:
        failures.append("receipt does not describe a complete object")
    if receipt.get("prefix_match") is not True:
        failures.append("probe prefix mismatch")
    if receipt.get("container_validation_status") != "PASS":
        failures.append("container validation failed")
    prism_profile_ok = True
    if receipt.get("source_id") == PRISM_SOURCE_ID and artifact_path.is_file() and not artifact_path.is_symlink():
        stored_profile = (receipt.get("container_metadata") or {}).get("product_profile") or {}
        try:
            recomputed_profile = inspect_prism_archive(
                artifact_path,
                temporal_key=receipt.get("temporal_key"),
                logical_archive_name=stored_profile.get("archive_name"),
            )
        except PrismProductError as exc:
            prism_profile_ok = False
            failures.append(f"PRISM product profile verification failed: {exc}")
        else:
            comparable_fields = [
                "product_profile_version",
                "product_validation_status",
                "temporal_key",
                "archive_name",
                "inventory_sha256",
                "primary_raster_member",
            ]
            if any(stored_profile.get(field) != recomputed_profile.get(field) for field in comparable_fields):
                prism_profile_ok = False
                failures.append("PRISM product profile receipt mismatch")
            if recomputed_profile.get("product_validation_status") != "PASS":
                prism_profile_ok = False
                failures.append("PRISM product profile validation failed")
    if receipt.get("credentials_used") or receipt.get("authorization_headers_present"):
        failures.append("credentials or authorization values present")
    checks.extend(
        [
            _check("checksum", not any("checksum" in item for item in failures)),
            _check("prefix_continuity", receipt.get("prefix_match") is True),
            _check("container_validation", receipt.get("container_validation_status") == "PASS"),
            _check("prism_product_profile", prism_profile_ok),
            _check("no_credentials", not receipt.get("credentials_used") and not receipt.get("authorization_headers_present")),
        ]
    )
    return {
        "verification_status": "PASS" if not failures else "FAIL",
        "check_count": len(checks),
        "passed_check_count": sum(1 for check in checks if check["status"] == "PASS"),
        "failed_check_count": sum(1 for check in checks if check["status"] == "FAIL"),
        "warnings": [],
        "failures": failures,
        "checks": checks,
    }


def verify_materialization_run(receipt_path: Path, *, repo_root: Path | None = None) -> dict[str, Any]:
    repo_root = repo_root or Path.cwd()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    run_dir = receipt_path.parent
    artifact_receipts = json.loads((run_dir / "artifact_receipts.json").read_text(encoding="utf-8")) if (run_dir / "artifact_receipts.json").exists() else []
    checks: list[dict[str, Any]] = []
    failures: list[str] = []
    computed = compute_materialization_run_receipt_sha256(receipt, repo_root)
    contract_ok = computed == receipt.get("materialization_run_receipt_contract_sha256")
    checks.append(_check("materialization_run_receipt_hash", contract_ok))
    if not contract_ok:
        failures.append("materialization run receipt hash mismatch")
    if receipt.get("artifact_receipt_count") != len(artifact_receipts):
        failures.append("artifact receipt count mismatch")
    artifact_failures: list[str] = []
    for item in artifact_receipts:
        verification = verify_artifact_receipt(item, repo_root=repo_root)
        if verification["verification_status"] != "PASS":
            artifact_failures.extend(f"artifact {item.get('artifact_id')}: {failure}" for failure in verification["failures"])
    failures.extend(artifact_failures)
    if receipt.get("credentials_used") or receipt.get("authorization_headers_present"):
        failures.append("credentials or authorization values present")
    execution_failed = receipt.get("run_status") == "failed" or bool(receipt.get("failed_source_count"))
    execution_blocked = receipt.get("run_status") == "blocked_policy"
    if execution_failed:
        failures.extend(str(item) for item in receipt.get("failure_classes", []) if item)
    checks.append(_check("artifact_receipts", not artifact_failures))
    checks.append(_check("no_credentials", not receipt.get("credentials_used") and not receipt.get("authorization_headers_present")))
    contract_status = "PASS" if contract_ok and receipt.get("artifact_receipt_count") == len(artifact_receipts) else "FAIL"
    execution_status = "FAILED" if execution_failed else ("BLOCKED" if execution_blocked else "PASS")
    artifact_status = "NOT_APPLICABLE" if not artifact_receipts else ("PASS" if not artifact_failures else "FAIL")
    catalog_status = receipt.get("catalog_update_status") or "NOT_APPLICABLE"
    blocking_reasons = [str(item) for item in receipt.get("failure_classes", []) if item] if execution_blocked else []
    informational_reasons: list[str] = []
    if execution_blocked:
        release_status = "NOT_APPLICABLE"
        verification_status = "NOT_APPLICABLE"
    else:
        release_status = "PASS" if contract_status == "PASS" and execution_status == "PASS" and artifact_status == "PASS" and catalog_status in {"PASS", "NOT_APPLICABLE"} and not failures else "FAIL"
        verification_status = "PASS" if release_status == "PASS" else "FAIL"
    return {
        "contract_verification_status": contract_status,
        "execution_outcome_status": execution_status,
        "artifact_verification_status": artifact_status,
        "catalog_verification_status": catalog_status,
        "release_evidence_status": release_status,
        "verification_status": verification_status,
        "check_count": len(checks),
        "passed_check_count": sum(1 for check in checks if check["status"] == "PASS"),
        "failed_check_count": sum(1 for check in checks if check["status"] == "FAIL"),
        "warnings": [],
        "blocking_reasons": sorted(set(blocking_reasons)),
        "informational_reasons": sorted(set(informational_reasons)),
        "failures": sorted(set(failures)),
        "checks": checks,
    }
