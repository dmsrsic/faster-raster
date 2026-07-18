---
schema_version: fasterraster.work/v2
name: meridian-cdl-development-change-reuse-only
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
  reuse: only
  allow_network: false

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

# Meridian strict zero-network CDL proxy replay

Reproduce the finalized three-epoch proxy analysis entirely from verified,
immutable cached source assets. No source metadata, export, or raster transfer
is permitted in this execution.
