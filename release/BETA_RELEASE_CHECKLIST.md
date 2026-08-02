# Public beta release checklist (state-aware)

Published release: `v1.0.0-beta.4` / package `1.0.0b4`

The repository is post-beta.4 development. The published beta.4 tag and GitHub
release are immutable; this checkout identifies as `1.0.0b5.dev0`. Capability
release state and evidence are generated from `configs/public_capabilities.yaml`.

## Local release candidate

- [x] Starting commit and protected original-worktree changes verified.
- [x] Public `main` is the Pages source and the beta.4 release is published.
- [x] Current-tree and history secret/path/large-file audits reviewed.
- [x] No raw raster, cache, handoff, publication directory, credential, or local environment is tracked.
- [x] Wheel and source distribution build successfully.
- [x] Built wheel installs into a fresh Python 3.12 environment.
- [x] Installed `fr` help/version/doctor/templates/init/validate checks pass outside the checkout.
- [x] Complete offline pytest suite passes.
- [x] Quick and full beta smoke checks pass.
- [x] Beta check passes without tracked-report mutation.
- [x] Documentation builds with `mkdocs build --strict`.
- [x] `git diff --check` passes.
- [x] README, license, notice, changelog, citation, contributing, security, and release notes are current.
- [x] Example captions distinguish analytical years, imagery year, context role, and proxy limitations.
- [x] No release mutation is performed by implementation work.
- [ ] Prepare a future beta.5 tag only after exact tag-commit validation, wheel,
      sdist, `SHA256SUMS`, release manifest, and release-note review.

The checked-in `v1.0.0-beta.5.manifest.example.json` is a schema/example only;
its zero digests are placeholders and must never be published as release data.

## Manual publication boundary

The following are intentionally not performed by this implementation pass:

- create, replace, or retag a release;
- push a branch or tag;
- upload to PyPI;
- configure a custom domain or DNS;
- ship automatic update application or anonymous registry intake.
