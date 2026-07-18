# FasterRaster Beta Gate 1 scope

Beta Gate 1 makes the existing FasterRaster alpha-3 stack installable, inspectable, and release-checkable without changing the scientific scope.

## Included

- The existing `faster-raster` evaluator and operational commands.
- The installed `fr` local-study workflow: doctor, source capabilities, workfile validation, planning, explanation, cooking, inspection, and preview opening.
- The four validated agricultural recipe contracts and their shared runtime.
- State-neutral agricultural bbox validation and bounded, source-driven
  coverage checks for the exact NAIP/CDL year and required 3DEP assets before
  raster acquisition.
- Stable handoff provenance based on final handoff IDs and handoff-relative paths.
- Explicit local asset readiness, remote source status, and action fields in JSON output.
- Friendly default terminal output, with exact identifiers and reasons available through `--verbose` or `--json`.
- A deterministic release inventory, isolated beta validation report, complete tests, wheel build, and clean-install smoke checks.

## Excluded

- New source families, scientific algorithms, raster products, or recipe semantics.
- Automatic execution of future-unverified sources.
- Unbounded network probes, full remote dataset extraction, or source tests that exceed configured byte ceilings.
- Global agricultural coverage claims. Actual support remains bounded by
  source-reported geography, requested year, required assets, resolution,
  local capability, and configured execution limits.
- Generated handoffs, reports, baseline evidence, operator orders, backups, local capability profiles, caches, and build artifacts from the source release.
- Performance promises. Doctor recommendations are conservative heuristics and are not applied unless the operator explicitly runs `fr configure --apply-recommendations`.

## Release checks

From a Python 3.12 environment with GDAL available:

```text
python -m build
python -m pytest -q
fr-beta-check
```

`fr-beta-check` redirects generated grader and compiler reports to a temporary root, verifies tracked reports are unchanged, compiles and packages the example task, runs the full system grader, validates a workfile and plan, performs a zero-network reuse-only cook when compatible local cache evidence exists, inspects that handoff, and writes JSON and Markdown evidence beneath `outputs/beta_gate_1/latest/`.

## Known bounded warnings

- Static HTTP range evidence remains deliberately bounded and stops before full extraction.
- The PRISM path remains fixture-only until a currently verified endpoint is promoted through the existing source policy.
- A latest materialization attempt may be policy-blocked while the latest successful materialization remains valid release evidence.
