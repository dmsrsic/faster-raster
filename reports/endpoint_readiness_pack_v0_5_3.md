# Endpoint Readiness Pack v0.5.3

No live probes were run. This pack ranks no-auth candidate sauces for the next bounded metadata/range test.

| Source | Endpoint status | Probe | Safety | Score | Next action |
| --- | --- | --- | --- | ---: | --- |
| `gridmet_daily` | `verified_docs_only` | `docs_only` | `needs_endpoint_first` | 92 | verify exact official THREDDS/OPeNDAP catalog endpoint |
| `terraclimate_monthly` | `verified_docs_only` | `docs_only` | `needs_endpoint_first` | 88 | verify exact official THREDDS/OPeNDAP/NCSS endpoint |
| `chirps_daily_precipitation` | `verified_docs_only` | `docs_only` | `needs_endpoint_first` | 82 | verify official daily TIFF directory/object semantics |
| `noaa_gfs_nomads_grib_filter` | `verified_catalog_candidate` | `metadata_http_dip` | `needs_endpoint_first` | 86 | choose dynamic date/run/file and construct bounded filter URL |
| `noaa_ncei_service_class` | `verified_catalog_candidate` | `catalog_http_dip` | `needs_adapter_first` | 78 | choose concrete NCEI dataset catalog before probe |
| `usgs_3dep_dem` | `blocked_by_adapter_design` | `docs_only` | `needs_adapter_first` | 74 | design TNM/API metadata adapter before live dip |
| `noaa_hrrr_public_cloud` | `blocked_by_adapter_design` | `docs_only` | `needs_adapter_first` | 70 | define public-cloud object path/range policy before live dip |
| `noaa_mrms_precipitation` | `blocked_by_endpoint_uncertainty` | `docs_only` | `needs_endpoint_first` | 64 | verify official endpoint/access pattern first |
| `worldclim_bioclim_normals` | `blocked_by_endpoint_uncertainty` | `docs_only` | `needs_endpoint_first` | 62 | verify official static URL template before live dip |
