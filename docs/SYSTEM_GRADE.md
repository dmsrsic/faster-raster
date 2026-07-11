# Whole-System Grade

`faster-raster grade system` writes a v0.7 whole-system grade report:

- `reports/system_grade/system_grade_v0_7_0.json`
- `reports/system_grade/system_grade_v0_7_0.md`

The grader evaluates local compile/package artifacts, existing static range live evidence, safety defaults, documentation coverage, determinism hashes, and DAG validity.

It does not perform network requests by default. Static Wave 1 live evidence is read from existing report artifacts instead of rerunning endpoints.

Release decisions:

- `release_ready`
- `release_ready_with_cautions`
- `hold_release`

A passing target requires no blocking failures, static Wave 1 4/4 live evidence, DAG pass, determinism pass, safety score 100, and overall score at least 90.
