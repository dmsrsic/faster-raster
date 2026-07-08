# Daymet NCSS Adapter Design

Status: experimental design. Runtime impact: none.

## Why Daymet Is The First Service-Aware Probe Target

Daymet is the right first service-aware probe target because it is not a static object-path source. It requires source-side variable, time, and spatial subsetting through THREDDS/NCSS-style service semantics. That exercises FasterRaster's next architecture boundary without adding raster downloads or GDAL execution.

The existing live probe showed that a guessed Daymet NCSS URL returned `401`, so this design does not promote any URL into runtime support. It defines how a metadata-first, bounded, opt-in probe should work once official endpoint syntax is confirmed.

## THREDDS / NCSS Discovery Model

The future adapter should work in two phases:

1. Metadata discovery: request THREDDS catalog or dataset metadata for a known Daymet collection endpoint.
2. Subset request planning: construct an NCSS request descriptor from dataset metadata, AOI bbox, variables, date range, and requested output format.

Metadata discovery must happen before byte probes. Normal validation and planning remain offline unless the user explicitly asks for probe mode.

## Required Query Parameters

Exact Daymet/THREDDS parameter names are `needs_official_verification`. Candidate fields are `var`, `north`, `south`, `east`, `west`, `time_start`, `time_end`, `timeStride`, `horizStride`, and `accept`.

Do not hardcode these into runtime support until current endpoint metadata confirms them.

## AOI Bbox Handling

The probe scenario uses a tiny EPSG:4326 bbox. The future adapter should normalize AOI bounds to EPSG:4326 for NCSS query construction unless endpoint metadata says otherwise, conservatively expand or round bbox edges so the returned subset contains the AOI, record both source AOI bbox and query bbox in the planned request object, and avoid reprojection libraries in this milestone.

## Variable Selection

Initial probe design uses one variable: `prcp`. `tmax` is also a reasonable first candidate. Supported variables must be read from official Daymet metadata before runtime support. Common candidate variables are `prcp`, `tmin`, `tmax`, `srad`, `vp`, `swe`, and `dayl`, but these remain verification targets in FasterRaster.

## Date Range Handling

The initial probe uses a one-day range. Future planning should preserve requested `start_date`, requested `end_date`, endpoint date-time syntax, and Daymet calendar caveats.

## Output Format Expectations

The expected output is a NetCDF subset. Prefer NetCDF for climate stacks unless Daymet documentation confirms a GeoTIFF option for the relevant service endpoint.

## Expected NetCDF Handling

This milestone does not read NetCDF. Future preflight can record stream handler `GDAL_NETCDF`, expected media type if metadata provides it, variable names, CRS/projection metadata, and nodata/fill values when confirmed.

## CRS / Projection Assumptions

Daymet uses a projected gridded climate product, commonly described as Lambert Conformal Conic in docs. Exact projection strings, grid origin, pixel size, and coordinate variable behavior must be read from official metadata. The probe spec keeps CRS fields as `needs_official_verification`.

## Daymet Calendar Caveat

Daymet uses a no-leap calendar convention in many products. Leap-day/date mapping behavior must be explicitly represented in future planning so requested dates and returned indices are reproducible.

## Nodata / Metadata Expectations

Nodata/fill values and metadata attributes are not runtime constants yet. Future adapter promotion must extract or cite `_FillValue`, missing value per variable, units, projection metadata, time/calendar metadata, and dataset version.

## Bounded Probe Strategy

Probe is explicit opt-in only:

1. Metadata-only probe: request catalog/dataset metadata with a small byte cap.
2. Tiny subset probe: one variable, one small bbox, one short date range, NetCDF response, max bytes cap.

The probe must not extract or parse full data. It writes JSON and Markdown reports and is skipped by normal tests.

## Failure States

- `401`: `AUTH_REQUIRED_OR_EXPIRED`; do not mark dataset missing.
- `403`: `ACCESS_POLICY_OR_ASSET_PATH_UNRESOLVED`.
- `404`: `CATALOG_VERSION_DRIFT` or endpoint mismatch.
- `429`: `RATE_LIMITED` with backoff recommendation.
- `5xx`: `SERVER_UNAVAILABLE`.
- capability mismatch: fail preflight before scheduler submission.

## Deterministic Manifest Fields

A future planned request descriptor should include `request_id`, `source_id`, `registry_key`, `adapter: thredds_ncss`, `discovery_mechanism: THREDDS_NCSS_QUERY`, `endpoint`, `method`, `params`, `variables`, `time_range`, `bbox`, `bbox_crs`, `target_grid_crs`, `semantic_type`, `resampling`, `expected_format: netcdf`, `max_probe_bytes`, and `status: planned_experimental`.

## Future Harmonization Implications

Daymet harmonization will differ from object downloads. The future plan should preserve variable dimension names, time axis metadata, calendar convention, source CRS, target CRS, nodata/fill values, and continuous resampling policy. No categorical resampling rules apply to Daymet climate variables.

## Access-Surface Classification Probe - 2026-07-07

A bounded metadata/access-surface probe checked five Daymet THREDDS/NCSS endpoints with a 65,536 byte cap and no subset request. No public metadata endpoint succeeded.

Observed classifications:

- THREDDS catalog XML: HTTP 400, `malformed_request_expected`
- THREDDS catalog HTML: HTTP 400, `malformed_request_expected`
- THREDDS dataset catalog page: HTTP 401, `unauthorized`
- NCSS dataset form: HTTP 401, `unauthorized`
- Raw NCSS endpoint without query: HTTP 401, `unauthorized`

Implication: do not update the experimental probe spec to a catalog endpoint yet. The next design step is credential/session-aware Daymet THREDDS handling or an alternate Daymet service surface, such as the single-pixel API, for the first public service proof.

## Pivot To Single-Pixel REST Proof - 2026-07-07

Daymet NCSS is now classified as `credential_or_session_gated_research` for FasterRaster planning. The access-surface probe found no public 200 metadata endpoint across the tested THREDDS catalog, dataset catalog, NCSS form, or raw NCSS endpoints.

The first public Daymet service proof should therefore use the documented single-pixel REST API:

`https://daymet.ornl.gov/single-pixel/api/data?lat=Latitude&lon=Longitude&vars=CommaSeparatedVariables&start=StartDate&end=EndDate`

This proof demonstrates deterministic service request compilation and bounded response capture for Daymet without implying raster/NCSS support. NCSS remains a future credential/session-aware adapter design.
