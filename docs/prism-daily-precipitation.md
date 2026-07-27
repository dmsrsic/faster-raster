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
11. cross-checks the raster against the PRISM projection, statistics, processing-information, auxiliary, and FGDC metadata members.

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

## Remaining roadmap

The next product stages are:

1. transform a requested AOI into the source CRS and compute an exact source window;
2. subset without silently expanding or changing the requested footprint;
3. define target-grid harmonization, including snapping, resolution, nodata, and precipitation-safe resampling rules;
4. write a deterministic harmonized COG with checksums and provenance;
5. expand bounded date ranges into one source contract per day;
6. connect PRISM to an ordinary `.fr.md` workfile and cook lifecycle; and
7. validate reuse-only replay and publication handoff behavior.

Until those stages are implemented, PRISM is a verified decoded source product, not yet a general harmonized cook input.
