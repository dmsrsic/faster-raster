# Static HTTP Range Results

runnable_source_count: 5
fixture_source_count: 0
attempted_source_count: 5
pass_count: 5
fail_count: 0
fixture_count: 0
network_run: True
decision: wave1_adapter_live_validated

Live validation passed for 5 selected runnable sources. Contract fixtures reported separately: 0.

| Source | Status | HTTP | Bytes | Magic | Family | Quality |
| --- | --- | ---: | ---: | --- | --- | --- |
| `chirps_daily_precipitation` | `pass_bounded_truncated` | `206` | `65536` | `gzip` | `gzip` | `candidate` |
| `gridmet_daily` | `pass_bounded_truncated` | `206` | `65536` | `hdf5` | `hdf5` | `candidate` |
| `terraclimate_monthly` | `pass_bounded_truncated` | `206` | `65536` | `hdf5` | `hdf5` | `candidate` |
| `worldclim_bioclim_normals` | `pass_bounded_truncated` | `206` | `65536` | `zip` | `zip` | `candidate` |
| `prism_daily_ppt_static_zip` | `pass_bounded_truncated` | `206` | `65536` | `zip` | `zip` | `candidate` |

## Content Families

| Source | Expected | Detected |
| --- | --- | --- |
| `chirps_daily_precipitation` | `gzip` | `gzip` |
| `gridmet_daily` | `['netcdf', 'hdf5']` | `hdf5` |
| `terraclimate_monthly` | `['netcdf', 'hdf5']` | `hdf5` |
| `worldclim_bioclim_normals` | `zip` | `zip` |
| `prism_daily_ppt_static_zip` | `zip` | `zip` |

## Magic Validation

| Source | Expected | Detected |
| --- | --- | --- |
| `chirps_daily_precipitation` | `gzip` | `gzip` |
| `gridmet_daily` | `['netcdf', 'hdf5']` | `hdf5` |
| `terraclimate_monthly` | `['netcdf', 'hdf5']` | `hdf5` |
| `worldclim_bioclim_normals` | `zip` | `zip` |
| `prism_daily_ppt_static_zip` | `zip` | `zip` |

## Strongest Candidates

- `chirps_daily_precipitation`
- `gridmet_daily`
- `terraclimate_monthly`
- `worldclim_bioclim_normals`
- `prism_daily_ppt_static_zip`

## Failures/Cautions

- None

## Decision

`wave1_adapter_live_validated`

## Next Live Command

```bash
faster-raster range wave1 --allow-network --max-bytes 65536 --plain
```
