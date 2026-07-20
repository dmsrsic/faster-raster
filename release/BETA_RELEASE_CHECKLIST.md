# Public beta release checklist

Target release: `v1.0.0-beta.1` / package `1.0.0b1`

## Local release candidate

- [ ] Starting commit and protected original-worktree changes verified.
- [ ] Release worktree is separate and clean.
- [ ] Current-tree and history secret/path/large-file audits reviewed.
- [ ] No raw raster, cache, handoff, publication directory, credential, or local environment is tracked.
- [ ] Wheel and source distribution build successfully.
- [ ] Built wheel installs into a fresh Python 3.12 environment.
- [ ] Installed `fr` help/version/doctor/templates/init/validate checks pass outside the checkout.
- [ ] Complete offline pytest suite passes.
- [ ] Quick and full beta smoke checks pass.
- [ ] Beta check passes without tracked-report mutation.
- [ ] Documentation builds with `mkdocs build --strict`.
- [ ] `git diff --check` passes.
- [ ] README, license, changelog, citation, contributing, security, and release notes are current.
- [ ] Example captions distinguish analytical years, imagery year, context role, and proxy limitations.
- [ ] No remote or release tag exists.

## Manual publication boundary

The following are intentionally not performed by the local preparation pass:

- create or connect a GitHub repository;
- push the branch or a tag;
- enable or deploy GitHub Pages;
- publish a GitHub Release;
- upload to PyPI;
- configure a custom domain or DNS.
