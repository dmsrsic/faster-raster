# Raw Model Source Enrichment

Source: attached Pasted text(59).txt
Status: unverified candidate research
Runtime impact: none

```yaml
registry_enrichment_patches:
  usda_nass_cdl_imageserver_existing:
    updates:
      discovery_mechanism:
        value: ARCGIS_IMAGESERVER
        confidence: high
      access_pattern_category:
        value: service_discovered
        confidence: high
      base_endpoint:
        value: "https://pdi.scinet.usda.gov/image/rest/services/CDL_WM/ImageServer"
        confidence: high
      query_pattern:
        value: "ArcGIS ImageServer exportImage with bbox, bboxSR, imageSR, size, format=tiff, f=image, and time/year or mosaic rule by Year"
        confidence: high
      asset_resolution_strategy:
        value: ARCGIS_EXPORT_IMAGE
        confidence: high
      stream_download_strategy:
        value: GDAL_HTTP_RANGE_OR_DIRECT_EXPORT
        confidence: medium
      credential_requirements:
        value: none_public_service
        confidence: high
      rate_limit_or_access_caveats:
        value: "Respect ImageServer maxImageWidth/maxImageHeight=4097 and maxDownloadSizeLimit=2048 from service metadata."
        confidence: high
      file_formats:
        value: ["tiff_export", "service_native_AMD"]
        confidence: high
      crs_projection_assumptions:
        value:
          service_crs: "EPSG:3857 / wkid 102100"
          native_pixel_size_m: 30
        confidence: high
      temporal_key_structure:
        value: "Service timeInfo uses Year as startTimeField/endTimeField; timeExtent currently spans 1997-2025 era in service metadata."
        confidence: high
      spatial_key_structure:
        value: "AOI bbox passed to exportImage; service full extent is Web Mercator."
        confidence: high
      nodata_metadata:
        value: 0
        confidence: high
      checksum_or_metadata_availability:
        value: "ArcGIS service metadata available; checksums unknown."
        confidence: high
      bounded_stream_probes_appropriate:
        value: true
        confidence: high
      deterministic_url_generation_direct:
        value: true
        confidence: high
      deterministic_asset_resolution_after_discovery:
        value: true
        confidence: high
    citations:
      - "[USDA CDL ImageServer service metadata](https://pdi.scinet.usda.gov/image/rest/services/CDL_WM/ImageServer?f=pjson)"
      - "[USDA NASS Cropland Data Layer page](https://www.nass.usda.gov/Research_and_Science/Cropland/SARS1a.php)"

  usgs_annual_nlcd_landcover_service_aware:
    updates:
      discovery_mechanism:
        value: "MRLC/USGS service catalog, EarthExplorer, ScienceBase, MRLC direct download, WMS, or requester-pays S3; do not treat failed anonymous HTTPS S3 probes as static_verified."
        confidence: high
      access_pattern_category:
        value: [service_discovered, credential_gated]
        confidence: high
      base_endpoint:
        value:
          requester_pays_s3_root: "s3://usgs-landcover"
          documented_s3_prefixes:
            - "s3://usgs-landcover/annual-nlcd/c1/v0/[region]/tile/h{xx}v{yy}/"
            - "s3://usgs-landcover/annual-nlcd/c1/v0/[region]/mosaic/"
          wms_endpoint: "unknown"
        confidence: high
      query_pattern:
        value: "Use official access method selector first: EarthExplorer indexed ARD tiles, MRLC direct CONUS mosaics, WMS/service access, ScienceBase, or requester-pays S3. Object filenames remain product/version-specific unless separately verified."
        confidence: high
      asset_resolution_strategy:
        value: SERVICE_OR_CATALOG_RESOLVED_ASSET
        confidence: high
      stream_download_strategy:
        value: "S3 requester-pays aware stream/download for S3; service/WMS for visualization; MRLC/ScienceBase/direct download for mosaics."
        confidence: high
      credential_requirements:
        value: "Requester-pays AWS credentials required for S3 access; other access paths may be public or workflow-gated."
        confidence: high
      rate_limit_or_access_caveats:
        value: "Requester-pays S3 can produce 403 without AWS requester-pays configuration; MRLC viewer may email download instructions."
        confidence: high
      file_formats:
        value: ["GeoTIFF_or_service_product_specific_tif", "unknown_for_each_access_path_until_resolved"]
        confidence: medium
      crs_projection_assumptions:
        value: "unknown_in_registry_patch; likely product-specific and must be taken from product guide/metadata, not inferred from old FctImp fixture"
        confidence: medium
      temporal_key_structure:
        value: "Annual products beginning in 1985; Collection 1.x products."
        confidence: high
      spatial_key_structure:
        value: "CONUS mosaics and Landsat ARD tile-indexed products; S3 tile path uses h{xx}v{yy}."
        confidence: high
      nodata_metadata:
        value: unknown
        confidence: low
      checksum_or_metadata_availability:
        value: "ScienceBase and product metadata available; checksum availability unknown."
        confidence: medium
      bounded_stream_probes_appropriate:
        value: "Only through requester-pays-aware S3 adapter or service-specific capability probe; anonymous HTTPS object probes are not sufficient."
        confidence: high
      deterministic_url_generation_direct:
        value: false
        confidence: high
      deterministic_asset_resolution_after_discovery:
        value: true
        confidence: high
    citations:
      - "[USGS Annual NLCD Data Access](https://www.usgs.gov/centers/eros/science/annual-nlcd-data-access)"
      - "[MRLC Data Services](https://www.mrlc.gov/data-services-page)"
      - "[USGS Annual NLCD Land Cover Science Data Catalog](https://data.usgs.gov/datacatalog/data/USGS%3A664e0d2bd34e702fe8744536)"

  prism_daily_static_range_existing:
    updates:
      discovery_mechanism:
        value: STATIC_TEMPLATE_OR_PRISM_WEB_SERVICE
        confidence: medium
      access_pattern_category:
        value: static_verified
        confidence: medium
      base_endpoint:
        value: "https://data.prism.oregonstate.edu"
        confidence: high
      query_pattern:
        value: "Existing static daily ZIP pattern may remain for verified legacy paths, but PRISM's 2025+ format/service transition means registry must version the path pattern."
        confidence: high
      asset_resolution_strategy:
        value: STATIC_TEMPLATE_WITH_VERSIONED_FORMAT_GUARD
        confidence: medium
      stream_download_strategy:
        value: "HTTP range for ZIP/COG when server supports ranges; full ZIP fetch otherwise."
        confidence: medium
      credential_requirements:
        value: none_public
        confidence: high
      rate_limit_or_access_caveats:
        value: "Preliminary daily grids are revised repeatedly for up to six months; automated downloads should account for update/stability metadata."
        confidence: high
      file_formats:
        value: ["zip_bundled_gridded_data", "COG_for_newer_800m_and_4km_products", "legacy_BIL_until_retirement_window"]
        confidence: high
      crs_projection_assumptions:
        value: unknown
        confidence: low
      temporal_key_structure:
        value: "daily 1981-present; date key YYYYMMDD; daily grids revised within rolling six-month window."
        confidence: high
      spatial_key_structure:
        value: "CONUS gridded products; no AOI bbox in legacy static file path."
        confidence: high
      nodata_metadata:
        value: unknown
        confidence: low
      checksum_or_metadata_availability:
        value: "FGDC XML metadata accompanies files; checksum availability unknown."
        confidence: high
      bounded_stream_probes_appropriate:
        value: true
        confidence: high
      deterministic_url_generation_direct:
        value: true
        confidence: medium
      deterministic_asset_resolution_after_discovery:
        value: true
        confidence: medium
    citations:
      - "[PRISM Data](https://prism.oregonstate.edu/data/)"
      - "[PRISM Formats](https://www.prism.oregonstate.edu/formats/)"
      - "[PRISM Bulk Downloads](https://prism.oregonstate.edu/downloads/)"
      - "[PRISM 2025 800m/COG Notice](https://www.prism.oregonstate.edu/notices/notice_20250327.php)"

  ornl_daymet_daily_ncss_service_aware:
    updates:
      discovery_mechanism:
        value: THREDDS_NCSS_QUERY
        confidence: high
      access_pattern_category:
        value: service_discovered
        confidence: high
      base_endpoint:
        value: "https://thredds.daac.ornl.gov"
        confidence: high
      query_pattern:
        value: "THREDDS catalog/NCSS or Daymet web service subset by variable, time period, and AOI; exact NCSS parameter names should be confirmed from current endpoint metadata."
        confidence: high
      asset_resolution_strategy:
        value: NCSS_DYNAMIC_SUBSET
        confidence: high
      stream_download_strategy:
        value: GDAL_NETCDF
        confidence: high
      credential_requirements:
        value: "public_or_earthdata_session_depending_on_ORNL_endpoint"
        confidence: medium
      bounded_stream_probes_appropriate:
        value: "Metadata probe and tiny NCSS subset probe yes; not arbitrary static path HEAD."
        confidence: high
      deterministic_url_generation_direct:
        value: false
        confidence: high
      deterministic_asset_resolution_after_discovery:
        value: true
        confidence: high
    citations:
      - "[ORNL DAAC Daymet Daily V4 Guide](https://daac.ornl.gov/DAYMET/guides/Daymet_Daily_V4.html)"
      - "[Daymet Get Data](https://daymet.ornl.gov/getdata)"
      - "[Daymet Daily V4R1 Guide](https://daac.ornl.gov/DAYMET/guides/Daymet_Daily_V4R1.html)"

  landsat_collection2_stac_future:
    updates:
      discovery_mechanism:
        value: STAC_API_OR_USGS_M2M_API
        confidence: high
      access_pattern_category:
        value: [api_discovered, credential_gated]
        confidence: high
      asset_resolution_strategy:
        value: STAC_ASSET_ID_OR_M2M_DOWNLOAD_URL
        confidence: high
      credential_requirements:
        value: "USGS M2M token for M2M; AWS requester-pays setup for direct S3."
        confidence: high
      deterministic_url_generation_direct:
        value: false
        confidence: high
      deterministic_asset_resolution_after_discovery:
        value: true
        confidence: high
    citations:
      - "[USGS Landsat Collection 2](https://www.usgs.gov/landsat-missions/landsat-collection-2)"
      - "[USGS Landsat Commercial Cloud Data Access](https://www.usgs.gov/landsat-missions/landsat-commercial-cloud-data-access)"
      - "[USGS M2M API](https://m2m.cr.usgs.gov/)"
      - "[USGS Landsat Data Access](https://www.usgs.gov/landsat-missions/landsat-data-access)"

  sentinel_copernicus_dataspace_future:
    updates:
      discovery_mechanism:
        value: STAC_API_OR_ODATA
        confidence: high
      access_pattern_category:
        value: [api_discovered, credential_gated]
        confidence: high
      deterministic_url_generation_direct:
        value: false
        confidence: high
      deterministic_asset_resolution_after_discovery:
        value: true
        confidence: high
    citations:
      - "[Copernicus Data Space STAC API](https://documentation.dataspace.copernicus.eu/APIs/STAC.html)"
      - "[Copernicus Data Space OData API](https://documentation.dataspace.copernicus.eu/APIs/OData.html)"
      - "[Copernicus Data Space APIs Overview](https://dataspace.copernicus.eu/analyse/apis)"

  modis_nasa_earthdata_cmr_future:
    updates:
      discovery_mechanism:
        value: CMR_API_OR_CMR_STAC
        confidence: high
      access_pattern_category:
        value: [api_discovered, credential_gated]
        confidence: high
      credential_requirements:
        value: NASA_EARTHDATA
        confidence: high
      deterministic_url_generation_direct:
        value: false
        confidence: high
      deterministic_asset_resolution_after_discovery:
        value: true
        confidence: high
    citations:
      - "[NASA CMR](https://www.earthdata.nasa.gov/about/esdis/eosdis/cmr)"
      - "[NASA CMR STAC API](https://cmr.earthdata.nasa.gov/stac/docs/index.html)"
      - "[NASA Earthdata Login cURL/Wget Access](https://urs.earthdata.nasa.gov/documentation/for_users/data_access/curl_and_wget)"
      - "[NASA LP DAAC](https://www.earthdata.nasa.gov/centers/lp-daac)"

  usgs_3dep_dem_service_aware:
    updates:
      discovery_mechanism:
        value: TNM_API_OR_USGS_SERVICE_DISCOVERY
        confidence: high
      access_pattern_category:
        value: [service_discovered, api_discovered]
        confidence: high
      deterministic_url_generation_direct:
        value: false
        confidence: high
      deterministic_asset_resolution_after_discovery:
        value: true
        confidence: high
    citations:
      - "[USGS 3DEP Products and Services](https://www.usgs.gov/3d-elevation-program/about-3dep-products-services)"
      - "[USGS 3DEP Elevation FAQ](https://www.usgs.gov/faqs/what-types-elevation-datasets-are-available-what-formats-do-they-come-and-where-can-i-download)"
      - "[USGS 1 Arc-second DEM Data Catalog](https://data.usgs.gov/datacatalog/data/USGS%3A35f9c4d4-b113-4c8d-8691-47c428c29a5b)"
      - "[USGS 1/3 Arc-second DEM Data Catalog](https://data.usgs.gov/datacatalog/data/USGS%3A3a81321b-c153-416f-98b7-cc8e5f0e17c3)"

  noaa_ncei_thredds_raster_future:
    updates:
      discovery_mechanism:
        value: THREDDS_NCSS_QUERY_OR_OPENDAP
        confidence: high
      access_pattern_category:
        value: service_discovered
        confidence: high
      deterministic_url_generation_direct:
        value: false
        confidence: high
      deterministic_asset_resolution_after_discovery:
        value: true
        confidence: high
    citations:
      - "[NOAA NCEI THREDDS Enhanced Access](https://www.ncei.noaa.gov/index.php/access/thredds-user-guide)"
      - "[NOAA NOMADS](https://nomads.ncep.noaa.gov/)"
      - "[NOAA NOMADS GRIB Filter Help](https://nomads.ncep.noaa.gov/info.php?page=gribfilter)"
      - "[NCEI Data Service API](https://www.ncei.noaa.gov/support/access-data-service-api-user-documentation)"

  nasa_earthdata_raster_sources_future:
    updates:
      discovery_mechanism:
        value: CMR_API_OR_CMR_STAC
        confidence: high
      access_pattern_category:
        value: [api_discovered, credential_gated]
        confidence: high
      credential_requirements:
        value: NASA_EARTHDATA
        confidence: high
      deterministic_url_generation_direct:
        value: false
        confidence: high
      deterministic_asset_resolution_after_discovery:
        value: true
        confidence: high
    citations:
      - "[NASA CMR](https://www.earthdata.nasa.gov/about/esdis/eosdis/cmr)"
      - "[NASA CMR Search API](https://cmr.earthdata.nasa.gov/search/site/docs/search/api.html)"
      - "[NASA CMR STAC API](https://cmr.earthdata.nasa.gov/stac/docs/index.html)"
      - "[Earthdata Login Data Access](https://urs.earthdata.nasa.gov/documentation/for_users/data_access)"
      - "[Earthdata User Tokens](https://urs.earthdata.nasa.gov/documentation/for_users/user_token)"

  usgs_raster_services_generic_future:
    updates:
      discovery_mechanism:
        value: USGS_API_OR_SERVICE_METADATA
        confidence: medium
      access_pattern_category:
        value: [service_discovered, api_discovered, future_unverified]
        confidence: medium
      deterministic_url_generation_direct:
        value: false
        confidence: high
      deterministic_asset_resolution_after_discovery:
        value: true
        confidence: medium
    citations:
      - "[USGS Landsat Data Access](https://www.usgs.gov/landsat-missions/landsat-data-access)"
      - "[USGS 3DEP Products and Services](https://www.usgs.gov/3d-elevation-program/about-3dep-products-services)"
      - "[USGS Annual NLCD Data Access](https://www.usgs.gov/centers/eros/science/annual-nlcd-data-access)"
```