from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from faster_raster.adapter_contract import stable_json

VOLATILE_RECEIPT_FIELDS = {
    "run_id",
    "started_at_utc",
    "finished_at_utc",
    "duration_ms",
    "updated_at_utc",
    "generated_at_utc",
    "timestamp_utc",
    "receipt_contract_sha256",
}
LOGICAL_RUNTIME_CACHE_ROOT = Path("cache/runtime/static_http_range")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def normalize_contract_value(value: Any, repo_root: Path | None = None) -> Any:
    if isinstance(value, dict):
        return {
            key: normalize_contract_value(item, repo_root)
            for key, item in value.items()
            if key not in VOLATILE_RECEIPT_FIELDS
        }
    if isinstance(value, list):
        return [normalize_contract_value(item, repo_root) for item in value]
    if isinstance(value, str) and repo_root is not None:
        try:
            path = Path(value)
            if path.is_absolute():
                return path.relative_to(repo_root).as_posix()
        except (ValueError, OSError):
            pass
        # Receipt contracts can be verified on a different operating system
        # from the one that produced them. Native Path semantics alone treat a
        # POSIX absolute path as relative on Windows (and vice versa).
        portable_pairs = (
            (PurePosixPath(value), PurePosixPath(repo_root.as_posix())),
            (PureWindowsPath(value), PureWindowsPath(str(repo_root))),
        )
        for path, root in portable_pairs:
            try:
                if path.is_absolute() and root.is_absolute():
                    return path.relative_to(root).as_posix()
            except ValueError:
                continue
    return value


def build_receipt_contract(receipt: dict[str, Any], repo_root: Path | None = None) -> dict[str, Any]:
    return normalize_contract_value(receipt, repo_root)


def compute_receipt_contract_sha256(receipt: dict[str, Any], repo_root: Path | None = None) -> str:
    return hashlib.sha256(stable_json(build_receipt_contract(receipt, repo_root)).encode("utf-8")).hexdigest()


def _check(name: str, passed: bool, details: str | None = None) -> dict[str, Any]:
    return {"name": name, "status": "PASS" if passed else "FAIL", "details": details}


def parse_content_range(value: str | None) -> dict[str, int | None]:
    if not value:
        raise ValueError("content_range_malformed")
    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+|\*)", value.strip())
    if not match:
        raise ValueError("content_range_malformed")
    start = int(match.group(1))
    end = int(match.group(2))
    total_raw = match.group(3)
    total = None if total_raw == "*" else int(total_raw)
    if end < start:
        raise ValueError("content_range_malformed")
    if total is not None and total <= end:
        raise ValueError("content_range_total_invalid")
    return {"start": start, "end": end, "total": total, "length": end - start + 1}


def validate_http_206_evidence(item: dict[str, Any]) -> list[str]:
    if item.get("fixture_only") or item.get("http_status") != 206:
        return []
    try:
        parsed = parse_content_range(item.get("content_range"))
    except ValueError as exc:
        return [str(exc)]
    errors: list[str] = []
    if item.get("range_requested") is not True or item.get("range_honored") is not True:
        errors.append("probe_evidence_inconsistent")
    if int(item.get("bytes_read") or -1) != parsed["length"]:
        errors.append("content_range_byte_count_mismatch")
    if int(item.get("bytes_read") or 0) > int(item.get("byte_cap") or 0):
        errors.append("probe_evidence_inconsistent")
    return errors


def verify_job_receipts(job_receipts: list[dict[str, Any]], *, allow_unknown_stages: bool = False) -> dict[str, Any]:
    known_stages = {
        "resolve_request",
        "bounded_fetch",
        "validate_http_status",
        "validate_byte_cap",
        "validate_magic",
        "validate_content_family",
        "compute_checksum",
        "record_source_evidence",
        "record_fixture_evidence",
    }
    checks: list[dict[str, Any]] = []
    failures: list[str] = []
    order = {job["job_id"]: index for index, job in enumerate(job_receipts)}
    for job in job_receipts:
        stage = job.get("stage")
        if stage not in known_stages and not allow_unknown_stages:
            failures.append(f"unknown stage executed: {stage}")
        for dep in job.get("dependencies") or []:
            if dep not in order or order[dep] >= order[job["job_id"]]:
                failures.append(f"dependency ordering invalid for {job['job_id']}: {dep}")
            dep_status = (job.get("dependency_statuses") or {}).get(dep)
            if dep_status in {"failed", "unsupported"} and job.get("status") != "skipped_dependency_failed":
                failures.append(f"failed dependency did not skip {job['job_id']}")
        if job.get("authorization_redacted") is False:
            failures.append(f"authorization not redacted for {job['job_id']}")
        if job.get("credentials_used"):
            failures.append(f"credentials used by {job['job_id']}")
    checks.append(_check("known_stages", not any("unknown stage" in item for item in failures)))
    checks.append(_check("dependency_ordering", not any("dependency ordering" in item for item in failures)))
    checks.append(_check("no_credentials_or_authorization", not any("authorization" in item or "credentials" in item for item in failures)))
    return {"verification_status": "PASS" if not failures else "FAIL", "checks": checks, "failures": failures, "warnings": []}


def resolve_cache_contract_path(path_value: str, *, cache_root: Path | None = None) -> Path:
    path = Path(path_value)
    if path.is_absolute() or cache_root is None:
        return path
    try:
        relative = path.relative_to(LOGICAL_RUNTIME_CACHE_ROOT)
    except ValueError:
        return path
    return cache_root / relative


def verify_cache_index(cache_index: dict[str, Any], *, cache_root: Path | None = None) -> dict[str, Any]:
    failures: list[str] = []
    for entry in cache_index.get("entries", []):
        payload = resolve_cache_contract_path(entry["cache_path"], cache_root=cache_root)
        sidecar = resolve_cache_contract_path(entry["receipt_path"], cache_root=cache_root)
        if not payload.exists() or not sidecar.exists():
            failures.append(f"cache entry missing: {entry.get('cache_path')}")
            continue
        sidecar_data = read_json(sidecar)
        digest = sha256_file(payload)
        if digest != sidecar_data.get("payload_sha256"):
            failures.append(f"cache payload hash mismatch: {entry.get('cache_path')}")
        if sidecar_data.get("cache_contract_version") != 1:
            failures.append(f"cache contract version mismatch: {entry.get('cache_path')}")
    return {"verification_status": "PASS" if not failures else "FAIL", "checks": [_check("cache_hashes", not failures)], "failures": failures, "warnings": []}


def verify_run_receipt(
    receipt_path: Path,
    *,
    package_path: Path | None = None,
    manifest_path: Path | None = None,
    dag_path: Path | None = None,
) -> dict[str, Any]:
    receipt = read_json(receipt_path)
    run_dir = receipt_path.parent
    job_receipts = read_json(run_dir / "job_receipts.json")
    source_evidence = read_json(run_dir / "source_evidence.json")
    checks: list[dict[str, Any]] = []
    failures: list[str] = []
    warnings: list[str] = []

    computed_hash = compute_receipt_contract_sha256(receipt, Path.cwd())
    checks.append(_check("receipt_contract_hash", computed_hash == receipt.get("receipt_contract_sha256")))
    if computed_hash != receipt.get("receipt_contract_sha256"):
        failures.append("stored receipt hash does not match recomputation")

    if package_path and package_path.exists():
        checks.append(_check("package_hash", sha256_file(package_path) == receipt.get("package_artifact_sha256")))
        if sha256_file(package_path) != receipt.get("package_artifact_sha256"):
            failures.append("package hash mismatch")
    if manifest_path and manifest_path.exists():
        checks.append(_check("manifest_hash", sha256_file(manifest_path) == receipt.get("manifest_artifact_sha256")))
        if sha256_file(manifest_path) != receipt.get("manifest_artifact_sha256"):
            failures.append("manifest hash mismatch")
    if dag_path and dag_path.exists():
        checks.append(_check("dag_hash", sha256_file(dag_path) == receipt.get("dag_artifact_sha256")))
        if sha256_file(dag_path) != receipt.get("dag_artifact_sha256"):
            failures.append("DAG hash mismatch")

    checks.append(_check("job_count", receipt.get("job_receipt_count") == len(job_receipts)))
    if receipt.get("job_receipt_count") != len(job_receipts):
        failures.append("job count mismatch")
    evidence_items = source_evidence.get("sources", [])
    checks.append(_check("source_evidence_count", len(evidence_items) == receipt.get("runnable_source_count", 0) + receipt.get("fixture_source_count", 0)))
    if len(evidence_items) != receipt.get("runnable_source_count", 0) + receipt.get("fixture_source_count", 0):
        failures.append("source evidence count mismatch")

    total_bytes = sum(int(item.get("bytes_read") or 0) for item in evidence_items if not item.get("fixture_only"))
    checks.append(_check("total_bytes", total_bytes == receipt.get("total_bytes_read")))
    if total_bytes != receipt.get("total_bytes_read"):
        failures.append("total bytes mismatch")
    for item in evidence_items:
        range_errors = validate_http_206_evidence(item)
        failures.extend(f"{error}: {item.get('source_id')}" for error in range_errors)
        if item.get("fixture_only") and item.get("network_attempted"):
            failures.append(f"fixture source attempted network: {item.get('source_id')}")
        if not item.get("fixture_only") and item.get("status") == "succeeded":
            if not item.get("sha256"):
                failures.append(f"successful source missing sha256: {item.get('source_id')}")
            if item.get("detected_magic") not in _as_set(item.get("expected_magic")):
                failures.append(f"magic validation failed: {item.get('source_id')}")
            if item.get("detected_content_family") not in _as_set(item.get("expected_content_family")):
                failures.append(f"content-family validation failed: {item.get('source_id')}")
            if int(item.get("bytes_read") or 0) > int(item.get("byte_cap") or 0):
                failures.append(f"source byte cap exceeded: {item.get('source_id')}")
    if receipt.get("total_bytes_read", 0) > receipt.get("max_total_bytes", 0):
        failures.append("total byte cap exceeded")
    if receipt.get("credentials_used") or receipt.get("authorization_headers_present"):
        failures.append("credentials or authorization values present")

    job_check = verify_job_receipts(job_receipts)
    failures.extend(job_check["failures"])
    checks.extend(job_check["checks"])

    return {
        "verification_status": "PASS" if not failures else "FAIL",
        "checks": checks,
        "failures": failures,
        "warnings": warnings,
    }


def _as_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, list):
        return {str(item) for item in value}
    return {str(value)}
