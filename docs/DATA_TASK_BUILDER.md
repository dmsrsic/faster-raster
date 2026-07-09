# Data Task Builder + Semantic Stack Preview

A FasterRaster data task is a local YAML planning object that describes a study AOI, target grid, time range, themes, and source IDs. It is not a downloader and it does not render raster pixels.

## Commands

```bash
faster-raster task new --id example_corn_belt_water_balance --name "Corn Belt water balance demo" --bbox=-83.2,39.8,-83.19,39.81 --bbox-crs EPSG:4326 --target-crs EPSG:5070 --resolution-m 30 --years 2023 --theme precipitation --theme landcover --theme elevation --source prism_daily_ppt_static_zip --source cdl_arcgis_tiny_export --source usgs_3dep_dem
faster-raster task list --plain
faster-raster task show example_corn_belt_water_balance --plain
faster-raster task validate example_corn_belt_water_balance --plain
faster-raster task preview example_corn_belt_water_balance --plain
faster-raster stack preview example_corn_belt_water_balance --plain
```

## Outputs

- `tasks/TASK_ID.yaml`
- `reports/task_builder/TASK_ID_task.json`
- `reports/task_builder/TASK_ID_task.md`
- `reports/task_previews/TASK_ID_stack_preview.png`
- `reports/task_previews/TASK_ID_stack_preview.json`
- `reports/task_previews/TASK_ID_stack_preview.md`

## Safety

Task preview is semantic/planning only. It uses local atlas/report files for source status, never contacts endpoints, never reads credentials, and never downloads or extracts source data.

The stack preview feeds future adapter-backed live cooking by making AOI, CRS, time, themes, source readiness, warnings, and output artifact paths visible before any runtime execution.

## Real-data preview dry run

```bash
faster-raster task preview-real example_corn_belt_water_balance --plain
faster-raster stack preview-real example_corn_belt_water_balance --plain
```

Live real-data preview remains opt-in with `--allow-network`; see `docs/REAL_DATA_PREVIEW.md`.

Real preview diagnostics example:

```bash
faster-raster task preview-real example_corn_belt_water_balance --allow-network --max-bytes-per-source 2500000 --preview-size 512 --debug-artifacts --plain
```

Dry-run remains the default when `--allow-network` is omitted.


## v0.5.8 Credential-Gated Sentinel Planning

Tasks may include `copernicus_sentinel2_l2a_cdse_stac` as a credential-gated source. The example `example_corn_belt_water_balance_plus_sentinel` keeps PRISM, CDL, USGS 3DEP, and Sentinel-2 together for semantic planning without requiring credentials.

Preview commands remain dry-run by default. Sentinel-2 CDSE is represented as a planned credential-gated layer; no live STAC search or product download is performed by task preview.
