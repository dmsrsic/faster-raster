---
schema_version: fasterraster.work/v1
name: champaign-prism-dem-ndvi-correlation-2023
workflow: prism-dem-ndvi-correlation-audit

area:
  bbox:
    - -88.55
    - 39.75
    - -87.75
    - 40.45

time:
  start: 2023-04-01
  end: 2023-10-31
  crop_year: 2023

sources:
  policy: pinned
  natural_imagery: usgs_naip_imageserver
  crop_classes: usda_nass_cdl_imageserver
  terrain: usgs_3dep_imageserver
  precipitation: prism_daily_ppt_static_zip

data:
  reuse: never
  allow_network: true
  allow_materialization: true

processing:
  resolution_m: 4000
  service_tile_size: 1800

limits:
  maximum_download_mb: 750

outputs:
  preview: true
  open_when_complete: false

correlation:
  precipitation_start: 2023-06-09
  precipitation_end: 2023-06-15
  maximum_precipitation_days: 7
  minimum_valid_cells: 12
  naip_analysis_resolution_m: 30
  elevation_resolution_m: 30
---

# Champaign regional PRISM × DEM × NDVI audit

This study tests exploratory spatial association among seven-day accumulated
PRISM precipitation, USGS 3DEP elevation, and NAIP-derived NDVI on a common
4 km EPSG:5070 grid. USDA CDL is included as crop-context evidence but is not
used as a predictor in the reported correlation coefficients.

The output is not a causal precipitation-response model, an independent
accuracy assessment, or an iid significance test.
