# Supported sources

Support is workflow-specific; a source entry does not imply every dataset or geography is executable.

| Source or family | Beta role | Routine CI |
|---|---|---|
| USDA NASS Cropland Data Layer | Live exact-year acquisition and mapped-development/crop context | Offline mocks and contracts only |
| USGS NAIP imagery | Exact-year visual context and raw four-band red/green/blue/NIR analytical source for classification/index recipes | Offline mocks and contracts only |
| USGS 3DEP | Bounded terrain context when a shipped recipe requires it | Offline mocks and contracts only |
| CHIRPS, gridMET, TerraClimate, WorldClim | Bounded static HTTP range planning/probe contracts | Offline fixtures only |
| PRISM daily precipitation ZIP | Deterministic official daily path, bounded live probe, guarded complete-object materialization, and safe COG/ancillary archive inventory | Offline synthetic ZIP/profile tests; live canary is explicit and opt-in |
| Copernicus CDSE | Credential-gated planning/readiness scaffolding | Offline contract tests only |

Routine CI never contacts USDA, USGS, ArcGIS, PRISM, STAC, THREDDS, or other raster services.

PRISM daily packages are treated as ZIP containers whose primary raster is a date-matched GeoTIFF accompanied by provider metadata and ancillary files. The current product profile validates archive paths, compression bounds, member CRCs, naming, and exactly one primary raster without extraction. It does not yet claim successful Rasterio decoding, internal COG conformance, spatial subsetting, or target-grid harmonization.

The opt-in `fr-prism-canary` command can create a bounded probe and a guarded full-object materialization receipt in an isolated workspace. Planning requires `--allow-network`; complete-object execution requires `--execute --allow-network --allow-materialization`.

## Spectral-band compatibility

Current executable index calculation uses raw four-band NAIP with semantic band
order red, green, blue, NIR. Capability evidence records actual band count and
order, dtype, scale/offset, data level, nodata/mask behavior, source ID, and
source hash. Compatibility is based on semantic bands, not band count alone.

NDVI, GNDVI, the precisely named green–NIR water/wet-surface proxy, visible
indices, and compatible custom expressions can run from this source. NDMI
requires SWIR1 and NBR requires SWIR2; both fail before acquisition/analysis
when requested from ordinary NAIP. FasterRaster does not ship a new Sentinel or
Landsat adapter in this development work.

The USDA states that CDL is public domain and free to redistribute. Source data still require accurate attribution and interpretation; retain the receipts shipped with each result.

## Exact-year behavior

FasterRaster does not silently substitute imagery years. If coverage is unavailable, the error reports intersecting available years when the source provides them. Edit the workfile to select another year explicitly, then validate and plan again.
