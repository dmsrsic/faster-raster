# FasterRaster v0 Diagnostics

## Environment

- Python: `3.12.13 | packaged by conda-forge | (main, Mar  5 2026, 16:50:00) [GCC 14.3.0]`
- Platform: `Linux-6.18.33.2-microsoft-standard-WSL2-x86_64-with-glibc2.35`
- Working directory: `/home/dmsrsic/raster-work/faster-raster`
- Package version: `0.3.1`

## Artifacts

- Spec: `/home/dmsrsic/raster-work/projects/ohio_cdl_edges/research_spec.json`
- Manifest: `/home/dmsrsic/raster-work/projects/ohio_cdl_edges/manifests/acquisition_manifest.jsonl`
- Harmonization plan: `/home/dmsrsic/raster-work/projects/ohio_cdl_edges/plans/harmonization_plan.json`

## Correctness Summary

- Manifest rows: `2`
- Manifest size bytes: `1798`
- Manifest SHA256: `fd46106cde9e8c51b0aae26296b46a48ec75d65ca47ed4e911329723693151cc`
- Harmonization plan SHA256: `4493ea4f494d589fc098bdb7744e07caef4bb15141a1877d12c9044205e4e2c6`
- Rows planned per second: `2849.003`
- Peak memory MB: `1.202`
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
| validate | 0.000312 |
| resolve | 0.000027 |
| plan_urls | 0.000702 |
| plan_harmonization | 0.000187 |
| inspect | 0.000265 |
| inspect_contract | 0.077580 |
| schema_export | 0.006014 |
| validate_outputs | 0.001049 |
| compile_execution_package | 0.008521 |
| export_slurm_scheduler | 0.002526 |
| export_local_dry_run_scheduler | 0.002389 |
| total | 0.125365 |

## Synthetic Planning Performance

| Target Rows | Planned Rows | Seconds | Rows/sec | Peak MB |
|---:|---:|---:|---:|---:|
| 100 | 100 | 0.007618 | 13126.908 | 0.115 |
| 1000 | 1000 | 0.066441 | 15050.866 | 1.191 |
| 10000 | 10000 | 0.577673 | 17310.834 | 11.915 |

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
  "peak_memory_mb": 0.016,
  "planned_rows": 8,
  "rows_per_second": 5910.336,
  "time_seconds": 0.001354
}
```



## Execution Package Eval

- Package ID: `fr_exec_51438d1287b43021`
- Jobs emitted: `8`
- Validation status: `PASS`
- Package SHA256: `44d1fe803ab88deb31497a66443815b16605e4bf1a5d3eb0c4eb2e39389489a3`
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
    "execution_package_sha256": "44d1fe803ab88deb31497a66443815b16605e4bf1a5d3eb0c4eb2e39389489a3",
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
| valid_registry | True | 0.000005 | `` |
| unsupported_adapter | True | 0.000003 | `Unsupported adapter for v0: stac` |
| missing_bboxsr_support | True | 0.000002 | `Source cdl must support bbox CRS parameter for v0 ArcGIS planning` |
| unsupported_year_strategy | True | 0.000002 | `Unsupported year_parameter_strategy for source cdl: mosaic_rule_by_attribute` |
| unsupported_bbox_transform | True | 0.000009 | `UnsupportedCRSTransform: EPSG:5070 -> EPSG:3857; install pyproj-backed transform support in a later milestone.` |

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
........................................................................ [ 56%]
.......................................................                  [100%]
============================= slowest 20 durations =============================
0.03s call     tests/test_cli_integration.py::test_cli_inspect_contract_check_goldens_detects_drift
0.02s call     tests/test_cli_integration.py::test_cli_inspect_contract_check_goldens_detects_present_goldens
0.01s call     tests/test_cli_integration.py::test_cli_inspect_contract_no_network_access
0.01s call     tests/test_cli_integration.py::test_cli_inspect_harmonization_prints_summary
0.01s call     tests/test_cli_integration.py::test_cli_plan_harmonization_writes_plan_and_summary
0.01s call     tests/test_cli_integration.py::test_cli_inspect_manifest_prints_summary
0.01s call     tests/test_cli_integration.py::test_cli_plan_urls_writes_manifest_and_summary
0.01s call     tests/test_cli_integration.py::test_cli_resolve_sources_summary
0.01s call     tests/test_cli_integration.py::test_cli_validate_success
0.01s call     tests/test_real_raster_url_structures.py::test_real_url_golden_byte_stability
0.01s call     tests/test_cli_integration.py::test_cli_inspect_contract_passes_for_example
0.01s call     tests/test_cli_integration.py::test_cli_inspect_contract_invalid_capability_returns_nonzero
0.01s call     tests/test_cli_integration.py::test_cli_inspect_contract_json_emits_expected_fields
0.01s call     tests/test_cli_integration.py::test_cli_inspect_contract_does_not_create_manifests
0.01s setup    tests/test_harmonization_planning.py::test_golden_harmonization_plan_bytes_are_stable
0.01s call     tests/test_generic_https_template.py::test_generic_url_template_byte_stability
0.01s call     tests/test_generic_https_template.py::test_generic_missing_url_template_fails_clearly
0.01s setup    tests/test_harmonization_planning.py::test_harmonization_plan_is_deterministic

(2 durations < 0.005s hidden.  Use -vv to show these durations.)
127 passed in 0.54s

```
