#!/usr/bin/env bash
set -euo pipefail

JOB_INDEX_FILE="${JOB_INDEX_FILE:-job_index.tsv}"
JOBS_FILE="${JOBS_FILE:-../jobs.jsonl}"
TASK_ID="${SLURM_ARRAY_TASK_ID:-0}"

# Suggested SBATCH directive for future use:
# #SBATCH --array=0-7

JOB_JSON=$(sed -n "$((TASK_ID + 1))p" "$JOBS_FILE")
echo "FasterRaster Slurm dry-run"
echo "Selected task index: $TASK_ID"
echo "$JOB_JSON"
echo "Future integration point: faster-raster run-job --job-json '<row>'"
