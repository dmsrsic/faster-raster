# Index-guided hybrid classification

Index-guided hybrid classification preserves a broad, weakly supervised
surface classification and then applies narrow, explicitly defined spectral
specialists. It is useful when a reproducible spectral rule separates a target
that a broad classifier represents poorly. It does not turn spectral
similarity into ground truth or physical causation.

This feature is current **Unreleased** development. The published
`v1.0.0-beta.3` V3 recipe remains unchanged.

## Start with the shipped workflow

```sh
fr indices list
fr indices show ndvi
fr templates show ag-naip-index-hybrid-classification
fr init studies/index-hybrid.fr.md \
  --template ag-naip-index-hybrid-classification \
  --name index-hybrid-demo \
  --bbox -83.2000 39.8500 -83.1990 39.8510 \
  --years 2023
fr validate studies/index-hybrid.fr.md
fr plan studies/index-hybrid.fr.md --offline
fr explain studies/index-hybrid.fr.md --offline
```

Review source compatibility, requested/persisted indices, class meanings,
specialist parents, candidate bounds, byte ceiling, and expected outputs before
a live `fr cook`.

## The two classification layers

The general layer uses the existing four-band NAIP random forest and CDL
superclass weak labels. Its class IDs, labels, output codes, mapping source,
requested count, and actual count are explicit. A requested number alone never
invents unlabeled classes.

The specialist layer contains explicit class IDs and meanings. Each specialist
declares:

- a stable output code and human label;
- intended and unsupported interpretations;
- a fixed or selected index strategy;
- calibration evidence;
- eligible general parent classes;
- priority and minimum support;
- uncertainty behavior.

The broad result is always retained independently. Specialists produce their
own score and candidate rasters before deterministic arbitration creates the
final hybrid and decision-state rasters.

## Built-in registry

Registry order, canonical serialization, definition hashes, and the registry
hash are deterministic. Use `fr indices show <id>` for the complete executable
definition and current NAIP compatibility.

| Index ID | Formula or operation | Required bands | Range | Important interpretation |
| --- | --- | --- | --- | --- |
| `red` | scaled source red | red | 0–1 for current uint8 NAIP contract | source-relative channel |
| `green` | scaled source green | green | 0–1 | source-relative channel |
| `blue` | scaled source blue | blue | 0–1 | source-relative channel |
| `nir` | scaled source NIR | nir | 0–1 | source-relative channel |
| `ndvi` | `(nir-red)/(nir+red+epsilon)` | nir, red | −1–1 | vegetation response proxy |
| `gndvi` | `(nir-green)/(nir+green+epsilon)` | nir, green | −1–1 | green–NIR vegetation proxy |
| `vari` | `(green-red)/(green+red-blue+epsilon)` | red, green, blue | −1–1 after clipping | visible-band scene proxy |
| `excess_green` | `2*green-red-blue` | red, green, blue | −2–2 | visible greenness proxy |
| `brightness` | mean of red, green, blue | red, green, blue | 0–1 | visible brightness |
| `saturation` | visible maximum minus minimum | red, green, blue | 0–1 | visible channel spread |
| `green_nir_water_proxy` | `(green-nir)/(green+nir+epsilon)` | green, nir | −1–1 | water/wet-surface spectral proxy; not NDMI |
| `ndmi` | `(nir-swir1)/(nir+swir1+epsilon)` | nir, swir1 | −1–1 | requires SWIR1 |
| `nbr` | `(nir-swir2)/(nir+swir2+epsilon)` | nir, swir2 | −1–1 | requires SWIR2 |
| `normalized_difference` | parameterized normalized difference | two declared bands | −1–1 | generic operation with explicit bands |
| `target_signature_similarity` | weighted Euclidean similarity `1/(1+distance)` | declared semantic bands | 0–1 | similarity score, not probability or proof |

Ordinary four-band NAIP provides red, green, blue, and NIR—not SWIR1 or
SWIR2. Consequently, NDMI and NBR fail planning/preflight. FasterRaster never
substitutes the green–NIR proxy for NDMI.

```text
requested_index: ndmi
required_bands: [nir, swir1]
available_bands: [red, green, blue, nir]
missing_bands: [swir1]
source_asset: naip_multispectral
alternative_configured_source_available: false
```

Raw NAIP digital numbers are not automatically atmospherically corrected
surface reflectance. Ratios and thresholds may be useful within the documented
scene and source contract, but may not transfer across dates, mosaics, sensors,
or radiometric products.

## Safe custom expressions

A custom index request names a new ID and supplies an arithmetic expression.
The parser permits semantic band references, finite numeric constants,
`+`, `-`, `*`, safe division, unary negation, parentheses, `abs`, `min`, `max`,
`clip`, and `normalized_difference`.

```yaml
indices:
  - index_id: green_red_contrast
    expression: "normalized_difference(green, red)"
    persist: true
    display: false
```

Expressions have limits on source length, AST nodes, nesting depth, and
referenced bands. Canonical text, required bands, engine version, epsilon
policy, clipping, and formula hash are recorded. Attribute access, imports,
dunder names, arbitrary calls, comprehensions, lambdas, subscripting, Python
statements, file access, and network access are rejected. The evaluator never
uses `eval` or `exec`.

## User-defined strategies

### Single-index threshold

Direction is explicit: `high`, `low`, or `range`.

```yaml
strategy:
  type: single_index_threshold
  condition:
    index_id: ndvi
    direction: high
    threshold: 0.45
```

This is a fixed rule unless separate calibration evidence establishes
otherwise. Its score raster contains the analytical index value, not a
probability.

### Multi-index Boolean rule

Boolean specialists support `all`, `any`, and `at_least_k`.

```yaml
strategy:
  type: multi_index_boolean
  operator: all
  conditions:
    - index_id: ndvi
      direction: high
      threshold: 0.45
    - index_id: gndvi
      direction: high
      threshold: 0.35
```

The specialist score is the fraction of declared conditions met. It is not a
calibrated probability.

### Normalized weighted score

Each input has an explicit minimum, maximum, and finite weight. Raw scores with
different scales are never added directly.

```yaml
strategy:
  type: multi_index_weighted_score
  inputs:
    - index_id: ndvi
      normalization_minimum: -1.0
      normalization_maximum: 1.0
      weight: 0.6
    - index_id: gndvi
      normalization_minimum: -1.0
      normalization_maximum: 1.0
      weight: 0.4
  intercept: 0.0
  direction: high
  threshold: 0.7
  weights_source: user_provided
```

Learned weights or normalizations are labeled
`learned_spatial_calibration`; user values remain `user_provided`.

### Target spectral signature

A target may be supplied explicitly or estimated as the deterministic
per-band mean of positive calibration points. The receipt records semantic
band order, target vector, scale/offset evidence, weights, formula, sample
count, pixel digest, threshold, and formula hash.

```yaml
strategy:
  type: target_signature_similarity
  target_bands:
    red: 0.42
    green: 0.41
    blue: 0.40
    nir: 0.29
  weights:
    red: 1.0
    green: 1.2
    blue: 1.0
    nir: 0.6
  threshold: 0.91
  target_source: user_provided
```

This can express a narrow gray disturbed-surface candidate inspired by
abandoned-coal-land analysis. Gray spectral similarity alone is not proof of
mining, abandonment, land use, contamination, ownership, safety, or causation.
Use local calibration and independent evidence.

## Calibration evidence

Fixed rules may declare `fixed_rule` and are reported as rule-based, not
validated supervised classifications. Learned recommendation and automatic
selection require either:

- `cdl_weak_labels` with explicit `positive_general_classes`; or
- `user_points` in EPSG:4326, longitude first.

Points must be finite, unique, inside the analysis AOI, on source/AOI-valid
pixels, and satisfy positive/negative support minimums. Point-to-pixel mapping
is deterministic. Public summaries contain counts and stable digests, not raw
coordinates; a local receipt preserves extraction evidence.

An explicit target vector is also valid evidence for a fixed target-similarity
rule. It does not by itself provide positive/negative labels for supervised
threshold search.

## Selection modes

### User-defined

FasterRaster validates and executes the exact indices, thresholds, weights,
logic, parents, and priority in the workfile. No hidden candidate search occurs.

### Recommendation

FasterRaster calculates compatible candidates and ranks them deterministically.
An interactive terminal shows leading candidates, metrics, and caveats; the
user may accept one, enter another exact candidate ID, decline, cancel, or
reach EOF. Acceptance applies only to the current run and never rewrites the
source workfile.

`--non-interactive` and JSON execution never prompt. They preserve a clearly
nonfinal review package with status `AWAITING_INDEX_SELECTION`; no completed
hybrid handoff is claimed.

### Automatic

Automatic selection requires `selection_mode: automatic` and
`automatic_authorized: true`. It records every candidate, ranking, guards,
selected candidate, rationale, and explicit authorization. It stops without a
final hybrid when no candidate meets support or performance guards.

## Nested spatial validation

Candidate search is intentionally bounded by maximum index, pair, triple,
total-model, sample, and inner-fold counts. It considers compatible singles,
bounded pairs, and bounded triples—never unrestricted symbolic discovery.

The existing final spatial holdout is reserved before search. It is not used to
choose an index/combination, direction, threshold, normalization, weight, or
complexity. Remaining blocks form deterministic inner spatial folds for
ranking and threshold fitting. The selected contract is evaluated once on the
untouched outer holdout.

The receipt distinguishes inner-selection metrics from outer-holdout metrics,
records formulas/combinations tested and tie rules, and prefers simpler
candidates when improvement does not clear the complexity guard. Agreement
with CDL weak labels is not independent accuracy.

## Parent restrictions and overlap arbitration

Specialists can override only explicitly eligible general classes. Candidate
masks are calculated independently. Arbitration uses declared priority and the
configured equal-priority tie rule; it never compares unrelated raw index
scores. Equal-priority unresolved ties use class code `255` in the shipped
contract.

The decision-state raster distinguishes invalid/excluded pixels, retained
general classes, specialist overrides, and unresolved overlap. Receipts record
pairwise overlap counts, winner counts, reasons, and final class areas.

## Analytical outputs

Completed V4 handoffs include equivalent artifacts under:

```text
data/indices/<index-id>.cog.tif
data/specialists/<class-id>_score.cog.tif
data/specialists/<class-id>_candidate.cog.tif
data/naip_<year>_surface_classification.cog.tif
data/final_hybrid_classification.cog.tif
data/hybrid_decision_state.cog.tif

analysis/indices/index_registry.json
analysis/indices/index_capability_report.json
analysis/indices/index_plan.json
analysis/indices/index_statistics.json
analysis/indices/index_candidate_ranking.json
analysis/indices/specialist_class_rules.json
analysis/indices/specialist_overlap_matrix.json
analysis/indices/hybrid_class_inventory.json
analysis/indices/index_validation_metrics.json

receipts/index_calculation_receipt.json
receipts/index_selection_receipt.json
receipts/specialist_classification_receipt.json
receipts/hybrid_classification_receipt.json
```

Published index COGs are windowed float32 analytical values with deterministic
masks, statistics, quantiles, hashes, and COG validation. Display stretches are
separate metadata and never alter analytical values.

## Publication and inspection

The deterministic 3840×2160 audit publication selects panels by a stable rule
and shows natural color, broad classification, final hybrid classes, selected
index, specialist score/candidate, decision state, legends, and receipt
evidence. Normalized differences use zero-aware displays; specialist scores use
sequential displays; no generic rainbow palette is used. Circular AOI masks
apply to analysis and display statistics.

```sh
fr inspect latest
fr inspect latest --verbose
fr inspect latest --json
```

Inspection does not import scikit-learn. It summarizes registry/formula/band
evidence, ranges, valid counts, selection/calibration, candidates, selected
contract, inner and untouched-holdout metrics, overlaps, arbitration, class
areas, temporal/AOI provenance, and beta.3 repair linkage. Older V3 handoffs
remain readable.

Publication-only rerendering reuses analytical artifacts, performs zero network
I/O, verifies their hashes, and records
`analytical_rasters_modified: false`.

## Scientific and transfer limitations

- Raw NAIP values are not automatically surface reflectance.
- Indices may be scene-relative spectral proxies.
- Weak-label agreement is not independent accuracy.
- Automatically selected indices are statistically useful under the tested
  evidence, not necessarily physically causal.
- NDMI requires SWIR1 and is unavailable from ordinary four-band NAIP.
- NBR requires SWIR2 and is unavailable from ordinary four-band NAIP.
- Thresholds and target vectors may not transfer across scenes, dates, mosaics,
  sensors, or radiometric products.
- Multi-index search creates multiple-comparison and overfitting risk; bounded
  nested spatial validation reduces but does not eliminate that risk.
- Specialist classes refine a declared analytical contract; they do not
  establish ground truth.
