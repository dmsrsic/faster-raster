# Contributing to FasterRaster

Thank you for helping improve the public beta. Small, reviewable changes with tests and explicit evidence are the best fit for this early project.

## Before opening a change

Open an issue once the public repository exists when a proposal changes a source contract, scientific interpretation, network policy, output schema, or public CLI. Security reports follow [SECURITY.md](SECURITY.md), not the issue tracker.

Do not include credentials, private endpoints, downloaded rasters, local caches, generated handoffs, publications, or machine-specific paths.

## Development setup

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev,docs]'
```

Run the offline checks before submitting:

```sh
python -m pytest -q
./scripts/fr-beta-smoke --quick
fr-beta-check --output /tmp/fasterraster-beta-check
mkdocs build --strict
git diff --check
```

Tests must not use live raster services. A live-source concern should include redacted, bounded evidence and a reproducible offline fixture.

## Change expectations

- Preserve exact-year behavior, checksums, byte ceilings, reuse guarantees, provenance, and categorical resampling semantics.
- Add or update tests for observable behavior; do not weaken an assertion to accommodate a regression.
- Keep public claims within the evidence supported by the workflow.
- Update documentation and release notes when a user-facing contract changes.
- Keep commits focused and avoid unrelated generated files.

The community beta covers public-source adapters and core orchestration. Roadmap references to managed infrastructure, private integrations, paid-source adapters, enterprise authentication, specialized classifiers, or cluster services are directional and do not mean those capabilities are implemented or promised.
