# FasterRaster CLI Commands

## Public `fr` Source Pack, time, and preview contracts

The following commands are implemented in the published beta.5 source tree as
experimental public contracts:
```bash { .illustrative }
fr capabilities --json
fr sauce init my-source
fr sauce validate my-source.sauce
fr sauce explain my-source.sauce --json
fr sauce test my-source.sauce
fr sauce probe my-source.sauce --allow-network --out build/probe.json
fr sauce pack my-source.sauce --out dist/
fr sauce time alternatives my-source.sauce --requested 2022 --json
fr sauce time select my-source.sauce --requested 2022 --candidate 2021 --out resolution.json --json
fr preview-templates list
fr preview-templates show ag_classification_audit_v1 --json
fr preview-templates validate general_multisource_v1
fr plan study.fr.md --resolve-imagery-year 2019 --resolve-cdl-year 2019
fr explain study.fr.md --resolve-imagery-year 2019 --resolve-cdl-year 2019
```

`validate`, `explain`, `test`, preview-template discovery, and Sauce Time
fixture ranking are offline. `probe` requires `--allow-network`, remains
metadata-only and byte-capped, and fails before network access if the pack
requires a credential resolver. `credential_ref` values are opaque names, not
secret values.

The paired classification year arguments create an immutable coherent or
imagery-only temporal-resolution contract. Both are required; neither edits
the workfile or authorizes raster acquisition during selection.

Professional commands remain stable:

Materialization commands are available in v0.9:

```bash
faster-raster materialize plan example_wave1_climate_stack --source chirps_daily_precipitation --plain
faster-raster materialize eligibility example_wave1_climate_stack --plain
faster-raster materialize local example_wave1_climate_stack --source chirps_daily_precipitation --plain
faster-raster materialize inspect example_wave1_climate_stack --plain
faster-raster materialize verify example_wave1_climate_stack --plain
faster-raster materialize evidence example_wave1_climate_stack --plain
faster-raster materialize catalog --plain
faster-raster materialize catalog-verify --plain
```

Planning performs no network requests. Complete-object materialization remains blocked until `--allow-network`, `--allow-materialization`, and a full exact `--approve-plan-sha256` are supplied.

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

## v0.7 Task compile, package, and grade

```bash
faster-raster task validate example_wave1_climate_stack --plain
faster-raster task compile example_wave1_climate_stack --plain
faster-raster task compile example_wave1_climate_stack --max-bytes-per-source 65536 --plain
faster-raster task inspect-compile example_wave1_climate_stack --plain
faster-raster task package example_wave1_climate_stack --plain
faster-raster grade system --plain
faster-raster grade task example_wave1_climate_stack --plain
```

These commands write deterministic planning and scheduler artifacts only. They do not fetch data, run jobs, decode rasters, extract archives, or contact Sentinel services.

## v0.8 Local bounded execution

```bash
faster-raster run plan example_wave1_climate_stack --plain
faster-raster run plan example_wave1_climate_stack --json
faster-raster run local example_wave1_climate_stack --plain
faster-raster run local example_wave1_climate_stack --allow-network --plain
faster-raster run inspect example_wave1_climate_stack --plain
faster-raster run verify example_wave1_climate_stack --plain
faster-raster run evidence example_wave1_climate_stack --plain
```

`run plan` never uses network. `run local` defaults to network disabled and reports `execution_blocked: network_not_allowed` instead of claiming live success. Live bounded evidence requires `--allow-network`.

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


## v0.5.9 Commands

Real preview layouts:

```bash
faster-raster task preview-real example_corn_belt_water_balance --layout clean --plain
faster-raster task preview-real example_corn_belt_water_balance --layout cockpit --plain
faster-raster task preview-real example_corn_belt_water_balance --layout report --plain
```

Copernicus/CDSE auth and Sentinel STAC readiness:

```bash
faster-raster copernicus auth-check --plain
faster-raster copernicus auth-check --json
faster-raster copernicus auth-check --live --allow-network --plain
faster-raster copernicus sentinel search-plan example_corn_belt_water_balance --plain
faster-raster copernicus sentinel search-live example_corn_belt_water_balance --allow-network --plain
```

`search-live` performs only a bounded STAC JSON search. It does not download OData products, asset files, Sentinel Hub Process API imagery, or Sentinel pixels. Authorization headers are redacted in reports.


## v0.5.10 Visibility and Auth Readiness

```bash
faster-raster task preview-real example_corn_belt_water_balance --visibility-mode typed-log --plain
faster-raster task preview-real example_corn_belt_water_balance --visibility-mode base-dominant --overlay-strength 0.75 --plain
faster-raster stack preview-real example_corn_belt_water_balance --visibility-mode equal --plain
faster-raster copernicus auth-check --live --allow-network --plain
```

`auth-check --live` probes only the CDSE/STAC root and records `no_downloads: true`. It does not run a Sentinel search. Sentinel search-live remains an explicit separate command and still downloads no products or assets.

### Probe selection for materialization

`faster-raster materialize plan` and `faster-raster materialize local` accept `--probe-run-id` and `--probe-receipt-sha256`. Live materialization still requires `--allow-network`, `--allow-materialization`, and the full approved plan hash.
