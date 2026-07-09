# Task example_corn_belt_water_balance

- Name: Corn Belt water balance demo
- Validation status: `PASS`
- AOI bbox: `[-83.2, 39.8, -83.19, 39.81]`
- AOI CRS: `EPSG:4326`
- Target CRS: `EPSG:5070`
- Years: `[2023]`
- Dates: `[]`
- Themes: `precipitation, landcover, elevation`

## Sources
- `prism_daily_ppt_static_zip`: `verified_now`
- `cdl_arcgis_tiny_export`: `verified_now`
- `usgs_3dep_dem`: `adapter_needed`

## Warnings
- usgs_3dep_dem is adapter_needed

## Next commands
```bash
faster-raster task validate example_corn_belt_water_balance --plain
faster-raster task preview example_corn_belt_water_balance --plain
```
