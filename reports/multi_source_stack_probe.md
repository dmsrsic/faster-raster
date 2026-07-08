# Multi-Source Stack Probe

- Stack status: `COMPLETED`
- Candidate sources: `10`
- Live probes: `3`
- Pass verified: `3`
- Credential gated: `3`
- Adapter needed: `4`
- Total live bytes: `67156`
- Total bytes including reused: `75905`

| Source | Result | Probe Type | HTTP | Bytes | Content-Type | Note |
| --- | --- | --- | ---: | ---: | --- | --- |
| `prism_daily_ppt_static_zip` | `pass_verified` | `live_bounded_probe` | 206 | 65536 | application/zip |  |
| `daymet_single_pixel_prcp_rest` | `pass_verified` | `live_bounded_probe` | 200 | 474 | text/csv; charset=utf-8 |  |
| `cdl_arcgis_tiny_export` | `pass_verified` | `live_bounded_probe` | 200 | 1146 | image/tiff |  |
| `nlcd_annual_landcover` | `credential_gated` | `classify_without_probe` | None | 0 |  | anonymous HTTPS/static template path is known to be 403/credential-gated from prior probes |
| `daymet_ncss_access_surface` | `credential_gated` | `existing_result_only` | None | 8749 |  | reuse access-surface report; do not rerun NCSS subset or metadata |
| `usgs_3dep_dem` | `adapter_needed` | `classify_without_probe` | None | 0 |  | no safe public metadata endpoint is defined in current research docs |
| `landsat_collection2` | `adapter_needed` | `classify_without_probe` | None | 0 |  | credential or catalog discovery needed |
| `sentinel_copernicus` | `adapter_needed` | `classify_without_probe` | None | 0 |  | credential or catalog discovery needed |
| `modis_nasa_earthdata` | `credential_gated` | `classify_without_probe` | None | 0 |  | Earthdata credentials needed |
| `noaa_ncei` | `adapter_needed` | `classify_without_probe` | None | 0 |  | no safe public metadata probe is defined in current research docs |
