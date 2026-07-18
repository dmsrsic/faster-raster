---
schema_version: fasterraster.work/v2
name: meridian-cdl-development-change
workflow: human_development_change

area:
  bbox:
    - -116.410
    - 43.540
    - -116.380
    - 43.570

epochs:
  - year: 2008
  - year: 2016
  - year: 2021

sources:
  policy: service_discovered
  source_id: usda_nass_cdl_imageserver
  mapping_id: usda_cdl_development_proxy_v1
  context_imagery_source_id: usgs_naip_imageserver
  context_year: 2021

data:
  reuse: auto
  allow_network: true

processing:
  target_crs: EPSG:5070
  resolution_m: 30
  window_size: 512
  service_tile_size: 2048

limits:
  maximum_download_mb: 100

outputs:
  preview: true
  include_context_imagery: true
  open_when_complete: false
---

# Meridian USDA CDL-derived mapped development proxy change

Compare exact-year raw USDA Cropland Data Layer classes over the Meridian,
Idaho AOI. Classes 121–124 are interpreted as ordered mapped-development proxy
states. CDL is crop-focused, and non-agricultural changes are not authoritative
Annual NLCD change or evidence of population, economics, construction dates,
occupancy, cadastral approval, or causal urban expansion.
