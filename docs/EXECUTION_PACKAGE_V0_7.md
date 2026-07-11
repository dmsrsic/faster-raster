# Execution Package v0.7

`faster-raster task package TASK_ID` compiles the task if needed and writes scheduler-ready dry-run artifacts under `reports/execution_packages/TASK_ID/`.

Artifacts:

- `execution_package.json`
- `execution_jobs.jsonl`
- `execution_jobs.json`
- `execution_summary.md`
- `cache_plan.json`
- `failure_policy.json`
- `dag.json`

For each runnable `static_http_range` request the package creates:

1. `resolve_request`
2. `bounded_fetch`
3. `validate_http_status`
4. `validate_byte_cap`
5. `validate_magic`
6. `validate_content_family`
7. `compute_checksum`
8. `record_source_evidence`

Fixture-only PRISM creates only `record_fixture_evidence`.

The package does not execute jobs, download bytes, decode NetCDF, decompress GZIP, extract ZIP archives, or download Sentinel imagery.

Cache paths are deterministic:

```text
cache/static_http_range/{source_id}/{temporal_key}/{url_sha256_short}.{extension}
```

For v0.7 all cache entries are bounded probes only, content-addressed, non-resumable, and not expected to contain full objects.

## v0.8 Local Executor

v0.8 consumes the same package artifacts and executes only the explicit stage handler map used by the local bounded executor. It does not execute arbitrary shell command strings from package artifacts.

Runtime cache payloads use `cache/runtime/static_http_range/...head{max_bytes}` names so truncated prefixes are not confused with ordinary archive or raster files. Run receipts record job state, source evidence, cache sidecars, safety events, and an execution log.
