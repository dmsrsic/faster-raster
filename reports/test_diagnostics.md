# FasterRaster v0 Diagnostics

## Environment

- Python: `3.10.12 (main, Mar  3 2026, 11:56:32) [GCC 11.4.0]`
- Platform: `Linux-6.18.33.2-microsoft-standard-WSL2-x86_64-with-glibc2.35`
- Working directory: `/home/dmsrsic/raster-work/faster-raster`
- Package version: `0.6.0`

## Artifacts

- Spec: `/home/dmsrsic/raster-work/projects/ohio_cdl_edges/research_spec.json`
- Manifest: `/home/dmsrsic/raster-work/projects/ohio_cdl_edges/manifests/acquisition_manifest.jsonl`
- Harmonization plan: `/home/dmsrsic/raster-work/projects/ohio_cdl_edges/plans/harmonization_plan.json`

## Correctness Summary

- Manifest rows: `2`
- Manifest size bytes: `1798`
- Manifest SHA256: `fd46106cde9e8c51b0aae26296b46a48ec75d65ca47ed4e911329723693151cc`
- Harmonization plan SHA256: `4493ea4f494d589fc098bdb7744e07caef4bb15141a1877d12c9044205e4e2c6`
- Rows planned per second: `3642.987`
- Peak memory MB: `1.361`
- Inspect contract status: `PASS`
- Inspect contract JSON sane: `True`
- Schema structural validation: `PASS`
- Generic HTTPS golden status: `PASS`
- Output validation status: `manifest=PASS`, `harmonization=PASS`
- Execution package status: `PASS`
- DAG validation status: `PASS`
- Cache extension counts: `{'.tiff': 2}`

## Timings

| Stage | Seconds |
|---|---:|
| validate | 0.000118 |
| resolve | 0.000019 |
| plan_urls | 0.000549 |
| plan_harmonization | 0.000206 |
| inspect | 0.000253 |
| inspect_contract | 0.067219 |
| schema_export | 0.003195 |
| validate_outputs | 0.001206 |
| compile_execution_package | 0.002727 |
| export_slurm_scheduler | 0.002479 |
| export_local_dry_run_scheduler | 0.002176 |
| total | 0.130337 |

## Synthetic Planning Performance

| Target Rows | Planned Rows | Seconds | Rows/sec | Peak MB |
|---:|---:|---:|---:|---:|
| 100 | 100 | 0.005834 | 17140.836 | 0.150 |
| 1000 | 1000 | 0.041062 | 24353.712 | 1.536 |
| 10000 | 10000 | 0.418817 | 23876.757 | 15.348 |

## Adapter Counts

| Adapter | Rows |
|---|---:|
| `arcgis_imageserver` | 2 |

## Mixed ArcGIS + Generic Benchmark

```json
{
  "adapter_counts": {
    "arcgis_imageserver": 2,
    "generic_https_template": 6
  },
  "peak_memory_mb": 0.019,
  "planned_rows": 8,
  "rows_per_second": 7626.892,
  "time_seconds": 0.001049
}
```



## Execution Package Eval

- Package ID: `fr_exec_51438d1287b43021`
- Jobs emitted: `8`
- Validation status: `PASS`
- Package SHA256: `85a1b22c97035bc347164cbccc30805f5a169546feb2973652558a078e4461ee`
- Jobs SHA256: `29c814e3fedbb69c80295e90b5c04e61df7aed04ffa56e115cce3a37230a339f`
- Cache plan SHA256: `1d3a4241ef9e8ca7c3c83b2150b97a17ba54dd2796f5f659b0133a5a5e310490`
- Failure policy SHA256: `a2102a8f82112f044e027d2dcc82fd5d6ed13dc676848154306ddfe54f5d7cc5`

```json
{
  "adapter_counts": {
    "arcgis_imageserver": 2
  },
  "cache_extension_counts": {
    ".tiff": 2
  },
  "dag_validation_status": "PASS",
  "dependency_count": 6,
  "hashes": {
    "cache_plan_sha256": "1d3a4241ef9e8ca7c3c83b2150b97a17ba54dd2796f5f659b0133a5a5e310490",
    "execution_package_sha256": "85a1b22c97035bc347164cbccc30805f5a169546feb2973652558a078e4461ee",
    "failure_policy_sha256": "a2102a8f82112f044e027d2dcc82fd5d6ed13dc676848154306ddfe54f5d7cc5",
    "jobs_sha256": "29c814e3fedbb69c80295e90b5c04e61df7aed04ffa56e115cce3a37230a339f"
  },
  "package_id": "fr_exec_51438d1287b43021",
  "request_count": 2,
  "source_counts": {
    "cdl": 2
  },
  "stage_counts": {
    "fetch": 2,
    "harmonize": 2,
    "inspect_output": 2,
    "validate_download": 2
  },
  "total_job_count": 8,
  "validation_status": "PASS"
}
```


## Scheduler Export Eval

- Slurm jobs: `8`
- Slurm DAG status: `PASS`
- Local dry-run jobs: `8`
- Local dry-run DAG status: `PASS`

```json
{
  "local_dry_run": {
    "dag_validation_status": "PASS",
    "dependency_count": 6,
    "hashes": {
      "README.md": "6de6ed731f41141536e09b8fcdfe126054b38ea942e68d94dba75890d6813f48",
      "job_index.tsv": "998fa40833ef03617028868cf179597f2791ac4fcbf9e2644ae19cbf69c11e59",
      "run_local_dry_run.sh": "a0d19c4e6adf87085406dbb480eaafc14a131b5ca0fe72f7e5559b73eb86b0c5",
      "scheduler_summary.json": "ee937073fda84ffde3db6aee4a0ce34591f9ad83c57b1042dd99d25994316234"
    },
    "job_count": 8,
    "notes": "Scheduler exports are dry-run artifacts only; no downloads are executed.",
    "output_directory": "/home/dmsrsic/raster-work/faster-raster/reports/diagnostic_scheduler_local_dry_run",
    "package_id": "fr_exec_51438d1287b43021",
    "request_count": 2,
    "scheduler": "local-dry-run",
    "stage_counts": {
      "fetch": 2,
      "harmonize": 2,
      "inspect_output": 2,
      "validate_download": 2
    }
  },
  "slurm": {
    "dag_validation_status": "PASS",
    "dependency_count": 6,
    "hashes": {
      "README.md": "1de9231a46e4b00d24b1c6a5484fe82ca1cae757b16eafb65e09cc7c43a9b3ce",
      "job_index.tsv": "998fa40833ef03617028868cf179597f2791ac4fcbf9e2644ae19cbf69c11e59",
      "scheduler_summary.json": "d42208cc0bc8aba9fdfd39021edb80ebbdb1239499e1fb222451f1b929a8a371",
      "slurm_array.sh": "957222eaa6ea9396d461402078a7b0b26e362d9bb980b5ab9bb2dcd7af8e447a"
    },
    "job_count": 8,
    "notes": "Scheduler exports are dry-run artifacts only; no downloads are executed.",
    "output_directory": "/home/dmsrsic/raster-work/faster-raster/reports/diagnostic_scheduler_slurm",
    "package_id": "fr_exec_51438d1287b43021",
    "request_count": 2,
    "scheduler": "slurm",
    "stage_counts": {
      "fetch": 2,
      "harmonize": 2,
      "inspect_output": 2,
      "validate_download": 2
    }
  }
}
```

## Output Validation Eval

- Manifest status: `PASS`
- Harmonization status: `PASS`
- Manifest rows checked: `2`
- Harmonization inputs checked: `2`
- Pass count: `2`
- Fail count: `2`
- Example failures: `line 1: malformed JSONL: Expecting property name enclosed in double quotes; malformed JSON: Expecting property name enclosed in double quotes`

## Capability Validation Eval

- Scenarios: `5`
- Passed: `5`
- Failed: `0`

| Scenario | Passed | Seconds | Errors |
|---|---:|---:|---|
| valid_registry | True | 0.000009 | `` |
| unsupported_adapter | True | 0.000002 | `Unsupported adapter for v0: stac` |
| missing_bboxsr_support | True | 0.000002 | `Source cdl must support bbox CRS parameter for v0 ArcGIS planning` |
| unsupported_year_strategy | True | 0.000002 | `Unsupported year_parameter_strategy for source cdl: mosaic_rule_by_attribute` |
| unsupported_bbox_transform | True | 0.000006 | `UnsupportedCRSTransform: EPSG:5070 -> EPSG:3857; install pyproj-backed transform support in a later milestone.` |

## Documentation Coverage

| File | Present |
|---|---:|
| `docs/CONTRACT.md` | True |
| `docs/ADAPTERS.md` | True |
| `docs/GENERIC_HTTPS_TEMPLATE.md` | True |
| `docs/REAL_RASTER_URL_STRUCTURES.md` | True |
| `docs/SCHEMAS.md` | True |

## Golden Fixture Coverage

- Expected: `23`
- Present: `23`
- Missing: `0`

| Fixture | Present |
|---|---:|
| `tests/golden/source_registry_cdl.yaml` | True |
| `tests/golden/research_spec_preserve_bbox.json` | True |
| `tests/golden/research_spec_project_bbox.json` | True |
| `tests/golden/acquisition_manifest_preserve_bbox.jsonl` | True |
| `tests/golden/acquisition_manifest_project_bbox.jsonl` | True |
| `tests/golden/harmonization_plan_preserve_bbox.json` | True |
| `tests/golden/harmonization_plan_project_bbox.json` | True |
| `tests/golden/source_registry_generic.yaml` | True |
| `tests/golden/research_spec_generic_https.json` | True |
| `tests/golden/acquisition_manifest_generic_https.jsonl` | True |
| `tests/golden/harmonization_plan_generic_https.json` | True |
| `tests/golden/source_registry_annual_nlcd_aws_tile.yaml` | True |
| `tests/golden/source_registry_annual_nlcd_aws_mosaic.yaml` | True |
| `tests/golden/source_registry_prism_time_series_daily_zip.yaml` | True |
| `tests/golden/research_spec_nlcd_aws_tile.json` | True |
| `tests/golden/research_spec_nlcd_aws_mosaic.json` | True |
| `tests/golden/research_spec_prism_daily_zip.json` | True |
| `tests/golden/acquisition_manifest_nlcd_aws_tile.jsonl` | True |
| `tests/golden/acquisition_manifest_nlcd_aws_mosaic.jsonl` | True |
| `tests/golden/acquisition_manifest_prism_daily_zip.jsonl` | True |
| `tests/golden/harmonization_plan_nlcd_aws_tile.json` | True |
| `tests/golden/harmonization_plan_nlcd_aws_mosaic.json` | True |
| `tests/golden/harmonization_plan_prism_daily_zip.json` | True |

## Schema Coverage

- Expected: `5`
- Present: `5`
- Structurally valid: `5`

| Schema | Present | Valid | Required Count |
|---|---:|---:|---:|
| `/home/dmsrsic/raster-work/faster-raster/schemas/research_spec.schema.json` | True | True | 5 |
| `/home/dmsrsic/raster-work/faster-raster/schemas/source_registry.schema.json` | True | True | 1 |
| `/home/dmsrsic/raster-work/faster-raster/schemas/acquisition_manifest_row.schema.json` | True | True | 26 |
| `/home/dmsrsic/raster-work/faster-raster/schemas/harmonization_plan.schema.json` | True | True | 4 |
| `/home/dmsrsic/raster-work/faster-raster/schemas/inspect_contract_report.schema.json` | True | True | 6 |

## Schema Hashes

| Schema | SHA256 |
|---|---|
| `research_spec.schema.json` | `e423b213b5a7924d038bd76c42ef4eca38988b1b32757789aee8473fbd3daa86` |
| `source_registry.schema.json` | `c3363bb9b0488af226a347828a6e5ecb01f2abf4ced46f75ecff6896257e4828` |
| `acquisition_manifest_row.schema.json` | `c62e8ad4c19fa36dba0aa6aad07e2f05b9c47ba0e913a91b2eb7aa192d45172a` |
| `harmonization_plan.schema.json` | `d2191bb2d806ee7a8131f347f4ae3cf0e38c9589f2c7d80cc6012dbcab598525` |
| `inspect_contract_report.schema.json` | `745d35a53c866a501dfa8c7e131f5870a09cd159a24be556b4475fb35daca9db` |

## Inspect Manifest

```json
{
  "by_source": {
    "cdl": 2
  },
  "by_thematic_layer": {
    "crop_type": 2
  },
  "by_year": {
    "2023": 1,
    "2024": 1
  },
  "records": 2,
  "statuses": {
    "planned": 2
  }
}
```

## Inspect Harmonization

```json
{
  "inputs": 2,
  "project_id": "ohio_cdl_edge_dynamics_v001",
  "resolution_m": 30,
  "target_crs": "EPSG:5070",
  "validation_checks": 7
}
```

## Pytest Durations

Exit code: `0`

```text
........................................................................ [ 21%]
........................................................................ [ 43%]
........................................................................ [ 65%]
........................................................................ [ 87%]
.........................................                                [100%]
============================= slowest 20 durations =============================
1.76s call     tests/test_real_preview.py::test_clean_cockpit_report_layouts_write_png_json_md
0.75s call     tests/test_cli_kitchen_aliases.py::test_explore_kitchen_slash_parser
0.75s call     tests/test_cli_kitchen_aliases.py::test_kitchen_aliases_return_success
0.71s call     tests/test_real_preview.py::test_debug_artifacts_written
0.65s call     tests/test_real_preview.py::test_byte_cap_enforced
0.61s call     tests/test_real_preview.py::test_multicolor_cdl_png_remains_real_raster_rendered
0.59s call     tests/test_real_preview.py::test_no_cache_raw_avoids_cache_but_renders
0.58s call     tests/test_real_preview.py::test_cdl_candidate_cascade_selects_no_time_and_records_attempts
0.58s call     tests/test_real_preview.py::test_mocked_daymet_fetch_renders_point_result
0.58s call     tests/test_real_preview.py::test_cdl_candidate_cascade_tries_until_meaningful
0.58s call     tests/test_real_preview.py::test_malformed_response_warning_not_crash
0.57s call     tests/test_real_preview.py::test_mocked_cdl_fetch_renders_png_and_json
0.51s call     tests/test_real_preview.py::test_single_color_cdl_png_with_meaningful_samples_becomes_manual_sample_result
0.50s call     tests/test_real_preview.py::test_single_color_cdl_png_with_no_sample_values_becomes_no_data
0.48s call     tests/test_real_preview.py::test_cdl_black_png_no_candidate_becomes_no_data_without_samples
0.45s call     tests/test_task_cli.py::test_task_cli_create_list_show_validate_preview
0.40s call     tests/test_cli_cook_toggles.py::test_cook_queue_and_aliases
0.35s call     tests/test_task_cli.py::test_task_preview_open_fallback
0.34s call     tests/test_render_cli_screenshots.py::test_render_cli_screenshots_creates_svg_and_text
0.28s call     tests/test_cli_models.py::test_load_sources_and_summary
329 passed in 20.10s

```
