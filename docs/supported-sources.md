# Supported sources

Support is workflow-specific; a source entry does not imply every dataset or geography is executable.

The status and execution columns below are generated from the canonical public
capability registry. Routine CI uses offline mocks, fixtures, and contracts; it
does not contact providers.

<!-- BEGIN GENERATED SOURCE CAPABILITY MATRIX -->
| Capability | Status | Plan | Preview | Materialize | Analyze | Public execution |
|---|---:|:---:|:---:|:---:|:---:|---|
| USDA NASS Cropland Data Layer | `released` | yes | yes | yes | yes | `exact_year_workflows` |
| USGS NAIP imagery | `released` | yes | yes | yes | yes | `shipped_agricultural_workflows` |
| USGS 3DEP | `released` | yes | yes | yes | yes | `recipe_bounded` |
| PRISM daily precipitation ZIP | `released` | yes | yes | yes | yes | `guarded_bounded` |
| CHIRPS, gridMET, TerraClimate, and WorldClim | `experimental` | yes | yes | no | no | `bounded_probe_and_fixture_paths` |
| Copernicus Data Space STAC | `experimental` | yes | no | no | no | `resolver_required` |
<!-- END GENERATED SOURCE CAPABILITY MATRIX -->

The four declarative Source Pack families—`static_https_template`,
`arcgis_imageserver`, `stac_search`, and `verified_local_raster`—have offline
family validation and frozen planning contracts. This does not make every
provider executable. The capability registry and each pack's provider-evidence,
temporal, credential, and host-boundary states remain authoritative. Use the
[Flavortown Sauce Wizard workflow](flavortown-wizard.md) to author from official
evidence, then validate with the public CLI.


Routine CI never contacts USDA, USGS, ArcGIS, PRISM, STAC, THREDDS, or other raster services.

PRISM daily packages are treated as ZIP containers whose primary raster is a date-matched GeoTIFF accompanied by provider metadata and ancillary files. The archive profile validates paths, compression bounds, member CRCs, naming, and exactly one primary raster. The decoded-raster stage then streams only that selected member, verifies its declared size and CRC, promotes it content-addressably, opens it with Rasterio in sidecar-isolated mode, validates the declared COG layout, and cross-checks grid, projection, nodata, statistics, units, date, and bounds against the provider sidecars. Spatial subsetting and target-grid harmonization are implemented for the guarded PRISM product path. The normal `prism_dem_ndvi_correlation_audit` workflow uses these contracts to build a bounded common-grid environmental association handoff.

The opt-in `fr-prism-canary` command can create a bounded probe, guarded full-object materialization receipt, decoded-raster receipt, and deterministic raster profile in an isolated workspace. Planning requires `--allow-network`; complete-object and raster execution requires `--execute --allow-network --allow-materialization`. See [PRISM daily precipitation](prism-daily-precipitation.md) for the staged contract and remaining roadmap.

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


## Environmental correlation workflow

The normal-workfile `prism_dem_ndvi_correlation_audit` workflow combines accumulated PRISM precipitation, USGS 3DEP elevation, and numeric NDVI derived from raw four-band NAIP. Same-year CDL is retained as crop context. Reported statistics are exploratory spatial associations without causal or ordinary iid significance claims. See [PRISM × DEM × NDVI correlation audit](prism-dem-ndvi-correlation.md).
