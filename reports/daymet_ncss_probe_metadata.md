# Daymet NCSS Probe Report

- Status: `FAIL`
- Source ID: `ornl_daymet_daily_ncss_service_aware`
- Request ID: `daymet_prcp_20230101_probe_bbox_000001`
- Network opt-in: `True`
- Metadata only: `True`
- Max bytes: `65536`

| Stage | Result | HTTP | Bytes | Content-Type | Seconds | Error | Endpoint |
| --- | --- | ---: | ---: | --- | ---: | --- | --- |
| `metadata` | `FAIL` | 401 | 0 | text/html; charset=utf-8 | 1.225919 | HTTPError: 401 Unauthorized | `https://thredds.daac.ornl.gov/thredds/ncss/ornldaac/1840/daymet_v4_daily_na_prcp_2023.nc/dataset.html` |
| `tiny_subset` | `SKIPPED` | None | 0 |  | 0.0 | metadata-only mode | `` |

## Next Recommended Action

Probe did not pass. Verify the official Daymet THREDDS/NCSS endpoint and query parameters before retrying.
