# Local Bounded Execution

FasterRaster v0.8 adds a local bounded execution engine for the v0.7 execution package DAG.

Bounded execution produces source evidence only. A bounded probe does not authorize or perform full-object acquisition. v0.9 materialization consumes verified bounded probe receipts, requires exact plan-hash approval, and validates probe-prefix continuity before committing a complete artifact.

The engine consumes:

- `reports/execution_packages/TASK_ID/execution_package.json`
- `reports/execution_packages/TASK_ID/execution_jobs.jsonl`
- `reports/execution_packages/TASK_ID/dag.json`
- `reports/execution_packages/TASK_ID/cache_plan.json`
- `reports/execution_packages/TASK_ID/failure_policy.json`
- `reports/task_compiles/TASK_ID/acquisition_manifest.jsonl`

It validates the package and DAG, topologically orders jobs, rejects cycles, dispatches only through explicit Python stage handlers, and records every job transition. It never executes command strings from package artifacts.

Supported stages are:

- `resolve_request`
- `bounded_fetch`
- `validate_http_status`
- `validate_byte_cap`
- `validate_magic`
- `validate_content_family`
- `compute_checksum`
- `record_source_evidence`
- `record_fixture_evidence`

Unsupported downstream stages are classified as `unsupported_downstream_stage`.

## Network Policy

Network is off by default:

```bash
faster-raster run local example_wave1_climate_stack --plain
```

Without `--allow-network`, network jobs are skipped as `skipped_network_disabled`, the run status is `blocked_policy`, and no successful live receipt is claimed.

Live bounded execution is explicit:

```bash
faster-raster run local example_wave1_climate_stack --allow-network --plain
```

The bounded fetch handler:

- allows only `http` and `https`
- verifies the rendered URL host against the manifest/package URL host
- sends `Range: bytes=0-(max_bytes-1)`
- uses a FasterRaster user agent
- enforces per-source and total byte caps
- retains no more than the configured prefix cap
- records HTTP status, content type, content range, range behavior, bytes, SHA256, magic, and content family
- rejects empty responses, magic mismatches, content-family mismatches, host mismatches, and non-HTTP schemes

HTTP `200` is allowed as bounded prefix evidence only. The engine retains no more than the cap, sets `range_honored: false` when no range evidence is present, and records `server_range_unconfirmed`.

## Cache Policy

Runtime cache payloads are written under:

```text
cache/runtime/static_http_range/{source_id}/{temporal_key}/{url_sha256_short}.{extension}.head{max_bytes}
```

Sidecars use:

```text
CACHE_FILE.receipt.json
```

The sidecar declares `bounded_probe_only: true` and `full_object: false`. Cache reuse requires the sidecar and payload hash to validate; corruption is a validation failure. Runtime cache files are ignored by Git.

## Boundaries

v0.8 does not perform full downloads, archive extraction, gzip decompression, NetCDF variable decoding, GeoTIFF raster decoding, harmonization execution, Sentinel imagery download, PRISM live execution, distributed execution, arbitrary shell execution, or runtime registry mutation.
