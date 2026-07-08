# FasterRaster Service-Aware Registry Reconstruction

This document is a design artifact. It does not change runtime registry behavior.

## 1. Proposed YAML Schema

```yaml
sources:
  example_source_key:
    adapter: stac_api | thredds_ncss | ogc_wcs | arcgis_imageserver | generic_https_template
    provider: string
    product: string
    status: implemented | future_adapter | experimental | needs_official_verification
    trust_level: repo_verified | docs_verified | external_candidate_unverified | inferred_unverified
    current_offline_v0_usable: true | false
    future_live_discovery_required: true | false

    semantic_contract:
      default_semantic_type: categorical | continuous | mixed
      allowed_layers:
        layer_name:
          semantic_type: categorical | continuous
          recommended_resampling: nearest | mode | bilinear | cubic
          nodata: null | number | string
          legend_url: null | string
          qa_policy: null | string
      crs:
        native_crs: null | EPSG code | proj string
        target_crs_recommendation: null | EPSG code
      resolution:
        native_resolution_m: null | number
        min_safe_resolution_m: null | number

    orchestration_logic:
      discovery_mechanism: STAC_API | THREDDS_NCSS_QUERY | OGC_WCS | ARCGIS_IMAGESERVER | STATIC_TEMPLATE
      credential_scope: PUBLIC | NASA_EARTHDATA | USGS_EROS | MRLC_PUBLIC | STATE_PORTAL | NONE
      asset_resolution_strategy: STAC_ASSET_ID | STAC_ASSET_ROLE | NCSS_DYNAMIC_SUBSET | WCS_COVERAGE_ID | ARCGIS_EXPORT_IMAGE | STATIC_TEMPLATE
      stream_handler: GDAL_HTTP_RANGE | GDAL_VSICURL | GDAL_NETCDF | ASYNC_DOWNLOADER | LOCAL_CACHE_ONLY
      live_mode_required: true | false
      source_side_subset:
        spatial: true | false
        temporal: true | false
        variables: true | false
        reprojection: true | false
      capability_probe:
        probe_type: STAC_CONFORMANCE | STAC_SEARCH_EMPTY_OK | THREDDS_METADATA | NCSS_ACCEPTS_SUBSET | WCS_GET_CAPABILITIES | HTTP_HEAD_RANGE | ARCGIS_SERVICE_INFO
        max_probe_bytes: integer
        validates:
          - authentication
          - collection_exists
          - asset_key_exists
          - range_request_supported
          - bbox_subset_supported
          - variable_subset_supported
          - temporal_subset_supported
      retry_policy:
        on_401: refresh_credentials | recatalog | fail_closed
        on_403: refresh_credentials | recatalog | requester_pays_adapter_required | fail_closed
        on_404: recatalog | version_drift | fail_closed
        on_429: backoff
        max_attempts: integer

    discovery:
      endpoint: string
      collection_id: null | string
      item_filters:
        datetime: "{start}/{end}"
        bbox: "{aoi_bbox_epsg4326}"
        intersects: "{aoi_geojson}"
      asset_selectors:
        preferred_asset_ids: []
        preferred_roles: []
        media_types: []

    query_template:
      base_url: null | string
      method: GET | POST
      params: {}

    documentation:
      official_urls: []
      notes: string
      open_questions: []
```

Validation rules:

- Categorical layers may only use `nearest` or `mode`.
- Dynamic discovery sources must declare `orchestration_logic.capability_probe`.
- Credentialed sources must declare a non-`NONE` `credential_scope`.
- `source_side_subset` claims must explicitly identify supported dimensions.
- `trust_level` in `external_candidate_unverified` or `inferred_unverified` cannot emit runtime-ready manifest URLs.
- 401/403 probe failures must route through retry policy handling and produce structured access/capability failures, not `DATASET_MISSING`.
- `STATIC_TEMPLATE` sources with failed 401/403 probes cannot be promoted until official docs plus a bounded probe or adapter-specific health check pass.

## 2. Source-Specific Draft Registry

See [service_aware_registry_draft.yaml](../research/service_aware_registry_draft.yaml).

The draft covers:

- `usgs_annual_nlcd_landcover_service_aware`
- `ornl_daymet_daily_ncss_service_aware`
- `usda_nass_cdl_imageserver_existing`
- `prism_daily_static_range_existing`
- `landsat_collection2_stac_future`

NLCD is intentionally modeled around categorical land cover and service/catalog resolution, not direct promotion of the failed static S3 HTTPS examples.

## 3. OrchestrationEngine Pseudocode

```python
class OrchestrationEngine:
    def plan(self, research_spec, registry, mode):
        aoi = load_aoi(research_spec.aoi)
        aoi4326 = aoi.to_crs("EPSG:4326")
        target_grid = research_spec.target_grid
        time_range = research_spec.time_range
        rows = []

        for source_request in research_spec.sources:
            registry_entry = registry.sources[source_request.registry_key]
            resolved = self.resolve_source(source_request, registry_entry, aoi4326, time_range, mode)
            session = CredentialManager().get_session(registry_entry.orchestration_logic.credential_scope)

            for asset in resolved:
                probe = self.capability_probe(registry_entry, asset, session)
                if probe.status != "PASS":
                    rows.append(build_failure_descriptor(source_request, registry_entry, asset, probe))
                    continue
                stream = self.build_stream_descriptor(registry_entry, asset, session)
                rows.append(compile_manifest_row(source_request, registry_entry, asset, stream, target_grid))

        return deterministic_sort(rows)

    def resolve_source(self, source_request, registry_entry, aoi, time_range, mode):
        logic = registry_entry.orchestration_logic
        if logic.live_mode_required and mode != "live_discovery":
            return [discovery_descriptor(source_request, registry_entry, reason="LIVE_DISCOVERY_REQUIRED")]

        if logic.discovery_mechanism == "STAC_API":
            adapter = StacAdapter()
            items = adapter.search(
                registry_entry.discovery.endpoint,
                registry_entry.discovery.collection_id,
                aoi,
                time_range,
                registry_entry.discovery.item_filters,
            )
            return adapter.select_assets(items, registry_entry.discovery.asset_selectors, source_request.layer)

        if logic.discovery_mechanism == "THREDDS_NCSS_QUERY":
            adapter = ThreddsNcssAdapter()
            metadata = adapter.dataset_metadata(registry_entry.discovery.endpoint)
            return [adapter.build_subset_query(metadata, aoi.bounds, time_range, source_request.variables, "netcdf4")]

        if logic.discovery_mechanism == "ARCGIS_IMAGESERVER":
            return ArcgisImageServerAdapter().plan_descriptor(source_request, registry_entry, aoi, time_range)

        if logic.discovery_mechanism == "STATIC_TEMPLATE":
            return GenericTemplateAdapter().plan_descriptor(source_request, registry_entry, aoi, time_range)

        raise UnsupportedDiscoveryMechanism(logic.discovery_mechanism)

    def capability_probe(self, registry_entry, resolved_asset, session):
        probe_type = registry_entry.orchestration_logic.capability_probe.probe_type
        if probe_type == "STAC_CONFORMANCE":
            return stac_conformance_probe(registry_entry.discovery.endpoint, session)
        if probe_type == "STAC_SEARCH_EMPTY_OK":
            return stac_empty_search_probe(registry_entry.discovery.endpoint, session)
        if probe_type == "THREDDS_METADATA":
            return thredds_metadata_probe(resolved_asset.metadata_url, session)
        if probe_type == "NCSS_ACCEPTS_SUBSET":
            return ncss_small_subset_probe(resolved_asset.request, session)
        if probe_type == "WCS_GET_CAPABILITIES":
            return wcs_get_capabilities_probe(registry_entry.discovery.endpoint, session)
        if probe_type == "HTTP_HEAD_RANGE":
            return bounded_head_or_range_probe(resolved_asset.stream_url, session, max_bytes=registry_entry.orchestration_logic.capability_probe.max_probe_bytes)
        if probe_type == "ARCGIS_SERVICE_INFO":
            return arcgis_service_info_probe(registry_entry.discovery.endpoint, session)
        raise UnsupportedProbeType(probe_type)

    def build_stream_descriptor(self, registry_entry, resolved_asset, session):
        logic = registry_entry.orchestration_logic
        return {
            "source_url": resolved_asset.source_url,
            "stream_url": resolved_asset.stream_url,
            "stream_handler": logic.stream_handler,
            "credential_scope": logic.credential_scope,
            "auth_headers_ref": session.header_ref if session.has_secret_headers else None,
            "source_side_subset": logic.source_side_subset,
        }
```

```python
class CredentialManager:
    def get_session(self, credential_scope):
        if credential_scope in ("PUBLIC", "NONE", "MRLC_PUBLIC"):
            return AnonymousSession()
        if credential_scope == "NASA_EARTHDATA":
            return EarthdataSession.from_netrc_or_token_ref()
        if credential_scope == "USGS_EROS":
            return UsgsErosSession.from_token_ref()
        if credential_scope == "STATE_PORTAL":
            return StatePortalSession.from_named_profile()
        raise UnsupportedCredentialScope(credential_scope)

    def refresh(self, credential_scope):
        session = self.get_session(credential_scope)
        return session.refresh()
```

```python
class StacAdapter:
    def search(self, endpoint, collection_id, aoi, time_range, filters):
        payload = {
            "collections": [collection_id] if collection_id else None,
            "bbox": aoi.bounds,
            "datetime": f"{time_range.start}/{time_range.end}",
            "intersects": aoi.geojson if filters.get("intersects") else None,
        }
        return stac_post_search(endpoint, remove_nulls(payload))

    def select_assets(self, items, asset_selectors, semantic_layer):
        selected = []
        for item in items:
            for asset_id, asset in item.assets.items():
                if asset_id in asset_selectors.preferred_asset_ids:
                    selected.append(asset_to_resolved_reference(item, asset_id, asset, semantic_layer))
                elif intersects(asset.roles, asset_selectors.preferred_roles):
                    selected.append(asset_to_resolved_reference(item, asset_id, asset, semantic_layer))
                elif asset.media_type in asset_selectors.media_types:
                    selected.append(asset_to_resolved_reference(item, asset_id, asset, semantic_layer))
        return deterministic_sort(selected)
```

```python
class ThreddsNcssAdapter:
    def dataset_metadata(self, endpoint):
        return parse_thredds_catalog_or_dataset_xml(endpoint)

    def build_subset_query(self, dataset, aoi_bbox, time_range, variables, output_format):
        params = {
            "var": variables,
            "north": aoi_bbox.north,
            "south": aoi_bbox.south,
            "east": aoi_bbox.east,
            "west": aoi_bbox.west,
            "time_start": time_range.start,
            "time_end": time_range.end,
            "accept": output_format,
        }
        return ResolvedAsset(
            source_url=dataset.url,
            stream_url=encode_url(dataset.ncss_endpoint, params),
            media_type="application/netcdf",
            source_side_subset=True,
        )
```

## 4. Daymet NCSS Dynamic Subset Construction

Exact Daymet NCSS parameter names remain `needs_official_verification` until checked against the current ORNL DAAC THREDDS/NCSS docs.

```python
def build_daymet_ncss_query(aoi_geom, start_date, end_date, variables, dataset_meta):
    bbox4326 = aoi_geom.to_crs("EPSG:4326").bounds
    subset_bbox = expand_to_grid_safe_bbox(bbox4326, dataset_meta.grid_resolution)
    validate_variables(variables, dataset_meta.supported_variables)
    validate_time_range(start_date, end_date, dataset_meta.temporal_extent)

    params = {
        "var": variables,                # needs_official_verification
        "north": subset_bbox.north,      # needs_official_verification
        "south": subset_bbox.south,      # needs_official_verification
        "east": subset_bbox.east,        # needs_official_verification
        "west": subset_bbox.west,        # needs_official_verification
        "time_start": start_date,        # needs_official_verification
        "time_end": end_date,            # needs_official_verification
        "accept": "netcdf4",             # needs_official_verification
    }
    return encode_ncss_url(dataset_meta.ncss_endpoint, params)
```

Required manifest row fields for Daymet:

```yaml
variable: prcp
time_range: {start: "2023-01-01", end: "2023-01-01"}
bbox: {values: [west, south, east, north], crs: EPSG:4326}
source_crs: "Lambert_Conformal_Conic_needs_exact_proj_verification"
output_format: netcdf4
endpoint_version: needs_official_verification
source_side_subset: true
```

## 5. Failure-State Workflow

```yaml
failure_states:
  401:
    steps:
      - refresh credential scope
      - retry metadata/capability request
      - if still failing, mark AUTH_REQUIRED_OR_EXPIRED
    not_allowed: DATASET_MISSING
  403:
    steps:
      - check credential scope
      - check requester-pays or terms-gated access
      - recatalog via STAC/metadata endpoint
      - if still failing, mark ACCESS_POLICY_OR_ASSET_PATH_UNRESOLVED
    not_allowed: DATASET_MISSING
  404:
    steps:
      - recatalog
      - check version/collection drift
      - mark CATALOG_VERSION_DRIFT when prior static path no longer resolves
  429_or_5xx:
    steps:
      - exponential backoff with jitter
      - preserve deterministic request IDs
      - record retry count and endpoint
  schema_or_capability_mismatch:
    steps:
      - fail inspect/preflight before scheduler submission
      - mark CAPABILITY_MISMATCH
```

Manifest-compatible failure object:

```yaml
failure:
  code: AUTH_REQUIRED_OR_EXPIRED | ACCESS_POLICY_OR_ASSET_PATH_UNRESOLVED | CATALOG_VERSION_DRIFT | CAPABILITY_MISMATCH | RATE_LIMITED | SERVER_UNAVAILABLE
  source_stage: discovery | capability_probe | credential_refresh | stream_descriptor | gdal_preflight
  retryable: true | false
  recommended_action: string
  evidence:
    http_status: null | integer
    endpoint: string
    probe_type: string
```

## 6. GDAL Preflight Compiler Interface

Resolved assets hand off to the existing FasterRaster compiler as:

```yaml
resolved_asset:
  request_id: string
  source_id: string
  registry_key: string
  adapter: string
  discovery_mechanism: string
  source_url: string
  stream_url: string
  auth_headers_ref: null | string
  credential_scope: string
  media_type: string
  semantic_type: categorical | continuous
  resampling: string
  source_crs: null | string
  target_grid_crs: string
  bbox:
    values: [xmin, ymin, xmax, ymax]
    crs: string
  time_range:
    start: YYYY-MM-DD
    end: YYYY-MM-DD
  layer: string
  variable: null | string
  nodata: null | number | string
  checksum: null | string
  cache_policy:
    key: string
    range_requests: true | false
    source_side_subset: true | false
```

Compilation mapping:

- `acquisition_manifest.jsonl`: one row per resolved asset or failure descriptor.
- `harmonization_plan.json`: target grid, resampling, nodata, QA policy, and GDAL-compatible input expectations.
- `execution_package.json`: package metadata, input hashes, adapter/source counts, DAG validation.
- `jobs.jsonl`: fetch, validate_download, harmonize, inspect_output stage jobs.
- future GDAL descriptors: `/vsicurl/`, `/vsis3/`, NetCDF subdataset selectors, auth header refs, cache paths, and warp/translate parameters.

## 7. Promotion Checklist

To move a source from `needs_official_verification` to runtime support:

1. Official docs identify the access endpoint and supported access mode.
2. Adapter type is selected: STAC, NCSS, WCS, ArcGIS, static template, or S3 requester-pays.
3. Required placeholders, asset selectors, or query params are documented.
4. CRS, resolution, semantic type, resampling, nodata, and QA policies are known or explicitly nullable.
5. Capability probe is implemented and bounded.
6. Auth/credential scope is declared and does not leak secrets into manifests.
7. 401/403/404 behavior maps to structured failure states.
8. Golden fixtures are added without relying on live network.
9. Optional live probe passes under explicit opt-in where appropriate.
10. Existing deterministic hashes do not drift unless a contract version bump is intentional.

## 8. Official URLs Needing Human Verification

- https://www.usgs.gov/centers/eros/science/annual-nlcd-data-access
- https://www.mrlc.gov/sites/default/files/docs/LSDS-2103%20Annual%20National%20Land%20Cover%20Database%20%28NLCD%29%20Collection%201%20Science%20Product%20User%20Guide%20-v1.0%202024_10_15.pdf
- https://daymet.ornl.gov/getdata
- https://daymet.ornl.gov/static/files/NCSS_Daymet_Subset_Guide_v3.pdf
- https://daac.ornl.gov/DAYMET/guides/Daymet_Daily_V4.html
- https://www.earthdata.nasa.gov/data/catalog/ornl-cloud-daymet-daily-v4r1-2129-4.1
- https://www.nass.usda.gov/Research_and_Science/Cropland/SARS1a.php
- https://pdi.scinet.usda.gov/image/rest/services/CDL_WM/ImageServer
- https://prism.oregonstate.edu/recent/
- https://prism.oregonstate.edu/
- https://www.usgs.gov/landsat-missions/landsat-collection-2
