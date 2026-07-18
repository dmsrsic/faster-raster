---
schema_version: fasterraster.work/v2
name: boise-meridian-development-change
workflow: human_development_change

area:
  bbox:
    - -116.410
    - 43.540
    - -116.380
    - 43.570

epochs:
  - year: 1985
    land_cover_path: inputs/Annual_NLCD_LndCov_1985_CU_C1V2.tif
    imperviousness_path: inputs/Annual_NLCD_FctImp_1985_CU_C1V2.tif
  - year: 2005
    land_cover_path: inputs/Annual_NLCD_LndCov_2005_CU_C1V2.tif
    imperviousness_path: inputs/Annual_NLCD_FctImp_2005_CU_C1V2.tif
  - year: 2025
    land_cover_path: inputs/Annual_NLCD_LndCov_2025_CU_C1V2.tif
    imperviousness_path: inputs/Annual_NLCD_FctImp_2025_CU_C1V2.tif

sources:
  policy: pinned
  source_id: usgs_annual_nlcd
  collection: 1
  version: 2
  region: CU

data:
  reuse: auto

processing:
  target_crs: EPSG:5070
  resolution_m: 30
  window_size: 512

limits:
  maximum_download_mb: 50

outputs:
  preview: true
  open_when_complete: false
---

# Boise–Meridian human-development change

This local-pinned study compares mapped Annual NLCD land-cover development
states across ordered epochs. It reports mapped cover transitions and optional
fractional-imperviousness differences. It does not infer population, economic
activity, construction date, causality, or occupancy.
