# Source Enrichment Review

This review compares the raw model enrichment in `research/model_source_enrichment_raw.md` against the existing service-aware draft and FasterRaster's current runtime behavior.

Runtime impact: none. The runtime registry was not changed.

## What The Model Response Got Right

- It correctly moves NLCD away from anonymous static HTTPS URL promotion after the 403 probe results.
- It correctly treats Daymet as a THREDDS/NCSS dynamic subset source rather than a static object URL template.
- It reinforces that Landsat, Sentinel, MODIS, and NASA Earthdata-style rasters need discovery/catalog and credential-aware workflows.
- It distinguishes CDL and PRISM as closer to current FasterRaster behavior.
- It identifies 3DEP and NOAA as strong candidates for service/capability-oriented future work rather than static-path guessing.

## What Is Unsafe To Apply Directly

- Model confidence values are not verification.
- Several endpoint, nodata, CRS, product-code, and rate-limit claims require official-doc verification.
- NLCD requester-pays or catalog-aware access cannot be implemented by simply changing a URL template.
- Daymet exact NCSS query parameters must come from current endpoint metadata before runtime support.
- Generic USGS, NASA, NOAA, Landsat, Sentinel, and MODIS entries are too broad for direct registry promotion.

## Closest To Runtime Support

- `usda_nass_cdl_imageserver_existing`: current runtime already supports the ArcGIS ImageServer pattern.
- `prism_daily_static_range_existing`: current bounded probe passed for a representative static ZIP URL.

## Needs Official-Doc Verification

- NLCD exact catalog/object layout, requester-pays behavior, categorical nodata/legend/checksum metadata.
- PRISM 2025+ COG/800m transition and versioned path matrix.
- CDL service metadata fields such as max dimensions, nodata, timeInfo, and pixel type.
- 3DEP selected endpoint/API and resolution-specific object/service metadata.

## Needs Bounded Probe Design

- NLCD through a requester-pays-aware or catalog-aware probe.
- Daymet via metadata and tiny NCSS subset probes, not static URL range probes.
- 3DEP after selecting a concrete TNM/API/ImageServer product path.
- NOAA after choosing a concrete THREDDS/NCSS/NOMADS dataset.

## Implies Future Adapters

- NLCD: requester-pays S3/catalog resolver or STAC/catalog-aware adapter.
- Daymet: `thredds_ncss` adapter.
- Landsat: STAC/M2M adapter.
- Sentinel: STAC/OData adapter.
- MODIS/NASA Earthdata: CMR/CMR-STAC adapter with credential handling.
- NOAA: THREDDS/NCSS/OPeNDAP/NOMADS adapter family.

## Review Table

| source_id | proposed access pattern | current FasterRaster status | recommended action | probe safety | adapter implication | verification priority |
|---|---|---|---|---|---|---|
| `usda_nass_cdl_imageserver_existing` | service_discovered | implemented | keep docs/probe metadata only | safe bounded service-info/tiny export | existing `arcgis_imageserver` | high |
| `usgs_annual_nlcd_landcover_service_aware` | service_discovered + credential_gated | research/future | design requester-pays/catalog probe | safe only with correct adapter/session | future catalog/S3 requester-pays | high |
| `prism_daily_static_range_existing` | static_verified | implemented | keep docs; verify versioned product matrix | safe bounded range on known URL | existing `generic_https_template` for legacy URL | high |
| `ornl_daymet_daily_ncss_service_aware` | service_discovered | future_adapter | design NCSS adapter/probe | safe metadata/tiny subset only | future `thredds_ncss` | high |
| `landsat_collection2_stac_future` | api_discovered + credential_gated | future | adapter stub/design only | safe after catalog/auth | future STAC/M2M | medium |
| `sentinel_copernicus_dataspace_future` | api_discovered + credential_gated | missing | adapter stub/design only | safe after catalog/auth | future STAC/OData | medium |
| `modis_nasa_earthdata_cmr_future` | api_discovered + credential_gated | missing | adapter stub/design only | metadata probe first | future CMR/STAC | medium |
| `usgs_3dep_dem_service_aware` | service_discovered + api_discovered | missing | bounded probe design | safe after concrete endpoint selection | future TNM/API/ImageServer mix | high |
| `noaa_ncei_thredds_raster_future` | service_discovered | missing | adapter design after choosing dataset | metadata/tiny subset only | future THREDDS/NCSS/NOMADS | medium |
| `nasa_earthdata_raster_sources_future` | api_discovered + credential_gated | missing | product-specific adapter planning | safe after auth/catalog | future CMR/STAC | medium |
| `usgs_raster_services_generic_future` | generic taxonomy | docs only | keep docs only | unsafe without concrete endpoint | product-specific adapters only | low |