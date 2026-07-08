# Real Raster URL Structures

FasterRaster v0.2.1 includes offline fixtures for real public raster URL families. These fixtures document URL structure only; tests do not download data and do not call endpoints.

## Annual NLCD AWS HTTPS Objects

USGS documents Annual NLCD Collection 1 data access and cloud distribution. USGS material describes Annual NLCD science products and public data access, and USGS EROS material identifies cloud S3 bucket paths for Annual NLCD mosaic and tile products.

References:

- USGS Annual NLCD data access: https://www.usgs.gov/centers/eros/science/annual-nlcd-data-access
- USGS EROS Annual NLCD access video/transcript with S3 path notes: https://www.usgs.gov/media/videos/new-annual-1985-2023-national-land-cover-database-improving-a-30-year-legacy
- Annual NLCD Collection 1 science product guide: https://www.mrlc.gov/sites/default/files/docs/LSDS-2103%20Annual%20National%20Land%20Cover%20Database%20%28NLCD%29%20Collection%201%20Science%20Product%20User%20Guide%20-v1.0%202024_10_15.pdf

Encoded tile fixture:

```text
https://usgs-landcover.s3.us-west-2.amazonaws.com/annual-nlcd/c1/v0/cu/tile/h14v15/Annual_NLCD_H14V15_FctImp_1985_CU_C1V0.tif
```

Encoded mosaic fixture:

```text
https://usgs-landcover.s3.us-west-2.amazonaws.com/annual-nlcd/c1/v0/cu/mosaic/Annual_NLCD_FctImp_1985_CU_C1V0.tif
```

The fixtures intentionally use Collection 1.0 style path components (`c1/v0`) because those are the exact object examples encoded here. Annual NLCD Collection 1.2 may require updated object paths after verification.

## PRISM Time-Series HTTPS Zip

PRISM documents time-series datasets and public web/FTP-style access for daily, monthly, and annual products.

References:

- PRISM time series data: https://prism.oregonstate.edu/recent/
- PRISM portal bulk/web service overview: https://prism.oregonstate.edu/

Encoded fixture:

```text
https://data.prism.oregonstate.edu/time_series/us/an/4km/ppt/daily/2026/prism_ppt_us_25m_20260101.zip
```

This is treated as a deterministic HTTPS zip URL template. FasterRaster does not download or inspect the zip.

## Daymet NCSS Query Templates

Daymet supports THREDDS and RESTful web services for subsets. NCSS is a query URL family rather than a static raster object URL family.

References:

- Daymet Get Data web services: https://daymet.ornl.gov/getdata
- Daymet NCSS subset guide: https://daymet.ornl.gov/static/files/NCSS_Daymet_Subset_Guide_v3.pdf

Experimental future fixture concept:

```text
daymet_ncss_query_template_EXPERIMENTAL
```

This should use a future adapter such as `thredds_ncss_template`, not `generic_https_template`, because query parameters, subsetting semantics, and temporal bounds need a stricter contract.

