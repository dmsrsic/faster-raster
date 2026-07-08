from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from faster_raster.execution_package import sha256_file, validate_execution_dag, write_json
from faster_raster.manifest import read_manifest

SUPPORTED_SCHEDULERS = {"slurm", "local-dry-run"}


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def write_job_index(jobs: list[dict], path: Path) -> None:
    lines = ["index\tjob_id\trequest_id\tstage\tdependencies"]
    for index, job in enumerate(jobs):
        lines.append(
            "\t".join(
                [
                    str(index),
                    job["job_id"],
                    job["request_id"],
                    job["stage"],
                    ",".join(job.get("dependencies", [])),
                ]
            )
        )
    write_text(path, "\n".join(lines) + "\n")


def slurm_script(job_count: int) -> str:
    array_max = max(0, job_count - 1)
    return f'''#!/usr/bin/env bash
set -euo pipefail

JOB_INDEX_FILE="${{JOB_INDEX_FILE:-job_index.tsv}}"
JOBS_FILE="${{JOBS_FILE:-../jobs.jsonl}}"
TASK_ID="${{SLURM_ARRAY_TASK_ID:-0}}"

# Suggested SBATCH directive for future use:
# #SBATCH --array=0-{array_max}

JOB_JSON=$(sed -n "$((TASK_ID + 1))p" "$JOBS_FILE")
echo "FasterRaster Slurm dry-run"
echo "Selected task index: $TASK_ID"
echo "$JOB_JSON"
echo "Future integration point: faster-raster run-job --job-json '<row>'"
'''


def local_dry_run_script(job_count: int) -> str:
    return f'''#!/usr/bin/env bash
set -euo pipefail

JOBS_FILE="${{JOBS_FILE:-../jobs.jsonl}}"
echo "FasterRaster local dry-run over {job_count} jobs"
index=0
while IFS= read -r job_json; do
  echo "--- job $index ---"
  echo "$job_json"
  echo "Future integration point: faster-raster run-job --job-json '<row>'"
  index=$((index + 1))
done < "$JOBS_FILE"
'''


def read_package(package_dir: Path) -> dict:
    with (package_dir / "execution_package.json").open("r", encoding="utf-8") as handle:
        return json.load(handle)


def scheduler_summary(package: dict, jobs: list[dict], scheduler: str, out_dir: Path) -> dict:
    dag_validation = validate_execution_dag(jobs)
    return {
        "scheduler": scheduler,
        "package_id": package["package_id"],
        "job_count": len(jobs),
        "request_count": package["request_count"],
        "stage_counts": dict(sorted(Counter(job["stage"] for job in jobs).items())),
        "dependency_count": sum(len(job.get("dependencies", [])) for job in jobs),
        "dag_validation_status": dag_validation["status"],
        "output_directory": str(out_dir),
        "notes": "Scheduler exports are dry-run artifacts only; no downloads are executed.",
    }


def export_scheduler_package(package_dir: Path, scheduler: str, out_dir: Path) -> dict:
    if scheduler not in SUPPORTED_SCHEDULERS:
        raise ValueError(f"Unsupported scheduler: {scheduler}")
    package = read_package(package_dir)
    jobs = read_manifest(package_dir / "jobs.jsonl")
    dag_validation = validate_execution_dag(jobs)
    if dag_validation["status"] != "PASS":
        raise ValueError(json.dumps({"dag_validation": dag_validation}, sort_keys=True))

    out_dir.mkdir(parents=True, exist_ok=True)
    write_job_index(jobs, out_dir / "job_index.tsv")
    if scheduler == "slurm":
        write_text(out_dir / "slurm_array.sh", slurm_script(len(jobs)))
    else:
        write_text(out_dir / "run_local_dry_run.sh", local_dry_run_script(len(jobs)))
    summary = scheduler_summary(package, jobs, scheduler, out_dir)
    write_json(summary, out_dir / "scheduler_summary.json")
    write_text(
        out_dir / "README.md",
        "# FasterRaster Scheduler Export\n\n"
        f"Scheduler: `{scheduler}`\n\n"
        "This export is a dry-run scheduler artifact. It does not download data or execute raster work. "
        "The generated script reads rows from `jobs.jsonl` and echoes the future `run-job` integration point.\n",
    )
    hashes = {path.name: sha256_file(path) for path in sorted(out_dir.iterdir()) if path.is_file()}
    summary["hashes"] = hashes
    write_json(summary, out_dir / "scheduler_summary.json")
    return summary
