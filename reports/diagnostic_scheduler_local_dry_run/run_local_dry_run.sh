#!/usr/bin/env bash
set -euo pipefail

JOBS_FILE="${JOBS_FILE:-../jobs.jsonl}"
echo "FasterRaster local dry-run over 8 jobs"
index=0
while IFS= read -r job_json; do
  echo "--- job $index ---"
  echo "$job_json"
  echo "Future integration point: faster-raster run-job --job-json '<row>'"
  index=$((index + 1))
done < "$JOBS_FILE"
