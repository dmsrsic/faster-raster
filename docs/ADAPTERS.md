# FasterRaster Adapter Contracts

Adapters convert semantic source requests plus registry metadata into deterministic acquisition manifest rows. They must not download data or call external endpoints in v0.

## Adapter Expectations

Every adapter must:

- Validate capability through `validate_spec` before planning.
- Generate stable `request_id` values.
- Preserve source, layer, year, CRS, bbox, tile, and resampling semantics.
- Sort output deterministically.
- Use registry-defined parameter names.
- Keep URL generation pure and offline.
- Fail clearly for unsupported capabilities.

## ArcGIS ImageServer Adapter

The ArcGIS adapter emits `exportImage` URLs.

Required URL params are registry-defined:

- `bbox`
- `bboxSR`
- `imageSR`
- `size`
- `format`
- `f`
- `time` when `year_parameter_strategy: time_value`

Required source capability fields:

- `bbox_request_policy`
- `supports_bbox_crs_param`
- `service_crs`
- `default_export_image_crs`
- `max_width`
- `max_height`
- `year_parameter_strategy`

Failure modes caught before planning:

- unsupported adapter
- unsupported bbox policy
- unsupported CRS transform
- missing `bboxSR` support
- missing URL parameter names
- unsupported year strategy

## Preflight Audit

Adapters must be inspectable before planning. The CLI preflight command:

```bash
faster-raster inspect-contract research_spec.json --json
```

reports adapter capability for every source, including bbox policy, CRS support, export CRS, target grid CRS, year strategy, resampling, and service size limits.

Adapter authors should add tests proving invalid capabilities fail during `inspect-contract` and `validate`, before any manifest row is generated.

## Future STAC Adapter

A future STAC adapter should compile deterministic search requests or item asset references from semantic fields:

- collection
- temporal filter
- bbox/intersects
- asset roles
- cloud/filter metadata where applicable

It must not query live STAC APIs during v0 planning unless an explicit later execution mode is added.

## Future COG/HTTP Adapter

The first COG/HTTP-style adapter is `generic_https_template`. It compiles deterministic URLs from registry templates such as:

```text
https://example.invalid/rasters/{product_slug}/{year}/{thematic_layer}/{tile_id}.tif
```

It preserves AOI/tile metadata in the manifest but does not add ArcGIS query parameters. See [GENERIC_HTTPS_TEMPLATE.md](GENERIC_HTTPS_TEMPLATE.md).

Real documented URL-structure fixtures are listed in [REAL_RASTER_URL_STRUCTURES.md](REAL_RASTER_URL_STRUCTURES.md).

A future richer COG/HTTP adapter may compile deterministic asset references from catalogs or registry templates.

It should preserve:

- source asset URL
- checksum metadata if registry-provided
- declared CRS and resolution
- nodata and semantic type

It must not open remote COGs in v0.

## Adding A Source

New sources should start with registry metadata and tests before adapter expansion. A source is acceptable only when:

- capability validation fails clearly for unsupported settings
- manifest rows are byte-stable
- harmonization inputs preserve every request ID exactly once
- no test requires network access

## Output Validation Before Execution

Adapters should emit enough contract metadata for downstream artifact validators to work without the original research spec. This is important for HPC orchestration because a manifest or harmonization plan may be passed to a Slurm array, Snakemake rule, Nextflow process, or AWS Batch job independently.

Before execution, run:

```bash
faster-raster validate-manifest acquisition_manifest.jsonl
faster-raster validate-harmonization harmonization_plan.json --manifest acquisition_manifest.jsonl
```

Adapter rows must preserve `request_id`, `adapter`, `source_id`, `registry_key`, `year`, `thematic_layer`, `url`, CRS fields, bbox provenance, semantic type, resampling, and tile pixel metadata where applicable. Validators fail fast with nonzero exit codes so orchestration systems can stop before expensive jobs are submitted.

## Execution Package Requirements

Adapters must emit enough fields for `compile-execution-package` to build scheduler jobs without source-specific logic. Required fields include URL, adapter, source ID, request ID, CRS contract fields, semantic type, resampling, year/layer/tile keys, and tile/cache metadata. The compiler validates manifests and harmonization plans before writing package files, so invalid artifacts fail before any scheduler handoff is created.

## v0.3.1 DAG Adapter Expectations

Adapters feed a four-stage scheduler DAG: `fetch`, `validate_download`, `harmonize`, and `inspect_output`. Adapter rows must provide deterministic URL, cache, CRS, semantic, resampling, and temporal/tile fields so the compiler can build stage jobs without adapter-specific scheduler logic. Scheduler exports currently dry-run only; future execution will plug into the generated `job_id`, `stage`, `expected_input_path`, `expected_cache_path`, and `expected_output_path` fields.
