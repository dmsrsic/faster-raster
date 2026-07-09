# Real Data Task Preview

`faster-raster task preview-real` is an opt-in, bounded preview path for tiny real-data checks where a source has a safe preview strategy. It is not a production downloader and it does not implement the future `static_http_range` adapter.

Semantic previews remain the default:

```bash
faster-raster task preview example_corn_belt_water_balance --plain
```

Real-data preview dry-run, no network:

```bash
faster-raster task preview-real example_corn_belt_water_balance --plain
faster-raster stack preview-real example_corn_belt_water_balance --json
```

Opt-in bounded live run for a user to execute manually later:

```bash
faster-raster task preview-real example_corn_belt_water_balance --allow-network --max-bytes-per-source 2500000 --plain
```

Safety defaults:

- no network without `--allow-network`
- byte cap per source
- tiny AOI preview only
- no credentials
- no registry or atlas mutation
- PRISM archives are skipped unless `--include-archives`, and v0.5.5 still does not extract archives
- 3DEP DEM tile downloads are skipped because no safe tiny DEM endpoint exists yet

Supported source behavior in v0.5.5:

| Source | Behavior |
| --- | --- |
| `cdl_arcgis_tiny_export` | Tiny ArcGIS ImageServer PNG preview when network is explicitly allowed |
| `daymet_single_pixel_prcp_rest` | Tiny point/time response rendered as point/card metadata |
| `prism_daily_ppt_static_zip` | Skipped by default as archive candidate |
| `usgs_3dep_dem` | Skipped with adapter-needed warning |

## v0.5.6 diagnostics

Real preview JSON now records raster diagnostics for supported fetched images:

- cached raw response path
- image width, height, and mode
- bytes read and SHA256
- content type
- unique color/class count
- dominant color and dominant fraction
- nontransparent pixel count
- transparent pixel fraction
- diversity score
- mostly-single-class and placeholder flags
- diagnostic notes

A CDL preview can look visually uniform when the AOI is tiny or the selected area contains mostly one CDL class. In that case, inspect the cached PNG under `reports/task_previews/cache/`, increase `--preview-size` within `--max-pixels`, or expand the AOI.

Manual bounded live command:

```bash
faster-raster task preview-real example_corn_belt_water_balance --allow-network --max-bytes-per-source 2500000 --preview-size 512 --debug-artifacts --plain
```

`--no-cache-raw` disables raw cache writes while still rendering from response bytes.

Result types:

- `real_raster_rendered`: actual tiny image response was fetched and rendered
- `real_point_rendered`: point/time data was fetched and rendered as annotation/card metadata
- `semantic_fallback`: source was represented semantically, not fetched
- `adapter_needed`: safe tiny endpoint or adapter still missing

## CDL sample verification flags

```bash
faster-raster task preview-real example_corn_belt_water_balance --allow-network --max-bytes-per-source 2500000 --max-pixels 262144 --preview-size 512 --sample-grid-size 5 --preview-expand-factor 10 --cdl-render-mode auto --debug-artifacts --plain
```

Options:

- `--cdl-verify-samples / --no-cdl-verify-samples` controls CDL identify sampling when the service PNG is single-color.
- `--sample-grid-size` sets a 1..7 sample grid; `--grid-size` is an alias.
- `--preview-expand-factor` expands only the preview fetch bbox around the task AOI centroid. It does not mutate task YAML.
- `--cdl-render-mode` accepts `auto`, `service_png`, `manual_samples`, or `service_tiff`.
