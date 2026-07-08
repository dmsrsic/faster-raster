# Daymet Access Surface Probe

- Timestamp UTC: `2026-07-07T20:12:47Z`
- Max bytes per endpoint: `65536`

| Name | Classification | HTTP | Bytes | Content-Type | Error | Endpoint |
| --- | --- | ---: | ---: | --- | --- | --- |
| `thredds_catalog_xml` | `malformed_request_expected` | 400 | 4332 | text/html;charset=ISO-8859-1 | HTTPError: 400  | `https://thredds.daac.ornl.gov/thredds/catalog/ornldaac/1840/catalog.xml` |
| `thredds_catalog_html` | `malformed_request_expected` | 400 | 4336 | text/html;charset=ISO-8859-1 | HTTPError: 400  | `https://thredds.daac.ornl.gov/thredds/catalog/ornldaac/1840/catalog.html` |
| `thredds_dataset_catalog_page` | `unauthorized` | 401 | 27 | text/html; charset=utf-8 | HTTPError: 401 Unauthorized | `https://thredds.daac.ornl.gov/thredds/catalog/ornldaac/1840/daymet_v4_daily_na_prcp_2023.nc.html` |
| `ncss_dataset_form` | `unauthorized` | 401 | 27 | text/html; charset=utf-8 | HTTPError: 401 Unauthorized | `https://thredds.daac.ornl.gov/thredds/ncss/ornldaac/1840/daymet_v4_daily_na_prcp_2023.nc/dataset.html` |
| `raw_ncss_no_query` | `unauthorized` | 401 | 27 | text/html; charset=utf-8 | HTTPError: 401 Unauthorized | `https://thredds.daac.ornl.gov/thredds/ncss/ornldaac/1840/daymet_v4_daily_na_prcp_2023.nc` |
