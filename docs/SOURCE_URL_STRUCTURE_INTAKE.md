# FasterRaster Source URL Structure Intake

Purpose: human-editable intake for enriching FasterRaster source URL contracts. Unknowns are explicit; do not treat them as confirmed facts.

Repository sources inspected:

- `configs/source_registry.yaml`
- `docs/REAL_RASTER_URL_STRUCTURES.md`
- `docs/GENERIC_HTTPS_TEMPLATE.md`
- `docs/ADAPTERS.md`
- `schemas/source_registry.schema.json`
- `tests/golden/source_registry_*.yaml`
- `tests/golden/research_spec_*.json`
- `tests/golden/acquisition_manifest_*.jsonl`

## Current Implemented Source Families

- USDA CDL / Cropland Data Layer: implemented as `arcgis_imageserver`.
- Annual NLCD AWS tile: implemented as `generic_https_template` fixture.
- Annual NLCD AWS mosaic: implemented as `generic_https_template` fixture.
- PRISM daily zip: implemented as `generic_https_template` fixture.
- Daymet NCSS: documented experimental future query-template family, not implemented.
- Generic demo COG: implemented demo/deprecated fixture, not a real source family.


## CDL / USDA Cropland Data Layer

- Current FasterRaster status: implemented
- Adapter type: `arcgis_imageserver`
- Known base URL: `https://pdi.scinet.usda.gov/image/rest/services/CDL_WM/ImageServer`
- Known URL template: ArcGIS ImageServer `exportImage` query built from registry params
- Required placeholders/parameters: `bbox`, `bboxSR`, `imageSR`, `size`, `format`, `f`, `time`
- Optional placeholders/parameters: `mosaicRule` planned for future strategy; not implemented
- Spatial key structure: AOI/tile bbox sent as request `bbox`; `bboxSR` identifies bbox CRS
- Temporal key structure: registry-driven `time={year}`
- Expected file format: `tiff` response format in current registry
- CRS assumptions: service/export image CRS currently `EPSG:3857`; target grid recommendation commonly `EPSG:5070`
- Bbox behavior: `preserve_input_bbox_with_bboxsr`
- Nodata expectation if known: unknown
- Semantic type: categorical
- Recommended resampling: `nearest` or `mode`; current example uses `nearest`
- Checksum/metadata availability if known: unknown
- Rate limit or access caveat if known: unknown
- Official documentation links already known: none encoded in current docs
- Open questions for external AI research:
  - Confirm official USDA/NASS ImageServer endpoint documentation and current service endpoint.
  - Confirm whether `time={year}` is the correct robust year selection method for the CDL_WM service.
  - Confirm nodata, pixel type, categorical legend endpoint, and metadata/checksum availability.
  - Confirm service max image dimensions and whether `4097` is official or observed.
- Confidence level: medium


## NLCD Annual

### Annual NLCD AWS Tile

- Current FasterRaster status: implemented
- Adapter type: `generic_https_template`
- Known base URL: `https://usgs-landcover.s3.us-west-2.amazonaws.com`
- Known URL template: `https://usgs-landcover.s3.us-west-2.amazonaws.com/annual-nlcd/{collection}/{version}/{region}/tile/{tile_id}/Annual_NLCD_{h}{v}_{product_code}_{year}_CU_C1V0.tif`
- Example URL: `https://usgs-landcover.s3.us-west-2.amazonaws.com/annual-nlcd/c1/v0/cu/tile/h14v15/Annual_NLCD_H14V15_FctImp_1985_CU_C1V0.tif`
- Required placeholders: `collection`, `version`, `region`, `tile_id`, `h`, `v`, `product_code`, `year`
- Optional placeholders: unknown
- Spatial key structure: tile key such as `h14v15`, with filename components such as `H14V15`
- Temporal key structure: year in filename
- Expected file format: `.tif`
- CRS assumptions: `EPSG:5070` in current registry
- Bbox behavior: `no_bbox_url_template`
- Nodata expectation if known: unknown
- Semantic type: current implemented fixture is continuous for `FctImp`; land cover should be researched separately as categorical
- Recommended resampling: continuous `bilinear`; categorical products should use `nearest` or `mode`
- Checksum/metadata availability if known: unknown
- Rate limit or access caveat if known: unknown
- Official documentation links already known:
  - https://www.usgs.gov/centers/eros/science/annual-nlcd-data-access
  - https://www.usgs.gov/media/videos/new-annual-1985-2023-national-land-cover-database-improving-a-30-year-legacy
  - https://www.mrlc.gov/sites/default/files/docs/LSDS-2103%20Annual%20National%20Land%20Cover%20Database%20%28NLCD%29%20Collection%201%20Science%20Product%20User%20Guide%20-v1.0%202024_10_15.pdf
- Open questions for external AI research:
  - Confirm all valid `product_code` values and whether land-cover products use a different naming structure.
  - Confirm Collection 1.2/current object paths and whether `c1/v0` remains current.
  - Confirm tile grid documentation and allowed h/v ranges.
  - Confirm nodata values and metadata/checksum sidecars.
- Confidence level: medium

### Annual NLCD AWS Mosaic

- Current FasterRaster status: implemented
- Adapter type: `generic_https_template`
- Known base URL: `https://usgs-landcover.s3.us-west-2.amazonaws.com`
- Known URL template: `https://usgs-landcover.s3.us-west-2.amazonaws.com/annual-nlcd/{collection}/{version}/{region}/mosaic/Annual_NLCD_{product_code}_{year}_CU_C1V0.tif`
- Example URL: `https://usgs-landcover.s3.us-west-2.amazonaws.com/annual-nlcd/c1/v0/cu/mosaic/Annual_NLCD_FctImp_1985_CU_C1V0.tif`
- Required placeholders: `collection`, `version`, `region`, `product_code`, `year`
- Optional placeholders: unknown
- Spatial key structure: mosaic path, no tile key
- Temporal key structure: year in filename
- Expected file format: `.tif`
- CRS assumptions: `EPSG:5070` in current registry
- Bbox behavior: `no_bbox_url_template`
- Nodata expectation if known: unknown
- Semantic type: current implemented fixture is continuous for `FctImp`; land cover should be researched separately as categorical
- Recommended resampling: continuous `bilinear`; categorical products should use `nearest` or `mode`
- Checksum/metadata availability if known: unknown
- Rate limit or access caveat if known: unknown
- Official documentation links already known: same as Annual NLCD tile section
- Open questions for external AI research:
  - Confirm current official mosaic paths for all product codes and versions.
  - Confirm file naming for categorical land-cover products.
  - Confirm metadata and checksum availability.
- Confidence level: medium


## PRISM

- Current FasterRaster status: implemented
- Adapter type: `generic_https_template`
- Known base URL: `https://data.prism.oregonstate.edu`
- Known URL template: `https://data.prism.oregonstate.edu/time_series/{region}/an/{resolution}/{variable}/{temporal_frequency}/{year}/prism_{variable}_{region}_25m_{yyyymmdd}.zip`
- Example URL: `https://data.prism.oregonstate.edu/time_series/us/an/4km/ppt/daily/2026/prism_ppt_us_25m_20260101.zip`
- Required placeholders: `region`, `resolution`, `variable`, `temporal_frequency`, `year`, `yyyymmdd`
- Optional placeholders: unknown
- Spatial key structure: region code such as `us`; no bbox in URL template
- Temporal key structure: year directory plus `yyyymmdd` filename key
- Expected file format: `.zip` containing raster-related files; FasterRaster does not extract by default
- CRS assumptions: `EPSG:4326` in current registry
- Bbox behavior: `no_bbox_url_template`
- Nodata expectation if known: unknown
- Semantic type: continuous
- Recommended resampling: `bilinear`; `nearest` or `cubic` also allowed by current continuous policy
- Checksum/metadata availability if known: unknown
- Rate limit or access caveat if known: unknown
- Official documentation links already known:
  - https://prism.oregonstate.edu/recent/
  - https://prism.oregonstate.edu/
- Open questions for external AI research:
  - Confirm all valid variables, temporal frequencies, regions, resolution path components, and filename resolution token `25m`.
  - Confirm CRS, nodata values, sidecar metadata, and checksum availability.
  - Confirm whether bounded streaming probes are acceptable and recommended size limits.
- Confidence level: high for encoded example URL, medium for broader template coverage


## Daymet

- Current FasterRaster status: experimental/documented only
- Adapter type: future `future_ncss` or `future_api`; not implemented
- Known base URL: `https://thredds.daac.ornl.gov/thredds/ncss/grid/ornldaac/1328/`
- Known URL template: experimental concept only, not implemented: `https://thredds.daac.ornl.gov/thredds/ncss/grid/ornldaac/1328/{year}/daymet_v3_{variable}_{year}_{region}.nc4?...params...`
- Required placeholders: unknown; likely `year`, `variable`, `region`, query params
- Optional placeholders: unknown
- Spatial key structure: likely NCSS query bbox/subset; requires research
- Temporal key structure: year and query date/time parameters; requires research
- Expected file format: likely NetCDF/NC4 from NCSS; requires confirmation
- CRS assumptions: unknown
- Bbox behavior: query/subset URL family; not `generic_https_template`
- Nodata expectation if known: unknown
- Semantic type: continuous for climate variables, but variable-specific
- Recommended resampling: unknown; likely continuous `bilinear` if gridded climate rasters are later harmonized
- Checksum/metadata availability if known: unknown
- Rate limit or access caveat if known: unknown
- Official documentation links already known:
  - https://daymet.ornl.gov/getdata
  - https://daymet.ornl.gov/static/files/NCSS_Daymet_Subset_Guide_v3.pdf
- Open questions for external AI research:
  - Determine whether deterministic NCSS query generation is safe and sufficient.
  - Determine required query parameters and output format options.
  - Determine whether a dedicated `thredds_ncss_template` adapter is needed.
- Confidence level: low


## DEM / elevation

- Current FasterRaster status: missing
- Adapter type: unknown; possible `generic_https_template`, `future_stac`, `future_api`, or ArcGIS depending on source
- Known base URL: unknown
- Known URL template: unknown
- Required placeholders: unknown
- Optional placeholders: unknown
- Spatial key structure: unknown
- Temporal key structure: unknown
- Expected file format: unknown
- CRS assumptions: unknown
- Bbox behavior: unknown
- Nodata expectation if known: unknown
- Semantic type: continuous
- Recommended resampling: `bilinear` or `cubic`, subject to source guidance
- Checksum/metadata availability if known: unknown
- Rate limit or access caveat if known: unknown
- Official documentation links already known: none encoded in current docs
- Open questions for external AI research:
  - Identify deterministic URL families for USGS 3DEP, Copernicus DEM, SRTM, or similar elevation products.
- Confidence level: low


## MODIS

- Current FasterRaster status: missing
- Adapter type: unknown; likely future API/STAC/Earthdata-style access depending on product
- Known base URL: unknown
- Known URL template: unknown
- Required placeholders: unknown
- Optional placeholders: unknown
- Spatial key structure: unknown
- Temporal key structure: unknown
- Expected file format: unknown
- CRS assumptions: unknown
- Bbox behavior: unknown
- Nodata expectation if known: unknown
- Semantic type: product-specific; categorical or continuous
- Recommended resampling: product-specific
- Checksum/metadata availability if known: unknown
- Rate limit or access caveat if known: unknown
- Official documentation links already known: none encoded in current docs
- Open questions for external AI research:
  - Identify official deterministic URL, Earthdata, STAC, or API pathways by product.
- Confidence level: low


## Landsat

- Current FasterRaster status: missing
- Adapter type: unknown; likely future STAC/API or cloud object template depending on collection/provider
- Known base URL: unknown
- Known URL template: unknown
- Required placeholders: unknown
- Optional placeholders: unknown
- Spatial key structure: unknown
- Temporal key structure: unknown
- Expected file format: unknown
- CRS assumptions: unknown
- Bbox behavior: unknown
- Nodata expectation if known: unknown
- Semantic type: continuous for reflectance/thermal bands; QA/categorical products vary
- Recommended resampling: product-specific
- Checksum/metadata availability if known: unknown
- Rate limit or access caveat if known: unknown
- Official documentation links already known: none encoded in current docs
- Open questions for external AI research:
  - Determine deterministic public cloud URL structures for Landsat Collection 2 assets and whether STAC search is required.
- Confidence level: low


## Sentinel

- Current FasterRaster status: missing
- Adapter type: unknown; likely future STAC/API or cloud object template depending on provider
- Known base URL: unknown
- Known URL template: unknown
- Required placeholders: unknown
- Optional placeholders: unknown
- Spatial key structure: unknown
- Temporal key structure: unknown
- Expected file format: unknown
- CRS assumptions: unknown
- Bbox behavior: unknown
- Nodata expectation if known: unknown
- Semantic type: product-specific
- Recommended resampling: product-specific
- Checksum/metadata availability if known: unknown
- Rate limit or access caveat if known: unknown
- Official documentation links already known: none encoded in current docs
- Open questions for external AI research:
  - Determine current official public access patterns and whether STAC/API search is required.
- Confidence level: low


## NOAA climate/ocean/coastal rasters

- Current FasterRaster status: missing
- Adapter type: unknown; likely future API, WCS/OPeNDAP/THREDDS/NCSS, or object templates depending on dataset
- Known base URL: unknown
- Known URL template: unknown
- Required placeholders: unknown
- Optional placeholders: unknown
- Spatial key structure: unknown
- Temporal key structure: unknown
- Expected file format: unknown
- CRS assumptions: unknown
- Bbox behavior: unknown
- Nodata expectation if known: unknown
- Semantic type: usually continuous, but product-specific
- Recommended resampling: product-specific
- Checksum/metadata availability if known: unknown
- Rate limit or access caveat if known: unknown
- Official documentation links already known: none encoded in current docs
- Open questions for external AI research:
  - Identify deterministic raster URL structures for NOAA gridded climate, ocean, and coastal products.
- Confidence level: low


## NASA Earthdata-style rasters

- Current FasterRaster status: missing
- Adapter type: unknown; likely future API/STAC/Earthdata/CMR access
- Known base URL: unknown
- Known URL template: unknown
- Required placeholders: unknown
- Optional placeholders: unknown
- Spatial key structure: unknown
- Temporal key structure: unknown
- Expected file format: unknown
- CRS assumptions: unknown
- Bbox behavior: unknown
- Nodata expectation if known: unknown
- Semantic type: product-specific
- Recommended resampling: product-specific
- Checksum/metadata availability if known: unknown
- Rate limit or access caveat if known: unknown
- Official documentation links already known: none encoded in current docs
- Open questions for external AI research:
  - Identify which NASA raster products are deterministic static URLs and which require Earthdata Search/CMR/STAC.
- Confidence level: low


## USGS raster services

- Current FasterRaster status: missing
- Adapter type: `generic_https_template`, `arcgis_imageserver`, future STAC/API depending on source
- Known base URL: unknown
- Known URL template: unknown
- Required placeholders: unknown
- Optional placeholders: unknown
- Spatial key structure: unknown
- Temporal key structure: unknown
- Expected file format: unknown
- CRS assumptions: unknown
- Bbox behavior: unknown
- Nodata expectation if known: unknown
- Semantic type: categorical or continuous depending on product
- Recommended resampling: product-specific
- Checksum/metadata availability if known: unknown
- Rate limit or access caveat if known: unknown
- Official documentation links already known: none encoded in current docs
- Open questions for external AI research:
  - Identify additional USGS deterministic raster object URL families and services requiring ArcGIS, STAC, WCS, or API search.
- Confidence level: low


## state/local ArcGIS ImageServer sources

- Current FasterRaster status: missing
- Adapter type: `arcgis_imageserver`
- Known base URL: unknown
- Known URL template: unknown
- Required placeholders: unknown
- Optional placeholders: unknown
- Spatial key structure: unknown
- Temporal key structure: unknown
- Expected file format: unknown
- CRS assumptions: unknown
- Bbox behavior: unknown
- Nodata expectation if known: unknown
- Semantic type: source-specific
- Recommended resampling: categorical `nearest`/`mode`; continuous `bilinear`/`cubic` as appropriate
- Checksum/metadata availability if known: unknown
- Rate limit or access caveat if known: unknown
- Official documentation links already known: none encoded in current docs
- Open questions for external AI research:
  - Identify state/local ImageServer services that publish downloadable raster data and their max dimensions/formats/time rules.
- Confidence level: low
