---
schema_version: fasterraster.work/v1
name: meridian-mixed-urban-ag-2023
workflow: irrigation-field-structure

area:
  bbox:
    - -116.410
    - 43.540
    - -116.380
    - 43.570

time:
  start: 2023-04-01
  end: 2023-10-31
  crop_year: 2023

sources:
  policy: auto

data:
  reuse: auto

processing:
  resolution_m: 1.0

limits:
  maximum_download_mb: 250

outputs:
  preview: true
  open_when_complete: false
---

# Meridian mixed urban–agricultural study

This study examines irrigation-field structure where expanding urban land uses
meet agricultural fields near Meridian, Idaho. The 2023 year is the initial
candidate and must still pass bounded source-response preflight before live
acquisition; an unavailable year is never silently substituted.

Research notes, observations, citations, and interpretation can be added here.
This prose is never parsed as execution configuration.
