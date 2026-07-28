from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from faster_raster import __version__
from faster_raster.harmonization_planner import read_harmonization_plan
from faster_raster.manifest import read_manifest, write_manifest
from faster_raster.output_validation import validate_harmonization, validate_manifest

STAGES = ["fetch", "validate_download", "harmonize", "inspect_output"]
STAGE_ORDER = {stage: index for index, stage in enumerate(STAGES)}
DEFAULT_FAILURE_POLICY_ID = "default"
DEFAULT_PROFILE = {
    "profile_id": "builtin_default_hpc",
    "default_retry_count": 2,
    "default_timeout_seconds": 3600,
    "stage_timeout_seconds": {
        "fetch": 3600,
        "validate_download": 600,
        "harmonize": 7200,
        "inspect_output": 300,
    },
    "stage_retry_count": {
        "fetch": 2,
        "validate_download": 1,
        "harmonize": 1,
        "inspect_output": 1,
    },
    "max_parallel_jobs_hint": 100,
    "failure_mode": "fail_fast",
    "scheduler": {
        "partition": None,
        "account": None,
        "qos": None,
    },
    "resources": {
        "fetch": {"cpus": 1, "memory_mb": 1024},
        "validate_download": {"cpus": 1, "memory_mb": 1024},
        "harmonize": {"cpus": 2, "memory_mb": 4096},
        "inspect_output": {"cpus": 1, "memory_mb": 1024},
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_json(value: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_execution_profile(path: Path | None) -> dict:
    profile = json.loads(json.dumps(DEFAULT_PROFILE))
    if path is None:
        return profile
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    for key, value in loaded.items():
        if isinstance(value, dict) and isinstance(profile.get(key), dict):
            profile[key].update(value)
        else:
            profile[key] = value
    return profile


def file_extension_from_url(url: str) -> str:
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix:
        return suffix
    params = parse_qs(parsed.query)
    fmt = (params.get("format") or params.get("FORMAT") or [None])[0]
    if fmt:
        normalized = fmt.lower().strip().lstrip(".")
        if normalized in {"tif", "tiff", "geotiff"}:
            return ".tif" if normalized != "tiff" else ".tiff"
        if normalized in {"zip"}:
            return ".zip"
    return ".bin"


def cache_key_for(row: dict) -> str:
    parts = [
        row["adapter"],
        row["source_id"],
        str(row["year"]),
        row["thematic_layer"],
        row["tile_id"],
        sha256_text(row["url"]),
    ]
    return sha256_text("|".join(parts))


def content_addressed_path(row: dict, source_url_hash: str, extension: str) -> str:
    return (
        f"cache/{row['source_id']}/{row['year']}/{row['thematic_layer']}/"
        f"{source_url_hash}{extension}"
    )


def build_failure_policy(profile: dict | None = None) -> dict:
    profile = profile or DEFAULT_PROFILE
    return {
        "policies": [
            {
                "failure_policy_id": DEFAULT_FAILURE_POLICY_ID,
                "retry_count": profile["default_retry_count"],
                "retry_backoff_strategy": "exponential_jitter_capped",
                "timeout_policy": {
                    "default_timeout_seconds": profile["default_timeout_seconds"],
                    "stage_timeout_seconds": profile["stage_timeout_seconds"],
                },
                "checksum_policy": "verify_when_checksum_present_else_record_observed_hash",
                "partial_file_handling_policy": "write_to_temp_then_atomic_rename; remove_temp_on_failure",
                "failure_mode": profile.get("failure_mode", "fail_fast"),
                "scheduler_exit_code_expectations": {
                    "success": 0,
                    "retryable_failure": 75,
                    "permanent_failure": 1,
                    "contract_validation_failure": 2,
                },
            }
        ]
    }


def build_cache_plan(manifest_rows: list[dict]) -> dict:
    entries = []
    for row in sorted(manifest_rows, key=lambda item: item["request_id"]):
        source_url_hash = sha256_text(row["url"])
        extension = file_extension_from_url(row["url"])
        entries.append(
            {
                "request_id": row["request_id"],
                "cache_key": cache_key_for(row),
                "source_url_hash": source_url_hash,
                "expected_file_extension": extension,
                "source_id": row["source_id"],
                "year": row.get("year"),
                "thematic_layer": row.get("thematic_layer"),
                "tile_id": row.get("tile_id"),
                "content_addressed_path": content_addressed_path(row, source_url_hash, extension),
                "url": row["url"],
            }
        )
    grouping: dict[str, int] = defaultdict(int)
    extension_counts: Counter[str] = Counter()
    for entry in entries:
        key = f"{entry['source_id']}|{entry['year']}|{entry['thematic_layer']}"
        grouping[key] += 1
        extension_counts[entry["expected_file_extension"]] += 1
    return {
        "cache_plan_version": "0.3.1",
        "entries": entries,
        "extension_counts": dict(sorted(extension_counts.items())),
        "group_counts": dict(sorted(grouping.items())),
        "notes": "No files are downloaded by this cache plan. Paths are deterministic proposals for later execution.",
    }


def harmonization_by_request_id(plan: dict) -> dict[str, dict]:
    return {item["request_id"]: item for item in plan.get("inputs", [])}


def stage_retry_count(profile: dict, stage: str) -> int:
    return int(profile.get("stage_retry_count", {}).get(stage, profile["default_retry_count"]))


def stage_timeout(profile: dict, stage: str) -> int:
    return int(profile.get("stage_timeout_seconds", {}).get(stage, profile["default_timeout_seconds"]))


def job_row(
    *,
    row: dict,
    plan_input: dict,
    cache_entry: dict,
    stage: str,
    dependencies: list[str],
    profile: dict,
) -> dict:
    cache_path = cache_entry["content_addressed_path"]
    output_path = plan_input["planned_output"]
    if stage == "fetch":
        expected_input_path = row["url"]
        expected_output_path = cache_path
    elif stage == "validate_download":
        expected_input_path = cache_path
        expected_output_path = cache_path
    elif stage == "harmonize":
        expected_input_path = cache_path
        expected_output_path = output_path
    elif stage == "inspect_output":
        expected_input_path = output_path
        expected_output_path = output_path
    else:
        raise ValueError(f"invalid stage: {stage}")
    return {
        "job_id": f"{row['request_id']}__{stage}",
        "request_id": row["request_id"],
        "source_id": row["source_id"],
        "adapter": row["adapter"],
        "url": row["url"],
        "expected_input_path": expected_input_path,
        "expected_output_path": expected_output_path,
        "expected_cache_path": cache_path,
        "stage": stage,
        "dependencies": dependencies,
        "retry_count": stage_retry_count(profile, stage),
        "timeout_seconds": stage_timeout(profile, stage),
        "max_bytes": row.get("max_bytes"),
        "semantic_type": row["semantic_type"],
        "resampling": row["resampling"],
        "target_grid_crs": row["target_grid_crs"],
        "year": row.get("year"),
        "thematic_layer": row.get("thematic_layer"),
        "tile_id": row.get("tile_id"),
        "failure_policy_id": DEFAULT_FAILURE_POLICY_ID,
        "resources": profile.get("resources", {}).get(stage, {}),
    }


def build_jobs(manifest_rows: list[dict], plan: dict, cache_plan: dict, profile: dict | None = None) -> list[dict]:
    profile = profile or DEFAULT_PROFILE
    plan_inputs = harmonization_by_request_id(plan)
    cache_entries = {entry["request_id"]: entry for entry in cache_plan["entries"]}
    jobs: list[dict] = []
    for row in sorted(manifest_rows, key=lambda item: item["request_id"]):
        request_id = row["request_id"]
        plan_input = plan_inputs[request_id]
        cache_entry = cache_entries[request_id]
        fetch_id = f"{request_id}__fetch"
        validate_id = f"{request_id}__validate_download"
        harmonize_id = f"{request_id}__harmonize"
        jobs.append(job_row(row=row, plan_input=plan_input, cache_entry=cache_entry, stage="fetch", dependencies=[], profile=profile))
        jobs.append(job_row(row=row, plan_input=plan_input, cache_entry=cache_entry, stage="validate_download", dependencies=[fetch_id], profile=profile))
        jobs.append(job_row(row=row, plan_input=plan_input, cache_entry=cache_entry, stage="harmonize", dependencies=[validate_id], profile=profile))
        jobs.append(job_row(row=row, plan_input=plan_input, cache_entry=cache_entry, stage="inspect_output", dependencies=[harmonize_id], profile=profile))
    return jobs


def write_jobs(jobs: list[dict], path: Path) -> None:
    write_manifest(jobs, path)


def summarize_counts(rows: list[dict], field: str) -> dict[str, int]:
    return dict(sorted(Counter(str(row[field]) for row in rows).items()))


def validate_execution_dag(jobs: list[dict]) -> dict:
    errors: list[str] = []
    job_ids = [job.get("job_id") for job in jobs]
    duplicates = sorted({job_id for job_id in job_ids if job_ids.count(job_id) > 1})
    for job_id in duplicates:
        errors.append(f"duplicate job_id: {job_id}")
    by_id = {job.get("job_id"): job for job in jobs if isinstance(job.get("job_id"), str)}
    for job in jobs:
        job_id = job.get("job_id")
        stage = job.get("stage")
        if stage not in STAGES:
            errors.append(f"invalid stage name for {job_id}: {stage}")
        for dependency in job.get("dependencies", []):
            if dependency not in by_id:
                errors.append(f"missing dependency for {job_id}: {dependency}")
            elif stage in STAGE_ORDER and by_id[dependency].get("stage") in STAGE_ORDER:
                if STAGE_ORDER[by_id[dependency]["stage"]] >= STAGE_ORDER[stage]:
                    errors.append(f"invalid dependency order for {job_id}: {dependency}")
    request_groups: dict[str, list[dict]] = defaultdict(list)
    for job in jobs:
        request_groups[str(job.get("request_id"))].append(job)
    for request_id, group in sorted(request_groups.items()):
        stages = [job.get("stage") for job in group]
        if stages != STAGES:
            errors.append(f"request_id {request_id} stages are invalid: {stages}")
        expected_deps = {
            "fetch": [],
            "validate_download": [f"{request_id}__fetch"],
            "harmonize": [f"{request_id}__validate_download"],
            "inspect_output": [f"{request_id}__harmonize"],
        }
        for job in group:
            stage = job.get("stage")
            if stage in expected_deps and job.get("dependencies") != expected_deps[stage]:
                errors.append(f"request_id {request_id} stage {stage} dependencies are invalid: {job.get('dependencies')}")
        if "harmonize" in stages and "validate_download" not in stages:
            errors.append(f"orphan harmonization job for request_id {request_id}")
    errors.extend(cycle_errors(jobs))
    dependency_count = sum(len(job.get("dependencies", [])) for job in jobs)
    return {
        "status": "PASS" if not errors else "FAIL",
        "job_count": len(jobs),
        "request_count": len(request_groups),
        "dependency_count": dependency_count,
        "stage_counts": summarize_counts(jobs, "stage") if jobs else {},
        "error_count": len(errors),
        "errors": errors,
    }


def cycle_errors(jobs: list[dict]) -> list[str]:
    graph = {job["job_id"]: list(job.get("dependencies", [])) for job in jobs if "job_id" in job}
    visiting: set[str] = set()
    visited: set[str] = set()
    errors: list[str] = []

    def visit(node: str) -> None:
        if node in visited:
            return
        if node in visiting:
            errors.append(f"cycle detected involving job_id: {node}")
            return
        visiting.add(node)
        for dependency in graph.get(node, []):
            if dependency in graph:
                visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for node in sorted(graph):
        visit(node)
    return sorted(set(errors))


def build_execution_package(
    *,
    manifest_path: Path,
    harmonization_path: Path,
    out_dir: Path,
    execution_profile: Path | None = None,
) -> dict:
    manifest_report = validate_manifest(manifest_path)
    harmonization_report = validate_harmonization(harmonization_path, manifest_path)
    validation_status = {
        "manifest": manifest_report,
        "harmonization": harmonization_report,
        "overall": "PASS" if manifest_report["status"] == "PASS" and harmonization_report["status"] == "PASS" else "FAIL",
    }
    if validation_status["overall"] != "PASS":
        raise ValueError(json.dumps(validation_status, sort_keys=True))

    profile = load_execution_profile(execution_profile)
    manifest_rows = read_manifest(manifest_path)
    plan = read_harmonization_plan(harmonization_path)
    manifest_hash = sha256_file(manifest_path)
    harmonization_hash = sha256_file(harmonization_path)
    package_id = f"fr_exec_{sha256_text(manifest_hash + '|' + harmonization_hash)[:16]}"
    cache_plan = build_cache_plan(manifest_rows)
    jobs = build_jobs(manifest_rows, plan, cache_plan, profile)
    dag_validation = validate_execution_dag(jobs)
    if dag_validation["status"] != "PASS":
        raise ValueError(json.dumps({"dag_validation": dag_validation}, sort_keys=True))
    failure_policy = build_failure_policy(profile)

    package = {
        "package_id": package_id,
        "package_version": "0.3.1",
        "created_by": f"FasterRaster {__version__}",
        "execution_profile": profile,
        "manifest_path": str(manifest_path),
        "harmonization_plan_path": str(harmonization_path),
        "execution_profile_path": str(execution_profile) if execution_profile else None,
        "manifest_sha256": manifest_hash,
        "harmonization_plan_sha256": harmonization_hash,
        "total_job_count": len(jobs),
        "request_count": len(manifest_rows),
        "source_counts": summarize_counts(manifest_rows, "source_id"),
        "adapter_counts": summarize_counts(manifest_rows, "adapter"),
        "stage_counts": summarize_counts(jobs, "stage"),
        "dependency_count": dag_validation["dependency_count"],
        "estimated_stages": STAGES,
        "dag_validation": dag_validation,
        "validation_status": validation_status,
        "outputs": {
            "jobs": "jobs.jsonl",
            "cache_plan": "cache_plan.json",
            "failure_policy": "failure_policy.json",
            "summary": "execution_summary.md",
        },
        "scheduler_compatibility_notes": [
            "jobs.jsonl is deterministic and can be mapped to Slurm arrays, Snakemake rules, Nextflow processes, Prefect tasks, AWS Batch jobs, or Ray tasks.",
            "This package is a preflight orchestration artifact only; it does not download, validate bytes, or harmonize rasters.",
            "Stage dependencies are represented as job_id strings and can be translated into scheduler-native dependency syntax.",
        ],
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(package, out_dir / "execution_package.json")
    write_jobs(jobs, out_dir / "jobs.jsonl")
    write_json(cache_plan, out_dir / "cache_plan.json")
    write_json(failure_policy, out_dir / "failure_policy.json")
    write_summary(package, jobs, out_dir / "execution_summary.md")
    return package


def write_summary(package: dict, jobs: list[dict], path: Path) -> None:
    lines = [
        "# FasterRaster Execution Package",
        "",
        f"- Package ID: `{package['package_id']}`",
        f"- Created by: `{package['created_by']}`",
        f"- Request count: `{package['request_count']}`",
        f"- Total job count: `{package['total_job_count']}`",
        f"- Dependency count: `{package['dependency_count']}`",
        f"- Manifest SHA256: `{package['manifest_sha256']}`",
        f"- Harmonization SHA256: `{package['harmonization_plan_sha256']}`",
        f"- Validation status: `{package['validation_status']['overall']}`",
        f"- DAG validation: `{package['dag_validation']['status']}`",
        "",
        "## Stage Counts",
        "",
    ]
    for stage, count in package["stage_counts"].items():
        lines.append(f"- `{stage}`: `{count}`")
    lines.extend(["", "## Scheduler Notes", ""])
    for note in package["scheduler_compatibility_notes"]:
        lines.append(f"- {note}")
    lines.extend(["", "## Example Job", "", "```json", json.dumps(jobs[0] if jobs else {}, indent=2, sort_keys=True), "```", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def package_hashes(out_dir: Path) -> dict[str, str]:
    return {
        "execution_package_sha256": sha256_file(out_dir / "execution_package.json"),
        "jobs_sha256": sha256_file(out_dir / "jobs.jsonl"),
        "cache_plan_sha256": sha256_file(out_dir / "cache_plan.json"),
        "failure_policy_sha256": sha256_file(out_dir / "failure_policy.json"),
    }
