# Changelog

All notable changes are documented here. Versions follow [Semantic Versioning](https://semver.org/) for release labels and PEP 440 for Python package metadata.

## 1.0.0b5 - 2026-08-02

This release publishes the post-beta.4 work that was previously carried on the
public development branch.

### Added

- Safe offline installation inspection with `fr update status` and explicitly
  authorized, bounded beta/stable release checking with `fr update check`.
- Release-manifest and deterministic update-receipt contracts, aggregate
  adoption-metric foundations, and public Handle Registry contracts.
- A public
  [FasterRaster Flavortown Sauce Wizard](https://chatgpt.com/g/g-6a692bb17b9c8191a318997fd0435bf7-fasterraster-flavortown-sauce-wizard)
  entry point, four family-specific Source Pack fixtures, explicit
  provider-evidence/readiness states, and deterministic frozen
  `fasterraster.source-pack-plan/v1` handoff compilation. These remain
  Unreleased / experimental.
- Declarative `fasterraster.source-pack/v1` Source Packs with offline
  validation, deterministic fixtures and archives, bounded opt-in probes,
  opaque credential requirements, two shipped examples, and `fr sauce`
  lifecycle commands.
- Explicit `fasterraster.temporal-alternatives/v1` ranking and
  `fasterraster.temporal-resolution/v1` selection contracts. Exact requested
  time remains authoritative until a user selects a candidate.
- Registry-driven `fasterraster.preview-template/v1` layouts, including
  agricultural audit and general multi-source templates, CLI discovery, JSON
  Schema, deterministic hashes, and legacy-task compatibility mappings.
- Canonical public capability registry, generated website and GPT surfaces,
  drift tests, and a content-hashed Flavortown Sauce Wizard grounding bundle.
- CRS-aware categorical area accounting that preserves native class counts,
  measures physical area on a declared equal-area grid, and reconciles class
  totals to the valid footprint.
- Mandatory maximum-class-probability threshold provenance across plans,
  receipts, publications, and inspection, including truthful legacy behavior.
- Explicit coherent NAIP–CDL temporal alternatives and immutable resolution
  contracts, with paired noninteractive CLI year arguments and zero raster
  acquisition during selection.
- Classification audit-template checks for readable legends, decision and
  confidence explanations, title bounds, supported class codes, minimum text
  size, provenance footers, and deterministic documentation derivatives.

### Security and compatibility

- Source Packs reject arbitrary code, traversal, escaping symlinks, unsafe
  hosts and redirects, embedded credentials, unsafe categorical resampling,
  unbounded templates, ambiguous nodata behavior, and volatile archives.
- Public credential requirements contain only scheme, opaque reference, and
  host scope. The public runtime fails before network access when a resolver is
  required; resolved secrets remain outside plans, logs, cache keys, receipts,
  and archives.
- Existing noninteractive exact-year behavior remains fail-closed. Existing
  preview tasks compile through released compatibility templates; render
  contracts now additionally bind their template schema, ID, and hash.

These contracts are included in beta.5. Their release state, evidence tier, and
execution boundary remain independent claims.

## 1.0.0-beta.4 - 2026-07-25

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
