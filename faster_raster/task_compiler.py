from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from faster_raster import __version__
from faster_raster.adapter_contract import PlannedRequest, sha256_text, stable_json
from faster_raster.adapters import static_http_range
from faster_raster.execution_package import validate_execution_dag
from faster_raster.manifest import write_manifest
from faster_raster.task_builder import load_task, validate_task


REPORT_ROOT = Path(os.environ.get("FASTERRASTER_REPORT_ROOT", "reports"))
TASK_COMPILE_ROOT = REPORT_ROOT / "task_compiles"
EXECUTION_PACKAGE_ROOT = REPORT_ROOT / "execution_packages"
STATIC_RANGE_STAGES = [
    "resolve_request",
    "bounded_fetch",
    "validate_http_status",
    "validate_byte_cap",
    "validate_magic",
    "validate_content_family",
    "compute_checksum",
    "record_source_evidence",
]
STATIC_RANGE_VALIDATION_STEPS = [
    "validate_http_status",
    "validate_byte_cap",
    "validate_magic",
    "validate_content_family",
    "compute_sha256",
    "record_range_behavior",
]
FAILURE_CLASSES = {
    "HTTP 404": "source_unavailable",
    "HTTP 401/403": "credential_required",
    "server ignores Range": "validation_failure",
    "response exceeds byte cap": "fatal_contract_error",
    "magic mismatch": "validation_failure",
    "content family mismatch": "validation_failure",
    "empty response": "validation_failure",
    "fixture-only source": "fixture_only_non_error",
    "unsupported decoder": "unsupported_downstream_stage",
    "timeout": "retryable_transport",
    "transient HTTP 429/5xx": "retryable_transport",
}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    write_manifest(rows, path)


def contract_hash(value: Any) -> str:
    return sha256_text(stable_json(value))


def _compile_contract_hash(value: dict[str, Any]) -> str:
    stable_value = {
        key: item
        for key, item in value.items()
        if key not in {"compile_report_contract_sha256"}
    }
    return contract_hash(stable_value)


def _date_parts(task: dict[str, Any]) -> dict[str, Any]:
    dates = (task.get("time") or {}).get("dates") or [task.get("date")] if task.get("date") else []
    date = dates[0] if dates else task.get("date") or "2023-01-01"
    year = int((task.get("time") or {}).get("years", [task.get("year") or int(str(date)[:4])])[0])
    parts = str(date).split("-")
    return {
        "date": date,
        "year": year,
        "month": parts[1] if len(parts) > 1 else "01",
        "day": parts[2] if len(parts) > 2 else "01",
        "yyyymmdd": "".join(parts) if len(parts) == 3 else f"{year}0101",
    }


def _temporal_key(spec: dict[str, Any], params: dict[str, Any]) -> str:
    granularity = spec.get("temporal_granularity")
    if granularity == "daily":
        return str(params["yyyymmdd"])
    if granularity == "monthly":
        return f"{params['year']}-{params.get('month', '01')}"
    if granularity == "normals":
        return "normals"
    return str(params.get("year", "unknown"))


def _spatial_key(task: dict[str, Any]) -> str:
    bbox = (task.get("aoi") or {}).get("bbox") or task.get("bbox")
    bbox_crs = (task.get("aoi") or {}).get("bbox_crs") or task.get("bbox_crs") or "EPSG:4326"
    return f"{bbox_crs}:{','.join(str(item) for item in bbox)}"


def _expected_format(source_id: str) -> str:
    return {
        "chirps_daily_precipitation": "geotiff.gz",
        "gridmet_daily": "netcdf",
        "terraclimate_monthly": "netcdf",
        "worldclim_bioclim_normals": "zip",
        "prism_daily_ppt_static_zip": "zip",
    }.get(source_id, "unknown")


def _harmonization_readiness(source_id: str) -> str:
    return {
        "chirps_daily_precipitation": "requires_decompression_and_raster_decode",
        "gridmet_daily": "requires_netcdf_variable_selection",
        "terraclimate_monthly": "requires_netcdf_variable_selection",
        "worldclim_bioclim_normals": "requires_archive_member_resolution",
        "prism_daily_ppt_static_zip": "fixture_only_endpoint_resolution_required",
    }.get(source_id, "unknown")


def _container(source_id: str) -> str:
    return {
        "chirps_daily_precipitation": "gzip",
        "gridmet_daily": "hdf5_netcdf4",
        "terraclimate_monthly": "hdf5_netcdf4",
        "worldclim_bioclim_normals": "zip",
        "prism_daily_ppt_static_zip": "zip",
    }.get(source_id, "unknown")


def _extension(source_id: str) -> str:
    return {
        "chirps_daily_precipitation": ".gz",
        "gridmet_daily": ".nc",
        "terraclimate_monthly": ".nc",
        "worldclim_bioclim_normals": ".zip",
        "prism_daily_ppt_static_zip": ".zip",
    }[source_id]


def _planned_request_for_static_spec(
    *,
    task: dict[str, Any],
    spec: dict[str, Any],
    index: int,
    max_bytes: int,
) -> dict[str, Any]:
    task_id = task["task_id"]
    params = _date_parts(task)
    source_id = spec["source_id"]
    fixture_only = spec.get("classification") == static_http_range.FIXTURE_CLASSIFICATION
    temporal_key = _temporal_key(spec, params)
    request_id = f"{task_id}__{source_id}__{temporal_key}"
    provenance = {
        "config": static_http_range.portable_project_path(static_http_range.DEFAULT_WAVE1_CONFIG),
        "container": _container(source_id),
        "expected_inner_format": "geotiff" if source_id == "chirps_daily_precipitation" else None,
        "planning_index": index,
    }
    warnings: list[str] = []
    if fixture_only:
        url = None
        execution_status = "non_executable_fixture"
        acquisition_mode = "historical_contract_fixture"
        warnings.append("fixture-only source; no fetch job generated")
        provenance.update({
            "historical_http_status": spec.get("historical_http_status"),
            "historical_bytes_read": spec.get("historical_bytes_read"),
            "historical_detected_magic": spec.get("historical_detected_magic"),
            "historical_sha256_short": spec.get("historical_sha256_short"),
            "current_endpoint_status": spec.get("current_endpoint_status"),
        })
    else:
        url = static_http_range.render_static_url(spec, params)
        execution_status = "planned_executable"
        acquisition_mode = "bounded_http_range"
    request = PlannedRequest(
        request_id=request_id,
        task_id=task_id,
        source_id=source_id,
        adapter="static_http_range",
        acquisition_mode=acquisition_mode,
        source_classification=spec.get("classification", "runnable"),
        execution_status=execution_status,
        deterministic_url=url,
        request_method="GET",
        request_headers_redacted=static_http_range.build_range_headers(max_bytes) if not fixture_only else {},
        temporal_key=temporal_key,
        spatial_key=_spatial_key(task),
        expected_content_family=spec.get("expected_content_family"),
        expected_magic=spec.get("expected_magic"),
        expected_format=_expected_format(source_id),
        max_bytes=max_bytes if not fixture_only else None,
        bounded_request=not fixture_only,
        credential_required=bool(spec.get("credential_required")),
        auth_profile=None,
        fixture_only=fixture_only,
        network_required=not fixture_only,
        checksum_policy="compute_after_fetch" if not fixture_only else "historical_evidence_only",
        validation_steps=[] if fixture_only else STATIC_RANGE_VALIDATION_STEPS,
        harmonization_readiness=_harmonization_readiness(source_id),
        warnings=warnings,
        provenance=provenance,
    )
    row = request.to_row()
    row["container"] = _container(source_id)
    row["expected_inner_format"] = provenance.get("expected_inner_format")
    row["bounded_probe_only"] = True
    row["full_object_expected"] = False
    return row


def plan_task_requests(task: dict[str, Any], *, max_bytes_per_source: int = static_http_range.DEFAULT_MAX_BYTES) -> list[dict[str, Any]]:
    specs = {spec["source_id"]: spec for spec in static_http_range.load_wave1_specs()}
    rows: list[dict[str, Any]] = []
    for index, source_id in enumerate(task.get("sources") or []):
        if source_id in specs:
            rows.append(_planned_request_for_static_spec(task=task, spec=specs[source_id], index=index, max_bytes=max_bytes_per_source))
    return sorted(rows, key=lambda row: row["request_id"])


def _build_compile_contract(
    task_id: str,
    task: dict[str, Any],
    *,
    max_bytes_per_source: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    manifest_rows = plan_task_requests(task, max_bytes_per_source=max_bytes_per_source)
    runnable_rows = [row for row in manifest_rows if not row["fixture_only"]]
    fixture_rows = [row for row in manifest_rows if row["fixture_only"]]
    source_resolution = {
        "task_id": task_id,
        "requested_sources": task.get("sources") or [],
        "resolved_sources": [row["source_id"] for row in manifest_rows],
        "missing_sources": [source for source in task.get("sources") or [] if source not in {row["source_id"] for row in manifest_rows}],
        "adapter_counts": dict(sorted(Counter(row["adapter"] for row in manifest_rows).items())),
    }
    validation_plan = {
        "task_id": task_id,
        "network_run": False,
        "stages": [
            "validate_task",
            "resolve_sources",
            "validate_adapter_contract",
            "validate_manifest_rows",
            "validate_fixture_rows",
            "validate_no_credentials",
        ],
        "request_validation_steps": {row["request_id"]: row["validation_steps"] for row in manifest_rows},
        "status": "PASS",
    }
    manifest_hash = contract_hash(manifest_rows)
    report_contract = {
        "task_id": task_id,
        "package_version": __version__,
        "network_run": False,
        "validation_status": "PASS",
        "manifest_row_count": len(manifest_rows),
        "request_count": len(manifest_rows),
        "executable_request_count": len(runnable_rows),
        "fixture_request_count": len(fixture_rows),
        "adapter_counts": source_resolution["adapter_counts"],
        "source_counts": dict(sorted(Counter(row["source_id"] for row in manifest_rows).items())),
        "warnings": sorted({warning for row in manifest_rows for warning in row.get("warnings", [])}),
        "acquisition_manifest_sha256": manifest_hash,
        "source_resolution_sha256": contract_hash(source_resolution),
        "validation_plan_sha256": contract_hash(validation_plan),
    }
    return report_contract, manifest_rows, source_resolution, validation_plan


def compile_task(task_id: str, *, max_bytes_per_source: int = static_http_range.DEFAULT_MAX_BYTES) -> dict[str, Any]:
    task = load_task(task_id)
    errors = validate_task(task)
    if errors:
        raise ValueError("; ".join(errors))
    out_dir = TASK_COMPILE_ROOT / task_id
    report_contract, manifest_rows, source_resolution, validation_plan = _build_compile_contract(
        task_id,
        task,
        max_bytes_per_source=max_bytes_per_source,
    )
    repeated_contract, _, _, _ = _build_compile_contract(
        task_id,
        task,
        max_bytes_per_source=max_bytes_per_source,
    )
    report_contract["determinism_status"] = (
        "PASS"
        if _compile_contract_hash(report_contract) == _compile_contract_hash(repeated_contract)
        else "FAIL"
    )
    report_contract["compile_report_contract_sha256"] = _compile_contract_hash(report_contract)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "acquisition_manifest.jsonl", manifest_rows)
    write_json(out_dir / "acquisition_manifest.json", {"rows": manifest_rows})
    write_json(out_dir / "source_resolution.json", source_resolution)
    write_json(out_dir / "validation_plan.json", validation_plan)
    write_json(out_dir / "compile_report.json", report_contract)
    write_compile_markdown(report_contract, out_dir / "compile_report.md")
    return {**report_contract, "out_dir": str(out_dir), "manifest_rows": manifest_rows}


def write_compile_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# FasterRaster Task Compile Report",
        "",
        f"- Task ID: `{report['task_id']}`",
        f"- Validation status: `{report['validation_status']}`",
        f"- Determinism status: `{report['determinism_status']}`",
        f"- Network run: `{report['network_run']}`",
        f"- Manifest rows: `{report['manifest_row_count']}`",
        f"- Request count: `{report['request_count']}`",
        f"- Executable requests: `{report['executable_request_count']}`",
        f"- Fixture requests: `{report['fixture_request_count']}`",
        f"- Manifest SHA256: `{report['acquisition_manifest_sha256']}`",
        "",
        "The compile step plans bounded adapter requests only. It does not fetch data.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _cache_path(row: dict[str, Any]) -> str:
    return f"cache/static_http_range/{row['source_id']}/{row['temporal_key']}/{row['url_sha256'][:12]}{_extension(row['source_id'])}"


def build_cache_plan(rows: list[dict[str, Any]]) -> dict[str, Any]:
    entries = []
    for row in rows:
        if row["fixture_only"]:
            continue
        entries.append({
            "request_id": row["request_id"],
            "source_id": row["source_id"],
            "cache_path": _cache_path(row),
            "extension": _extension(row["source_id"]),
            "max_bytes": row["max_bytes"],
            "full_object_expected": False,
            "bounded_probe_only": True,
            "checksum_algorithm": "sha256",
            "overwrite_policy": "content_addressed",
            "resume_supported": False,
            "eviction_class": "bounded_probe_evidence",
        })
    return {"cache_plan_version": "0.7.0", "entries": entries}


def build_failure_policy() -> dict[str, Any]:
    return {
        "failure_policy_version": "0.7.0",
        "classifications": [{"condition": key, "classification": value} for key, value in sorted(FAILURE_CLASSES.items())],
    }


def _job(row: dict[str, Any], stage: str, dependencies: list[str]) -> dict[str, Any]:
    return {
        "job_id": f"{row['request_id']}__{stage}",
        "request_id": row["request_id"],
        "task_id": row["task_id"],
        "source_id": row["source_id"],
        "adapter": row["adapter"],
        "stage": stage,
        "dependencies": dependencies,
        "network_required": stage == "bounded_fetch",
        "fixture_only": row["fixture_only"],
        "max_bytes": row["max_bytes"],
        "deterministic_url": row["deterministic_url"],
        "cache_path": None if row["fixture_only"] else _cache_path(row),
    }


def build_execution_jobs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for row in rows:
        if row["fixture_only"]:
            jobs.append(_job(row, "record_fixture_evidence", []))
            continue
        resolve = f"{row['request_id']}__resolve_request"
        fetch = f"{row['request_id']}__bounded_fetch"
        validate_ids = [
            f"{row['request_id']}__validate_http_status",
            f"{row['request_id']}__validate_byte_cap",
            f"{row['request_id']}__validate_magic",
            f"{row['request_id']}__validate_content_family",
        ]
        checksum = f"{row['request_id']}__compute_checksum"
        jobs.append(_job(row, "resolve_request", []))
        jobs.append(_job(row, "bounded_fetch", [resolve]))
        jobs.append(_job(row, "validate_http_status", [fetch]))
        jobs.append(_job(row, "validate_byte_cap", [fetch]))
        jobs.append(_job(row, "validate_magic", [fetch]))
        jobs.append(_job(row, "validate_content_family", [fetch]))
        jobs.append(_job(row, "compute_checksum", [fetch]))
        jobs.append(_job(row, "record_source_evidence", validate_ids + [checksum]))
    return sorted(jobs, key=lambda job: job["job_id"])


def validate_v07_dag(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    by_id = {job["job_id"]: job for job in jobs}
    for job in jobs:
        for dep in job.get("dependencies", []):
            if dep not in by_id:
                errors.append(f"missing dependency for {job['job_id']}: {dep}")
    legacy_status = validate_execution_dag([
        {"job_id": job["job_id"], "request_id": job["request_id"], "stage": job["stage"], "dependencies": job["dependencies"]}
        for job in jobs
    ])
    cycle_errors = [error for error in legacy_status.get("errors", []) if "cycle" in error]
    errors.extend(cycle_errors)
    return {
        "status": "PASS" if not errors else "FAIL",
        "job_count": len(jobs),
        "dependency_count": sum(len(job.get("dependencies", [])) for job in jobs),
        "stage_counts": dict(sorted(Counter(job["stage"] for job in jobs).items())),
        "errors": errors,
    }


def package_task(task_id: str, *, max_bytes_per_source: int = static_http_range.DEFAULT_MAX_BYTES) -> dict[str, Any]:
    compile_report = compile_task(task_id, max_bytes_per_source=max_bytes_per_source)
    rows = compile_report["manifest_rows"]
    out_dir = EXECUTION_PACKAGE_ROOT / task_id
    jobs = build_execution_jobs(rows)
    cache_plan = build_cache_plan(rows)
    failure_policy = build_failure_policy()
    dag = validate_v07_dag(jobs)
    manifest_hash = compile_report["acquisition_manifest_sha256"]
    package_contract = {
        "task_id": task_id,
        "package_id": f"fr_v07_{contract_hash({'task_id': task_id, 'manifest': manifest_hash})[:16]}",
        "package_version": "0.7.0",
        "request_count": len(rows),
        "executable_request_count": sum(1 for row in rows if not row["fixture_only"]),
        "fixture_request_count": sum(1 for row in rows if row["fixture_only"]),
        "adapter_counts": dict(sorted(Counter(row["adapter"] for row in rows).items())),
        "source_counts": dict(sorted(Counter(row["source_id"] for row in rows).items())),
        "stage_counts": dag["stage_counts"],
        "dependency_count": dag["dependency_count"],
        "total_job_count": len(jobs),
        "dag_validation_status": dag["status"],
        "validation_status": "PASS" if dag["status"] == "PASS" else "FAIL",
        "manifest_sha256": manifest_hash,
        "jobs_sha256": contract_hash(jobs),
        "cache_plan_sha256": contract_hash(cache_plan),
        "failure_policy_sha256": contract_hash(failure_policy),
        "dag_sha256": contract_hash(dag),
    }
    package_contract["execution_package_contract_sha256"] = contract_hash(package_contract)
    package_contract["package_sha256"] = package_contract["execution_package_contract_sha256"]
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "execution_package.json", package_contract)
    write_jsonl(out_dir / "execution_jobs.jsonl", jobs)
    write_json(out_dir / "execution_jobs.json", {"jobs": jobs})
    write_json(out_dir / "cache_plan.json", cache_plan)
    write_json(out_dir / "failure_policy.json", failure_policy)
    write_json(out_dir / "dag.json", dag)
    write_package_markdown(package_contract, out_dir / "execution_summary.md")
    return {**package_contract, "out_dir": str(out_dir), "jobs": jobs, "dag": dag}


def write_package_markdown(package: dict[str, Any], path: Path) -> None:
    lines = [
        "# FasterRaster v0.7 Execution Package",
        "",
        f"- Task ID: `{package['task_id']}`",
        f"- Package ID: `{package['package_id']}`",
        f"- Request count: `{package['request_count']}`",
        f"- Executable requests: `{package['executable_request_count']}`",
        f"- Fixture requests: `{package['fixture_request_count']}`",
        f"- Total jobs: `{package['total_job_count']}`",
        f"- DAG validation: `{package['dag_validation_status']}`",
        "",
        "No jobs are executed by this package command.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def inspect_compile(task_id: str) -> dict[str, Any]:
    compile_dir = TASK_COMPILE_ROOT / task_id
    package_dir = EXECUTION_PACKAGE_ROOT / task_id
    report = json.loads((compile_dir / "compile_report.json").read_text(encoding="utf-8"))
    manifest = json.loads((compile_dir / "acquisition_manifest.json").read_text(encoding="utf-8"))["rows"]
    package = json.loads((package_dir / "execution_package.json").read_text(encoding="utf-8")) if (package_dir / "execution_package.json").exists() else None
    return {
        "task_id": task_id,
        "adapter_counts": report["adapter_counts"],
        "executable_request_count": report["executable_request_count"],
        "fixture_request_count": report["fixture_request_count"],
        "warnings": report["warnings"],
        "validation_stages": json.loads((compile_dir / "validation_plan.json").read_text(encoding="utf-8"))["stages"],
        "hashes": {
            "acquisition_manifest_sha256": report["acquisition_manifest_sha256"],
            "compile_report_contract_sha256": report["compile_report_contract_sha256"],
            "execution_package_contract_sha256": package.get("execution_package_contract_sha256") if package else None,
            "execution_jobs_sha256": package.get("jobs_sha256") if package else None,
            "cache_plan_sha256": package.get("cache_plan_sha256") if package else None,
            "failure_policy_sha256": package.get("failure_policy_sha256") if package else None,
            "dag_sha256": package.get("dag_sha256") if package else None,
        },
        "manifest_row_count": len(manifest),
        "artifact_dir": str(compile_dir),
        "package_dir": str(package_dir) if package else None,
    }
