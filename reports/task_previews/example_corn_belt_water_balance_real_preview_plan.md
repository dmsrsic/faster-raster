# Real Preview Plan example_corn_belt_water_balance

- Network run: `False`
- Real fetch attempted: `False`
- Max bytes/source: `65536`

## Source plan
Canonical source ids are preserved in JSON; visual labels are display-only.

## Typed visibility ledger
| Source | Role | Visible | Transparent | Status |
| --- | --- | ---: | ---: | --- |
| `cdl_arcgis_tiny_export` | `real_base` | 90% | 10% | `supported_real_preview` |
| `prism_daily_ppt_static_zip` | `climate_signal` | 12% | 88% | `semantic_fallback` |
| `usgs_3dep_dem` | `terrain_context` | 12% | 88% | `adapter_needed` |
| `copernicus_sentinel2_l2a_cdse_stac` | `credential_gated_context` | 12% | 88% | `stac_discovered_no_pixels` |

| Source | Status | Render kind | Warning |
| --- | --- | --- | --- |
| `prism_daily_ppt_static_zip` | `semantic_fallback` | `semantic_fallback` | `archive_requires_explicit_include_archives` |
| `cdl_arcgis_tiny_export` | `supported_real_preview` | `real_raster` | `requires --allow-network to fetch tiny CDL preview` |
| `usgs_3dep_dem` | `adapter_needed` | `semantic_fallback` | `no_safe_tiny_dem_endpoint_yet` |
