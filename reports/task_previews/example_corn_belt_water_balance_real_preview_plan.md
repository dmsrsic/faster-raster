# Real Preview Plan example_corn_belt_water_balance

- Network run: `False`
- Real fetch attempted: `False`
- Max bytes/source: `65536`

## Source plan
Canonical source ids are preserved in JSON; visual labels are display-only.

| Source | Status | Render kind | Warning |
| --- | --- | --- | --- |
| `prism_daily_ppt_static_zip` | `semantic_fallback` | `semantic_fallback` | `archive_requires_explicit_include_archives` |
| `cdl_arcgis_tiny_export` | `supported_real_preview` | `real_raster` | `requires --allow-network to fetch tiny CDL preview` |
| `usgs_3dep_dem` | `adapter_needed` | `semantic_fallback` | `no_safe_tiny_dem_endpoint_yet` |
