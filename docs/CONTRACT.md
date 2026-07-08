# FasterRaster v0 Contract

FasterRaster v0 compiles semantic raster acquisition intent into deterministic request plans and harmonization plans. It does not download raster data, call source endpoints, run GDAL warps, or perform raster analytics.

## `research_spec.json`

The research spec is user-authored semantic input. It must describe what data is needed, not how to hand-build URLs.

Required concepts:

- `project.id`: stable project identifier.
- `aoi.path`: GeoJSON AOI path.
- `aoi.input_crs`: CRS of AOI coordinates.
- `target_grid.crs`: later harmonization CRS, commonly `EPSG:5070` for CONUS.
- `target_grid.resolution_m`: target grid resolution in meters.
- `sources[].registry_key`: source metadata key in `source_registry.yaml`.
- `sources[].years`: requested years.
- `sources[].thematic_layers`: requested semantic layers.
- `sources[].semantic_type`: `categorical` or `continuous`.
- `sources[].resampling`: requested harmonization resampling.

Example preserve-bbox CDL source:

```json
{
  "id": "cdl",
  "registry_key": "usda_nass_cdl_imageserver",
  "years": [2023, 2024],
  "thematic_layers": ["crop_type"],
  "acquisition_mode": "arcgis_export_image",
  "semantic_type": "categorical",
  "resampling": "nearest"
}
```

## `source_registry.yaml`

The source registry is maintainer-authored metadata. It defines endpoint shape, CRS policy, limits, and adapter capability.

Required ArcGIS ImageServer fields:

- `adapter`
- `provider`
- `product`
- `base_url`
- `operation`
- `bbox_param`
- `bbox_crs_param`
- `image_crs_param`
- `size_param`
- `format_param`
- `response_format_param`
- `default_image_format`
- `default_response_format`
- `max_width`
- `max_height`
- `service_crs`
- `default_export_image_crs`
- `bbox_request_policy`
- `supports_bbox_crs_param`
- `year_parameter_strategy`
- `time_param`
- `time_value`

## `acquisition_manifest.jsonl`

The acquisition manifest is deterministic compiler output. Each line is one planned request. v0 writes planned requests only; it does not fetch or validate remote data.

Important fields:

- `request_id`: deterministic stable ID.
- `source_id`, `registry_key`, `adapter`, `provider`, `product`
- `year`, `thematic_layer`
- `source_aoi_bbox`: original AOI tile bbox before request CRS policy.
- `source_aoi_crs`: CRS of `source_aoi_bbox`.
- `bbox`: exact bbox coordinates sent in the URL.
- `bbox_crs`: CRS of `bbox`.
- `export_image_crs`: CRS requested for exported image pixels.
- `target_grid_crs`: later harmonization CRS.
- `tile_planning_crs`: CRS used to calculate tile pixel dimensions.
- `tile_width_pixels`, `tile_height_pixels`
- `url`: compiled HTTPS URL.
- `status`: `planned`

## `harmonization_plan.json`

The harmonization plan is deterministic compiler output describing future raster alignment work. It does not execute reprojection, resampling, or validation.

Each input must preserve:

- `request_id`
- `source_bbox`
- `bbox_crs`
- `export_image_crs`
- `target_grid_crs`
- `tile_width_pixels`
- `tile_height_pixels`
- `tile_planning_crs`
- `semantic_type`
- `resampling`
- `planned_output`

## CRS Field Meanings

- `source_aoi_crs`: CRS of the original AOI tile bbox.
- `bbox_crs`: CRS of the request bbox sent to the source endpoint.
- `export_image_crs`: CRS requested from the source export endpoint.
- `target_grid_crs`: CRS for later harmonized outputs.
- `tile_planning_crs`: metric CRS used to compute deterministic tile pixel size.

## `bbox_request_policy`

`preserve_input_bbox_with_bboxsr`

The request `bbox` is the AOI tile bbox in the input AOI CRS. The URL includes `bboxSR` so ArcGIS can interpret the coordinates.

`project_bbox_to_service_crs`

The request `bbox` is deterministically projected to `service_crs` before URL generation. v0 supports only `EPSG:4326 <-> EPSG:3857` without pyproj.

## `year_parameter_strategy`

`time_value`

Adds a registry-defined time parameter, usually:

```yaml
time_param: time
time_value: "{year}"
```

`mosaic_rule_by_attribute`

Reserved for a later milestone. Current v0 rejects this strategy during capability validation.

## Categorical Resampling

Categorical rasters must use `nearest`. The following are forbidden:

- `bilinear`
- `cubic`
- `lanczos`
- `average`

## Invalid Source Example

```yaml
sources:
  bad_source:
    adapter: stac
```

Expected validation error:

```text
Unsupported adapter for v0: stac
```

## Inspecting The Contract

Use `inspect-contract` before URL planning to audit source/spec compatibility without writing manifests:

```bash
faster-raster inspect-contract research_spec.json
```

Machine-readable output:

```bash
faster-raster inspect-contract research_spec.json --json
```

Use a non-default registry:

```bash
faster-raster inspect-contract research_spec.json --registry configs/source_registry.yaml
```

Check committed golden fixtures:

```bash
faster-raster inspect-contract research_spec.json --check-goldens
```

The command returns `PASS` only when all requested source/spec pairs pass capability validation. It returns nonzero on invalid source capabilities, unsupported CRS transforms, unsupported year strategies, or golden fixture drift.

## HPC Preflight Output Validation

FasterRaster is intended to sit before execution systems such as Slurm, Snakemake, Nextflow, AWS Batch, or other HPC/cloud runners. In that role, generated artifacts should be validated before any worker job consumes them.

Use `validate-manifest` to check an existing acquisition manifest independently from the original research spec:

```bash
faster-raster validate-manifest manifests/acquisition_manifest.jsonl
faster-raster validate-manifest manifests/acquisition_manifest.jsonl --json
```

The manifest validator checks JSONL parseability, required request fields, request ID uniqueness, HTTPS URL structure, adapter/source/layer/year fields, CRS fields, bbox shape, tile pixel dimensions, and semantic resampling safety.

Use `validate-harmonization` to check a harmonization plan:

```bash
faster-raster validate-harmonization plans/harmonization_plan.json
faster-raster validate-harmonization plans/harmonization_plan.json --manifest manifests/acquisition_manifest.jsonl
faster-raster validate-harmonization plans/harmonization_plan.json --manifest manifests/acquisition_manifest.jsonl --json
```

When a manifest is supplied, every manifest `request_id` must appear exactly once in the harmonization plan. These commands are offline and do not contact source endpoints. They are suitable as preflight steps before submitting distributed jobs.

## HPC Execution Package Compiler

`compile-execution-package` turns validated planning artifacts into deterministic scheduler inputs without downloading data:

```bash
faster-raster compile-execution-package \
  --manifest manifests/acquisition_manifest.jsonl \
  --harmonization plans/harmonization_plan.json \
  --out execution_package/
```

Outputs:

- `execution_package.json`: package metadata, input hashes, source/adapter counts, stage counts, validation status, and scheduler notes.
- `jobs.jsonl`: one deterministic job row per request/stage. Current stages are `fetch`, `validate`, `harmonize`, and `inspect`.
- `cache_plan.json`: deterministic cache keys and content-addressed path proposals. No files are downloaded.
- `failure_policy.json`: retry, timeout, checksum, partial-file, and scheduler exit-code expectations.
- `execution_summary.md`: human-readable package summary.

Schedulers can consume `jobs.jsonl` as a preflight handoff:

- Slurm: map rows to array tasks and translate `dependencies` into `--dependency` relationships.
- Snakemake: create rules keyed by `job_id`, `expected_cache_path`, and `expected_output_path`.
- Nextflow: treat each row as a process input and stage dependency metadata externally.
- Prefect/Ray: map rows to tasks with explicit retry and timeout policy.
- AWS Batch: submit per-row jobs or grouped array jobs by stage/source.

This milestone is still offline. The package describes future execution; it does not fetch, validate downloaded bytes, or harmonize rasters.

## v0.3.1 DAG and Scheduler Exports

Execution packages now represent a deterministic four-stage DAG per request:

1. `fetch`
2. `validate_download`
3. `harmonize`
4. `inspect_output`

Dependency rules are fixed: `fetch` has no dependencies, `validate_download` depends on `fetch`, `harmonize` depends on `validate_download`, and `inspect_output` depends on `harmonize`. The compiler validates that every dependency exists, every request has the same stage set, job IDs are unique, dependency order is valid, and no cycles are present.

Scheduler exports are still dry-run only:

```bash
faster-raster export-scheduler --package execution_package/ --scheduler slurm --out scheduler/slurm/
faster-raster export-scheduler --package execution_package/ --scheduler local-dry-run --out scheduler/local/
```

Slurm export writes `slurm_array.sh`, `job_index.tsv`, `scheduler_summary.json`, and `README.md`. Local dry-run export writes `run_local_dry_run.sh`, `job_index.tsv`, `scheduler_summary.json`, and `README.md`. Both scripts echo the selected job row and mark the future `faster-raster run-job` integration point. They do not download or harmonize data.
