# Gemini Prompt: Clarify Failed FasterRaster URL Probes

You are helping FasterRaster, an offline-first HPC preflight compiler for deterministic raster acquisition and harmonization.

FasterRaster has candidate URL structures for several public raster data families. A bounded live probe was run with a maximum read of 65,536 bytes per URL. The goal was not to download data, only to verify whether the documented URL structure responds to a very small HTTP request.

## Probe Results Needing Clarification

These two source families failed and need official-documentation review:

```yaml
failed_probes:
  - probe_id: nlcd_aws_tile
    source_family: Annual NLCD
    url_tested: "https://usgs-landcover.s3.us-west-2.amazonaws.com/annual-nlcd/c1/v0/cu/tile/h14v15/Annual_NLCD_H14V15_FctImp_1985_CU_C1V0.tif"
    result:
      http_status: 403
      bytes_read: 0
      interpretation: "The current S3 URL pattern or access method may be wrong, outdated, permission-restricted, or missing required current Collection/version path details."

  - probe_id: nlcd_aws_mosaic
    source_family: Annual NLCD
    url_tested: "https://usgs-landcover.s3.us-west-2.amazonaws.com/annual-nlcd/c1/v0/cu/mosaic/Annual_NLCD_FctImp_1985_CU_C1V0.tif"
    result:
      http_status: 403
      bytes_read: 0
      interpretation: "The current S3 mosaic URL pattern or access method may be wrong, outdated, permission-restricted, or missing required current Collection/version path details."

  - probe_id: daymet_ncss_tiny_query_experimental
    source_family: Daymet
    url_tested: "https://thredds.daac.ornl.gov/thredds/ncss/grid/ornldaac/2129/daymet_v4_daily_na_prcp_2023.nc?var=prcp&north=40.1&west=-83.2&east=-83.1&south=40.0&disableProjSubset=on&horizStride=1&time_start=2023-01-01T12:00:00Z&time_end=2023-01-01T12:00:00Z&timeStride=1&accept=netcdf"
    result:
      http_status: 401
      bytes_read: 0
      interpretation: "The current Daymet NCSS path, dataset id, service endpoint, query parameters, or access policy may be wrong. This is experimental and not implemented in FasterRaster runtime."
```

## What to Research

Use official dataset documentation wherever possible. Do not invent working URLs. If a URL pattern is inferred, label it as inferred.

For **Annual NLCD**, determine:

- The current official AWS/S3/HTTPS access documentation.
- Whether `usgs-landcover.s3.us-west-2.amazonaws.com` is still the correct public host.
- Whether Annual NLCD Collection 1 paths use `c1/v0`, another version path, or a newer Collection 1.2 layout.
- The current official tile URL pattern for fractional impervious, land cover, and other products.
- The current official mosaic URL pattern.
- Valid placeholders and values:
  - `collection`
  - `version`
  - `region`
  - `tile_id`
  - `h`
  - `v`
  - `product_code`
  - `year`
- One small-ish confirmed example URL if available.
- Whether public HTTPS supports `Range` requests.
- Whether requests need a specific host style, signed URL, requester pays, user-agent, or alternate access mechanism.
- Whether bounded streaming probes are acceptable.

For **Daymet**, determine:

- The current official Daymet THREDDS/NCSS service endpoint.
- The correct dataset id/path for daily Daymet data.
- Whether Daymet v3, v4, or another version should be used for current URLs.
- Correct NCSS query parameter names for:
  - variable
  - north/south/east/west
  - time start/end
  - output format
- Whether access requires authentication or a different endpoint.
- A minimal confirmed NCSS query URL for one variable, one day, and a tiny bbox.
- Whether deterministic NCSS query generation is suitable for FasterRaster.
- Whether this should become adapter type `future_ncss` / `thredds_ncss_template` instead of `generic_https_template`.

## Required Output Format

Return YAML-compatible output only.

```yaml
source_url_probe_fixes:
  - source_family:
    current_probe_id:
    diagnosis:
      likely_failure_reason:
      confirmed_or_inferred:
      official_citations:
    corrected_access:
      base_url:
      url_template:
      example_url:
      placeholders:
      allowed_values:
      requires_auth:
      supports_range_requests:
      recommended_probe_method:
    adapter_recommendation:
    fasterraster_change_recommendation:
      update_runtime_registry: true_or_false
      add_new_adapter: true_or_false
      add_docs_only: true_or_false
      reason:
    confidence:
    unresolved_questions:
```

## Constraints

- Prefer official documentation, official bucket docs, official THREDDS docs, official API docs, or official examples.
- Do not use random blog posts as the only source.
- Do not claim a URL is confirmed unless the official docs or a live official endpoint example support it.
- Keep unknowns as `unknown` or `null`.
- Do not recommend implementing Daymet as a static URL template if NCSS query logic is required.

