# NAIP–CDL weak-supervised surface classification

`naip_cdl_classification_audit` is a source-aware agricultural recipe for
single-date, high-resolution spectral surface classification. It uses same-year
USDA Cropland Data Layer (CDL) superclasses as weak labels and publishes a
spatial agreement audit.

The supported scientific claim is:

> Single-date high-resolution NAIP spectral surface classification weakly
> supervised by same-year USDA CDL superclasses. Spatial holdout metrics measure
> agreement with weak labels, not independent ground-truth accuracy.

The output is not crop-species truth, authoritative land cover, a parcel or
ownership map, construction or occupancy evidence, population or economic
activity, irrigation status, yield, causal land-use change, independent
accuracy assessment, or historical change.

The additive Unreleased V4 workflow preserves this broad result and applies
explicit spectral specialist classes afterward. See
[Index-guided hybrid classification](index-guided-classification.md). Existing
V3 recipes, features, output meanings, handoffs, inspection, and zero-network
rerendering do not require new fields.

![Finalized NAIP–CDL classification audit publication](assets/naip-cdl-classification-audit.png)

*Finalized real-imagery example. The holdout and agreement values shown here
measure consistency with same-year CDL weak labels, not independent accuracy.*

## Source and feature contract

The spectral source is an unrendered four-band `uint8` NAIP COG requested from
`USGSNAIPImagery/ImageServer` with zero-based `bandIds=0,1,2,3`. Output band
order is red, green, blue, near infrared. A three-band response, rendered
NaturalColor/NDVI response, missing CRS, invalid transform, mismatched band
masks, excessive pixel size, or non-COG result fails closed.

The model scales source bytes to `[0,1]` `float32`. With a centrally recorded
`epsilon = 1e-6`, it calculates:

| Feature | Equation |
| --- | --- |
| red, green, blue, nir | unstretched scaled source bands |
| NDVI | `(NIR - R) / (NIR + R + epsilon)` |
| GNDVI | `(NIR - G) / (NIR + G + epsilon)` |
| VARI | `(G - R) / (G + R - B + epsilon)` |
| excess green | `2G - R - B` |
| brightness | `(R + G + B) / 3` |
| saturation | `(max(R,G,B) - min(R,G,B)) / (max(R,G,B) + epsilon)` |

Numeric NDVI is calculated from the original numeric bands. A display NDVI
image has already been quantized, color-mapped, and often stretched; RGB values
from that picture are not NDVI measurements and never enter this model.
Natural-color `(R,G,B)` and color-infrared `(NIR,R,G)` views are derived locally
for publication only.

Features are calculated in deterministic raster windows. FasterRaster never
materializes an AOI-wide floating-point feature cube.

## Weak-label mapping and cores

Mapping `cdl_surface_superclasses_v1` is immutable and content-hashed. Every
valid code in the repository's complete `CDL_CLASS_LABELS` legend is either
explicitly mapped or explicitly excluded.

| Code | Output class |
| ---: | --- |
| 0 | unknown_or_uncertain |
| 1 | cropland |
| 2 | fallow_or_barren |
| 3 | developed_open_or_low |
| 4 | developed_medium_or_high |
| 5 | noncrop_vegetation |
| 6 | water |

CDL values `0`, `81`, and `255` are invalid. Ambiguous valid classes including
wetlands, aquaculture, ice/snow, and nonagricultural/undefined are excluded.
They become class 0 and cannot enter training.

CDL is mapped on its categorical source grid. A radius of one retains a center
cell only when its complete 3×3 neighborhood has the same nonzero superclass.
This removes weak-label boundary cells before labels are warped. The complete
superclass raster and eroded training-core raster are then aligned to the exact
NAIP CRS, affine transform, width, height, extent, and pixel size using nearest
neighbor only.

## Sampling, spatial holdout, and model

Training extraction follows a fixed row-major window order and uses a bounded,
deterministic minimum-priority reservoir within each superclass. The receipt
records eligible and selected counts, masks, coordinate digest, feature and
label hashes, and train/holdout hashes. Raw training coordinates are not
published.

NAIP-grid blocks are assigned to five folds by a stable SHA-256 hash of block
row, block column, and seed. Fold zero is held out; no random-pixel split is
used. Train and holdout block sets are checked for overlap and their counts and
digests are recorded. Classes without the configured training and holdout
support are excluded with a diagnostic; classification fails if fewer than two
supported classes remain.

Install the lazy classifier extra only when this recipe is needed:

```bash
python -m pip install -e '.[classification]'
```

The default backend is scikit-learn `RandomForestClassifier` with 192 trees,
maximum depth 20, minimum leaf size 5, `max_features="sqrt"`,
`class_weight="balanced_subsample"`, seed `20260724`, and `n_jobs=1`. No pickle
or other executable model artifact is written.

Metrics are named **weak-label spatial holdout agreement**. They include the
confusion matrix, overall and balanced agreement, macro precision/recall/F1,
Cohen's kappa, and per-class precision/recall/F1/support with an explicit
zero-division policy. They do not constitute independent accuracy.

## Inference, confidence, and agreement

Inference is blockwise on the raw NAIP grid. Maximum class probability becomes
confidence. Predictions below the configured threshold become class 0;
valid-pixel confidence is
stored as rounded percent `1–100`, while invalid pixels use 0. The optional
nine-pixel sieve is currently recorded as disabled to preserve invalid gaps and
exact blockwise results; pre/post counts are still reported.

The threshold is not inferred from the rendered image. Planning, the model
receipt, final receipt, publication evidence, and `fr inspect` carry one
mandatory provenance object:

```json
{
  "confidence_metric": "maximum_class_probability",
  "confidence_threshold": 0.60,
  "unknown_class_code": 0,
  "threshold_source": "recipe_default"
}
```

The numeric value is the actual recipe or workfile value, and
`threshold_source` becomes `configured_override` for a workfile override.
Finalization fails closed if uncertainty products exist without this evidence.
Inspection of an older handoff remains supported but reports the provenance as
legacy-unavailable rather than guessing a value.

The agreement-state raster uses:

| Code | State |
| ---: | --- |
| 0 | invalid_or_excluded |
| 1 | prediction_agrees_with_cdl |
| 2 | low_confidence_or_unknown |
| 3 | high_confidence_disagreement |

Disagreement is an audit state. It is not automatically a CDL error or a model
error.

## Artifact and transaction contract

Primary raster products are:

```text
data/cdl_superclasses.cog.tif
data/cdl_training_cores.cog.tif
data/naip_{year}_surface_classification.cog.tif
data/naip_{year}_classification_confidence.cog.tif
data/naip_{year}_cdl_agreement_state.cog.tif
```

Machine-readable analysis includes feature, training, and model receipts;
holdout confusion matrices and metrics; class agreement matrices; disagreement
summary; class-area inventory; and
`analysis/classification/area_accounting.json`. The 3840×2160 preview contains natural
color, CIR, numeric NDVI, predicted classes, and confidence/agreement panels.
Its main map uses a muted prediction overlay and high-confidence disagreement
outlines—not universal CDL pixel boundaries.

### Physical area accounting

Categorical class counts are counted exactly on the native analytical raster.
Physical area is a separate deterministic calculation. A raster already in a
declared equal-area CRS uses its native grid; other rasters are reprojected
with nearest-neighbor categorical resampling to EPSG:6933 before area is
measured. Nodata and the analytical AOI mask are preserved. This prevents the
latitude-dependent inflation produced by multiplying nominal EPSG:3857 pixel
width and height.

The area receipt records the method, source and equal-area grids, reference
CRS, units, native and equal-area counts, square metres, hectares,
reconciliation tolerance, valid-footprint area, summed class area, status, and
SHA-256. Summed class area must reconcile to the valid footprint within
`0.001` (0.1%). The Greeley regression bbox
`[-104.80, 40.34, -104.58, 40.51]` measures approximately 352.432 km², not the
approximately 608.851 km² implied by nominal Web Mercator pixel area.

The asset plan is written before acquisition. Reuse-only performs no network
transfer, selective acquisition fetches only missing assets, and the four-band
estimate is exactly four bytes per requested NAIP pixel. Final publication is
transactional: failed work remains a bounded failed diagnostic, required
outputs and COGs validate before finalization, checksums are regenerated last,
and receipts contain handoff-relative paths only.

### Publication panel interpretation

| Panel | Interpretation |
| --- | --- |
| Main map | Muted predicted surface classes over natural-color context; thin outlines mark filtered high-confidence disagreement regions. |
| Natural color | Display-only `(R,G,B)` view derived locally from the raw four-band NAIP source. |
| Color infrared | Display-only `(NIR,R,G)` view that makes vigorous vegetation visually prominent. |
| Numeric NDVI | Display-only color rendering of NDVI computed from raw numeric red and NIR bands, with a zero-centered percentile stretch. |
| Predicted classes | Nearest-neighbor rendering of the raw analytical class codes; class 0 remains unknown or uncertain. |
| Confidence and CDL audit | Maximum model probability beside agreement states, weak-label metrics, coverage, and class-area inventory. |

Natural-color, CIR, and NDVI styling are interpretive views, not model inputs.
The publication renderer preserves the predicted class codes and does not
rewrite the classification, confidence, or agreement COGs. A disagreement
outline indicates where a confident prediction differs from a usable CDL weak
label; it does not establish which source is correct.

### Finalized inspection and publication-only derivation

`fr inspect <handoff> --verbose` distinguishes the initial asset plan from the
completed execution. For each finalized asset it reports initial readiness,
planned action, acquired or reused execution action, final verification,
handoff-relative artifact path, source ID, network bytes, and checksum. When
classification analysis artifacts are present, the same command summarizes the
backend, mapping, sample counts, weak-label metrics, confidence/coverage audit,
and predicted hectares without importing scikit-learn.

The publication renderer preserves raw NAIP acquisition evidence and interprets
supported Unix epoch seconds or milliseconds as deterministic UTC calendar
dates. Malformed values remain visible and are explicitly marked unparsed.
Numeric NDVI uses a display-only, zero-centered 2nd–98th-percentile stretch with
negative, near-zero, and positive legend anchors. Predicted classes remain the
raw nearest-neighbor analytical codes.

High-confidence disagreement is rendered as a thin outer line with a subtle
dark halo. The configured minimum-region filter is display-only; it does not
alter the classification, confidence, or agreement COGs. Publication receipts
record the mode, region threshold, line width, halo width, and
`analytical_rasters_modified: false`.

To rerender an existing finalized classification handoff without network access
or model execution:

```bash
python scripts/derive_classification_publication.py \
  outputs/handoffs/<source-classification-handoff>
```

The command creates a new transactional handoff, verifies that every
analytical hash is unchanged, records the source handoff and manifest hash,
sets network bytes to zero, and regenerates checksums. It never mutates the
historical source handoff.

## Explicit coherent temporal repair

Classification planning uses Sauce Time contracts specialized for an
imagery/weak-label pair. When the requested pair is unavailable it reports one
of `EXACT_TIME_AVAILABLE`, `AWAITING_TEMPORAL_SELECTION`, or
`NO_COHERENT_ALTERNATIVE`. Coherent same-year candidates rank before
imagery-only candidates; the latter remain available when the operator accepts
temporally mismatched weak supervision.

Selection is explicit and produces
`fasterraster.classification-temporal-resolution/v1` with original and resolved
years plus search, alternatives, and resolution hashes. The original workfile
is unchanged, and selection authorizes no raster acquisition. Noninteractive
planning, explanation, or cooking can resolve a pair directly:

```bash
fr plan study.fr.md --resolve-imagery-year 2019 --resolve-cdl-year 2019
fr cook study.fr.md --resolve-imagery-year 2019 --resolve-cdl-year 2019
```

Both arguments are required together. For the Greeley 2023/2023 case, an
available 2019/2019 pair is the preferred coherent replacement and is applied
only after one explicit approval or the paired CLI arguments.

## Interactive contract repair

`fr cook` can repair a bounded source-contract failure for this workflow when
NAIP is unavailable for the requested imagery year, acquisition-date range, or
location. It never substitutes data automatically. The prompt identifies the
failed `naip_multispectral` asset, shows source-backed alternatives when they
are known, validates only the replacement field, recompiles the normal study
plan, recalculates cache matches and byte ceilings, shows the original and
resolved request, and requires confirmation before the executor performs any
source request. The executor validates catalog coverage before raster transfer;
if the replacement is still unsupported, no raster is transferred and the
bounded prompt resumes.

Prompting is enabled by default only when both standard input and standard
output are interactive terminals. Use `--interactive` to opt in when terminal
detection is unavailable, or `--non-interactive` to require fail-closed
behavior:

```bash
fr cook study.fr.md --interactive
fr cook study.fr.md --non-interactive
```

Redirected input, CI, scheduled jobs, HPC jobs, `--json`, and other
noninteractive execution never wait for input. They return the structured
source-coverage failure. `--interactive` and `--json` cannot be combined.
Entering `q`, reaching end-of-input, exceeding the bounded invalid-attempt
limit, rejecting a temporal mismatch, or declining final confirmation cancels
without starting source access for the proposed replacement.

An unavailable-year repair resembles:

```text
Source resolution blocked

Asset: NAIP multispectral imagery
Requested year: 2022
Reason: no compatible imagery was found

Source-reported compatible years:
  [1] 2021
  [2] 2023
  [3] Enter another year
  [q] Cancel
```

The ranked choices distinguish a coherent imagery/CDL replacement from an
imagery-only replacement. A coherent choice moves both years; an imagery-only
choice preserves the workfile CDL year. If those years differ, FasterRaster
displays a prominent warning and requires a separate explicit acceptance.
Receipts describe the result as temporally
mismatched weak supervision; they do not claim that the replacement imagery
represents the originally requested year. Date-range recovery asks for start
and end separately in `YYYY-MM-DD` form and rejects malformed, cross-year, or
inverted ranges.

Location recovery accepts either the normal explicit
`west,south,east,north` bbox or a point and metric buffer:

```text
Location recovery options:
  [1] Enter replacement bbox
  [2] Create location from point and buffer
  [q] Cancel

Center longitude (longitude first): -83.0123
Center latitude: 39.9987
Buffer distance: 2.0
Unit:
  [1] meters
  [2] kilometers
  [3] miles
Shape:
  [1] square
  [2] circle
```

Coordinates are always longitude first, then latitude. Supported units are
meters, kilometers, and miles. For a square, the entered distance is the
half-width and half-height, so the full side is twice that value. For a circle,
it is the radius. FasterRaster constructs the geometry in a point-centered
metric azimuthal-equidistant CRS and transforms it to EPSG:4326; it never
pretends that degrees are meters. Circles use a fixed 128-segment polygon.
Invalid coordinates, non-finite or nonpositive distances, unsupported
units/shapes, distances above the bounded 500 km limit, unsafe high latitudes,
and antimeridian-crossing results fail clearly and re-prompt.

A square is axis-aligned and remains a true square in its local metric
construction CRS, and its source-request bbox is the geographic envelope
derived from that square. A
circle remains a true circular analysis AOI, while its rectangular envelope is
sent to bbox-only source services. Projected square edges and geographic
envelope edges are deterministically densified before transformation. Pixels in
an envelope but outside the generated AOI are masked from training, inference,
confidence, agreement, previews, metrics, coverage counts, and class-area
inventories and analytical valid-coverage counts. The prompt warns about this
distinction and reports the excluded envelope-only area.

The repair is run-scoped: the original workfile is never modified. The final
handoff records the original request, resolved request, failure type, evidence,
alternatives, confirmations, original and resolved plan hashes, temporal
mismatch, point/buffer construction, analysis AOI, request envelope, areas,
geometry hash, and intervention ID. `interventions.jsonl`, the asset plan,
manifest, recipe receipt, training/model receipts, and publication receipt make
the human repair visible rather than hiding it. Publication-only rerendering
reuses the recorded imagery year, CDL year, AOI mask, and intervention
reference.

Repair is available to both the V3 classification audit and its additive
Unreleased V4 index-guided workflow. There is no place-name or address
geocoding, arbitrary polygon drawing, cloud optimization, arbitrary resolution
repair, or automatic workfile write-back.

## Workfile and bounded examples

Create and inspect the built-in study:

```bash
fr templates show ag-naip-classification
fr init study.fr.md --template ag-naip-classification \
  --name "classification audit" \
  --bbox -112.05 33.40 -112.049 33.401 \
  --years 2023
fr validate study.fr.md
fr plan study.fr.md --offline
fr explain study.fr.md --offline
```

The offline synthetic acceptance fixture is exercised with:

```bash
python -m pytest -q \
  tests/test_ag_classification.py \
  tests/test_ag_classification_acquisition.py
```

For an opt-in live probe, use only a tiny mixed agricultural/suburban AOI and
keep `limits.maximum_download_mb` at or below 75. Run `fr plan` first, confirm
the estimated transfer and same-year source evidence, then run `fr cook`.
Never enlarge the AOI or ceiling merely to force a successful canary, and never
generalize national performance from one local result.
