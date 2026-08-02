# Agricultural Recipe Runtime

`scripts/fr-cook-ag` supports the original version-2 agricultural recipes plus
the V3 NAIP–CDL classification audit and the published beta.4 index-guided
hybrid classification audit.

- `crop_vigor_classification` compares natural color, NAIP NDVI, and raw USDA CDL classes.
- `irrigation_field_structure` emphasizes irrigation geometry and field edges.
- `crop_class_area_inventory` produces CDL class counts and approximate hectares.
- `crop_terrain_relationship` adds USGS 3DEP hillshade to the crop/vegetation view.
- `naip_cdl_classification_audit` preserves the beta.3 raw four-band
  weak-supervised broad classifier.
- `naip_cdl_index_hybrid_classification_audit` preserves that broad result and
  applies source-compatible spectral specialist classes.

Each recipe requires a WGS 84 bbox (`west,south,east,north`), growing-season
start and end dates, and an exact CDL year. The recipe declares its assets,
maximum acceptable NAIP pixel size, semantic resampling policies, preview,
inspection focus, execution limits, and required output artifacts. Recipe
schema validation happens before cache inspection or network activity.

## V4 index-guided contract

`AgriculturalRecipeV4` contains explicit general class IDs/count/codes,
persisted index requests, explicit specialist IDs/count/codes/parents,
calibration evidence, user/recommendation/automatic selection mode, bounded
candidate search, and deterministic arbitration. Source compatibility is based
on semantic bands. Four-band NAIP cannot satisfy NDMI or NBR because it lacks
SWIR1/SWIR2.

The existing V3 recipe is unchanged. V4 reuses the same acquisition, caching,
AOI masks, CDL weak labels, broad classifier, transaction, repair, manifest,
inspection, and zero-network publication derivation. See
[`index-guided-classification.md`](index-guided-classification.md).

## Geographic coverage

Agricultural execution has no state-boundary eligibility rule. A requested
bbox must be finite, ordered, within the supported coordinate domains, and
small enough for the configured byte-safety envelope. Acquisition is then
allowed only when each required registered source supplies auditable coverage
evidence for the requested bbox, year, resolution, and asset semantics.

NAIP uses intersecting catalog records for the exact requested year. USDA CDL
checks both the source-reported year inventory and an intersecting catalog
response, while categorical output remains nearest-neighbor. USGS 3DEP is
checked only for recipes that require hillshade, with continuous terrain
resampling kept bilinear. A reachable hostname or successful metadata request
alone is not treated as proof of coverage. Source unavailability, invalid
responses, unavailable years, geographic noncoverage, and export failures are
reported separately and leave no completed-looking handoff.

This is not a claim of global agricultural support. Usable geography is
strongest where the required NAIP, USDA CDL, and USGS services provide
compatible records. Prior Kansas studies remain historical proof cases. The
Meridian workfile is the bounded mixed urban–agricultural transfer study; its
original 2023 request is never silently changed when source preflight requires
a different common year.

## Asset-level resolution

The runtime inventories valid COGs inside completed directories under
`outputs/handoffs`. Raster extent, CRS, pixel size, shape, and nodata evidence
come from `gdalinfo`, rather than filename matching alone. Temporal identity,
semantic type, structural validity, and recipe resolution limits are evaluated
for every asset.

One cook can reuse assets from several handoffs. Exact matching coverage is
reused directly. A larger raster that contains the requested bbox is cropped
locally with the recipe's declared resampling method. Partial overlap is never
treated as complete coverage; the current runtime acquires the complete missing
asset instead of constructing a partial mosaic. Categorical CDL assets always
use nearest-neighbor resampling, imagery uses cubic resampling, and terrain uses
bilinear resampling.

The reuse modes are:

- `--reuse auto`: reuse every compatible asset, crop/reproject locally when
  needed, and acquire only assets still missing or incompatible.
- `--reuse only`: prohibit acquisition and fail transactionally if any required
  asset cannot be resolved locally.
- `--reuse never`: ignore cached source assets and acquire all recipe assets
  within the normal tile and byte limits.

The runtime writes a deterministic `asset_plan.json` before resolution. A
successful handoff contains the resolved COGs under `data/`, a recipe-specific
4K dashboard and class inventory under `preview/`, a complete per-asset
`recipe_receipt.json`, and SHA256 checksums. Final handoffs are published under
`outputs/handoffs` only after resolved assets and outputs validate. Failed
transactions retain a hidden `.failed-*` diagnostic directory and never appear
as completed handoffs.

## Examples

Fresh acquisition:

```bash
./scripts/fr-cook-ag \
  --recipe crop_terrain_relationship \
  --reuse never \
  --name example_ag_fresh_2023 \
  --bbox=-98.905,38.300,-98.875,38.330 \
  --start 2023-04-01 \
  --end 2023-10-31 \
  --cdl-year 2023
```

Zero-download reuse:

```bash
./scripts/fr-cook-ag \
  --recipe crop_vigor_classification \
  --reuse only \
  --name example_ag_reuse_2023 \
  --bbox=-100.985,38.000,-100.955,38.030 \
  --start 2023-04-01 \
  --end 2023-10-31 \
  --cdl-year 2023
```

Selective acquisition uses the same public command. If compatible natural
color and CDL assets exist but NDVI does not, this requests only NDVI:

```bash
./scripts/fr-cook-ag \
  --recipe crop_vigor_classification \
  --reuse auto \
  --name example_ag_selective_2023 \
  --bbox=-100.980,38.005,-100.979,38.006 \
  --start 2023-04-01 \
  --end 2023-10-31 \
  --cdl-year 2023 \
  --max-total-bytes 250000000 \
  --service-tile-size 400
```

Inspect `asset_plan.json` for the pre-execution decision and rationale for each
asset. Inspect the JSON recipe receipt for source handoff or service contract,
source/output CRS and resolution, crop/reprojection state, resampling,
downloaded/reused bytes, checksum, validation, generated outputs, and final
status. The dashboard contains a shorter human-readable summary.
