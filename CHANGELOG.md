# Changelog

All notable changes are documented here. Versions follow [Semantic Versioning](https://semver.org/) for release labels and PEP 440 for Python package metadata.

## Unreleased

### Added

- Auditable terminal repair when classification imagery is unavailable for
  the requested year, date range, or location.
- Direct bounding-box and point-and-buffer location construction with square
  or circular AOIs in meters, kilometers, or miles.
- True circular AOI masking across classification analysis and publication.

### Safety and provenance

- Fail-closed noninteractive behavior, explicit confirmation before
  acquisition, and separate acceptance of imagery/CDL temporal mismatches.
- Original and resolved request values, acquisition-envelope versus
  analysis-AOI provenance, and compact intervention metadata in final
  handoffs and `fr inspect` output.

## 1.0.0-beta.2 - 2026-07-25

### Added

- Auditable `naip_cdl_classification_audit` workflow with raw four-band NAIP
  features, CDL weak supervision, deterministic spatial holdout, confidence
  and disagreement COGs, finalized inspection summaries, and
  publication-only rerendering.

### Fixed

- Install the optional classification dependency in the complete GitHub
  Actions test environment.

## 1.0.0-beta.1 - 2026-07-20

### Added

- Public `fr` workflow for templates, validation, deterministic planning, cooking, inspection, and human-development hybrid publication.
- Exact-year CDL and NAIP coverage contracts with explicit available-year recovery.
- Multi-epoch CDL mapped-development proxy analysis using a common all-epoch footprint.
- Transactional handoffs, checksums, provenance, byte ceilings, bounded workers, and strict reuse-only verification.
- Public documentation site, contribution/security/citation guidance, and offline CI release gates.

### Changed

- Package version advanced from `1.0.0a3` to `1.0.0b1`.
- Public packaging and tests no longer depend on ignored backup files, local caches, external project paths, or the development `.beta-tools` environment.
- Release-facing descriptions distinguish implemented beta capabilities from roadmap directions.

### Known limitations

- The CDL human-development result is a mapped-development proxy, not authoritative urbanization or causal evidence.
- PRISM static ZIP evidence remains fixture-only pending a currently verified endpoint.
- Ubuntu/Python 3.12 is the public CI target; other operating systems are not yet part of the beta matrix.
