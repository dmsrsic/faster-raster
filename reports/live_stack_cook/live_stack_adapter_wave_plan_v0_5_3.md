# Live Stack Adapter Wave Plan v0.5.3

- source_success_count: `13`
- endpoint_pass_count: `13`
- endpoint_fail_count: `0`
- total_bytes_read: `531428`
- default_knobs_change_recommended: `False`
- recommended_default_network_mode: `off`
- experimental_adapter_candidate_count: `7`

## Adapter wave table

| Live source | Atlas source | Result | Bytes | Type | Wave | Decision |
| --- | --- | --- | ---: | --- | --- | --- |
| `prism_daily_ppt` | `prism_daily_ppt_static_zip` | `pass_range_limited` | 65536 | application/zip | `wave_1_static_range_http_fixture` | `preserve_as_contract_fixture` |
| `daymet_single_pixel` | `daymet_single_pixel_prcp_rest` | `pass_verified` | 474 | text/csv; charset=utf-8 | `wave_1_parameterized_rest_fixture` | `preserve_as_contract_fixture` |
| `usda_cdl` | `cdl_arcgis_tiny_export` | `pass_verified` | 17190 | image/tiff | `wave_1_arcgis_imageserver_fixture` | `preserve_as_contract_fixture` |
| `chirps_daily` | `chirps_daily_precipitation` | `pass_range_limited` | 65536 | application/octet-stream | `wave_1_static_range_http` | `ready_for_experimental_static_range_adapter` |
| `gridmet_daily` | `gridmet_daily` | `pass_range_limited` | 65536 | application/x-netcdf | `wave_1_static_range_http` | `ready_for_experimental_static_range_adapter` |
| `terraclimate_monthly` | `terraclimate_monthly` | `pass_range_limited` | 65536 | application/x-netcdf | `wave_1_static_range_http` | `ready_for_experimental_static_range_adapter` |
| `worldclim_normals` | `worldclim_bioclim_normals` | `pass_range_limited` | 65536 | application/zip | `wave_1_static_range_http` | `ready_for_experimental_static_range_adapter` |
| `noaa_gfs_nomads` | `noaa_gfs_nomads_grib_filter` | `pass_bounded_truncated` | 65536 | application/octet-stream | `wave_3_nomads_filter_validation` | `needs_magic_byte_and_parameter_validation` |
| `noaa_hrrr_open_data` | `noaa_hrrr_public_cloud` | `pass_range_limited` | 9108 | binary/octet-stream | `wave_3_grib_index_probe` | `ready_for_experimental_grib_index_adapter` |
| `noaa_mrms_open_data` | `noaa_mrms_precipitation` | `pass_range_limited` | 36822 | text/html | `wave_3_public_bucket_product_resolver` | `needs_product_specific_key_resolution` |
| `usgs_3dep_tnm` | `usgs_3dep_dem` | `pass_verified` | 8232 | application/json | `wave_2_metadata_json_discovery` | `ready_for_experimental_metadata_json_adapter` |
| `nasa_cmr_metadata` | `modis_nasa_earthdata_cmr` | `pass_bounded_truncated` | 65536 | application/json;charset=utf-8 | `wave_2_metadata_json_discovery_auth_caution` | `metadata_only_keep_auth_caution` |
| `noaa_ncei_thredds` | `noaa_ncei_service_class` | `pass_verified` | 850 | application/xml;charset=UTF-8 | `wave_2_thredds_catalog_discovery` | `ready_for_experimental_thredds_catalog_adapter` |

## Recommended implementation order

1. Add generic `static_http_range` experimental adapter.
2. Preserve PRISM, Daymet single-pixel, and CDL proofs as contract fixtures.
3. Add CHIRPS, gridMET, TerraClimate, and WorldClim as static/range candidates.
4. Add USGS TNM JSON and NOAA NCEI THREDDS as metadata discovery candidates.
5. Hold GFS, MRMS, NASA CMR, and HRRR for specialized validation steps.
