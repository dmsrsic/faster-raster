from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from faster_raster import __version__
from faster_raster.adapter_contract import stable_json
from faster_raster.content_magic import detect_content_magic
from faster_raster.run_receipts import compute_receipt_contract_sha256, sha256_file, write_json, write_jsonl

DEFAULT_MAX_BYTES_PER_SOURCE = 65_536
DEFAULT_MAX_TOTAL_BYTES = 1_048_576
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_RETRY_LIMIT = 1
RUN_ROOT = Path("reports/runs")
PACKAGE_ROOT = Path("reports/execution_packages")
COMPILE_ROOT = Path("reports/task_compiles")
RUNTIME_CACHE_ROOT = Path("cache/runtime/static_http_range")
USER_AGENT = f"FasterRaster/{__version__} local-bounded-executor"
CACHE_CONTRACT_VERSION = 1
SUPPORTED_STAGES = {
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
RETRY_HTTP = {429, 500, 502, 503, 504}
NO_RETRY_HTTP = {400, 401, 403, 404}


class LocalExecutionError(ValueError):
    pass


@dataclass(frozen=True)
class RuntimeOptions:
    max_bytes_per_source: int = DEFAULT_MAX_BYTES_PER_SOURCE
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    retry_limit: int = DEFAULT_RETRY_LIMIT
    fail_fast: bool = False
    allow_network: bool = False
    cache_root: Path = RUNTIME_CACHE_ROOT


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _duration_ms(start: float) -> int:
    return int(round((time.monotonic() - start) * 1000))


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl_or_json(path_jsonl: Path, path_json: Path) -> list[dict[str, Any]]:
    if path_jsonl.exists():
        return [json.loads(line) for line in path_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    return json.loads(path_json.read_text(encoding="utf-8"))


def _contract_hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def _as_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, list):
        return {str(item) for item in value}
    return {str(value)}


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _validate_options(options: RuntimeOptions) -> None:
    if options.max_bytes_per_source <= 0:
        raise LocalExecutionError("max_bytes_per_source must be positive")
    if options.max_total_bytes <= 0:
        raise LocalExecutionError("max_total_bytes must be positive")
    if options.timeout_seconds <= 0:
        raise LocalExecutionError("timeout_seconds must be positive")
    if options.retry_limit < 0:
        raise LocalExecutionError("retry_limit must not be negative")
    if options.max_bytes_per_source > options.max_total_bytes:
        raise LocalExecutionError("max_bytes_per_source must not exceed max_total_bytes")


def load_execution_inputs(task_id: str) -> dict[str, Any]:
    package_dir = PACKAGE_ROOT / task_id
    compile_dir = COMPILE_ROOT / task_id
    paths = {
        "package": package_dir / "execution_package.json",
        "jobs_json": package_dir / "execution_jobs.json",
        "jobs_jsonl": package_dir / "execution_jobs.jsonl",
        "dag": package_dir / "dag.json",
        "cache_plan": package_dir / "cache_plan.json",
        "failure_policy": package_dir / "failure_policy.json",
        "manifest": compile_dir / "acquisition_manifest.jsonl",
    }
    missing = [str(path) for path in paths.values() if not path.exists() and path.name != "execution_jobs.json"]
    if not paths["jobs_json"].exists() and not paths["jobs_jsonl"].exists():
        missing.append(str(paths["jobs_jsonl"]))
    if missing:
        raise LocalExecutionError(f"missing execution artifact(s): {missing}")
    return {
        "paths": paths,
        "package": _read_json(paths["package"]),
        "jobs": _read_jsonl_or_json(paths["jobs_jsonl"], paths["jobs_json"]),
        "dag": _read_json(paths["dag"]),
        "cache_plan": _read_json(paths["cache_plan"]),
        "failure_policy": _read_json(paths["failure_policy"]),
        "manifest": _read_jsonl_or_json(paths["manifest"], paths["manifest"]),
    }


def topological_order(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {job["job_id"]: job for job in jobs}
    if len(by_id) != len(jobs):
        raise LocalExecutionError("duplicate job_id in execution package")
    visiting: set[str] = set()
    visited: set[str] = set()
    ordered: list[dict[str, Any]] = []

    def visit(job_id: str) -> None:
        if job_id in visited:
            return
        if job_id in visiting:
            raise LocalExecutionError("dependency cycle detected")
        if job_id not in by_id:
            raise LocalExecutionError(f"unknown dependency job_id: {job_id}")
        visiting.add(job_id)
        for dep in by_id[job_id].get("dependencies") or []:
            visit(dep)
        visiting.remove(job_id)
        visited.add(job_id)
        ordered.append(by_id[job_id])

    for job in sorted(jobs, key=lambda item: item["job_id"]):
        visit(job["job_id"])
    return ordered


def validate_package_and_dag(inputs: dict[str, Any]) -> None:
    package = inputs["package"]
    jobs = inputs["jobs"]
    dag = inputs["dag"]
    if package.get("validation_status") != "PASS":
        raise LocalExecutionError("execution package validation_status is not PASS")
    if package.get("dag_validation_status") != "PASS" or dag.get("status") != "PASS":
        raise LocalExecutionError("execution package DAG is not valid")
    if package.get("total_job_count") != len(jobs):
        raise LocalExecutionError("execution package job count mismatch")
    for job in jobs:
        if job.get("stage") not in SUPPORTED_STAGES:
            raise LocalExecutionError(f"unsupported stage in package: {job.get('stage')}")
    topological_order(jobs)


def build_run_plan(
    task_id: str,
    *,
    max_bytes_per_source: int = DEFAULT_MAX_BYTES_PER_SOURCE,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    retry_limit: int = DEFAULT_RETRY_LIMIT,
    fail_fast: bool = False,
    allow_network: bool = False,
    write_artifacts: bool = True,
    run_root: Path | None = None,
) -> dict[str, Any]:
    options = RuntimeOptions(max_bytes_per_source, max_total_bytes, timeout_seconds, retry_limit, fail_fast, allow_network)
    _validate_options(options)
    inputs = load_execution_inputs(task_id)
    validate_package_and_dag(inputs)
    package = inputs["package"]
    jobs = inputs["jobs"]
    source_ids = sorted({job["source_id"] for job in jobs})
    network_jobs = [job for job in jobs if job.get("network_required")]
    fixture_jobs = [job for job in jobs if job.get("stage") == "record_fixture_evidence"]
    runnable_sources = {job["source_id"] for job in jobs if not job.get("fixture_only")}
    plan = {
        "task_id": task_id,
        "package_id": package["package_id"],
        "package_version": package["package_version"],
        "package_sha256": package["package_sha256"],
        "package_artifact_sha256": sha256_file(inputs["paths"]["package"]),
        "manifest_sha256": package["manifest_sha256"],
        "manifest_artifact_sha256": sha256_file(inputs["paths"]["manifest"]),
        "dag_sha256": package["dag_sha256"],
        "dag_artifact_sha256": sha256_file(inputs["paths"]["dag"]),
        "executable_request_count": package["executable_request_count"],
        "fixture_request_count": package["fixture_request_count"],
        "planned_job_count": len(jobs),
        "planned_network_job_count": len(network_jobs),
        "planned_fixture_job_count": len(fixture_jobs),
        "max_bytes_per_source": max_bytes_per_source,
        "max_total_bytes": max_total_bytes,
        "timeout_seconds": timeout_seconds,
        "retry_limit": retry_limit,
        "fail_fast": fail_fast,
        "network_required": bool(network_jobs),
        "network_allowed": allow_network,
        "source_ids": source_ids,
        "expected_total_max_bytes": min(max_total_bytes, max_bytes_per_source * len(runnable_sources)),
        "validation_steps": [
            "validate_http_status",
            "validate_byte_cap",
            "validate_magic",
            "validate_content_family",
            "compute_checksum",
            "record_source_evidence",
        ],
        "safety_checks": [
            "explicit_network_opt_in",
            "bounded_range_request",
            "host_template_match",
            "byte_caps_enforced",
            "no_credentials",
            "no_arbitrary_shell",
        ],
        "warnings": [] if allow_network else ["network_not_allowed; local execution will be policy-blocked"],
    }
    plan["run_plan_contract_sha256"] = _contract_hash({key: value for key, value in plan.items() if key != "run_plan_contract_sha256"})
    if write_artifacts:
        out_dir = (run_root or RUN_ROOT) / task_id
        write_json(out_dir / "run_plan.json", plan)
        write_run_plan_markdown(plan, out_dir / "run_plan.md")
    return plan


def write_run_plan_markdown(plan: dict[str, Any], path: Path) -> None:
    lines = [
        "# FasterRaster v0.8 Run Plan",
        "",
        f"- Task: `{plan['task_id']}`",
        f"- Package: `{plan['package_id']}`",
        f"- Planned jobs: `{plan['planned_job_count']}`",
        f"- Network jobs: `{plan['planned_network_job_count']}`",
        f"- Fixture jobs: `{plan['planned_fixture_job_count']}`",
        f"- Network allowed: `{plan['network_allowed']}`",
        f"- Run plan contract SHA256: `{plan['run_plan_contract_sha256']}`",
    ]
    if plan["warnings"]:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in plan["warnings"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _base_job_receipt(job: dict[str, Any], task_id: str, now: Callable[[], str]) -> dict[str, Any]:
    return {
        "job_id": job["job_id"],
        "request_id": job.get("request_id"),
        "task_id": task_id,
        "source_id": job.get("source_id"),
        "adapter": job.get("adapter"),
        "stage": job.get("stage"),
        "status": "pending",
        "dependencies": job.get("dependencies") or [],
        "dependency_statuses": {},
        "network_attempted": False,
        "started_at_utc": None,
        "finished_at_utc": None,
        "duration_ms": None,
        "input_contract_sha256": _contract_hash(job),
        "output_contract_sha256": None,
        "http_status": None,
        "content_type": None,
        "content_range": None,
        "bytes_requested": job.get("max_bytes"),
        "bytes_read": None,
        "byte_cap": job.get("max_bytes"),
        "range_requested": False,
        "range_honored": None,
        "expected_magic": None,
        "detected_magic": None,
        "expected_content_family": None,
        "detected_content_family": None,
        "sha256": None,
        "sha256_short": None,
        "cache_path": job.get("cache_path"),
        "warnings": [],
        "errors": [],
        "failure_class": None,
        "retry_count": 0,
        "credentials_used": False,
        "authorization_redacted": True,
    }


def execute_local(
    task_id: str,
    *,
    allow_network: bool = False,
    max_bytes_per_source: int = DEFAULT_MAX_BYTES_PER_SOURCE,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    retry_limit: int = DEFAULT_RETRY_LIMIT,
    fail_fast: bool = False,
    timestamp_utc: str | None = None,
    now_fn: Callable[[], str] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    urlopen: Callable[..., Any] | None = None,
    cache_root: Path | None = None,
    reports_root: Path | None = None,
) -> dict[str, Any]:
    options = RuntimeOptions(max_bytes_per_source, max_total_bytes, timeout_seconds, retry_limit, fail_fast, allow_network, cache_root or RUNTIME_CACHE_ROOT)
    run_root = (reports_root / "runs") if reports_root is not None else RUN_ROOT
    _validate_options(options)
    now = now_fn or utc_now
    sleep = sleep_fn or time.sleep
    opener = urlopen or urllib.request.urlopen
    deterministic_test_fixture = urlopen is not None
    inputs = load_execution_inputs(task_id)
    validate_package_and_dag(inputs)
    plan = build_run_plan(
        task_id,
        max_bytes_per_source=max_bytes_per_source,
        max_total_bytes=max_total_bytes,
        timeout_seconds=timeout_seconds,
        retry_limit=retry_limit,
        fail_fast=fail_fast,
        allow_network=allow_network,
        write_artifacts=True,
        run_root=run_root,
    )
    package = inputs["package"]
    package_hash_short = package["package_sha256"][:12]
    run_id = f"fr_run_{(timestamp_utc or now()).replace('-', '').replace(':', '').replace('Z', 'Z')}_{package_hash_short}"
    run_dir = run_root / task_id / run_id
    log: list[dict[str, Any]] = []
    safety_events: list[dict[str, Any]] = []
    state: dict[str, Any] = {
        "fetched": {},
        "source_evidence": {},
        "cache_entries": [],
        "total_bytes_read": 0,
        "total_bytes_requested": 0,
        "status_by_job": {},
    }

    def event(event_type: str, job: dict[str, Any] | None = None, status: str | None = None, details: dict[str, Any] | None = None) -> None:
        log.append(
            {
                "sequence": len(log) + 1,
                "event_type": event_type,
                "run_id": run_id,
                "job_id": (job or {}).get("job_id"),
                "request_id": (job or {}).get("request_id"),
                "source_id": (job or {}).get("source_id"),
                "stage": (job or {}).get("stage"),
                "status": status,
                "timestamp_utc": now(),
                "details_redacted": details or {},
            }
        )

    event("run_planned", details={"run_plan_contract_sha256": plan["run_plan_contract_sha256"]})
    event("package_validated", details={"package_id": package["package_id"]})
    event("dag_validated", details={"job_count": len(inputs["jobs"])})

    job_receipts: list[dict[str, Any]] = []
    ordered_jobs = topological_order(inputs["jobs"])
    manifest_by_request = {row["request_id"]: row for row in inputs["manifest"]}
    handlers = _stage_handlers(manifest_by_request, options, opener, sleep, now, state, event, safety_events)
    blocked_by_policy = False
    for job in ordered_jobs:
        receipt = _base_job_receipt(job, task_id, now)
        receipt["dependency_statuses"] = {dep: state["status_by_job"].get(dep) for dep in receipt["dependencies"]}
        failed_deps = [
            dep
            for dep, status in receipt["dependency_statuses"].items()
            if status in {"failed", "unsupported", "skipped_network_disabled", "skipped_dependency_failed"}
        ]
        if failed_deps:
            receipt.update({"status": "skipped_dependency_failed", "failure_class": "policy_blocked", "errors": [f"dependency failed: {dep}" for dep in failed_deps]})
            event("job_skipped", job, receipt["status"], {"failed_dependencies": failed_deps})
        elif job.get("network_required") and not allow_network:
            blocked_by_policy = True
            receipt.update({"status": "skipped_network_disabled", "failure_class": "policy_blocked", "errors": ["network_not_allowed"]})
            safety_events.append({"event_type": "network_blocked", "job_id": job["job_id"], "source_id": job["source_id"], "timestamp_utc": now()})
            event("job_skipped", job, receipt["status"], {"reason": "network_not_allowed"})
        elif job["stage"] not in handlers:
            receipt.update({"status": "unsupported", "failure_class": "unsupported_downstream_stage", "errors": [f"unsupported stage: {job['stage']}"]})
            event("job_failed", job, receipt["status"], {"failure_class": receipt["failure_class"]})
        else:
            receipt["status"] = "running"
            receipt["started_at_utc"] = now()
            start = time.monotonic()
            event("job_started", job, "running")
            try:
                handlers[job["stage"]](job, receipt)
            except Exception as exc:
                receipt["status"] = "failed"
                receipt["failure_class"] = receipt["failure_class"] or _classify_exception(exc)
                receipt["errors"].append(str(exc))
                event("validation_failed" if receipt["failure_class"] == "validation_failure" else "job_failed", job, "failed", {"failure_class": receipt["failure_class"]})
            receipt["duration_ms"] = _duration_ms(start)
            receipt["finished_at_utc"] = now()
            if receipt["status"] in {"succeeded", "fixture_recorded", "cache_hit"}:
                event("job_succeeded" if receipt["status"] != "fixture_recorded" else "fixture_recorded", job, receipt["status"])
            if fail_fast and receipt["status"] == "failed":
                state["fail_fast_triggered"] = True
        receipt["output_contract_sha256"] = _contract_hash({key: value for key, value in receipt.items() if key != "output_contract_sha256"})
        state["status_by_job"][job["job_id"]] = receipt["status"]
        job_receipts.append(receipt)
        if state.get("fail_fast_triggered"):
            break

    if state.get("fail_fast_triggered"):
        pending = [job for job in ordered_jobs if job["job_id"] not in state["status_by_job"]]
        for job in pending:
            receipt = _base_job_receipt(job, task_id, now)
            receipt.update({"status": "skipped_dependency_failed", "failure_class": "policy_blocked", "errors": ["fail_fast_triggered"]})
            receipt["output_contract_sha256"] = _contract_hash(receipt)
            state["status_by_job"][job["job_id"]] = receipt["status"]
            job_receipts.append(receipt)
            event("job_skipped", job, receipt["status"], {"reason": "fail_fast_triggered"})

    source_evidence = {"task_id": task_id, "run_id": run_id, "sources": sorted(state["source_evidence"].values(), key=lambda item: item["source_id"])}
    receipt = _build_run_receipt(run_id, task_id, package, plan, inputs, options, job_receipts, source_evidence, safety_events, blocked_by_policy, now(), deterministic_test_fixture)
    receipt["receipt_contract_sha256"] = compute_receipt_contract_sha256(receipt, Path.cwd())
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(run_dir / "run_receipt.json", receipt)
    write_receipt_markdown(receipt, run_dir / "run_receipt.md")
    write_json(run_dir / "job_receipts.json", job_receipts)
    write_jsonl(run_dir / "job_receipts.jsonl", job_receipts)
    write_json(run_dir / "source_evidence.json", source_evidence)
    write_json(run_dir / "cache_index.json", {"task_id": task_id, "run_id": run_id, "entries": state["cache_entries"]})
    write_json(run_dir / "safety_events.json", {"task_id": task_id, "run_id": run_id, "events": safety_events})
    event("run_completed", details={"run_status": receipt["run_status"]})
    event("receipt_written", details={"receipt_path": str(run_dir / "run_receipt.json")})
    write_jsonl(run_dir / "execution_log.jsonl", log)
    latest_payload = {
        "task_id": task_id,
        "run_id": run_id,
        "receipt_path": str(run_dir / "run_receipt.json"),
        "receipt_contract_sha256": receipt["receipt_contract_sha256"],
        "run_status": receipt["run_status"],
        "evidence_class": receipt["evidence_class"],
        "updated_at_utc": now(),
    }
    write_json(run_root / task_id / "latest_run.json", latest_payload)
    if receipt["run_status"] in {"completed", "completed_with_warnings"} and receipt["evidence_class"] == "live_network":
        write_json(run_root / task_id / "latest_live_verified_run.json", latest_payload)
    if receipt["evidence_class"] == "deterministic_test_fixture":
        write_json(run_root / task_id / "latest_test_fixture_run.json", latest_payload)
    return {"run_status": receipt["run_status"], "run_id": run_id, "receipt": receipt, "receipt_path": str(run_dir / "run_receipt.json")}


def _stage_handlers(
    manifest_by_request: dict[str, dict[str, Any]],
    options: RuntimeOptions,
    urlopen: Callable[..., Any],
    sleep: Callable[[float], None],
    now: Callable[[], str],
    state: dict[str, Any],
    event: Callable[..., None],
    safety_events: list[dict[str, Any]],
) -> dict[str, Callable[[dict[str, Any], dict[str, Any]], None]]:
    def manifest(job: dict[str, Any]) -> dict[str, Any]:
        return manifest_by_request.get(job["request_id"], {})

    def resolve_request(job: dict[str, Any], receipt: dict[str, Any]) -> None:
        row = manifest(job)
        _validate_url_host(job, row)
        receipt.update({"status": "succeeded", "expected_magic": row.get("expected_magic"), "expected_content_family": row.get("expected_content_family")})

    def bounded_fetch(job: dict[str, Any], receipt: dict[str, Any]) -> None:
        row = manifest(job)
        _validate_url_host(job, row)
        cached, cache_errors = _read_valid_cache(job, row, options.max_bytes_per_source, options.cache_root)
        if cached:
            state["fetched"][job["request_id"]] = cached
            receipt.update(cached)
            receipt.update({"status": "cache_hit", "network_attempted": False})
            state["cache_entries"].append(
                {
                    "source_id": job["source_id"],
                    "request_id": job["request_id"],
                    "cache_path": cached["cache_path"],
                    "receipt_path": cached["cache_receipt_path"],
                    "cache_status": "hit",
                    "payload_sha256": cached["sha256"],
                    "bytes_read": cached["bytes_read"],
                    "http_status": cached["http_status"],
                    "range_honored": cached["range_honored"],
                    "validated": True,
                }
            )
            return
        if cache_errors:
            safety_events.append(
                {
                    "event_type": "invalid_cache_entry",
                    "source_id": job["source_id"],
                    "request_id": job["request_id"],
                    "validation_errors": cache_errors,
                    "action": "refetch" if options.allow_network else "block",
                    "timestamp_utc": now(),
                }
            )
        if state["total_bytes_read"] >= options.max_total_bytes:
            raise LocalExecutionError("total byte cap exhausted")
        result = _bounded_fetch(job, row, options, urlopen, sleep, now, event)
        retained = result.pop("_data")
        if state["total_bytes_read"] + len(retained) > options.max_total_bytes:
            raise LocalExecutionError("total byte cap exceeded")
        cache_path, sidecar_path = _write_runtime_cache(job, row, retained, result, options.max_bytes_per_source, now(), options.cache_root)
        logical_cache_path = _logical_runtime_cache_path(cache_path, options.cache_root)
        logical_sidecar_path = _logical_runtime_cache_path(sidecar_path, options.cache_root)
        result["cache_path"] = logical_cache_path.as_posix()
        state["cache_entries"].append(
            {
                "source_id": job["source_id"],
                "request_id": job["request_id"],
                "cache_path": logical_cache_path.as_posix(),
                "receipt_path": logical_sidecar_path.as_posix(),
                "cache_status": "invalid_refetched" if cache_errors else "fetched",
                "validation_errors": cache_errors,
                "payload_sha256": result["sha256"],
                "bytes_read": result["bytes_read"],
                "http_status": result["http_status"],
                "range_honored": result["range_honored"],
                "validated": True,
            }
        )
        state["fetched"][job["request_id"]] = result
        state["total_bytes_requested"] += options.max_bytes_per_source
        state["total_bytes_read"] += result["bytes_read"]
        receipt.update(result)
        receipt.update({"status": "succeeded", "network_attempted": True})

    def validation_stage(job: dict[str, Any], receipt: dict[str, Any]) -> None:
        fetched = state["fetched"].get(job["request_id"])
        if not fetched:
            raise LocalExecutionError("bounded fetch result missing")
        receipt.update(fetched)
        stage = job["stage"]
        if stage == "validate_http_status" and receipt["http_status"] not in {200, 206}:
            raise LocalExecutionError(f"HTTP status {receipt['http_status']}")
        if stage == "validate_byte_cap" and int(receipt["bytes_read"] or 0) > options.max_bytes_per_source:
            raise LocalExecutionError("byte cap exceeded")
        if stage == "validate_magic" and receipt["detected_magic"] not in _as_set(manifest(job).get("expected_magic")):
            raise LocalExecutionError("magic mismatch")
        if stage == "validate_content_family" and receipt["detected_content_family"] not in _as_set(manifest(job).get("expected_content_family")):
            raise LocalExecutionError("content-family mismatch")
        receipt["status"] = "succeeded"
        event("validation_passed", job, "succeeded", {"stage": stage})

    def compute_checksum(job: dict[str, Any], receipt: dict[str, Any]) -> None:
        fetched = state["fetched"].get(job["request_id"])
        if not fetched or not fetched.get("sha256"):
            raise LocalExecutionError("checksum source missing")
        receipt.update(fetched)
        receipt["status"] = "succeeded"

    def record_source_evidence(job: dict[str, Any], receipt: dict[str, Any]) -> None:
        fetched = state["fetched"].get(job["request_id"])
        row = manifest(job)
        if not fetched:
            raise LocalExecutionError("source evidence missing fetch result")
        evidence = {**fetched, "task_id": job["task_id"], "request_id": job["request_id"], "source_id": job["source_id"], "adapter": job["adapter"], "fixture_only": False, "status": "succeeded", "expected_magic": row.get("expected_magic"), "expected_content_family": row.get("expected_content_family")}
        state["source_evidence"][job["source_id"]] = evidence
        receipt.update(fetched)
        receipt["status"] = "succeeded"
        event("source_evidence_recorded", job, "succeeded")

    def record_fixture_evidence(job: dict[str, Any], receipt: dict[str, Any]) -> None:
        row = manifest(job)
        provenance = row.get("provenance") or {}
        evidence = {
            "task_id": job["task_id"],
            "request_id": job["request_id"],
            "source_id": job["source_id"],
            "adapter": job["adapter"],
            "status": "fixture_recorded",
            "fixture_only": True,
            "network_attempted": False,
            "http_status": provenance.get("historical_http_status"),
            "historical_http_status": provenance.get("historical_http_status"),
            "historical_bytes_read": provenance.get("historical_bytes_read"),
            "historical_detected_magic": provenance.get("historical_detected_magic"),
            "historical_sha256_short": provenance.get("historical_sha256_short"),
            "current_endpoint_status": provenance.get("current_endpoint_status"),
            "bytes_read": 0,
            "byte_cap": None,
            "expected_magic": row.get("expected_magic"),
            "detected_magic": provenance.get("historical_detected_magic"),
            "expected_content_family": row.get("expected_content_family"),
            "detected_content_family": provenance.get("historical_detected_magic"),
            "sha256": None,
            "sha256_short": provenance.get("historical_sha256_short"),
            "range_honored": provenance.get("historical_http_status") == 206,
            "cache_path": None,
        }
        state["source_evidence"][job["source_id"]] = evidence
        receipt.update(evidence)
        receipt["status"] = "fixture_recorded"

    return {
        "resolve_request": resolve_request,
        "bounded_fetch": bounded_fetch,
        "validate_http_status": validation_stage,
        "validate_byte_cap": validation_stage,
        "validate_magic": validation_stage,
        "validate_content_family": validation_stage,
        "compute_checksum": compute_checksum,
        "record_source_evidence": record_source_evidence,
        "record_fixture_evidence": record_fixture_evidence,
    }


def _validate_url_host(job: dict[str, Any], row: dict[str, Any]) -> None:
    url = job.get("deterministic_url")
    if job.get("fixture_only"):
        return
    parsed = urllib.parse.urlparse(url or "")
    if parsed.scheme not in {"http", "https"}:
        raise LocalExecutionError("only http and https URLs are allowed")
    expected = urllib.parse.urlparse(row.get("deterministic_url") or "")
    if parsed.hostname != expected.hostname:
        raise LocalExecutionError("rendered URL host does not match source template host")


def _bounded_fetch(
    job: dict[str, Any],
    row: dict[str, Any],
    options: RuntimeOptions,
    urlopen: Callable[..., Any],
    sleep: Callable[[float], None],
    now: Callable[[], str],
    event: Callable[..., None],
) -> dict[str, Any]:
    url = job["deterministic_url"]
    headers = {"Range": f"bytes=0-{options.max_bytes_per_source - 1}", "User-Agent": USER_AGENT}
    attempt = 0
    while True:
        request = urllib.request.Request(url, headers=headers, method="GET")
        event("network_request_started", job, "running", {"range": headers["Range"]})
        try:
            with urlopen(request, timeout=options.timeout_seconds) as response:
                status = getattr(response, "status", None) or response.getcode()
                response_headers = response.headers
                data = response.read(options.max_bytes_per_source + 1)
            break
        except urllib.error.HTTPError as exc:
            status = exc.code
            if status in RETRY_HTTP and attempt < options.retry_limit:
                attempt += 1
                sleep(min(2 ** attempt, 8))
                continue
            raise LocalExecutionError(f"HTTP status {status}")
        except TimeoutError:
            if attempt < options.retry_limit:
                attempt += 1
                sleep(min(2 ** attempt, 8))
                continue
            raise LocalExecutionError("timeout")
    retained = data[: options.max_bytes_per_source]
    if not retained:
        raise LocalExecutionError("empty response")
    if len(data) > options.max_bytes_per_source:
        raise LocalExecutionError("byte cap exceeded")
    content_type = response_headers.get("Content-Type")
    content_range = response_headers.get("Content-Range")
    magic = detect_content_magic(retained, content_type)
    expected_magic = row.get("expected_magic")
    expected_family = row.get("expected_content_family")
    if magic.magic not in _as_set(expected_magic):
        raise LocalExecutionError("magic mismatch")
    if magic.content_family not in _as_set(expected_family):
        raise LocalExecutionError("content-family mismatch")
    digest = hashlib.sha256(retained).hexdigest()
    warnings = [] if int(status) == 206 or content_range else ["server_range_unconfirmed"]
    event("network_request_finished", job, "succeeded", {"http_status": int(status), "bytes_read": len(retained)})
    return {
        "_data": retained,
        "http_status": int(status),
        "content_type": content_type,
        "content_range": content_range,
        "bytes_requested": options.max_bytes_per_source,
        "bytes_read": len(retained),
        "byte_cap": options.max_bytes_per_source,
        "range_requested": True,
        "range_honored": int(status) == 206 or bool(content_range),
        "expected_magic": expected_magic,
        "detected_magic": magic.magic,
        "expected_content_family": expected_family,
        "detected_content_family": magic.content_family,
        "sha256": digest,
        "sha256_short": digest[:12],
        "warnings": warnings,
        "retry_count": attempt,
    }


def _runtime_cache_path(job: dict[str, Any], row: dict[str, Any], max_bytes: int, cache_root: Path) -> Path:
    ext = Path(job.get("cache_path") or "").suffix or f".{row.get('expected_format') or 'bin'}"
    temporal_key = row.get("temporal_key") or "unknown"
    return cache_root / job["source_id"] / temporal_key / f"{row.get('url_sha256', _short_hash(job['deterministic_url']))[:12]}{ext}.head{max_bytes}"


def _logical_runtime_cache_path(path: Path, cache_root: Path) -> Path:
    try:
        relative = path.relative_to(cache_root)
    except ValueError:
        return path
    return RUNTIME_CACHE_ROOT / relative


def _write_runtime_cache(job: dict[str, Any], row: dict[str, Any], data: bytes, result: dict[str, Any], max_bytes: int, generated_at: str, cache_root: Path) -> tuple[Path, Path]:
    path = _runtime_cache_path(job, row, max_bytes, cache_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)
    sidecar = Path(str(path) + ".receipt.json")
    sidecar_value = {
        "cache_contract_version": CACHE_CONTRACT_VERSION,
        "source_id": job["source_id"],
        "request_id": job["request_id"],
        "url_sha256": row.get("url_sha256"),
        "payload_sha256": result["sha256"],
        "bytes_read": result["bytes_read"],
        "byte_cap": max_bytes,
        "bounded_probe_only": True,
        "full_object": False,
        "http_status": result["http_status"],
        "content_type": result["content_type"],
        "content_range": result["content_range"],
        "range_requested": result["range_requested"],
        "range_honored": result["range_honored"],
        "expected_magic": result["expected_magic"],
        "detected_magic": result["detected_magic"],
        "expected_content_family": result["expected_content_family"],
        "detected_content_family": result["detected_content_family"],
        "generated_at_utc": generated_at,
    }
    write_json(sidecar, sidecar_value)
    return path, sidecar


def _read_valid_cache(job: dict[str, Any], row: dict[str, Any], max_bytes: int, cache_root: Path) -> tuple[dict[str, Any] | None, list[str]]:
    path = _runtime_cache_path(job, row, max_bytes, cache_root)
    sidecar = Path(str(path) + ".receipt.json")
    if not path.exists() or not sidecar.exists():
        return None, []
    errors: list[str] = []
    try:
        sidecar_data = _read_json(sidecar)
    except (json.JSONDecodeError, OSError) as exc:
        return None, [f"sidecar unreadable: {exc}"]
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    expected_magic = row.get("expected_magic")
    expected_family = row.get("expected_content_family")
    magic = detect_content_magic(data, None)
    required = [
        "cache_contract_version",
        "source_id",
        "request_id",
        "url_sha256",
        "payload_sha256",
        "bytes_read",
        "byte_cap",
        "bounded_probe_only",
        "full_object",
        "http_status",
        "content_type",
        "content_range",
        "range_requested",
        "range_honored",
        "expected_magic",
        "detected_magic",
        "expected_content_family",
        "detected_content_family",
        "generated_at_utc",
    ]
    for field in required:
        if field not in sidecar_data:
            errors.append(f"missing {field}")
    if sidecar_data.get("cache_contract_version") != CACHE_CONTRACT_VERSION:
        errors.append("cache_contract_version mismatch")
    if digest != sidecar_data.get("payload_sha256"):
        errors.append("payload_sha256 mismatch")
    if sidecar_data.get("url_sha256") != row.get("url_sha256"):
        errors.append("url_sha256 mismatch")
    if sidecar_data.get("byte_cap") != max_bytes:
        errors.append("byte_cap mismatch")
    if sidecar_data.get("bytes_read") != len(data):
        errors.append("bytes_read payload size mismatch")
    if len(data) > max_bytes:
        errors.append("bytes_read exceeds byte_cap")
    if sidecar_data.get("http_status") not in {200, 206}:
        errors.append("http_status not reusable")
    if magic.magic not in _as_set(expected_magic) or sidecar_data.get("detected_magic") not in _as_set(expected_magic):
        errors.append("detected_magic mismatch")
    if magic.content_family not in _as_set(expected_family) or sidecar_data.get("detected_content_family") not in _as_set(expected_family):
        errors.append("detected_content_family mismatch")
    if sidecar_data.get("range_requested") is not True:
        errors.append("range_requested not true")
    if sidecar_data.get("bounded_probe_only") is not True:
        errors.append("bounded_probe_only not true")
    if sidecar_data.get("full_object") is not False:
        errors.append("full_object not false")
    if errors:
        return None, errors
    return {
        "http_status": sidecar_data["http_status"],
        "content_type": sidecar_data["content_type"],
        "content_range": sidecar_data["content_range"],
        "bytes_requested": max_bytes,
        "bytes_read": len(data),
        "byte_cap": max_bytes,
        "range_requested": True,
        "range_honored": sidecar_data["range_honored"],
        "expected_magic": expected_magic,
        "detected_magic": magic.magic,
        "expected_content_family": expected_family,
        "detected_content_family": magic.content_family,
        "sha256": digest,
        "sha256_short": digest[:12],
        "cache_path": _logical_runtime_cache_path(path, cache_root).as_posix(),
        "cache_receipt_path": _logical_runtime_cache_path(
            sidecar, cache_root
        ).as_posix(),
        "warnings": [],
        "retry_count": 0,
    }, []


def _classify_exception(exc: Exception) -> str:
    text = str(exc)
    if "HTTP status 404" in text:
        return "source_unavailable"
    if "HTTP status 401" in text or "HTTP status 403" in text:
        return "credential_required"
    if any(f"HTTP status {code}" in text for code in RETRY_HTTP) or "timeout" in text:
        return "retryable_transport"
    if "unsupported" in text:
        return "unsupported_downstream_stage"
    if "network_not_allowed" in text:
        return "policy_blocked"
    return "validation_failure"


def _build_run_receipt(
    run_id: str,
    task_id: str,
    package: dict[str, Any],
    plan: dict[str, Any],
    inputs: dict[str, Any],
    options: RuntimeOptions,
    job_receipts: list[dict[str, Any]],
    source_evidence: dict[str, Any],
    safety_events: list[dict[str, Any]],
    blocked_by_policy: bool,
    finished_at: str,
    deterministic_test_fixture: bool = False,
) -> dict[str, Any]:
    statuses = [job["status"] for job in job_receipts]
    sources = source_evidence["sources"]
    runnable = [item for item in sources if not item.get("fixture_only")]
    fixture = [item for item in sources if item.get("fixture_only")]
    failed_sources = {job["source_id"] for job in job_receipts if job["status"] == "failed" and not job.get("fixture_only")}
    if blocked_by_policy:
        run_status = "blocked_policy"
    elif any(status == "failed" for status in statuses):
        run_status = "failed"
    elif any(status in {"unsupported", "skipped_dependency_failed"} for status in statuses):
        run_status = "completed_with_warnings"
    else:
        run_status = "completed"
    total_bytes = sum(int(item.get("bytes_read") or 0) for item in runnable)
    network_run = any(job.get("network_attempted") is True for job in job_receipts)
    if blocked_by_policy:
        evidence_class = "blocked_policy"
    elif deterministic_test_fixture:
        evidence_class = "deterministic_test_fixture"
    elif network_run:
        evidence_class = "live_network"
    elif any(job.get("status") == "cache_hit" for job in job_receipts):
        evidence_class = "validated_cache_reuse"
    else:
        evidence_class = "historical_fixture"
    return {
        "run_id": run_id,
        "task_id": task_id,
        "package_id": package["package_id"],
        "package_version": "0.8.0",
        "package_sha256": package["package_sha256"],
        "package_artifact_sha256": sha256_file(inputs["paths"]["package"]),
        "manifest_sha256": package["manifest_sha256"],
        "manifest_artifact_sha256": sha256_file(inputs["paths"]["manifest"]),
        "dag_sha256": package["dag_sha256"],
        "dag_artifact_sha256": sha256_file(inputs["paths"]["dag"]),
        "run_plan_contract_sha256": plan["run_plan_contract_sha256"],
        "receipt_contract_sha256": None,
        "started_at_utc": run_id.split("_", 2)[2].rsplit("_", 1)[0],
        "finished_at_utc": finished_at,
        "duration_ms": None,
        "run_status": run_status,
        "network_run": network_run,
        "evidence_class": evidence_class,
        "allow_network": options.allow_network,
        "max_bytes_per_source": options.max_bytes_per_source,
        "max_total_bytes": options.max_total_bytes,
        "timeout_seconds": options.timeout_seconds,
        "retry_limit": options.retry_limit,
        "fail_fast": options.fail_fast,
        "planned_job_count": plan["planned_job_count"],
        "executed_job_count": sum(1 for status in statuses if status in {"succeeded", "failed", "fixture_recorded", "cache_hit"}),
        "succeeded_job_count": sum(1 for status in statuses if status == "succeeded"),
        "failed_job_count": sum(1 for status in statuses if status == "failed"),
        "skipped_job_count": sum(1 for status in statuses if status.startswith("skipped")),
        "fixture_job_count": sum(1 for status in statuses if status == "fixture_recorded"),
        "cache_hit_count": sum(1 for status in statuses if status == "cache_hit"),
        "runnable_source_count": package["executable_request_count"],
        "successful_source_count": len([item for item in runnable if item.get("status") == "succeeded"]),
        "failed_source_count": len(failed_sources),
        "fixture_source_count": len(fixture),
        "total_bytes_requested": sum(int(item.get("bytes_requested") or 0) for item in runnable),
        "total_bytes_read": total_bytes,
        "total_bytes_cached": total_bytes,
        "byte_budget_remaining": options.max_total_bytes - total_bytes,
        "all_byte_caps_respected": all(int(item.get("bytes_read") or 0) <= int(item.get("byte_cap") or options.max_bytes_per_source) for item in runnable) and total_bytes <= options.max_total_bytes,
        "all_magic_valid": all(item.get("detected_magic") in _as_set(item.get("expected_magic")) for item in runnable) if runnable else not options.allow_network,
        "all_content_families_valid": all(item.get("detected_content_family") in _as_set(item.get("expected_content_family")) for item in runnable) if runnable else not options.allow_network,
        "all_checksums_present": all(bool(item.get("sha256")) for item in runnable) if runnable else not options.allow_network,
        "dag_execution_valid": True,
        "credentials_used": False,
        "authorization_headers_present": False,
        "warnings": [] if not blocked_by_policy else ["execution_blocked: network_not_allowed"],
        "errors": [],
        "failure_classes": sorted({job["failure_class"] for job in job_receipts if job.get("failure_class")}),
        "source_evidence": sources,
        "job_receipt_count": len(job_receipts),
        "safety_event_count": len(safety_events),
    }


def write_receipt_markdown(receipt: dict[str, Any], path: Path) -> None:
    lines = [
        "# FasterRaster v0.8 Run Receipt",
        "",
        f"- Run ID: `{receipt['run_id']}`",
        f"- Task: `{receipt['task_id']}`",
        f"- Status: `{receipt['run_status']}`",
        f"- Network run: `{receipt['network_run']}`",
        f"- Successful sources: `{receipt['successful_source_count']}`",
        f"- Failed sources: `{receipt['failed_source_count']}`",
        f"- Fixture sources: `{receipt['fixture_source_count']}`",
        f"- Bytes read: `{receipt['total_bytes_read']}`",
        f"- Receipt contract SHA256: `{receipt['receipt_contract_sha256']}`",
    ]
    if receipt["warnings"]:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in receipt["warnings"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
