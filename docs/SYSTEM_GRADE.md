# Whole-System Grade

`faster-raster grade system` writes a v0.8 whole-system grade report:

- `reports/system_grade/system_grade_v0_8_0.json`
- `reports/system_grade/system_grade_v0_8_0.md`

The grader evaluates local compile/package artifacts, existing static range live evidence, safety defaults, documentation coverage, determinism hashes, DAG validity, local execution status, and run receipt verification.

It does not perform network requests by default. Static Wave 1 live evidence is read from existing report artifacts instead of rerunning endpoints.

Release decisions:

- `release_ready`
- `release_ready_with_cautions`
- `hold_release`

Before a valid live local receipt exists, the grader emits `no_live_local_execution_receipt` and the maximum release decision is `release_ready_with_cautions`. This is not a blocking failure.

After a valid live local receipt exists, full `release_ready` requires four successful runnable sources, one fixture-only PRISM source, zero failed runnable sources, byte caps respected, total cap respected, magic/content-family validation, checksums, no credentials, and no Authorization values.

Invalid live receipts are blocking failures and produce `hold_release`.
