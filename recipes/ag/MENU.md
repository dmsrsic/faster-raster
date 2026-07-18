# FasterRaster Agricultural Recipe Library


## Pair 02

### crop_class_area_inventory

Creates a crop and land-cover inventory with CDL class counts,
approximate hectares, imagery context, NDVI context, and a verified
handoff package.

Example:

    ./scripts/fr-recipes run crop_class_area_inventory       --bbox=-100.985,38.000,-100.955,38.030       --start 2023-04-01       --end 2023-10-31       --year 2023       --dry-run

### crop_terrain_relationship

Examines crop placement, vegetation condition, field boundaries, and
CDL classification relative to USGS 3DEP terrain context.

Example:

    ./scripts/fr-recipes run crop_terrain_relationship       --bbox=-97.750,38.350,-97.730,38.370       --start 2023-04-01       --end 2023-10-31       --year 2023       --dry-run
