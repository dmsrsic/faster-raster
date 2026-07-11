# Run Receipts

FasterRaster v0.8 writes auditable local execution receipts under:

Run receipts are bounded evidence receipts. v0.9 artifact receipts build on them by requiring the complete object's first retained-prefix bytes to hash to the verified v0.8 probe SHA256.

```text
reports/runs/TASK_ID/RUN_ID/
```

Files:

- `run_receipt.json`
- `run_receipt.md`
- `job_receipts.json`
- `job_receipts.jsonl`
- `source_evidence.json`
- `cache_index.json`
- `safety_events.json`
- `execution_log.jsonl`

`reports/runs/TASK_ID/latest_run.json` points to the latest receipt.

## Receipt Hashing

`receipt_contract_sha256` is computed from a normalized receipt contract. Volatile fields are excluded, including run IDs, timestamps, durations, generated timestamps, and absolute machine-specific paths.

Verification checks:

- stored receipt hash matches recomputation
- package, manifest, and DAG artifact hashes match
- job and source evidence counts reconcile
- byte totals reconcile
- source and total byte caps are respected
- fixture-only sources made no network attempt
- successful runnable sources have SHA256, magic validation, and content-family validation
- credentials and Authorization values are absent
- dependency ordering is valid
- failed dependencies cause downstream skips
- no unknown stages executed

CLI:

```bash
faster-raster run inspect example_wave1_climate_stack --plain
faster-raster run verify example_wave1_climate_stack --plain
faster-raster run evidence example_wave1_climate_stack --plain
```

## Fixture-Only PRISM

`prism_daily_ppt_static_zip` remains fixture-only. Its v0.8 receipt records historical bounded evidence:

- `fixture_only: true`
- `network_attempted: false`
- `historical_http_status: 206`
- `historical_bytes_read: 65536`
- `historical_detected_magic: zip`
- `historical_sha256_short: cc89306d4d5b`
- `current_endpoint_status: unresolved_or_stale`

No PRISM bounded fetch job is generated or executed.
