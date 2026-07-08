# Daymet NCSS Probe Plan

This plan is intentionally not a default diagnostic. It is an opt-in future probe design.

## Command Shape

```bash
python scripts/daymet_ncss_probe.py   --allow-network   --spec research/daymet_ncss_probe_spec.yaml   --out-json reports/daymet_ncss_probe.json   --out-md reports/daymet_ncss_probe.md
```

The script should not be implemented until the endpoint and query parameters are verified from official Daymet/THREDDS metadata.

## Required Safety Rules

- Refuse to run without `--allow-network`.
- Run metadata-only probe first.
- Run tiny subset second only if metadata probe passes.
- Enforce `max_bytes` from the YAML spec.
- Do not extract NetCDF.
- Do not require credentials in this milestone.
- Write JSON and Markdown reports.
- Normal tests must not call the network.

## Expected Failure Interpretation

- `401`: auth/session requirement; not dataset missing.
- `403`: access policy or endpoint unresolved.
- `404`: catalog version drift or endpoint mismatch.
- `429`: rate limited.
- `5xx`: service unavailable.
