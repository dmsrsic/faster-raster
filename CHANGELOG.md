# Changelog

All notable changes are documented here. Versions follow [Semantic Versioning](https://semver.org/) for release labels and PEP 440 for Python package metadata.

## Unreleased

### Added

- Source-aware, deterministically hashed spectral-index registry with semantic
  band compatibility, published analytical index COGs, statistics, receipts,
  CLI discovery, and fail-closed NAIP SWIR checks.
- Bounded custom arithmetic index expressions with canonicalization,
  complexity limits, safe division, and no arbitrary Python execution.
- Additive V4 hybrid classification recipe/template separating broad general
  classes from single-index, Boolean multi-index, normalized weighted-score,
  and target spectral-signature specialist classes.
- User-defined, deterministic recommendation, and explicitly authorized
  automatic selection modes, including calibration points and CDL weak-label
  targets.
- Nested spatial candidate selection that keeps the existing outer holdout
  untouched by index, combination, direction, threshold, and weight choices.
- Specialist score/candidate rasters, deterministic parent/priority overlap
  arbitration, final hybrid/decision-state COGs, 4K audit publication,
  zero-network rerendering, and concise `fr inspect` evidence.

### Safety and provenance

- Noninteractive recommendation produces a clearly nonfinal
  `AWAITING_INDEX_SELECTION` review package; automatic mode requires explicit
  authorization and fails closed when support or performance guards are not
  met.
- Records formulas, source-band/scaling evidence, candidate bounds/ranking,
  thresholds, normalizations, spatial folds, calibration digests, untouched
  holdout metrics, overlap decisions, analytical hashes, and display-only
  stretches without treating index scores as probabilities.

## 1.0.0-beta.3 - 2026-07-25

### Added

- Auditable terminal repair when classification imagery is unavailable for
  the requested year, date range, or location, including source-listed and
  manually entered replacement years and replacement temporal ranges.
- Direct bounding-box repair and point-and-buffer construction with square or
  circular AOIs in meters, kilometers, or miles.
- True circular AOI masking across classification analysis and publication.

### Safety and provenance

- Fail-closed noninteractive behavior, explicit confirmation before
  acquisition, and separate acceptance of imagery/CDL temporal mismatches.
- Original and resolved request values, acquisition-envelope versus
  analysis-AOI provenance, and compact intervention metadata in final
  handoffs and `fr inspect` output.

### Fixed

- Preserve cross-year imagery/CDL values, repaired AOIs, and intervention
  provenance during publication-only rerendering, including compatibility
  with older finalized handoffs.

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
