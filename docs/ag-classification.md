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
confidence. Predictions below `0.60` become class 0; valid-pixel confidence is
stored as rounded percent `1–100`, while invalid pixels use 0. The optional
nine-pixel sieve is currently recorded as disabled to preserve invalid gaps and
exact blockwise results; pre/post counts are still reported.

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
summary; and class-area inventory. The 3840×2160 preview contains natural
color, CIR, numeric NDVI, predicted classes, and confidence/agreement panels.
Its main map uses a muted prediction overlay and high-confidence disagreement
outlines—not universal CDL pixel boundaries.

The asset plan is written before acquisition. Reuse-only performs no network
transfer, selective acquisition fetches only missing assets, and the four-band
estimate is exactly four bytes per requested NAIP pixel. Final publication is
transactional: failed work remains a bounded failed diagnostic, required
outputs and COGs validate before finalization, checksums are regenerated last,
and receipts contain handoff-relative paths only.

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
