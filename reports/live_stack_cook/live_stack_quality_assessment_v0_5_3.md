# Live Stack Quality Assessment v0.5.3

- overall_score: `94.09`
- overall_grade: `excellent`
- grade_language: Strong enough to drive the next experimental adapter implementation wave.

## Explanation

Quality assessment: EXCELLENT (94.09/100)

The cook is high quality because 13 of 13 sauces responded successfully, with 0 endpoint failures and only 531428 total bytes read.
The safety posture is also strong: the report keeps default live networking off, does not recommend changing default knobs, and does not allow runtime promotion from this evidence alone.
The evidence is broad rather than narrow. It covers 8 content families: csv, html, json, netcdf, octet_stream, tiff, xml, zip.

Component scores:
- Evidence: 96.46/100
- Safety: 100.0/100
- Adapter planning: 77.15/100
- Format/source diversity: 100.0/100

The best immediate implementation target is a generic static_http_range adapter, because CHIRPS, gridMET, TerraClimate, WorldClim, and PRISM all produced bounded range-readable evidence.
The caution set should not be treated as failed. NOAA GFS, NOAA MRMS, and NASA CMR are useful, but they need specialized validation before promotion: magic-byte/parameter validation, product key resolution, or asset-level auth checks.

Decision:
- Do not change default knobs.
- Preserve this run as live evidence.
- Implement experimental static_http_range first.
- Keep runtime registry promotion disabled until the adapter has tests and fixtures.

## Component scores

| Component | Score |
| --- | ---: |
| `evidence_score` | 96.46 |
| `safety_score` | 100.0 |
| `adapter_score` | 77.15 |
| `diversity_score` | 100.0 |

## Metrics

| Metric | Value |
| --- | ---: |
| `endpoint_count` | `13` |
| `endpoint_pass_count` | `13` |
| `endpoint_fail_count` | `0` |
| `pass_rate_percent` | `100.0` |
| `bounded_rate_percent` | `100.0` |
| `sha_rate_percent` | `100.0` |
| `http_good_rate_percent` | `100.0` |
| `high_confidence_rate_percent` | `84.62` |
| `provisional_rate_percent` | `15.38` |
| `total_bytes_read` | `531428` |
| `content_family_count` | `8` |
| `content_families` | `{"csv": 1, "html": 1, "json": 2, "netcdf": 2, "octet_stream": 3, "tiff": 1, "xml": 1, "zip": 2}` |
| `ready_adapter_decision_count` | `7` |
| `fixture_decision_count` | `3` |
| `caution_decision_count` | `3` |
| `default_network_mode_off` | `True` |
| `default_knobs_change_recommended` | `False` |
| `runtime_registry_safe` | `True` |

## Row assessments

| Source | Class | HTTP | Bytes | Type | Quality | Recommendation |
| --- | --- | ---: | ---: | --- | --- | --- |
| `prism_daily_ppt` | `pass_range_limited` | 206 | 65536 | application/zip | `high_bounded` | Preserve this as a known-good fixture and contract regression test. |
| `daymet_single_pixel` | `pass_verified` | 200 | 474 | text/csv; charset=utf-8 | `high` | Preserve this as a known-good fixture and contract regression test. |
| `usda_cdl` | `pass_verified` | 200 | 17190 | image/tiff | `high` | Preserve this as a known-good fixture and contract regression test. |
| `chirps_daily` | `pass_range_limited` | 206 | 65536 | application/octet-stream | `good_bounded_needs_magic` | Route into the experimental static_http_range adapter with magic-byte validation. |
| `gridmet_daily` | `pass_range_limited` | 206 | 65536 | application/x-netcdf | `high_bounded` | Route into the experimental static_http_range adapter with magic-byte validation. |
| `terraclimate_monthly` | `pass_range_limited` | 206 | 65536 | application/x-netcdf | `high_bounded` | Route into the experimental static_http_range adapter with magic-byte validation. |
| `worldclim_normals` | `pass_range_limited` | 206 | 65536 | application/zip | `high_bounded` | Route into the experimental static_http_range adapter with magic-byte validation. |
| `noaa_gfs_nomads` | `pass_bounded_truncated` | 200 | 65536 | application/octet-stream | `provisional_needs_content_validation` | Do not promote yet; validate GRIB magic bytes and filter parameters first. |
| `noaa_hrrr_open_data` | `pass_range_limited` | 206 | 9108 | binary/octet-stream | `good_bounded_needs_magic` | Promote as GRIB index evidence; next step is index-to-byte-range mapping. |
| `noaa_mrms_open_data` | `pass_range_limited` | 206 | 36822 | text/html | `good_bounded_needs_magic` | Do not promote yet; resolve product-specific object keys first. |
| `usgs_3dep_tnm` | `pass_verified` | 200 | 8232 | application/json | `high` | Promote as metadata JSON discovery evidence, then resolve actual DEM asset URLs separately. |
| `nasa_cmr_metadata` | `pass_bounded_truncated` | 200 | 65536 | application/json;charset=utf-8 | `provisional_needs_content_validation` | Keep as metadata-only CMR evidence with Earthdata/auth caution for assets. |
| `noaa_ncei_thredds` | `pass_verified` | 200 | 850 | application/xml;charset=UTF-8 | `high` | Promote as THREDDS catalog evidence, then descend into dataset-specific catalogs. |

## Strongest candidates

- `prism_daily_ppt`: Preserve this as a known-good fixture and contract regression test.
- `chirps_daily`: Route into the experimental static_http_range adapter with magic-byte validation.
- `gridmet_daily`: Route into the experimental static_http_range adapter with magic-byte validation.
- `terraclimate_monthly`: Route into the experimental static_http_range adapter with magic-byte validation.
- `worldclim_normals`: Route into the experimental static_http_range adapter with magic-byte validation.

## Caution candidates

- `noaa_gfs_nomads`: Do not promote yet; validate GRIB magic bytes and filter parameters first.
- `noaa_mrms_open_data`: Do not promote yet; resolve product-specific object keys first.
- `nasa_cmr_metadata`: Keep as metadata-only CMR evidence with Earthdata/auth caution for assets.
