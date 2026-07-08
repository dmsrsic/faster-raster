# Gemini Source URL Enrichment Prompt

You are helping enrich FasterRaster, an HPC preflight compiler for deterministic raster acquisition and harmonization.

Task: Review the attached source URL structure intake file and enrich it using official public dataset documentation.

Rules:

- Use official dataset documentation wherever possible.
- Do not invent URL structures.
- If a URL pattern is inferred, label it as inferred.
- Prefer official documentation, official cloud bucket docs, official API docs, or official examples.
- Distinguish confirmed URL templates from inferred ones.
- Provide example URLs.
- Identify placeholders and allowed values.
- Identify date, tile, product, version, band, variable, and region naming rules.
- Identify CRS, format, nodata, and metadata/checksum details where documented.
- Identify whether each source is suitable for deterministic URL generation.
- Identify whether STAC, API, search, NCSS, WCS, WMS, WMTS, or other service logic is required instead.
- Output changes in a YAML-compatible structure.
- Cite official docs for each claim.
- Keep unknowns as `null` or `unknown`.
- Do not simplify across dataset families.
- Treat CDL, NLCD, PRISM, Daymet, DEM, MODIS, Landsat, Sentinel, NOAA, NASA Earthdata, USGS, and ArcGIS ImageServer sources separately.

For each dataset family, provide:

- confirmed base URL or access endpoint
- confirmed URL template if deterministic
- example URL
- placeholders and allowed values
- temporal key structure
- spatial tile/grid key structure
- file format
- CRS/projection assumptions
- nodata metadata if documented
- checksum or metadata availability
- whether bounded streaming probes are safe
- whether the source is suitable for deterministic URL generation
- whether access requires API/search/STAC/NCSS instead
- official citation links
- confidence level
- unresolved questions

Return YAML-compatible structure using this shape:

```yaml
sources:
  - source_family:
    source_id:
    status:
    adapter_recommendation:
    deterministic_url_generation:
      suitable:
      confidence:
      reason:
    confirmed_access:
      base_url:
      url_templates:
        - name:
          template:
          example_url:
          confirmed_or_inferred:
          placeholders:
          allowed_values:
    spatial_keys:
    temporal_keys:
    format:
    crs:
    nodata:
    metadata:
      checksum_available:
      metadata_endpoint:
      sidecar_files:
    access_caveats:
    bounded_probe:
      safe:
      suggested_max_bytes:
      reason:
    official_citations:
    unresolved_questions:
```
