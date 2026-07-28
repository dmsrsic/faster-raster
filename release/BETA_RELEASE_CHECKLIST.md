# Public beta release checklist

Published release: `v1.0.0-beta.4` / package `1.0.0b4`

The Bring Your Own Sauce, Sauce Time, preview-template, and public credential
contracts in the current tree are Unreleased / experimental until a maintainer
performs a separate release decision.

## Local release candidate

- [x] Starting commit and protected original-worktree changes verified.
- [x] Release worktree is separate and clean after the local evidence commit.
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
- [x] No remote or release tag exists.

## Manual publication boundary

The following are intentionally not performed by the local preparation pass:

- create or connect a GitHub repository;
- push the branch or a tag;
- enable or deploy GitHub Pages;
- publish a GitHub Release;
- upload to PyPI;
- configure a custom domain or DNS.
