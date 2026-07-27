# PRISM daily precipitation

FasterRaster treats PRISM daily precipitation as a staged, fail-closed product pipeline rather than as an opaque download.

## Implemented contract

For a declared date, the current public pipeline:

1. builds the deterministic official PRISM daily ZIP URL;
2. performs a bounded HTTP probe and records live source evidence;
3. requires explicit approval before complete-object transfer;
4. stores the complete ZIP content-addressably with a whole-object SHA-256 receipt;
5. validates safe archive paths, supported compression, expansion ceilings, member CRCs, and exactly one date-matched GeoTIFF;
6. streams only the selected GeoTIFF into a bounded staging path;
7. verifies extracted byte count and CRC before content-addressed raster promotion;
8. opens the promoted raster with Rasterio while suppressing accidental adjacent-sidecar discovery;
9. validates the expected CONUS 4 km grid, EPSG:4269 CRS, float32 single-band structure, -9999 nodata, internal tiling, overviews, compression, and GDAL-declared COG layout;
10. computes deterministic full-raster pixel accounting and statistics; and
11. cross-checks the raster against the PRISM projection, statistics, processing-information, auxiliary, and FGDC metadata members;
12. plans an explicit AOI source window and target grid; and
13. executes a nodata-aware deterministic harmonized COG with a content-bound receipt.

The decoded-raster receipt binds the source archive hash and inventory hash to the selected member CRC, extracted raster hash, raster profile hash, and content-addressed paths. Receipt verification reopens both artifacts and recomputes the archive and raster profiles.

## Scientific interpretation

The implemented product is daily total precipitation in millimetres for the conterminous United States. Provider metadata and embedded tags are preserved as evidence. FasterRaster does not reinterpret PRISM's interpolation method, convert the values to independent ground truth, or infer uncertainty not supplied by the provider.

## Safety and network policy

Routine tests are offline and use synthetic PRISM-shaped archives and COGs. The real network canary is explicit and opt-in:

```sh
fr-prism-canary \
  --execute \
  --allow-network \
  --allow-materialization
```

The canary uses separate object and total-transfer ceilings, writes into an isolated workspace, and does not publish a study result.

## Normal environmental workflow

The bounded `prism_dem_ndvi_correlation_audit` workfile workflow expands a
declared precipitation window into one guarded daily PRISM contract per day,
harmonizes each daily depth surface to a common EPSG:5070 grid, and sums the
daily depths. It then compares that accumulated surface with common-grid USGS
3DEP elevation and NAIP-derived NDVI. Same-year USDA CDL is retained as crop
context. See [PRISM × DEM × NDVI correlation audit](prism-dem-ndvi-correlation.md).

## Remaining roadmap

Remaining work is broader reuse and publication generalization rather than the
first normal cook path:

1. reusable multi-date climate aggregation outside the correlation workflow;
2. reuse-only replay for all daily product and derived-output stages;
3. additional precipitation-safe aggregation and uncertainty contracts; and
4. generic multi-source publication templates beyond this bounded audit.

PRISM is now a verified decoded and harmonized input for the declared normal
environmental-correlation workflow. That support does not imply every recipe or
source combination is automatically executable.
