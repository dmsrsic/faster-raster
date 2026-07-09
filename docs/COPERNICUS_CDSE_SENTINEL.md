# Copernicus CDSE Sentinel Scaffold

FasterRaster v0.5.8 adds a credential-gated Sentinel-2 L2A scaffold. It does not implement production acquisition, unbounded downloads, or live CDSE search by default.

## Endpoints and Collections

- STAC endpoint: `https://stac.dataspace.copernicus.eu/v1/`
- Collections of interest: `sentinel-2-l2a`, `sentinel-2-l1c`, `sentinel-1-grd`
- Source id: `copernicus_sentinel2_l2a_cdse_stac`

STAC discovery can identify deterministic assets after a search result is selected. OData product download and Sentinel Hub Process API preview work remain future, explicit, credential-gated steps.

## Credential Safety

Credentials must stay outside git. Supported environment variables are:

- `CDSE_ACCESS_TOKEN`
- `CDSE_REFRESH_TOKEN`
- `CDSE_USERNAME`
- `CDSE_PASSWORD`
- `CDSE_CLIENT_ID`

Token auth is preferred when `CDSE_ACCESS_TOKEN` exists. Reports redact authorization headers and never write token values. Local files such as `configs/auth_profiles.local.yaml`, `configs/*secret*.yaml`, and `.env` are ignored.

## Dry-Run Commands

```bash
faster-raster copernicus auth-check --plain
faster-raster copernicus sentinel search-plan example_corn_belt_water_balance --plain
faster-raster copernicus sentinel search-plan example_corn_belt_water_balance --json
```

Search-plan output is written to:

- `reports/copernicus/example_corn_belt_water_balance_sentinel2_l2a_search_plan.json`
- `reports/copernicus/example_corn_belt_water_balance_sentinel2_l2a_search_plan.md`

The search plan contains bbox, datetime range, cloud-cover filter, auth presence, `network_run: false`, the STAC payload, warnings, and the next explicit live command placeholder.


## v0.5.9 Live-Readiness

`copernicus auth-check --live --allow-network` performs a bounded read of the CDSE STAC root to verify live reachability. It uses `CDSE_ACCESS_TOKEN` when present and redacts the Authorization header.

`copernicus sentinel search-live TASK_ID --allow-network` sends a bounded STAC `/search` request to `https://stac.dataspace.copernicus.eu/v1/search` for `sentinel-2-l2a` by default. The query uses the task bbox, the task year as the datetime range, cloud cover max 30 by default, max items 5 by default, a timeout, and a byte cap. HTTP 401/403 is reported as a credential/readiness condition instead of a catastrophic failure.

Search-live reports contain item summaries only: id, collection, datetime, cloud cover, platform, constellation, instruments, MGRS tile if available, bbox, asset keys, band-presence booleans, and `hrefs_redacted: true`. They also record `no_downloads: true`. OData product download, Sentinel product acquisition, and Sentinel Hub Process API imagery remain future gated work.

Credentials are read only from environment variables: `CDSE_ACCESS_TOKEN`, `CDSE_REFRESH_TOKEN`, `CDSE_USERNAME`, `CDSE_PASSWORD`, and `CDSE_CLIENT_ID`. Do not commit credentials. `.env`, `configs/auth_profiles.local.yaml`, `configs/*secret*.yaml`, and `configs/*credentials*.yaml` are ignored.
