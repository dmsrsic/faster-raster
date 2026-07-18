---
schema_version: fasterraster.work/v1
name: meridian-mixed-urban-ag-2021
workflow: irrigation-field-structure

area:
  bbox:
    - -116.410
    - 43.540
    - -116.380
    - 43.570

time:
  start: 2021-04-01
  end: 2021-10-31
  crop_year: 2021

sources:
  policy: auto

data:
  reuse: auto

processing:
  resolution_m: 1.0
  service_tile_size: 400

limits:
  maximum_download_mb: 250

outputs:
  preview: true
  open_when_complete: false
---

# Meridian mixed urban–agricultural fallback study

This separate workfile preserves the explicitly selected 2021 fallback for the
Meridian, Idaho urban–agricultural fringe. Bounded live preflight found no
intersecting NAIP records for the original 2023 request, reported 2021 as the
available intersecting NAIP year, and confirmed that USDA CDL also supplies
2021 coverage. The original 2023 workfile remains unchanged.

Research notes, observations, citations, and interpretation can be added here.
This prose is never parsed as execution configuration.
