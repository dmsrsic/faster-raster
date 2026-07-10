# Static HTTP Range Plan

runnable_source_count: 4
fixture_source_count: 1
attempted_source_count: 0
pass_count: 0
fail_count: 0
fixture_count: 1
network_run: False
decision: not_promoted

PRISM is preserved separately as historical bounded contract evidence and is not counted as a runnable Wave 1 adapter failure.

| Source | Status | HTTP | Bytes | Magic | Family | Quality |
| --- | --- | ---: | ---: | --- | --- | --- |
| `chirps_daily_precipitation` | `skipped_dry_run` | `None` | `0` | `gzip` | `gzip` | `planned` |
| `gridmet_daily` | `skipped_dry_run` | `None` | `0` | `['netcdf', 'hdf5']` | `['netcdf', 'hdf5']` | `planned` |
| `terraclimate_monthly` | `skipped_dry_run` | `None` | `0` | `['netcdf', 'hdf5']` | `['netcdf', 'hdf5']` | `planned` |
| `worldclim_bioclim_normals` | `skipped_dry_run` | `None` | `0` | `zip` | `zip` | `planned` |

## Contract Fixtures

| Source | Status | Historical evidence | Current endpoint |
| --- | --- | --- | --- |
| `prism_daily_ppt_static_zip` | `fixture_only` | `application/zip / zip / cc89306d4d5b` | `unresolved_or_stale` |

## Dry-Run Source Plan

| Source | Expected magic | Expected family | Required params | Default params | URL/template |
| --- | --- | --- | --- | --- | --- |
| `chirps_daily_precipitation` | `gzip` | `gzip` | `['year', 'month', 'day']` | `{'year': 2023, 'month': '01', 'day': '01'}` | `https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_daily/tifs/p05/{year}/chirps-v2.0.{year}.{month}.{day}.tif.gz` |
| `gridmet_daily` | `['netcdf', 'hdf5']` | `['netcdf', 'hdf5']` | `['year']` | `{'year': 2023}` | `https://www.northwestknowledge.net/metdata/data/pr_{year}.nc` |
| `terraclimate_monthly` | `['netcdf', 'hdf5']` | `['netcdf', 'hdf5']` | `['year']` | `{'year': 2023}` | `https://climate.northwestknowledge.net/TERRACLIMATE-DATA/TerraClimate_ppt_{year}.nc` |
| `worldclim_bioclim_normals` | `zip` | `zip` | `[]` | `{}` | `https://geodata.ucdavis.edu/climate/worldclim/2_1/base/wc2.1_10m_prec.zip` |

## Content Families

| Source | Expected | Detected |
| --- | --- | --- |
| `chirps_daily_precipitation` | `gzip` | `None` |
| `gridmet_daily` | `['netcdf', 'hdf5']` | `None` |
| `terraclimate_monthly` | `['netcdf', 'hdf5']` | `None` |
| `worldclim_bioclim_normals` | `zip` | `None` |

## Magic Validation

| Source | Expected | Detected |
| --- | --- | --- |
| `chirps_daily_precipitation` | `gzip` | `None` |
| `gridmet_daily` | `['netcdf', 'hdf5']` | `None` |
| `terraclimate_monthly` | `['netcdf', 'hdf5']` | `None` |
| `worldclim_bioclim_normals` | `zip` | `None` |

## Strongest Candidates

- None

## Failures/Cautions

- None

## Decision

`not_promoted`

## Next Live Command

```bash
faster-raster range wave1 --allow-network --max-bytes 65536 --plain
```
