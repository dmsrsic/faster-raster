# Live Stack Cook Analysis v0.5.3

- source_success_count: `13`
- endpoint_pass_count: `13`
- endpoint_fail_count: `0`
- total_bytes_read: `531428`
- direct_data_candidate_count: `7`
- discovery_metadata_candidate_count: `4`
- index_or_filter_candidate_count: `2`

| Source | Class | HTTP | Bytes | Type | Maturity | Next action |
| --- | --- | ---: | ---: | --- | --- | --- |
| `prism_daily_ppt` | `pass_range_limited` | 206 | 65536 | application/zip | `adapter_candidate_direct_data` | preserve existing proof and convert to contract fixture |
| `daymet_single_pixel` | `pass_verified` | 200 | 474 | text/csv; charset=utf-8 | `adapter_candidate_direct_data` | preserve existing proof and convert to contract fixture |
| `usda_cdl` | `pass_verified` | 200 | 17190 | image/tiff | `adapter_candidate_direct_data` | preserve existing proof and convert to contract fixture |
| `chirps_daily` | `pass_range_limited` | 206 | 65536 | application/octet-stream | `adapter_candidate_direct_data` | add static TIF.GZ range adapter proposal |
| `gridmet_daily` | `pass_range_limited` | 206 | 65536 | application/x-netcdf | `adapter_candidate_direct_data` | add static/range NetCDF metadata adapter proposal |
| `terraclimate_monthly` | `pass_range_limited` | 206 | 65536 | application/x-netcdf | `adapter_candidate_direct_data` | add static/range NetCDF metadata adapter proposal |
| `worldclim_normals` | `pass_range_limited` | 206 | 65536 | application/zip | `adapter_candidate_direct_data` | add static ZIP bundle adapter proposal |
| `noaa_gfs_nomads` | `pass_bounded_truncated` | 200 | 65536 | application/octet-stream | `adapter_candidate_index_or_filter` | run magic-byte/content validation before adapter proposal |
| `noaa_hrrr_open_data` | `pass_range_limited` | 206 | 9108 | binary/octet-stream | `adapter_candidate_index_or_filter` | add GRIB index probe adapter proposal |
| `noaa_mrms_open_data` | `pass_range_limited` | 206 | 36822 | text/html | `adapter_candidate_discovery_metadata` | resolve product-specific MRMS key before adapter proposal |
| `usgs_3dep_tnm` | `pass_verified` | 200 | 8232 | application/json | `adapter_candidate_discovery_metadata` | add TNM JSON discovery adapter proposal |
| `nasa_cmr_metadata` | `pass_bounded_truncated` | 200 | 65536 | application/json;charset=utf-8 | `adapter_candidate_discovery_metadata` | keep as metadata discovery only; verify auth status for assets later |
| `noaa_ncei_thredds` | `pass_verified` | 200 | 850 | application/xml;charset=UTF-8 | `adapter_candidate_discovery_metadata` | descend from root THREDDS catalog to dataset-specific catalog |
