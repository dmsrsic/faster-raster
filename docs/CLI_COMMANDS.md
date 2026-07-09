# FasterRaster CLI Commands

Professional commands remain stable:

```bash
faster-raster sources list
faster-raster sources tree
faster-raster sources show prism_daily_ppt_static_zip
faster-raster stack summary
faster-raster unlocks next
faster-raster probe atlas gridmet_daily --dry-run
faster-raster help style
faster-raster explore
```

Kitchen aliases:

```bash
faster-raster pantry
faster-raster sauces
faster-raster sauce gridmet_daily
faster-raster reigns
faster-raster buckets
faster-raster goods
faster-raster bads
faster-raster recipe
faster-raster batcher
faster-raster dips gridmet_daily --dry-run
faster-raster menu lingo
```

Output modes:

```bash
faster-raster sources list --plain
faster-raster sources list --json
faster-raster sources list --lingo kitchen
```

JSON output keeps canonical field names such as `source_id`, `provider`, `credential_requirement`, and `promotion_status`.


## User toggles and cook planning

```bash
faster-raster toggles show
faster-raster toggles explain
faster-raster cook plan
faster-raster cook queue
faster-raster cook dip gridmet_daily --dry-run
faster-raster cook propose gridmet_daily
```

Kitchen aliases:

```bash
faster-raster knobs
faster-raster knobs explain
faster-raster cookplan
faster-raster queue
faster-raster cookdip gridmet_daily --dry-run
faster-raster cookproposal gridmet_daily
```

Cook commands are planning/proposal surfaces. They do not edit the runtime registry and live dips remain opt-in.

## Source scope and endpoint readiness

```bash
faster-raster source-scope --plain
faster-raster scope --plain
faster-raster cook endpoints --plain
faster-raster cook endpoints --wide --plain
faster-raster cook endpoints --ready-only --plain
faster-raster endpoints --plain
```

Use `python3 -m json.tool` for JSON validation in WSL environments.

## Data task builder and stack preview

```bash
faster-raster task new --id example_corn_belt_water_balance --name "Corn Belt water balance demo" --bbox=-83.2,39.8,-83.19,39.81 --bbox-crs EPSG:4326 --target-crs EPSG:5070 --years 2023 --theme precipitation --theme landcover --source prism_daily_ppt_static_zip --source cdl_arcgis_tiny_export
faster-raster task list --plain
faster-raster task show example_corn_belt_water_balance --plain
faster-raster task validate example_corn_belt_water_balance --plain
faster-raster task preview example_corn_belt_water_balance --plain
faster-raster stack preview example_corn_belt_water_balance --open
```

Task previews are static local PNG artifacts. They do not download rasters, contact endpoints, or render live data. For negative bbox coordinates, use Click's reliable `--bbox=-83.2,...` form.

See `docs/DATA_TASK_BUILDER.md` for the local data task and semantic stack preview workflow.

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

## CDL sample verification flags

```bash
faster-raster task preview-real example_corn_belt_water_balance --allow-network --max-bytes-per-source 2500000 --max-pixels 262144 --preview-size 512 --sample-grid-size 5 --preview-expand-factor 10 --cdl-render-mode auto --debug-artifacts --plain
```

Options:

- `--cdl-verify-samples / --no-cdl-verify-samples` controls CDL identify sampling when the service PNG is single-color.
- `--sample-grid-size` sets a 1..7 sample grid; `--grid-size` is an alias.
- `--preview-expand-factor` expands only the preview fetch bbox around the task AOI centroid. It does not mutate task YAML.
- `--cdl-render-mode` accepts `auto`, `service_png`, `manual_samples`, or `service_tiff`.


## Copernicus/CDSE Commands

`faster-raster copernicus auth-check --plain` checks whether CDSE environment variables are present without printing token values.

`faster-raster copernicus sentinel search-plan TASK_ID --plain` writes a dry-run STAC search plan to `reports/copernicus/`. Use `--json` for the same plan as JSON output. The command does not make a network request.

Supported local environment variables are `CDSE_ACCESS_TOKEN`, `CDSE_REFRESH_TOKEN`, `CDSE_USERNAME`, `CDSE_PASSWORD`, and `CDSE_CLIENT_ID`. Keep them in your shell or an ignored local file such as `configs/auth_profiles.local.yaml`; do not commit credentials.
