# Live URL Structure Probe

This is an explicit opt-in bounded streaming diagnostic. It does not update runtime registries, golden fixtures, or default diagnostics.

- Started: `2026-07-07T19:17:30Z`
- Completed: `2026-07-07T19:17:33Z`
- Max bytes per URL: `65536`
- Probe count: `5`
- Pass count: `2`
- Fail count: `3`

| Probe | Kind | Status | HTTP | Bytes | Content-Type | Error |
| --- | --- | --- | ---: | ---: | --- | --- |
| `prism_daily_zip` | `static_https_zip` | `PASS` | 206 | 65536 | application/zip |  |
| `nlcd_aws_tile` | `static_https_tif_tile` | `FAIL` | 403 | 0 | application/xml | HTTPError: 403 Forbidden |
| `nlcd_aws_mosaic` | `static_https_tif_mosaic` | `FAIL` | 403 | 0 | application/xml | HTTPError: 403 Forbidden |
| `cdl_imageserver_tiny_export` | `arcgis_imageserver_export_image` | `PASS` | 200 | 1146 | image/tiff |  |
| `daymet_ncss_tiny_query_experimental` | `thredds_ncss_query_experimental` | `FAIL` | 401 | 0 | text/html; charset=utf-8 | HTTPError: 401 Unauthorized |
