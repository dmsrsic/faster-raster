# Source Stack Matrix

## Verified now

| Source | Provider | Access | Probe | Bytes | Next unlock |
| --- | --- | --- | --- | ---: | --- |
| `prism_daily_ppt_static_zip` | PRISM Climate Group | `static_https` | `pass_verified` | 65536 | preserve proof and add contract fixture |
| `daymet_single_pixel_prcp_rest` | ORNL DAAC | `parameterized_rest` | `pass_verified` | 474 | preserve proof and add contract fixture |
| `cdl_arcgis_tiny_export` | USDA_NASS | `arcgis_imageserver` | `pass_verified` | 1146 | preserve proof and add contract fixture |

## Credential gated

| Source | Provider | Access | Probe | Bytes | Next unlock |
| --- | --- | --- | --- | ---: | --- |
| `daymet_ncss` | ORNL DAAC | `thredds_ncss` | `not_in_stack_probe` | 0 | complete auth scaffold before probe |
| `nlcd_annual_landcover` | USGS/MRLC | `s3_requester_pays` | `credential_gated` | 0 | complete auth scaffold before probe |
| `landsat_collection2` | USGS | `stac_api` | `adapter_needed` | 0 | complete auth scaffold before probe |
| `sentinel_copernicus_dataspace` | Copernicus | `stac_api` | `not_in_stack_probe` | 0 | complete auth scaffold before probe |
| `modis_nasa_earthdata_cmr` | NASA Earthdata | `cmr_api` | `not_in_stack_probe` | 0 | complete auth scaffold before probe |
| `era5_cds` | ECMWF Copernicus Climate Data Store | `authenticated_https` | `not_in_stack_probe` | 0 | complete auth scaffold before probe |
| `nldas_gldas_gesdisc` | NASA GES DISC | `cmr_api` | `not_in_stack_probe` | 0 | complete auth scaffold before probe |
| `cmip6_esgf` | ESGF | `authenticated_https` | `not_in_stack_probe` | 0 | complete auth scaffold before probe |
| `microsoft_planetary_computer_stac` | Microsoft Planetary Computer | `stac_api` | `not_in_stack_probe` | 0 | complete auth scaffold before probe |
| `opentopography_dem` | OpenTopography | `authenticated_https` | `not_in_stack_probe` | 0 | complete auth scaffold before probe |
| `eobs_european_climate` | ECA&D / Copernicus-adjacent | `mirror_https` | `not_in_stack_probe` | 0 | complete auth scaffold before probe |
| `ecmwf_copernicus_mirror_candidate` | ECMWF/Copernicus | `mirror_https` | `not_in_stack_probe` | 0 | complete auth scaffold before probe |

## Adapter needed

| Source | Provider | Access | Probe | Bytes | Next unlock |
| --- | --- | --- | --- | ---: | --- |
| `usgs_3dep_dem` | USGS | `unknown_future` | `adapter_needed` | 0 | design adapter and metadata probe |
| `noaa_ncei_service_class` | NOAA/NCEI | `thredds_catalog` | `not_in_stack_probe` | 0 | design adapter and metadata probe |
| `terraclimate_monthly` | University of Idaho / TerraClimate | `opendap` | `not_in_stack_probe` | 0 | design adapter and metadata probe |
| `gridmet_daily` | University of Idaho / gridMET | `opendap` | `not_in_stack_probe` | 0 | design adapter and metadata probe |
| `noaa_gfs_nomads_grib_filter` | NOAA/NCEP | `grib_filter` | `not_in_stack_probe` | 0 | design adapter and metadata probe |
| `noaa_hrrr_public_cloud` | NOAA | `s3_public` | `not_in_stack_probe` | 0 | design adapter and metadata probe |
| `noaa_mrms_precipitation` | NOAA | `unknown_future` | `not_in_stack_probe` | 0 | design adapter and metadata probe |

## Mirror candidates

| Source | Provider | Access | Probe | Bytes | Next unlock |
| --- | --- | --- | --- | ---: | --- |
| `pangeo_climate_zarr_catalogs` | Pangeo community | `mirror_https` | `not_in_stack_probe` | 0 | verify provenance and bounded probe |

## Future unverified

| Source | Provider | Access | Probe | Bytes | Next unlock |
| --- | --- | --- | --- | ---: | --- |
| `chirps_daily_precipitation` | UCSB CHC | `static_https` | `not_in_stack_probe` | 0 | docs verification or bounded probe design |
| `worldclim_bioclim_normals` | WorldClim | `static_https` | `not_in_stack_probe` | 0 | docs verification or bounded probe design |
| `daymet_single_pixel_rest_duplicate_guard` | ORNL DAAC | `parameterized_rest` | `not_in_stack_probe` | 0 | docs verification or bounded probe design |
