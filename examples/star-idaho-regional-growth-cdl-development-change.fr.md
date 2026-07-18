---
schema_version: fasterraster.work/v2
name: star-idaho-regional-growth-cdl-development-change
workflow: human_development_change
area:
  bbox:
  - -116.579057
  - 43.63716
  - -116.420943
  - 43.74784
epochs:
- year: 2008
- year: 2016
- year: 2021
sources:
  policy: service_discovered
  source_id: usda_nass_cdl_imageserver
  mapping_id: usda_cdl_development_proxy_v1
data:
  reuse: auto
  allow_network: true
processing:
  target_crs: EPSG:5070
  resolution_m: 30
  window_size: 512
  service_tile_size: 2048
limits:
  maximum_download_mb: 25
outputs:
  preview: true
  include_context_imagery: false
  open_when_complete: false
---

# Star, Idaho regional growth-front development proxy

This approximately ten-times-expanded study screens the wider Star-area
urban-agricultural interface for mapped development-proxy change.

The analytical grid remains EPSG:5070 at 30 metres. USDA CDL classes 121
through 124 are interpreted as ordered mapped-development proxy states.
All categorical processing uses nearest-neighbour resampling.

This first regional pass intentionally omits full-resolution NAIP context to
keep network input small and isolate spatial scaling. Its endpoint and interval
change products will be used to select a smaller high-change focus area for a
later full-resolution NAIP investigation.

The result is not authoritative Annual NLCD change and does not establish
population growth, construction timing, occupancy, economic activity, or
causality.
