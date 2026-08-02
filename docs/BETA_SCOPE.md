# Historical beta.1 scope

This file records the historical `v1.0.0-beta.1` scope. It is not the current release contract. The latest published release is `v1.0.0-beta.4`; current development is `1.0.0b5.dev0`.

## Included

- The public `fr` lifecycle: doctor, templates, init, validate, plan, cook, inspect, publish, and reuse-only verification.
- Deterministic source and harmonization planning, exact-year checks, byte ceilings, checksums, provenance, bounded workers, transactional handoffs, and strict zero-network reuse.
- Live USDA CDL acquisition and the CDL mapped-development proxy workflow, including regional and deterministic hotspot hybrid publication.
- Implemented agricultural CDL/NAIP workflows and bounded public-source contracts.
- Python 3.12 wheel and source distributions, offline release gates, and a static documentation site.

## Excluded

- Authoritative urbanization, population, economic, construction-date, occupancy, cadastral, or causal claims.
- New source adapters, provider integrations, credentials, paid or restricted sources, classifiers, execution engines, Slurm support, or architectural redesigns.
- Silent imagery-year substitution, unbounded network access, weakened validation, or performance guarantees.
- Automatic release publication, GitHub Pages deployment, PyPI upload, custom-domain setup, or any account-specific configuration.

Roadmap material describes possible directions, not implemented functionality or promises. Live integration remains explicit, bounded, and separate from routine offline CI.
