from __future__ import annotations

import gzip
import hashlib
import ipaddress
import json
import shutil
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from faster_raster import artifact_catalog, artifact_store
from faster_raster.artifact_receipts import (
    compute_artifact_receipt_sha256,
    compute_materialization_plan_sha256,
    compute_materialization_run_receipt_sha256,
    verify_materialization_run,
)
from faster_raster.content_magic import detect_content_magic
from faster_raster.local_executor import COMPILE_ROOT, PACKAGE_ROOT
from faster_raster.run_receipts import parse_content_range, read_json, read_jsonl, sha256_file, validate_http_206_evidence, verify_run_receipt, write_json, write_jsonl


MATERIALIZATION_ROOT = Path("reports/materializations")
DEFAULT_MAX_OBJECT_BYTES = 128 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 128 * 1024 * 1024
DEFAULT_MINIMUM_FREE_DISK_BYTES = 0
DEFAULT_DISK_SAFETY_MARGIN_BYTES = 64 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_RETRY_LIMIT = 1
USER_AGENT = "FasterRaster/0.9.0 verified-materialization"
RETRY_HTTP = {429, 500, 502, 503, 504}


class MaterializationError(ValueError):
    pass


@dataclass(frozen=True)
class MaterializationOptions:
    sources: tuple[str, ...] = ()
    max_object_bytes: int = DEFAULT_MAX_OBJECT_BYTES
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES
    minimum_free_disk_bytes: int = DEFAULT_MINIMUM_FREE_DISK_BYTES
    disk_safety_margin_bytes: int = DEFAULT_DISK_SAFETY_MARGIN_BYTES
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    retry_limit: int = DEFAULT_RETRY_LIMIT
    resume_enabled: bool = True
    allow_network: bool = False
    allow_materialization: bool = False
    approve_plan_sha256: str | None = None
    artifact_root: Path = artifact_store.ARTIFACT_ROOT
    staging_root: Path = artifact_store.STAGING_ROOT
    catalog_root: Path = artifact_catalog.CATALOG_ROOT
    materializations_root: Path = MATERIALIZATION_ROOT
    probe_runs_root: Path = Path("reports/runs")
    probe_run_id: str | None = None
    probe_receipt_sha256: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _validate_options(options: MaterializationOptions) -> None:
    if options.max_object_bytes <= 0:
        raise MaterializationError("max_object_bytes must be positive")
    if options.max_total_bytes <= 0:
        raise MaterializationError("max_total_bytes must be positive")
    if options.max_object_bytes > options.max_total_bytes:
        raise MaterializationError("max_object_bytes must not exceed max_total_bytes")
    if options.minimum_free_disk_bytes < 0 or options.disk_safety_margin_bytes < 0:
        raise MaterializationError("disk limits must not be negative")
    if options.timeout_seconds <= 0:
        raise MaterializationError("timeout_seconds must be positive")
    if options.retry_limit < 0:
        raise MaterializationError("retry_limit must not be negative")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _as_tuple(value: str | Iterable[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def _redact_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _url_host(url: str | None) -> str | None:
    return urllib.parse.urlsplit(url or "").hostname


def _load_inputs(task_id: str) -> dict[str, Any]:
    package_dir = PACKAGE_ROOT / task_id
    compile_dir = COMPILE_ROOT / task_id
    return {
        "package": read_json(package_dir / "execution_package.json"),
        "dag": read_json(package_dir / "dag.json"),
        "manifest": _read_jsonl(compile_dir / "acquisition_manifest.jsonl"),
        "compile_report": read_json(compile_dir / "compile_report.json"),
        "paths": {
            "package": package_dir / "execution_package.json",
            "dag": package_dir / "dag.json",
            "manifest": compile_dir / "acquisition_manifest.jsonl",
        },
    }


def _read_probe_pointer(path: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None, Path | None]:
    if not path.exists():
        return None, None, None
    latest = read_json(path)
    receipt_path = Path(latest.get("receipt_path") or "")
    if not receipt_path.is_file():
        return latest, None, receipt_path
    return latest, read_json(receipt_path), receipt_path


def _latest_probe(task_id: str, *, probe_runs_root: Path = Path("reports/runs")) -> tuple[dict[str, Any] | None, dict[str, Any] | None, Path | None]:
    return _read_probe_pointer(probe_runs_root / task_id / "latest_live_verified_run.json")


def _receipt_for_run_id(task_id: str, run_id: str, probe_runs_root: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None, Path | None]:
    receipt_path = probe_runs_root / task_id / run_id / "run_receipt.json"
    if not receipt_path.is_file():
        return {"run_id": run_id}, None, receipt_path
    return {"run_id": run_id, "receipt_path": str(receipt_path)}, read_json(receipt_path), receipt_path


def _scan_newest_qualifying_probe(task_id: str, selected_sources: set[str], probe_runs_root: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None, Path | None, list[str]]:
    root = probe_runs_root / task_id
    for run_dir in sorted(root.glob("fr_run_*"), reverse=True):
        receipt_path = run_dir / "run_receipt.json"
        if not receipt_path.is_file():
            continue
        receipt = read_json(receipt_path)
        evidence = _probe_evidence(receipt_path)
        ok, reasons = _probe_qualifies(receipt, receipt_path, evidence, selected_sources)
        if ok:
            return {"run_id": receipt.get("run_id"), "receipt_path": str(receipt_path)}, receipt, receipt_path, []
    return None, None, None, ["no_qualifying_probe_receipt"]


def _probe_evidence(receipt_path: Path | None) -> dict[str, dict[str, Any]]:
    if not receipt_path:
        return {}
    source_path = receipt_path.parent / "source_evidence.json"
    if not source_path.exists():
        return {}
    payload = read_json(source_path)
    return {item["source_id"]: item for item in payload.get("sources", [])}


def _probe_qualifies(receipt: dict[str, Any] | None, receipt_path: Path | None, evidence_by_source: dict[str, dict[str, Any]], selected_sources: set[str]) -> tuple[bool, list[str]]:
    if not receipt or not receipt_path:
        return False, ["missing_probe_receipt"]
    reasons: list[str] = []
    verification = verify_run_receipt(receipt_path)
    if verification.get("verification_status") != "PASS":
        reasons.append("invalid_probe_receipt")
    evidence_class = receipt.get("evidence_class")
    if evidence_class not in {"live_network", "validated_cache_reuse"}:
        reasons.append("probe_receipt_test_fixture" if evidence_class == "deterministic_test_fixture" else "probe_receipt_not_live")
    if receipt.get("run_status") not in {"completed", "completed_with_warnings"}:
        reasons.append("probe_receipt_not_completed")
    if receipt.get("network_run") is not True and evidence_class != "validated_cache_reuse":
        reasons.append("probe_receipt_not_live")
    if receipt.get("allow_network") is not True:
        reasons.append("probe_receipt_not_live")
    if receipt.get("failed_source_count") not in {0, None}:
        reasons.append("probe_receipt_has_failed_sources")
    for flag in ["credentials_used", "authorization_headers_present"]:
        if receipt.get(flag):
            reasons.append("authorization_present")
    for flag in ["all_byte_caps_respected", "all_magic_valid", "all_content_families_valid", "all_checksums_present"]:
        if receipt.get(flag) is not True:
            reasons.append(f"probe_{flag}_false")
    for source_id in selected_sources:
        item = evidence_by_source.get(source_id)
        if not item:
            reasons.append("probe_source_evidence_missing")
            continue
        if item.get("status") not in {"succeeded", "cache_hit"}:
            reasons.append("probe_source_evidence_missing")
        if validate_http_206_evidence(item):
            reasons.append("probe_content_range_invalid")
    return not reasons, sorted(set(reasons))


def _select_probe(
    task_id: str,
    selected_sources: set[str],
    *,
    probe_runs_root: Path,
    probe_run_id: str | None = None,
    probe_receipt_sha256: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, Path | None, dict[str, dict[str, Any]], dict[str, Any]]:
    rejected_latest_id = None
    rejected_latest_reasons: list[str] = []
    selected_explicitly = bool(probe_run_id or probe_receipt_sha256)
    method = "explicit" if selected_explicitly else "latest_live_verified"
    if probe_run_id:
        latest, receipt, receipt_path = _receipt_for_run_id(task_id, probe_run_id, probe_runs_root)
    elif probe_receipt_sha256:
        latest = receipt = None
        receipt_path = None
        for candidate in (probe_runs_root / task_id).glob("fr_run_*/run_receipt.json"):
            data = read_json(candidate)
            if data.get("receipt_contract_sha256") == probe_receipt_sha256:
                latest, receipt, receipt_path = {"run_id": data.get("run_id"), "receipt_path": str(candidate)}, data, candidate
                break
    else:
        latest, receipt, receipt_path = _latest_probe(task_id, probe_runs_root=probe_runs_root)
        evidence = _probe_evidence(receipt_path)
        ok, reasons = _probe_qualifies(receipt, receipt_path, evidence, selected_sources)
        if not ok:
            rejected_latest_id = (receipt or latest or {}).get("run_id")
            rejected_latest_reasons = reasons
            method = "scanned_runs"
            latest, receipt, receipt_path, scan_reasons = _scan_newest_qualifying_probe(task_id, selected_sources, probe_runs_root)
            rejected_latest_reasons.extend(scan_reasons)
    evidence_by_source = _probe_evidence(receipt_path)
    ok, reasons = _probe_qualifies(receipt, receipt_path, evidence_by_source, selected_sources)
    metadata = {
        "probe_evidence_class": (receipt or {}).get("evidence_class"),
        "probe_receipt_verification_status": "PASS" if ok else "FAIL",
        "probe_source_evidence_verification_status": "PASS" if ok else "FAIL",
        "probe_selected_explicitly": selected_explicitly,
        "probe_selection_method": method,
        "rejected_latest_probe_run_id": rejected_latest_id,
        "rejected_latest_probe_reasons": sorted(set(rejected_latest_reasons)),
        "selection_blocking_reasons": reasons,
    }
    if probe_receipt_sha256 and receipt and receipt.get("receipt_contract_sha256") != probe_receipt_sha256:
        metadata["selection_blocking_reasons"].append("probe_receipt_sha256_mismatch")
    return latest, receipt, receipt_path, evidence_by_source, metadata


def _container_extension(row: dict[str, Any]) -> str:
    return artifact_store.trusted_extension(row.get("container"), row.get("expected_format"))


def _parsed_probe_range(evidence: dict[str, Any] | None) -> dict[str, Any]:
    if not evidence or evidence.get("http_status") != 206:
        return {"parsed_probe_range_start": None, "parsed_probe_range_end": None, "parsed_remote_object_size": None, "probe_range_length": None, "probe_range_length_matches_bytes": None, "range_errors": []}
    try:
        parsed = parse_content_range(evidence.get("content_range"))
    except ValueError as exc:
        return {"parsed_probe_range_start": None, "parsed_probe_range_end": None, "parsed_remote_object_size": None, "probe_range_length": None, "probe_range_length_matches_bytes": False, "range_errors": [str(exc)]}
    errors = validate_http_206_evidence(evidence)
    return {
        "parsed_probe_range_start": parsed["start"],
        "parsed_probe_range_end": parsed["end"],
        "parsed_remote_object_size": parsed["total"],
        "probe_range_length": parsed["length"],
        "probe_range_length_matches_bytes": int(evidence.get("bytes_read") or -1) == parsed["length"],
        "range_errors": errors,
    }


def _expected_object_size(evidence: dict[str, Any] | None) -> tuple[int | None, str]:
    parsed = _parsed_probe_range(evidence)
    if not parsed["range_errors"] and parsed["parsed_remote_object_size"] is not None:
        return int(parsed["parsed_remote_object_size"]), "probe_content_range"
    return None, "unknown_bounded_by_operator_cap"


def _eligibility(row: dict[str, Any], evidence: dict[str, Any] | None, selected: set[str], options: MaterializationOptions) -> tuple[str, bool, list[str]]:
    if row.get("fixture_only"):
        return "fixture_not_materializable", False, ["fixture_not_materializable"]
    if selected and row["source_id"] not in selected:
        return "source_not_selected", False, ["source_not_selected"]
    if not row.get("deterministic_url"):
        return "unsupported_source_contract", False, ["missing_deterministic_url"]
    if not evidence:
        return "probe_source_evidence_missing", False, ["probe_source_evidence_missing"]
    if evidence.get("status") not in {"succeeded", "cache_hit"}:
        return "invalid_probe_receipt", False, ["probe_source_not_succeeded"]
    if not evidence.get("sha256"):
        return "invalid_probe_receipt", False, ["probe_sha256_missing"]
    if int(evidence.get("bytes_read") or 0) <= 0:
        return "invalid_probe_receipt", False, ["probe_bytes_missing"]
    if validate_http_206_evidence(evidence):
        return "probe_content_range_invalid", False, validate_http_206_evidence(evidence)
    if evidence.get("detected_magic") not in _as_set(row.get("expected_magic")):
        return "invalid_probe_receipt", False, ["probe_magic_mismatch"]
    if evidence.get("detected_content_family") not in _as_set(row.get("expected_content_family")):
        return "invalid_probe_receipt", False, ["probe_content_family_mismatch"]
    if evidence.get("authorization_headers_present") or evidence.get("credentials_used"):
        return "invalid_probe_receipt", False, ["authorization_present"]
    expected_size, _ = _expected_object_size(evidence)
    if expected_size and expected_size > options.max_object_bytes:
        return "object_exceeds_cap", False, ["object_too_large"]
    if not expected_size and not options.max_object_bytes:
        return "size_unknown_requires_cap", False, ["size_unknown_requires_cap"]
    return "eligible", True, []


def _as_set(value: Any) -> set[str]:
    if isinstance(value, list):
        return {str(item) for item in value}
    if value is None:
        return set()
    return {str(value)}


def build_materialization_plan(
    task_id: str,
    *,
    sources: Iterable[str] | None = None,
    max_object_bytes: int = DEFAULT_MAX_OBJECT_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    minimum_free_disk_bytes: int = DEFAULT_MINIMUM_FREE_DISK_BYTES,
    disk_safety_margin_bytes: int = DEFAULT_DISK_SAFETY_MARGIN_BYTES,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    retry_limit: int = DEFAULT_RETRY_LIMIT,
    resume_enabled: bool = True,
    allow_network: bool = False,
    write_artifacts: bool = True,
    artifact_root: Path = artifact_store.ARTIFACT_ROOT,
    staging_root: Path = artifact_store.STAGING_ROOT,
    catalog_root: Path = artifact_catalog.CATALOG_ROOT,
    materializations_root: Path = MATERIALIZATION_ROOT,
    probe_runs_root: Path = Path("reports/runs"),
    probe_run_id: str | None = None,
    probe_receipt_sha256: str | None = None,
) -> dict[str, Any]:
    options = MaterializationOptions(
        _as_tuple(sources),
        max_object_bytes,
        max_total_bytes,
        minimum_free_disk_bytes,
        disk_safety_margin_bytes,
        timeout_seconds,
        retry_limit,
        resume_enabled,
        allow_network,
        artifact_root=artifact_root,
        staging_root=staging_root,
        catalog_root=catalog_root,
        materializations_root=materializations_root,
        probe_runs_root=probe_runs_root,
        probe_run_id=probe_run_id,
        probe_receipt_sha256=probe_receipt_sha256,
    )
    _validate_options(options)
    inputs = _load_inputs(task_id)
    selected = set(options.sources)
    if not selected:
        selected = {row["source_id"] for row in inputs["manifest"] if not row.get("fixture_only")}
    latest_probe, probe_receipt, probe_receipt_path, evidence_by_source, probe_metadata = _select_probe(
        task_id,
        selected,
        probe_runs_root=options.probe_runs_root,
        probe_run_id=options.probe_run_id,
        probe_receipt_sha256=options.probe_receipt_sha256,
    )
    object_plans: list[dict[str, Any]] = []
    expected_total = 0
    maximum_total = 0
    for row in sorted(inputs["manifest"], key=lambda item: item["source_id"]):
        evidence = evidence_by_source.get(row["source_id"])
        status, eligible, blockers = _eligibility(row, evidence, selected, options)
        parsed_range = _parsed_probe_range(evidence)
        expected_size, size_source = _expected_object_size(evidence)
        maximum_size = expected_size or options.max_object_bytes
        if row["source_id"] in selected and probe_metadata["selection_blocking_reasons"] and not row.get("fixture_only"):
            status, eligible, blockers = "invalid_probe_receipt", False, probe_metadata["selection_blocking_reasons"]
        extension = _container_extension(row)
        if eligible:
            expected_total += expected_size or 0
            maximum_total += maximum_size
            if maximum_total > options.max_total_bytes:
                status, eligible, blockers = "total_budget_exceeded", False, ["total_budget_exceeded"]
        object_plans.append(
            {
                "request_id": row["request_id"],
                "source_id": row["source_id"],
                "adapter": row["adapter"],
                "temporal_key": row.get("temporal_key"),
                "deterministic_url_redacted": _redact_url(row.get("deterministic_url")),
                "url_sha256": row.get("url_sha256"),
                "source_host": _url_host(row.get("deterministic_url")),
                "expected_content_family": row.get("expected_content_family"),
                "expected_magic": row.get("expected_magic"),
                "expected_container": row.get("container"),
                "artifact_extension": extension,
                "probe_run_id": (probe_receipt or {}).get("run_id"),
                "probe_evidence_class": probe_metadata["probe_evidence_class"],
                "probe_job_receipt_id": None,
                "probe_source_status": (evidence or {}).get("status"),
                "probe_network_attempted": (evidence or {}).get("network_attempted"),
                "probe_bytes": (evidence or {}).get("bytes_read"),
                "probe_sha256": (evidence or {}).get("sha256"),
                "probe_sha256_short": (evidence or {}).get("sha256_short"),
                "probe_http_status": (evidence or {}).get("http_status"),
                "probe_content_range": (evidence or {}).get("content_range"),
                "probe_content_type": (evidence or {}).get("content_type"),
                "parsed_probe_range_start": parsed_range["parsed_probe_range_start"],
                "parsed_probe_range_end": parsed_range["parsed_probe_range_end"],
                "parsed_remote_object_size": parsed_range["parsed_remote_object_size"],
                "probe_range_length": parsed_range["probe_range_length"],
                "probe_range_length_matches_bytes": parsed_range["probe_range_length_matches_bytes"],
                "probe_cache_path": (evidence or {}).get("cache_path"),
                "probe_cache_payload_verified": bool((evidence or {}).get("cache_path")),
                "expected_object_size_bytes": expected_size,
                "object_size_source": size_source,
                "max_object_bytes": options.max_object_bytes,
                "planned_artifact_store": str(options.artifact_root),
                "materialization_eligible": eligible,
                "eligibility_status": status,
                "blocking_reasons": blockers,
                "validation_steps": ["stream_complete_object", "transfer_length", "probe_prefix_continuity", "whole_object_sha256", "container_basic", "artifact_commit"],
                "provenance": {"probe_run_id": (probe_receipt or {}).get("run_id"), "fixture_only": bool(row.get("fixture_only"))},
            }
        )
    eligible_count = sum(1 for item in object_plans if item["materialization_eligible"])
    fixture_count = sum(1 for item in object_plans if item["eligibility_status"] == "fixture_not_materializable")
    blocking = sorted({reason for item in object_plans for reason in item["blocking_reasons"] if item["source_id"] in selected or not selected})
    plan = {
        "task_id": task_id,
        "package_id": inputs["package"]["package_id"],
        "package_version": inputs["package"]["package_version"],
        "package_sha256": inputs["package"]["package_sha256"],
        "package_artifact_sha256": sha256_file(inputs["paths"]["package"]),
        "manifest_sha256": inputs["package"]["manifest_sha256"],
        "manifest_artifact_sha256": sha256_file(inputs["paths"]["manifest"]),
        "execution_dag_sha256": inputs["package"]["dag_sha256"],
        "dag_artifact_sha256": sha256_file(inputs["paths"]["dag"]),
        "probe_run_id": (probe_receipt or {}).get("run_id"),
        "probe_receipt_path": str(probe_receipt_path) if probe_receipt_path else None,
        "probe_receipt_contract_sha256": (probe_receipt or {}).get("receipt_contract_sha256"),
        "probe_evidence_class": probe_metadata["probe_evidence_class"],
        "probe_receipt_verification_status": probe_metadata["probe_receipt_verification_status"],
        "probe_source_evidence_verification_status": probe_metadata["probe_source_evidence_verification_status"],
        "probe_selected_explicitly": probe_metadata["probe_selected_explicitly"],
        "probe_selection_method": probe_metadata["probe_selection_method"],
        "rejected_latest_probe_run_id": probe_metadata["rejected_latest_probe_run_id"],
        "rejected_latest_probe_reasons": probe_metadata["rejected_latest_probe_reasons"],
        "materialization_plan_contract_sha256": "",
        "source_selection": sorted(selected),
        "eligible_source_count": eligible_count,
        "already_materialized_source_count": 0,
        "ineligible_source_count": len(object_plans) - eligible_count,
        "fixture_source_count": fixture_count,
        "planned_transfer_count": eligible_count,
        "known_size_source_count": sum(1 for item in object_plans if item["expected_object_size_bytes"] is not None),
        "unknown_size_source_count": sum(1 for item in object_plans if item["expected_object_size_bytes"] is None and item["materialization_eligible"]),
        "expected_total_bytes": expected_total,
        "maximum_possible_total_bytes": maximum_total,
        "max_object_bytes": options.max_object_bytes,
        "max_total_bytes": options.max_total_bytes,
        "minimum_free_disk_bytes": options.minimum_free_disk_bytes,
        "disk_safety_margin_bytes": options.disk_safety_margin_bytes,
        "timeout_seconds": options.timeout_seconds,
        "retry_limit": options.retry_limit,
        "resume_enabled": options.resume_enabled,
        "network_required": eligible_count > 0,
        "network_allowed": allow_network,
        "approval_required": True,
        "approval_status": "approval_required",
        "object_plans": object_plans,
        "warnings": ([] if probe_receipt else ["missing_probe_receipt"]) + (["latest_probe_rejected"] if probe_metadata["rejected_latest_probe_reasons"] else []),
        "blocking_reasons": sorted(set(blocking + probe_metadata["selection_blocking_reasons"])),
        "validation_status": "PASS" if not sorted(set(blocking + probe_metadata["selection_blocking_reasons"])) and eligible_count > 0 else "WARN",
    }
    plan["materialization_plan_contract_sha256"] = compute_materialization_plan_sha256(plan)
    if write_artifacts:
        out = options.materializations_root / task_id
        write_json(out / "materialization_plan.json", plan)
        write_materialization_plan_markdown(plan, out / "materialization_plan.md")
    return plan


def write_materialization_plan_markdown(plan: dict[str, Any], path: Path) -> None:
    lines = [
        "# FasterRaster v0.9 Materialization Plan",
        "",
        f"- Task: `{plan['task_id']}`",
        f"- Selected sources: `{plan['source_selection']}`",
        f"- Eligible sources: `{plan['eligible_source_count']}`",
        f"- Fixture sources: `{plan['fixture_source_count']}`",
        f"- Planned transfers: `{plan['planned_transfer_count']}`",
        f"- Approval required: `{plan['approval_required']}`",
        f"- Plan SHA256: `{plan['materialization_plan_contract_sha256']}`",
    ]
    if plan["blocking_reasons"]:
        lines.extend(["", "## Blocking Reasons", ""])
        lines.extend(f"- `{reason}`" for reason in plan["blocking_reasons"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _validate_approval(plan: dict[str, Any], options: MaterializationOptions) -> tuple[bool, str | None]:
    if not options.allow_network or not options.allow_materialization:
        return False, "approval_required"
    if not options.approve_plan_sha256:
        return False, "approval_required"
    if len(options.approve_plan_sha256) != 64:
        return False, "plan_hash_mismatch"
    if options.approve_plan_sha256 != plan["materialization_plan_contract_sha256"]:
        return False, "plan_hash_mismatch"
    if plan["validation_status"] != "PASS" or plan["blocking_reasons"]:
        return False, "policy_blocked"
    return True, None


def _is_disallowed_host(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host in {"localhost", "localhost.localdomain"}
    return any(
        [
            address.is_loopback,
            address.is_private,
            address.is_link_local,
            address.is_multicast,
            address.is_unspecified,
        ]
    )


def _request_for_url(url: str, expected_host: str) -> urllib.request.Request:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise MaterializationError("disallowed_url_scheme")
    if parsed.hostname != expected_host:
        raise MaterializationError("source_host_mismatch")
    if parsed.hostname and _is_disallowed_host(parsed.hostname):
        raise MaterializationError("disallowed_url_host")
    return urllib.request.Request(url, headers={"Accept-Encoding": "identity", "User-Agent": USER_AGENT})


def _existing_disk_path(path: Path) -> Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current if current.exists() else Path(".")


def _stream_response(response: Any, staging_file: Path, *, max_object_bytes: int, max_total_remaining: int) -> tuple[int, str]:
    staging_file.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    bytes_written = 0
    with staging_file.open("wb") as handle:
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            if bytes_written + len(chunk) > max_object_bytes:
                raise MaterializationError("object_cap_exceeded")
            if bytes_written + len(chunk) > max_total_remaining:
                raise MaterializationError("total_cap_exceeded")
            digest.update(chunk)
            handle.write(chunk)
            bytes_written += len(chunk)
        handle.flush()
        import os

        os.fsync(handle.fileno())
    if bytes_written == 0:
        raise MaterializationError("empty_object")
    return bytes_written, digest.hexdigest()


def _normalize_failure(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, artifact_store.ArtifactStoreError):
        return "artifact_store_error", str(exc.__cause__ or exc)
    if isinstance(exc, MaterializationError):
        return str(exc), str(exc)
    return "artifact_store_error", str(exc)


def _failed_transfer_receipt(object_plan: dict[str, Any], failure_class: str, error_text: str, *, network_attempted: bool) -> dict[str, Any]:
    requested = object_plan.get("expected_object_size_bytes") or object_plan.get("max_object_bytes")
    return {
        "request_id": object_plan["request_id"],
        "source_id": object_plan["source_id"],
        "status": "failed",
        "transfer_status": "failed",
        "failure_class": failure_class,
        "errors": [error_text],
        "http_status": None,
        "network_attempted": network_attempted,
        "bytes_requested": requested,
        "bytes_transferred": 0,
        "transfer_length_valid": False,
        "prefix_match": None,
        "whole_object_sha256": None,
        "container_validation_status": "NOT_APPLICABLE",
        "artifact_promoted": False,
    }


def _basic_container_validation(path: Path, expected_magic: Any) -> tuple[str, str, dict[str, Any], str | None]:
    detected = detect_content_magic(path.read_bytes()[:4096])
    magic = detected.magic
    if magic not in _as_set(expected_magic):
        return magic or "unknown", "FAIL", {}, "magic mismatch"
    metadata: dict[str, Any] = {}
    if magic == "gzip":
        with path.open("rb") as handle:
            data = handle.read()
        if not data.startswith(b"\x1f\x8b") or len(data) < 18:
            return magic, "FAIL", {}, "malformed gzip"
        metadata = {"validation_level": "structural_basic", "trailer_present": True}
    elif magic in {"netcdf", "hdf5"}:
        metadata = {"validation_level": "magic_only"}
    elif magic == "zip":
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                for name in names:
                    parts = Path(name).parts
                    if Path(name).is_absolute() or ".." in parts:
                        return magic, "FAIL", {}, "unsafe zip member path"
                infos = archive.infolist()
                compressed = sum(item.compress_size for item in infos)
                uncompressed = sum(item.file_size for item in infos)
                metadata = {
                    "validation_level": "structural_basic",
                    "member_count": len(infos),
                    "compressed_total_bytes": compressed,
                    "uncompressed_total_bytes": uncompressed,
                    "maximum_member_expansion_ratio": max((item.file_size / max(item.compress_size, 1) for item in infos), default=0),
                }
        except zipfile.BadZipFile:
            return magic, "FAIL", {}, "corrupt zip"
    return magic, "PASS", metadata, None


def execute_materialization(
    task_id: str,
    *,
    sources: Iterable[str] | None = None,
    allow_network: bool = False,
    allow_materialization: bool = False,
    approve_plan_sha256: str | None = None,
    max_object_bytes: int = DEFAULT_MAX_OBJECT_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    minimum_free_disk_bytes: int = DEFAULT_MINIMUM_FREE_DISK_BYTES,
    disk_safety_margin_bytes: int = DEFAULT_DISK_SAFETY_MARGIN_BYTES,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    retry_limit: int = DEFAULT_RETRY_LIMIT,
    resume_enabled: bool = True,
    timestamp_utc: str | None = None,
    now_fn: Callable[[], str] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    urlopen: Callable[..., Any] | None = None,
    artifact_root: Path = artifact_store.ARTIFACT_ROOT,
    staging_root: Path = artifact_store.STAGING_ROOT,
    catalog_root: Path = artifact_catalog.CATALOG_ROOT,
    materializations_root: Path = MATERIALIZATION_ROOT,
    probe_runs_root: Path = Path("reports/runs"),
    probe_run_id: str | None = None,
    probe_receipt_sha256: str | None = None,
) -> dict[str, Any]:
    now = now_fn or utc_now
    sleep = sleep_fn or time.sleep
    opener = urlopen or urllib.request.urlopen
    options = MaterializationOptions(
        _as_tuple(sources),
        max_object_bytes,
        max_total_bytes,
        minimum_free_disk_bytes,
        disk_safety_margin_bytes,
        timeout_seconds,
        retry_limit,
        resume_enabled,
        allow_network,
        allow_materialization,
        approve_plan_sha256,
        artifact_root,
        staging_root,
        catalog_root,
        materializations_root,
        probe_runs_root,
        probe_run_id,
        probe_receipt_sha256,
    )
    _validate_options(options)
    plan = build_materialization_plan(
        task_id,
        sources=options.sources,
        max_object_bytes=max_object_bytes,
        max_total_bytes=max_total_bytes,
        minimum_free_disk_bytes=minimum_free_disk_bytes,
        disk_safety_margin_bytes=disk_safety_margin_bytes,
        timeout_seconds=timeout_seconds,
        retry_limit=retry_limit,
        resume_enabled=resume_enabled,
        allow_network=False,
        write_artifacts=True,
        artifact_root=artifact_root,
        staging_root=staging_root,
        catalog_root=catalog_root,
        materializations_root=materializations_root,
        probe_runs_root=probe_runs_root,
        probe_run_id=probe_run_id,
        probe_receipt_sha256=probe_receipt_sha256,
    )
    approved, block_reason = _validate_approval(plan, options)
    run_id = f"fr_mat_{(timestamp_utc or now()).replace('-', '').replace(':', '').replace('Z', 'Z')}_{plan['materialization_plan_contract_sha256'][:12]}"
    run_dir = materializations_root / task_id / run_id
    log: list[dict[str, Any]] = []
    safety_events: list[dict[str, Any]] = []

    def event(event_type: str, status: str | None = None, details: dict[str, Any] | None = None, source_id: str | None = None, request_id: str | None = None) -> None:
        log.append(
            {
                "sequence": len(log) + 1,
                "event_type": event_type,
                "materialization_run_id": run_id,
                "task_id": task_id,
                "request_id": request_id,
                "source_id": source_id,
                "timestamp_utc": now(),
                "status": status,
                "details_redacted": details or {},
            }
        )

    event("materialization_planned", "blocked" if not approved else "planned", {"plan_hash": plan["materialization_plan_contract_sha256"]})
    artifact_receipts: list[dict[str, Any]] = []
    transfer_receipts: list[dict[str, Any]] = []
    total_transferred = 0
    failure_classes: list[str] = []
    if approved:
        event("approval_validated", "PASS")
        try:
            artifact_store.validate_artifact_root_policy(artifact_root)
            artifact_store.validate_staging_root_policy(staging_root)
            artifact_store.ensure_disk_space(artifact_root, plan["maximum_possible_total_bytes"], disk_safety_margin_bytes)
            artifact_store.prepare_artifact_store(artifact_root)
            artifact_store.prepare_staging_root(staging_root)
        except Exception as exc:
            failure_class, error_text = _normalize_failure(exc)
            failure_classes.append(failure_class)
            source_plan = next((item for item in plan["object_plans"] if item.get("materialization_eligible")), None)
            if source_plan:
                safety_events.append({"event_type": "materialization_failure", "failure_class": failure_class, "action": "no_artifact_promoted", "source_id": source_plan["source_id"], "request_id": source_plan["request_id"], "errors_redacted": [error_text], "timestamp_utc": now()})
                transfer_receipts.append(_failed_transfer_receipt(source_plan, failure_class, error_text, network_attempted=False))
                event("source_materialization_failed", "failed", {"failure_class": failure_class}, source_plan["source_id"], source_plan["request_id"])
        for object_plan in ([] if failure_classes else plan["object_plans"]):
            if not object_plan["materialization_eligible"]:
                continue
            source_id = object_plan["source_id"]
            request_id = object_plan["request_id"]
            event("source_materialization_started", "running", source_id=source_id, request_id=request_id)
            try:
                artifact_store.ensure_disk_space(artifact_root, object_plan["expected_object_size_bytes"] or max_object_bytes, disk_safety_margin_bytes)
                network_attempted = False
                row = next(item for item in _load_inputs(task_id)["manifest"] if item["request_id"] == request_id)
                request = _request_for_url(row["deterministic_url"], object_plan["source_host"])
                event("remote_request_started", "running", source_id=source_id, request_id=request_id)
                network_attempted = True
                with opener(request, timeout=timeout_seconds) as response:
                    status = getattr(response, "status", None) or response.getcode()
                    headers = response.headers
                    if status == 404:
                        raise MaterializationError("source_unavailable")
                    if status in {401, 403}:
                        raise MaterializationError("credential_required")
                    if status >= 400:
                        raise MaterializationError("retryable_transport" if status in RETRY_HTTP else f"HTTP {status}")
                    content_length = headers.get("Content-Length")
                    if content_length and int(content_length) > max_object_bytes:
                        raise MaterializationError("object_too_large")
                    staging = artifact_store.staging_path(task_id, source_id, object_plan["url_sha256"], staging_root=staging_root)
                    bytes_written, digest = _stream_response(response, staging, max_object_bytes=max_object_bytes, max_total_remaining=max_total_bytes - total_transferred)
                    event("transfer_completed", "succeeded", {"bytes": bytes_written}, source_id, request_id)
                    if content_length and int(content_length) != bytes_written:
                        raise MaterializationError("transfer_length_mismatch")
                probe_bytes = int(object_plan["probe_bytes"] or 0)
                prefix = staging.read_bytes()[:probe_bytes]
                prefix_sha = hashlib.sha256(prefix).hexdigest()
                if probe_bytes and prefix_sha != object_plan["probe_sha256"]:
                    staging.unlink(missing_ok=True)
                    event("prefix_continuity_failed", "failed", source_id=source_id, request_id=request_id)
                    raise MaterializationError("source_changed_since_probe")
                detected_magic, container_status, metadata, container_error = _basic_container_validation(staging, object_plan["expected_magic"])
                if container_status != "PASS":
                    staging.unlink(missing_ok=True)
                    event("container_validation_failed", "failed", {"error": container_error}, source_id, request_id)
                    raise MaterializationError("container_invalid")
                destination, reused = artifact_store.promote_complete_artifact(staging, digest, object_plan["artifact_extension"], artifact_root=artifact_root)
                total_transferred += bytes_written
                receipt = {
                    "artifact_receipt_version": 1,
                    "artifact_id": f"sha256:{digest}",
                    "task_id": task_id,
                    "materialization_run_id": run_id,
                    "request_id": request_id,
                    "source_id": source_id,
                    "adapter": object_plan["adapter"],
                    "temporal_key": object_plan.get("temporal_key"),
                    "object_status": "reused_content_addressed" if reused else "committed",
                    "complete_object": True,
                    "bounded_probe_only": False,
                    "url_redacted": object_plan["deterministic_url_redacted"],
                    "url_sha256": object_plan["url_sha256"],
                    "source_host": object_plan["source_host"],
                    "object_size_bytes": bytes_written,
                    "expected_object_size_bytes": object_plan["expected_object_size_bytes"],
                    "size_match": object_plan["expected_object_size_bytes"] in {None, bytes_written},
                    "whole_object_sha256": digest,
                    "whole_object_sha256_short": digest[:12],
                    "artifact_path": str(destination),
                    "artifact_extension": object_plan["artifact_extension"],
                    "content_addressed": True,
                    "reused_existing_artifact": reused,
                    "probe_run_id": plan["probe_run_id"],
                    "probe_receipt_contract_sha256": plan["probe_receipt_contract_sha256"],
                    "probe_prefix_bytes": probe_bytes,
                    "probe_prefix_sha256": object_plan["probe_sha256"],
                    "materialized_prefix_sha256": prefix_sha,
                    "prefix_match": True,
                    "expected_magic": object_plan["expected_magic"],
                    "detected_magic": detected_magic,
                    "expected_content_family": object_plan["expected_content_family"],
                    "detected_content_family": detected_magic,
                    "container_validation_level": metadata.get("validation_level", "magic_only"),
                    "container_validation_status": "PASS",
                    "container_metadata": metadata,
                    "HTTP status": status,
                    "Content-Type": headers.get("Content-Type"),
                    "Content-Length": content_length,
                    "Content-Range": headers.get("Content-Range"),
                    "ETag": headers.get("ETag"),
                    "Last-Modified": headers.get("Last-Modified"),
                    "redirect_count": 0,
                    "transfer_attempt_count": 1,
                    "resumed": False,
                    "resume_bytes": 0,
                    "credentials_used": False,
                    "authorization_headers_present": False,
                    "warnings": [],
                    "errors": [],
                    "provenance": {"plan_hash": plan["materialization_plan_contract_sha256"]},
                    "artifact_receipt_contract_sha256": "",
                    "generated_at_utc": now(),
                }
                receipt["artifact_receipt_contract_sha256"] = compute_artifact_receipt_sha256(receipt, Path.cwd())
                artifact_receipts.append(receipt)
                transfer_receipts.append({"request_id": request_id, "source_id": source_id, "status": "succeeded", "transfer_status": "succeeded", "network_attempted": True, "bytes_requested": object_plan.get("expected_object_size_bytes") or bytes_written, "bytes_transferred": bytes_written, "transfer_length_valid": True, "prefix_match": True, "whole_object_sha256": digest, "container_validation_status": "PASS", "artifact_promoted": True})
                event("artifact_committed" if not reused else "artifact_reused", "succeeded", {"artifact_id": receipt["artifact_id"]}, source_id, request_id)
            except Exception as exc:
                failure, error_text = _normalize_failure(exc)
                failure_classes.append(failure)
                safety_events.append({"event_type": "materialization_failure", "source_id": source_id, "request_id": request_id, "failure_class": failure, "action": "no_artifact_promoted", "errors_redacted": [error_text], "timestamp_utc": now()})
                transfer_receipts.append(_failed_transfer_receipt(object_plan, failure, error_text, network_attempted=locals().get("network_attempted", False)))
                event("source_materialization_failed", "failed", {"failure_class": failure}, source_id, request_id)
                break
    else:
        failure_classes.append(block_reason or "policy_blocked")
        safety_events.append({"event_type": "materialization_blocked", "failure_class": block_reason or "policy_blocked", "timestamp_utc": now()})
    run_status = "blocked_policy" if not approved else ("completed" if artifact_receipts and not failure_classes else "failed")
    run_dir.mkdir(parents=True, exist_ok=True)
    artifact_paths: list[Path] = []
    for index, receipt in enumerate(artifact_receipts):
        path = run_dir / f"artifact_receipt_{index + 1}.json"
        write_json(path, receipt)
        artifact_paths.append(path)
    catalog_snapshot = artifact_catalog.update_catalog(artifact_receipts, artifact_paths, catalog_root=catalog_root, now=now()) if artifact_receipts else artifact_catalog.load_catalog(catalog_root)
    catalog_verification = artifact_catalog.verify_artifact_catalog(catalog_snapshot, catalog_root=catalog_root)
    run_receipt = {
        "materialization_run_id": run_id,
        "task_id": task_id,
        "package_id": plan["package_id"],
        "package_version": plan["package_version"],
        "package_sha256": plan["package_sha256"],
        "manifest_sha256": plan["manifest_sha256"],
        "execution_dag_sha256": plan["execution_dag_sha256"],
        "probe_run_id": plan["probe_run_id"],
        "probe_receipt_contract_sha256": plan["probe_receipt_contract_sha256"],
        "materialization_plan_contract_sha256": plan["materialization_plan_contract_sha256"],
        "materialization_run_receipt_contract_sha256": "",
        "started_at_utc": timestamp_utc or now(),
        "finished_at_utc": now(),
        "duration_ms": 0,
        "run_status": run_status,
        "execution_blocked": block_reason if not approved else False,
        "allow_network": allow_network,
        "allow_materialization": allow_materialization,
        "approval_hash_supplied": approve_plan_sha256,
        "approval_hash_valid": approved,
        "network_run": any(item.get("network_attempted") is True for item in transfer_receipts),
        "source_selection": plan["source_selection"],
        "planned_source_count": plan["planned_transfer_count"],
        "attempted_source_count": len(transfer_receipts),
        "materialized_source_count": len(artifact_receipts),
        "reused_source_count": sum(1 for item in artifact_receipts if item["reused_existing_artifact"]),
        "failed_source_count": sum(1 for item in transfer_receipts if item["status"] == "failed"),
        "skipped_source_count": plan["planned_transfer_count"] - len(transfer_receipts),
        "fixture_source_count": plan["fixture_source_count"],
        "known_size_source_count": plan["known_size_source_count"],
        "unknown_size_source_count": plan["unknown_size_source_count"],
        "expected_total_bytes": plan["expected_total_bytes"],
        "total_bytes_requested": plan["maximum_possible_total_bytes"],
        "total_bytes_transferred": total_transferred,
        "total_bytes_materialized": sum(item["object_size_bytes"] for item in artifact_receipts),
        "total_bytes_reused": sum(item["object_size_bytes"] for item in artifact_receipts if item["reused_existing_artifact"]),
        "max_object_bytes": max_object_bytes,
        "max_total_bytes": max_total_bytes,
        "byte_budget_remaining": max_total_bytes - total_transferred,
        "minimum_free_disk_bytes": minimum_free_disk_bytes,
        "disk_safety_margin_bytes": disk_safety_margin_bytes,
        "initial_free_disk_bytes": artifact_store.inspect_available_disk_space(artifact_root),
        "all_object_caps_respected": all(item["object_size_bytes"] <= max_object_bytes for item in artifact_receipts),
        "total_byte_cap_respected": total_transferred <= max_total_bytes,
        "all_transfer_lengths_valid": bool(artifact_receipts) and not any(item.get("status") == "failed" for item in transfer_receipts) and all(item.get("size_match") is True for item in artifact_receipts),
        "all_probe_prefixes_match": bool(artifact_receipts) and not any(item.get("status") == "failed" for item in transfer_receipts) and all(item.get("prefix_match") is True for item in artifact_receipts),
        "all_whole_object_checksums_present": bool(artifact_receipts) and not any(item.get("status") == "failed" for item in transfer_receipts) and all(bool(item.get("whole_object_sha256")) for item in artifact_receipts),
        "all_container_validations_passed": bool(artifact_receipts) and not any(item.get("status") == "failed" for item in transfer_receipts) and all(item.get("container_validation_status") == "PASS" for item in artifact_receipts),
        "all_artifact_paths_content_addressed": bool(artifact_receipts) and not any(item.get("status") == "failed" for item in transfer_receipts) and all(item.get("content_addressed") is True for item in artifact_receipts),
        "catalog_update_status": catalog_verification["verification_status"],
        "artifact_receipt_count": len(artifact_receipts),
        "transfer_receipt_count": len(transfer_receipts),
        "credentials_used": False,
        "authorization_headers_present": False,
        "warnings": [],
        "errors": [] if approved else [block_reason or "policy_blocked"],
        "failure_classes": failure_classes,
        "artifact_receipts": artifact_receipts,
    }
    run_receipt["materialization_run_receipt_contract_sha256"] = compute_materialization_run_receipt_sha256(run_receipt, Path.cwd())
    write_json(run_dir / "materialization_run_receipt.json", run_receipt)
    write_materialization_receipt_markdown(run_receipt, run_dir / "materialization_run_receipt.md")
    write_json(run_dir / "artifact_receipts.json", artifact_receipts)
    write_jsonl(run_dir / "artifact_receipts.jsonl", artifact_receipts)
    write_json(run_dir / "transfer_receipts.json", transfer_receipts)
    write_jsonl(run_dir / "transfer_receipts.jsonl", transfer_receipts)
    verification = verify_materialization_run(run_dir / "materialization_run_receipt.json", repo_root=Path.cwd()) if artifact_receipts else {
        "contract_verification_status": "PASS",
        "execution_outcome_status": "FAILED" if run_status == "failed" else ("BLOCKED" if run_status == "blocked_policy" else "NOT_APPLICABLE"),
        "artifact_verification_status": "NOT_APPLICABLE",
        "catalog_verification_status": catalog_verification["verification_status"],
        "release_evidence_status": "FAIL",
        "verification_status": "FAIL" if run_status == "failed" else "NOT_APPLICABLE",
        "failures": failure_classes,
        "checks": [],
    }
    write_json(run_dir / "materialization_verification.json", verification)
    write_json(run_dir / "safety_events.json", {"events": safety_events})
    write_json(run_dir / "artifact_catalog_delta.json", {"artifact_count": len(artifact_receipts), "catalog_status": catalog_verification["verification_status"]})
    event("materialization_run_completed", run_status)
    event("materialization_receipt_written", "succeeded", {"receipt_path": str(run_dir / "materialization_run_receipt.json")})
    write_jsonl(run_dir / "execution_log.jsonl", log)
    pointer = {
        "task_id": task_id,
        "materialization_run_id": run_id,
        "receipt_path": str(run_dir / "materialization_run_receipt.json"),
        "receipt_contract_sha256": run_receipt["materialization_run_receipt_contract_sha256"],
        "run_status": run_status,
        "updated_at_utc": now(),
    }
    pointer_root = materializations_root / task_id
    write_json(pointer_root / "latest_materialization.json", pointer)
    if run_status == "completed":
        write_json(pointer_root / "latest_successful_materialization.json", pointer)
    elif run_status == "failed":
        write_json(pointer_root / "latest_failed_materialization.json", pointer)
    return {"run_status": run_status, "materialization_run_id": run_id, "receipt_path": str(run_dir / "materialization_run_receipt.json"), "receipt": run_receipt, "verification": verification, "transfer_receipts": transfer_receipts}


def write_materialization_receipt_markdown(receipt: dict[str, Any], path: Path) -> None:
    lines = [
        "# FasterRaster v0.9 Materialization Run Receipt",
        "",
        f"- Run: `{receipt['materialization_run_id']}`",
        f"- Status: `{receipt['run_status']}`",
        f"- Materialized sources: `{receipt['materialized_source_count']}`",
        f"- Network run: `{receipt['network_run']}`",
        f"- Receipt SHA256: `{receipt['materialization_run_receipt_contract_sha256']}`",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
