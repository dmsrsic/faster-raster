# Whole-System Grade

`faster-raster grade system` writes a v0.9 whole-system grade report:

- `reports/system_grade/system_grade_v0_9_0.json`
- `reports/system_grade/system_grade_v0_9_0.md`

The grader evaluates local compile/package artifacts, existing static range live evidence, safety defaults, documentation coverage, determinism hashes, DAG validity, local execution status, and run receipt verification.

It does not perform network requests by default. Static Wave 1 live evidence is read from existing report artifacts instead of rerunning endpoints.

v0.9 adds `materialization_score`, `artifact_integrity_score`, and `artifact_catalog_score`. Before a real materialization receipt exists, the grader emits `no_live_materialization_receipt` and caps the decision at `release_ready_with_cautions`. A valid one-source canary artifact can permit `release_ready`; full Wave 1 coverage remains tracked separately.

Release decisions:

- `release_ready`
- `release_ready_with_cautions`
- `hold_release`

Before a valid live local receipt exists, the grader emits `no_live_local_execution_receipt` and the maximum release decision is `release_ready_with_cautions`. This is not a blocking failure.

After a valid live local receipt exists, full `release_ready` requires four successful runnable sources, one fixture-only PRISM source, zero failed runnable sources, byte caps respected, total cap respected, magic/content-family validation, checksums, no credentials, and no Authorization values.

Invalid live receipts are blocking failures and produce `hold_release`.

### Materialization failure policy

A failed latest materialization receipt is blocking release evidence. An absent pre-artifact catalog is not blocking by itself; the grade blocks on the failed run, invalid artifact receipt, or invalid catalog entry.
