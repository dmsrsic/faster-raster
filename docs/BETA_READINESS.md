# Historical beta.1 readiness

This is archived beta.1 evidence, not a current release checklist. The published release is `v1.0.0-beta.4`; current development is `1.0.0b5.dev0`.

- Release label: **`v1.0.0-beta.1`**
- Python package: **`1.0.0b1`**
- Local decision: **ready for the operator's GitHub connection and publication review**

The release candidate was prepared in a separate worktree from commit `900a61ecec85a1e0b368af7d211cad4c77e7a092`. Its local Python 3.12 evidence is:

- complete offline suite: **626 passed**, with three upstream NumPy/Rasterio deprecation warnings;
- quick smoke: **PASS**, including 46 focused tests;
- full smoke: **PASS**;
- beta readiness check: **PASS**, including compile determinism and execution-DAG validation;
- isolated offline system grade: **`release_ready_with_cautions`**, with no blockers and expected cautions for absent live run/materialization evidence;
- wheel and source distribution: **PASS**;
- fresh wheel installation outside the checkout: **PASS** for help, version, offline doctor, templates, Meridian generation/validation, and public imports;
- strict MkDocs build and Git whitespace validation: **PASS**.

The GitHub Actions workflow mirrors these offline checks on Ubuntu 24.04 and Python 3.12. It has not been executed on GitHub because this local preparation intentionally created no remote, repository, tag, deployment, or release.

Live source checks are not routine CI requirements. Exact-year and byte-budget behavior remains enforced, and the operator must explicitly approve any later bounded network integration run.

Machine-readable evidence is summarized in `release/beta_readiness.json`; the public publication boundary is in `release/BETA_RELEASE_CHECKLIST.md`.
