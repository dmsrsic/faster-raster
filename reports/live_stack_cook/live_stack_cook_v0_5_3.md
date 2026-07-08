# FasterRaster live stack cook

- created_at_utc: `2026-07-08T21:29:55Z`
- max_bytes_per_source: `65536`
- target_date: `2023-01-01`
- target_bbox: `[-83.2, 39.8, -83.19, 39.81]`
- endpoint_count: `13`
- source_success_count: `13`
- endpoint_pass_count: `13`
- total_bytes_read: `531428`

| Source | Endpoint | Class | HTTP | Bytes | Type | SHA256 short | Error |
| --- | --- | --- | ---: | ---: | --- | --- | --- |
| `prism_daily_ppt` | `prism_daily_zip` | `pass_range_limited` | 206 | 65536 | application/zip | `cc89306d4d5b` |  |
| `daymet_single_pixel` | `daymet_single_pixel_csv` | `pass_verified` | 200 | 474 | text/csv; charset=utf-8 | `e182dfb0db67` |  |
| `usda_cdl` | `cdl_tiny_imageserver_export` | `pass_verified` | 200 | 17190 | image/tiff | `29472f6c99f2` |  |
| `chirps_daily` | `chirps_20230101_tif_gz` | `pass_range_limited` | 206 | 65536 | application/octet-stream | `fa6755981504` |  |
| `gridmet_daily` | `gridmet_pr_2023_nc` | `pass_range_limited` | 206 | 65536 | application/x-netcdf | `b1a8ed52034a` |  |
| `terraclimate_monthly` | `terraclimate_ppt_2023_nc` | `pass_range_limited` | 206 | 65536 | application/x-netcdf | `6f90094fe5e7` |  |
| `worldclim_normals` | `worldclim_10m_prec_zip` | `pass_range_limited` | 206 | 65536 | application/zip | `9c647f5f3380` |  |
| `noaa_gfs_nomads` | `gfs_prate_tiny_bbox_or_filter_page` | `pass_bounded_truncated` | 200 | 65536 | application/octet-stream | `044438752f7b` |  |
| `noaa_hrrr_open_data` | `hrrr_20230101_idx` | `pass_range_limited` | 206 | 9108 | binary/octet-stream | `0ee1558a9e2a` |  |
| `noaa_mrms_open_data` | `mrms_bucket_index` | `pass_range_limited` | 206 | 36822 | text/html | `48661c476a2b` |  |
| `usgs_3dep_tnm` | `tnm_products_bbox_json` | `pass_verified` | 200 | 8232 | application/json | `23f88b2f3196` |  |
| `nasa_cmr_metadata` | `cmr_modis_collections_json` | `pass_bounded_truncated` | 200 | 65536 | application/json;charset=utf-8 | `f9094076d7aa` |  |
| `noaa_ncei_thredds` | `ncei_thredds_catalog_xml` | `pass_verified` | 200 | 850 | application/xml;charset=UTF-8 | `6b2d17e0d0a4` |  |

## Text previews

### daymet_single_pixel / daymet_single_pixel_csv
```text
Latitude: 39.805  Longitude: -83.195
X & Y on Lambert Conformal Conic: 1365461.22 -147590.39
Tile: 11569
Elevation: 278 meters
All years; all variables; Daymet Software Version 4.0
How to cite: Thornton; M.M.; R. Shrestha; Y. Wei; P.E. Thornton; S-C. Kao; and B.E. Wilson. 2022. Daymet: Daily Surface Weather Data on a 1-km Grid for North America; Version 4 R1. ORNL DAAC; Oak Ridge; Tennessee; USA. https://doi.org/10.3334/ORNLDAAC/2129
year,yday,prcp (mm/day)
2023,1,3.60
```

### noaa_hrrr_open_data / hrrr_20230101_idx
```text
1:0:d=2023010100:REFC:entire atmosphere:anl:
2:520665:d=2023010100:RETOP:cloud top:anl:
3:896876:d=2023010100:var discipline=0 center=7 local_table=1 parmcat=16 parm=201:entire atmosphere:anl:
4:1560420:d=2023010100:VIL:entire atmosphere:anl:
5:2010606:d=2023010100:VIS:surface:anl:
6:3335525:d=2023010100:REFD:1000 m above ground:anl:
7:3678781:d=2023010100:REFD:4000 m above ground:anl:
8:3993194:d=2023010100:REFD:263 K level:anl:
```

### noaa_mrms_open_data / mrms_bucket_index
```text
<!DOCTYPE html>

<!--
Copyright 2014-2018 Amazon.com, Inc. or its affiliates. All Rights Reserved.

Licensed under the Apache License, Version 2.0 (the "License").

You may not use this file except in compliance with the License. A copy
```

### usgs_3dep_tnm / tnm_products_bbox_json
```text
{"total": 2, "items": [{"title": "USGS 1 Meter 17 x31y441 OH_Statewide_Phase3_2021_B21", "moreInfo": "This is a tile of the standard one-meter resolution digital elevation model (DEM) produced through the 3D Elevation Program (3DEP) . The elevations in this DEM represent the topographic bare-earth surface. USGS standard one-meter DEMs are produced exclusively from high resolution light detection and ranging (lidar) source data of one-meter or higher resolution. One-meter DEM surfaces are seamless within collection projects, but, not necessarily seamless across projects. The spatial reference used for tiles of the one-meter DEM within the conterminous United States (CONUS) is Universal Transverse Mercator (UTM) in units of meters, and in conformance with the North American Datum of 1983 (NAD83). All bare earth elevation values are in [...]", "sourceId": "68ca18ccd4be0274ff4eb396", "sourceName": "ScienceBase", "sourceOriginId": null, "sourceOriginName": "gda", "metaUrl": "https://www.sciencebase.gov/catalog/item/68ca18ccd4be0274ff4eb396", "vendorMetaUrl": "https://thor-f5.er.usgs.gov/ngtoc/metadata/waf/elevation/1_meter/geotiff/OH_Statewide_Phase3_2021_B21/USGS_1M_17_x31y441_OH_Statewide_Phase3_2021_B21.xml", "publicationDate": "2025-09-14", "lastUpdated": "2025-09-16T20:11:24.660-06:00", "dateCreated": "2025-09-16T20:11:24.624-06:00", "sizeInBytes": 293607029, "extent": "10000 x 10000 meter", "format": "GeoTIFF", "downloadURL": "https://prd-tnm.s3.amazonaws.com/StagedProducts/
```

### nasa_cmr_metadata / cmr_modis_collections_json
```text
{"feed":{"updated":"2026-07-08T21:29:51.042Z","id":"https://cmr.earthdata.nasa.gov:443/search/collections.json?keyword=MODIS&page_size=5","title":"ECHO dataset metadata","entry":[{"processing_level_id":"2","cloud_hosted":true,"boxes":["-90 -180 90 180"],"has_combine":false,"time_start":"2002-08-31T00:00:00.000Z","version_id":"1","updated":"2026-04-29T00:00:00.000Z","dataset_id":"Aqua AIRS-MODIS 1-km Matchup Indexes V1 (Aqua_AIRS_MODIS1km_IND) at GES_DISC","entry_id":"Aqua_AIRS_MODIS1km_IND_1","has_spatial_subsetting":false,"has_transforms":false,"has_variables":false,"data_center":"GES_DISC","short_name":"Aqua_AIRS_MODIS1km_IND","organizations":["NASA/GSFC/SED/ESD/TISL/GESDISC"],"title":"Aqua AIRS-MODIS 1-km Matchup Indexes V1 (Aqua_AIRS_MODIS1km_IND) at GES_DISC","coordinate_system":"CARTESIAN","summary":" This dataset includes Aqua AIRS to MODIS 1-km collocation index product, within the framework of the Multidecadal Satellite Record of Water Vapor, Temperature, and Clouds (PI: Eric Fetzer) funded by NASA’s Making Earth System Data Records for Use in Research Environments (MEaSUREs) Program, 2017. The dataset is built upon work by Wang et al. (doi: 10.3390/rs8010076) and Yue (doi:10.5194/amt-15-2099-2022).\n\nThe short name for this collections is Aqua_AIRS_MODIS1km_IND\n\n","time_end":"2025-11-25T23:59:59.999Z","service_features":{"opendap":{"has_formats":false,"has_variables":false,"has_transforms":false,"has_combine":false,"has_spatial_subsetting":false,"has_temporal_sub
```

### noaa_ncei_thredds / ncei_thredds_catalog_xml
```text
<?xml version="1.0" encoding="UTF-8"?>
<catalog xmlns="http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0" xmlns:xlink="http://www.w3.org/1999/xlink" name="NCEI THREDDS Server" version="1.2">
  <catalogRef xlink:href="blended-global/blended-global.xml" xlink:title="Blended-Global" name="Blended-Global" />
  <catalogRef xlink:href="in_situ/in_situ.xml" xlink:title="In Situ" name="In Situ" />
  <catalogRef xlink:href="marine-ocean/marine-ocean.xml" xlink:title="Marine and Ocean" name="Marine and Ocean" />
  <catalogRef xlink:href="model/model.xml" xlink:title="Model" name="Model" />
  <catalogRef xlink:href="satellite/satellite.xml" xlink:title="Satellite" name="Satellite" />
  <catalogRef xlink:href="data-in-development/data-in-development.xml" xlink:title="Data in Development" name="Data in Development" />
```
