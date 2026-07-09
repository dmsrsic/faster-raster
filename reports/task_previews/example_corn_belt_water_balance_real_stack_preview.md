# Real Data Stack Preview example_corn_belt_water_balance

- PNG: `reports/task_previews/example_corn_belt_water_balance_real_stack_preview.png`
- Network run: `True`
- Real raster data rendered: `True`
- Preview layout: `clean`
- Recommended next action: `inspect_cache_image`

## Typed visibility ledger
| Source | Role | Visible | Transparent | Status |
| --- | --- | ---: | ---: | --- |
| `cdl_arcgis_tiny_export` | `real_base` | 86% | 14% | `real_raster_rendered` |
| `prism_daily_ppt_static_zip` | `climate_signal` | 20% | 80% | `semantic_fallback` |
| `usgs_3dep_dem` | `terrain_context` | 18% | 82% | `adapter_needed` |
| `copernicus_sentinel2_l2a_cdse_stac` | `credential_gated_context` | 16% | 84% | `stac_discovered_no_pixels` |

Sentinel STAC metadata discovered; no Sentinel pixels downloaded.

## Source results
| Source | Attempted | Rendered | Kind | Bytes | Unique | Dominant | Status | Warning |
| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |
| `prism_daily_ppt_static_zip` | `False` | `False` | `semantic_fallback` | 0 | None | None | `semantic_fallback` | `archive_requires_explicit_include_archives` |
| `cdl_arcgis_tiny_export` | `True` | `True` | `real_raster` | 74523 | 26 | 0.256500244140625 | `real_raster_rendered` | `` |
| `usgs_3dep_dem` | `False` | `False` | `semantic_fallback` | 0 | None | None | `adapter_needed` | `no_safe_tiny_dem_endpoint_yet` |
