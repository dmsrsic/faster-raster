from __future__ import annotations

import argparse
import copy
import hashlib
import json
import platform
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path

from faster_raster import __version__
from faster_raster.contract import inspect_contract
from faster_raster.execution_package import build_execution_package, package_hashes
from faster_raster.harmonization_planner import (
    plan_from_manifest,
    summarize_harmonization_plan,
    write_harmonization_plan,
)
from faster_raster.manifest import read_manifest, summarize_manifest, write_manifest
from faster_raster.output_validation import validate_harmonization as validate_harmonization_output
from faster_raster.output_validation import validate_manifest as validate_manifest_output
from faster_raster.schema_export import SCHEMA_FILENAMES, export_schemas, schema_structural_status
from faster_raster.scheduler_export import export_scheduler_package
from faster_raster.schemas import ResearchSpec, SourceRegistry
from faster_raster.source_registry import load_registry
from faster_raster.url_planner import plan_urls
from faster_raster.validation import load_spec, validate_or_raise, validate_spec


SYNTHETIC_ROW_TARGETS = [100, 1000, 10000]
REQUIRED_DOCS = [
    "docs/CONTRACT.md",
    "docs/ADAPTERS.md",
    "docs/GENERIC_HTTPS_TEMPLATE.md",
    "docs/REAL_RASTER_URL_STRUCTURES.md",
    "docs/SCHEMAS.md",
]
REQUIRED_GOLDENS = [
    "tests/golden/source_registry_cdl.yaml",
    "tests/golden/research_spec_preserve_bbox.json",
    "tests/golden/research_spec_project_bbox.json",
    "tests/golden/acquisition_manifest_preserve_bbox.jsonl",
    "tests/golden/acquisition_manifest_project_bbox.jsonl",
    "tests/golden/harmonization_plan_preserve_bbox.json",
    "tests/golden/harmonization_plan_project_bbox.json",
    "tests/golden/source_registry_generic.yaml",
    "tests/golden/research_spec_generic_https.json",
    "tests/golden/acquisition_manifest_generic_https.jsonl",
    "tests/golden/harmonization_plan_generic_https.json",
    "tests/golden/source_registry_annual_nlcd_aws_tile.yaml",
    "tests/golden/source_registry_annual_nlcd_aws_mosaic.yaml",
    "tests/golden/source_registry_prism_time_series_daily_zip.yaml",
    "tests/golden/research_spec_nlcd_aws_tile.json",
    "tests/golden/research_spec_nlcd_aws_mosaic.json",
    "tests/golden/research_spec_prism_daily_zip.json",
    "tests/golden/acquisition_manifest_nlcd_aws_tile.jsonl",
    "tests/golden/acquisition_manifest_nlcd_aws_mosaic.jsonl",
    "tests/golden/acquisition_manifest_prism_daily_zip.jsonl",
    "tests/golden/harmonization_plan_nlcd_aws_tile.json",
    "tests/golden/harmonization_plan_nlcd_aws_mosaic.json",
    "tests/golden/harmonization_plan_prism_daily_zip.json",
]
REQUIRED_SCHEMAS = [f"schemas/{filename}" for filename in SCHEMA_FILENAMES]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Timer:
    def __init__(self) -> None:
        self.timings: dict[str, float] = {}

    def run(self, name: str, func):
        start = time.perf_counter()
        result = func()
        self.timings[f"{name}_time_seconds"] = round(time.perf_counter() - start, 6)
        return result


def run_pytest_durations(repo_dir: Path) -> dict:
    start = time.perf_counter()
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--durations=20"],
        cwd=repo_dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return {
        "exit_code": completed.returncode,
        "time_seconds": round(time.perf_counter() - start, 6),
        "output": completed.stdout,
    }


def presence_check(repo_dir: Path, relative_paths: list[str]) -> dict:
    results = []
    for relative_path in relative_paths:
        exists = (repo_dir / relative_path).exists()
        results.append({"path": relative_path, "present": exists})
    return {
        "expected": len(relative_paths),
        "present": sum(1 for result in results if result["present"]),
        "missing": [result["path"] for result in results if not result["present"]],
        "items": results,
    }


def synthetic_spec(base_spec: ResearchSpec, target_rows: int) -> ResearchSpec:
    raw = copy.deepcopy(base_spec.model_dump())
    raw["sources"] = [raw["sources"][0]]
    raw["sources"][0]["years"] = [2023, 2024]
    layers_needed = target_rows // len(raw["sources"][0]["years"])
    raw["sources"][0]["thematic_layers"] = [f"crop_type_{idx:05d}" for idx in range(layers_needed)]
    raw["project"]["id"] = f"synthetic_{target_rows}_row_url_planning"
    return ResearchSpec.model_validate(raw)


def mixed_synthetic_spec(base_spec: ResearchSpec) -> ResearchSpec:
    raw = copy.deepcopy(base_spec.model_dump())
    raw["project"]["id"] = "synthetic_mixed_arcgis_generic_url_planning"
    raw["sources"] = [
        raw["sources"][0],
        {
            "id": "demo_cog",
            "registry_key": "generic_demo_cog",
            "years": [2023, 2024],
            "thematic_layers": ["ndvi", "elevation"],
            "acquisition_mode": "https_template",
            "semantic_type": "continuous",
            "resampling": "bilinear",
        },
        {
            "id": "nlcd_tile",
            "registry_key": "annual_nlcd_aws_tile",
            "years": [1985],
            "thematic_layers": ["fractional_impervious"],
            "acquisition_mode": "https_template",
            "semantic_type": "continuous",
            "resampling": "bilinear",
        },
        {
            "id": "prism_daily",
            "registry_key": "prism_time_series_daily_zip",
            "years": [2026],
            "thematic_layers": ["ppt"],
            "acquisition_mode": "https_template",
            "semantic_type": "continuous",
            "resampling": "bilinear",
        },
    ]
    return ResearchSpec.model_validate(raw)


def adapter_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["adapter"]] = counts.get(row["adapter"], 0) + 1
    return dict(sorted(counts.items()))


def run_synthetic_performance(base_spec: ResearchSpec, registry, spec_path: Path) -> list[dict]:
    results = []
    for target_rows in SYNTHETIC_ROW_TARGETS:
        spec = synthetic_spec(base_spec, target_rows)
        tracemalloc.start()
        start = time.perf_counter()
        rows = plan_urls(spec, registry, spec_path)
        elapsed = time.perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        results.append(
            {
                "target_rows": target_rows,
                "planned_rows": len(rows),
                "time_seconds": round(elapsed, 6),
                "rows_per_second": round(len(rows) / elapsed, 3) if elapsed else None,
                "peak_memory_mb": round(peak / 1024 / 1024, 3),
                "first_request_id": rows[0]["request_id"] if rows else None,
                "last_request_id": rows[-1]["request_id"] if rows else None,
            }
        )
    return results


def run_mixed_planning_benchmark(base_spec: ResearchSpec, registry, spec_path: Path) -> dict:
    spec = mixed_synthetic_spec(base_spec)
    tracemalloc.start()
    start = time.perf_counter()
    rows = plan_urls(spec, registry, spec_path)
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "planned_rows": len(rows),
        "adapter_counts": adapter_counts(rows),
        "time_seconds": round(elapsed, 6),
        "rows_per_second": round(len(rows) / elapsed, 3) if elapsed else None,
        "peak_memory_mb": round(peak / 1024 / 1024, 3),
    }


def registry_with_updates(registry, **updates):
    raw = registry.model_dump()
    raw["sources"]["usda_nass_cdl_imageserver"].update(updates)
    return SourceRegistry.model_validate(raw)


def run_capability_validation_eval(base_spec: ResearchSpec, registry) -> dict:
    scenarios = [
        {
            "name": "valid_registry",
            "registry": registry,
            "spec": base_spec,
            "expected_substring": None,
        },
        {
            "name": "unsupported_adapter",
            "registry": registry_with_updates(registry, adapter="stac"),
            "spec": base_spec,
            "expected_substring": "Unsupported adapter for v0: stac",
        },
        {
            "name": "missing_bboxsr_support",
            "registry": registry_with_updates(registry, supports_bbox_crs_param=False),
            "spec": base_spec,
            "expected_substring": "must support bbox CRS parameter",
        },
        {
            "name": "unsupported_year_strategy",
            "registry": registry_with_updates(registry, year_parameter_strategy="mosaic_rule_by_attribute"),
            "spec": base_spec,
            "expected_substring": "Unsupported year_parameter_strategy",
        },
    ]
    unsupported_raw = copy.deepcopy(base_spec.model_dump())
    unsupported_raw["aoi"]["input_crs"] = "EPSG:5070"
    scenarios.append(
        {
            "name": "unsupported_bbox_transform",
            "registry": registry_with_updates(registry, bbox_request_policy="project_bbox_to_service_crs"),
            "spec": ResearchSpec.model_validate(unsupported_raw),
            "expected_substring": "UnsupportedCRSTransform: EPSG:5070 -> EPSG:3857",
        }
    )

    results = []
    for scenario in scenarios:
        start = time.perf_counter()
        errors = validate_spec(scenario["spec"], scenario["registry"])
        elapsed = round(time.perf_counter() - start, 6)
        expected = scenario["expected_substring"]
        passed = not errors if expected is None else any(expected in error for error in errors)
        results.append(
            {
                "name": scenario["name"],
                "passed": passed,
                "time_seconds": elapsed,
                "expected_substring": expected,
                "errors": errors,
            }
        )
    return {
        "scenario_count": len(results),
        "passed": sum(1 for result in results if result["passed"]),
        "failed": sum(1 for result in results if not result["passed"]),
        "scenarios": results,
    }


def run_output_validation_eval(manifest_path: Path, plan_path: Path, reports_dir: Path) -> dict:
    manifest_report = validate_manifest_output(manifest_path)
    harmonization_report = validate_harmonization_output(plan_path, manifest_path)

    bad_manifest = reports_dir / "_diagnostic_bad_manifest.jsonl"
    bad_manifest.write_text('{bad json}\n', encoding="utf-8")
    bad_plan = reports_dir / "_diagnostic_bad_harmonization.json"
    bad_plan.write_text('{bad json}', encoding="utf-8")
    bad_manifest_report = validate_manifest_output(bad_manifest)
    bad_plan_report = validate_harmonization_output(bad_plan)
    bad_manifest.unlink(missing_ok=True)
    bad_plan.unlink(missing_ok=True)

    reports = [manifest_report, harmonization_report, bad_manifest_report, bad_plan_report]
    return {
        "manifest_status": manifest_report["status"],
        "harmonization_status": harmonization_report["status"],
        "manifest_rows_checked": manifest_report["row_count"],
        "harmonization_inputs_checked": harmonization_report["input_count"],
        "pass_count": sum(1 for report in reports if report["status"] == "PASS"),
        "fail_count": sum(1 for report in reports if report["status"] == "FAIL"),
        "example_failure_messages": (bad_manifest_report["errors"] + bad_plan_report["errors"])[:4],
    }


def markdown_report(metrics: dict) -> str:
    timing_rows = "\n".join(
        f"| {name.replace('_time_seconds', '')} | {metrics[name]:.6f} |"
        for name in [
            "validate_time_seconds",
            "resolve_time_seconds",
            "plan_urls_time_seconds",
            "plan_harmonization_time_seconds",
            "inspect_time_seconds",
            "inspect_contract_time_seconds",
            "schema_export_time_seconds",
            "validate_outputs_time_seconds",
            "compile_execution_package_time_seconds",
            "export_slurm_scheduler_time_seconds",
            "export_local_dry_run_scheduler_time_seconds",
            "total_time_seconds",
        ]
    )
    synthetic_rows = "\n".join(
        "| {target_rows} | {planned_rows} | {time_seconds:.6f} | {rows_per_second:.3f} | {peak_memory_mb:.3f} |".format(
            **item
        )
        for item in metrics["synthetic_performance"]
    )
    eval_rows = "\n".join(
        f"| {item['name']} | {item['passed']} | {item['time_seconds']:.6f} | `{'; '.join(item['errors'])}` |"
        for item in metrics["capability_validation_eval"]["scenarios"]
    )
    docs_rows = "\n".join(
        f"| `{item['path']}` | {item['present']} |" for item in metrics["docs_presence_check"]["items"]
    )
    golden_rows = "\n".join(
        f"| `{item['path']}` | {item['present']} |" for item in metrics["golden_fixture_check"]["items"]
    )
    schema_rows = "\n".join(
        f"| `{item['path']}` | {item['present']} | {item['valid']} | {item['required_count']} |"
        for item in metrics["schema_structural_status"]["items"]
    )
    schema_hash_rows = "\n".join(
        f"| `{name}` | `{digest}` |" for name, digest in metrics["schema_hashes"].items()
    )
    adapter_count_rows = "\n".join(
        f"| `{name}` | {count} |" for name, count in metrics["adapter_counts"].items()
    )
    return f"""# FasterRaster v0 Diagnostics

## Environment

- Python: `{metrics['python_version']}`
- Platform: `{metrics['platform']}`
- Working directory: `{metrics['working_directory']}`
- Package version: `{metrics['package_version']}`

## Artifacts

- Spec: `{metrics['spec_path']}`
- Manifest: `{metrics['manifest_path']}`
- Harmonization plan: `{metrics['harmonization_plan_path']}`

## Correctness Summary

- Manifest rows: `{metrics['manifest_row_count']}`
- Manifest size bytes: `{metrics['manifest_file_size_bytes']}`
- Manifest SHA256: `{metrics['manifest_sha256']}`
- Harmonization plan SHA256: `{metrics['harmonization_plan_sha256']}`
- Rows planned per second: `{metrics['rows_planned_per_second']}`
- Peak memory MB: `{metrics['peak_memory_mb']}`
- Inspect contract status: `{metrics['inspect_contract_status']}`
- Inspect contract JSON sane: `{metrics['inspect_contract_json_sane']}`
- Schema structural validation: `{metrics['schema_structural_validation_status']}`
- Generic HTTPS golden status: `{metrics['inspect_contract_report']['golden_check']['status']}`
- Output validation status: `manifest={metrics['output_validation_eval']['manifest_status']}`, `harmonization={metrics['output_validation_eval']['harmonization_status']}`
- Execution package status: `{metrics['execution_package_eval']['validation_status']}`
- DAG validation status: `{metrics['execution_package_eval']['dag_validation_status']}`
- Cache extension counts: `{metrics['execution_package_eval']['cache_extension_counts']}`

## Timings

| Stage | Seconds |
|---|---:|
{timing_rows}

## Synthetic Planning Performance

| Target Rows | Planned Rows | Seconds | Rows/sec | Peak MB |
|---:|---:|---:|---:|---:|
{synthetic_rows}

## Adapter Counts

| Adapter | Rows |
|---|---:|
{adapter_count_rows}

## Mixed ArcGIS + Generic Benchmark

```json
{json.dumps(metrics['mixed_planning_benchmark'], indent=2, sort_keys=True)}
```



## Execution Package Eval

- Package ID: `{metrics['execution_package_eval']['package_id']}`
- Jobs emitted: `{metrics['execution_package_eval']['total_job_count']}`
- Validation status: `{metrics['execution_package_eval']['validation_status']}`
- Package SHA256: `{metrics['execution_package_eval']['hashes']['execution_package_sha256']}`
- Jobs SHA256: `{metrics['execution_package_eval']['hashes']['jobs_sha256']}`
- Cache plan SHA256: `{metrics['execution_package_eval']['hashes']['cache_plan_sha256']}`
- Failure policy SHA256: `{metrics['execution_package_eval']['hashes']['failure_policy_sha256']}`

```json
{json.dumps(metrics['execution_package_eval'], indent=2, sort_keys=True)}
```


## Scheduler Export Eval

- Slurm jobs: `{metrics['scheduler_export_eval']['slurm']['job_count']}`
- Slurm DAG status: `{metrics['scheduler_export_eval']['slurm']['dag_validation_status']}`
- Local dry-run jobs: `{metrics['scheduler_export_eval']['local_dry_run']['job_count']}`
- Local dry-run DAG status: `{metrics['scheduler_export_eval']['local_dry_run']['dag_validation_status']}`

```json
{json.dumps(metrics['scheduler_export_eval'], indent=2, sort_keys=True)}
```

## Output Validation Eval

- Manifest status: `{metrics['output_validation_eval']['manifest_status']}`
- Harmonization status: `{metrics['output_validation_eval']['harmonization_status']}`
- Manifest rows checked: `{metrics['output_validation_eval']['manifest_rows_checked']}`
- Harmonization inputs checked: `{metrics['output_validation_eval']['harmonization_inputs_checked']}`
- Pass count: `{metrics['output_validation_eval']['pass_count']}`
- Fail count: `{metrics['output_validation_eval']['fail_count']}`
- Example failures: `{'; '.join(metrics['output_validation_eval']['example_failure_messages'])}`

## Capability Validation Eval

- Scenarios: `{metrics['capability_validation_eval']['scenario_count']}`
- Passed: `{metrics['capability_validation_eval']['passed']}`
- Failed: `{metrics['capability_validation_eval']['failed']}`

| Scenario | Passed | Seconds | Errors |
|---|---:|---:|---|
{eval_rows}

## Documentation Coverage

| File | Present |
|---|---:|
{docs_rows}

## Golden Fixture Coverage

- Expected: `{metrics['golden_fixture_check']['expected']}`
- Present: `{metrics['golden_fixture_check']['present']}`
- Missing: `{len(metrics['golden_fixture_check']['missing'])}`

| Fixture | Present |
|---|---:|
{golden_rows}

## Schema Coverage

- Expected: `{metrics['schema_presence_check']['expected']}`
- Present: `{metrics['schema_presence_check']['present']}`
- Structurally valid: `{metrics['schema_structural_status']['valid']}`

| Schema | Present | Valid | Required Count |
|---|---:|---:|---:|
{schema_rows}

## Schema Hashes

| Schema | SHA256 |
|---|---|
{schema_hash_rows}

## Inspect Manifest

```json
{json.dumps(metrics['manifest_summary'], indent=2, sort_keys=True)}
```

## Inspect Harmonization

```json
{json.dumps(metrics['harmonization_summary'], indent=2, sort_keys=True)}
```

## Pytest Durations

Exit code: `{metrics['pytest_durations']['exit_code']}`

```text
{metrics['pytest_durations']['output']}
```
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Run FasterRaster v0 diagnostics.")
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--harmonization-plan", required=True, type=Path)
    args = parser.parse_args()

    repo_dir = Path(__file__).resolve().parent.parent
    reports_dir = repo_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    timer = Timer()
    tracemalloc.start()
    total_start = time.perf_counter()

    spec = timer.run("validate", lambda: load_spec(args.spec))
    registry = load_registry()
    timer.run("resolve", lambda: validate_or_raise(spec, registry))
    rows = timer.run("plan_urls", lambda: plan_urls(spec, registry, args.spec))
    timer.run("write_manifest", lambda: write_manifest(rows, args.manifest))
    plan = timer.run("plan_harmonization", lambda: plan_from_manifest(spec, args.manifest))
    timer.run("write_harmonization", lambda: write_harmonization_plan(plan, args.harmonization_plan))

    def inspect_outputs() -> tuple[dict, dict]:
        return summarize_manifest(read_manifest(args.manifest)), summarize_harmonization_plan(plan)

    manifest_summary, harmonization_summary = timer.run("inspect", inspect_outputs)
    inspect_contract_report = timer.run("inspect_contract", lambda: inspect_contract(args.spec, check_goldens=True))
    output_validation_eval = timer.run("validate_outputs", lambda: run_output_validation_eval(args.manifest, args.harmonization_plan, reports_dir))
    execution_package_dir = reports_dir / "diagnostic_execution_package"
    execution_package = timer.run("compile_execution_package", lambda: build_execution_package(manifest_path=args.manifest, harmonization_path=args.harmonization_plan, out_dir=execution_package_dir))
    slurm_export_dir = reports_dir / "diagnostic_scheduler_slurm"
    local_export_dir = reports_dir / "diagnostic_scheduler_local_dry_run"
    slurm_summary = timer.run("export_slurm_scheduler", lambda: export_scheduler_package(execution_package_dir, "slurm", slurm_export_dir))
    local_summary = timer.run("export_local_dry_run_scheduler", lambda: export_scheduler_package(execution_package_dir, "local-dry-run", local_export_dir))
    schema_dir = repo_dir / "schemas"
    timer.run("schema_export", lambda: export_schemas(schema_dir))
    schema_hashes = {
        filename: sha256_file(schema_dir / filename)
        for filename in SCHEMA_FILENAMES
        if (schema_dir / filename).exists()
    }
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    total_time = round(time.perf_counter() - total_start, 6)
    plan_urls_time = timer.timings["plan_urls_time_seconds"]
    metrics = {
        "python_version": sys.version.replace("\n", " "),
        "platform": platform.platform(),
        "working_directory": str(Path.cwd()),
        "package_version": __version__,
        "spec_path": str(args.spec),
        "manifest_path": str(args.manifest),
        "harmonization_plan_path": str(args.harmonization_plan),
        "manifest_row_count": len(rows),
        "manifest_file_size_bytes": args.manifest.stat().st_size,
        "manifest_sha256": sha256_file(args.manifest),
        "harmonization_plan_sha256": sha256_file(args.harmonization_plan),
        "rows_planned_per_second": round(len(rows) / plan_urls_time, 3) if plan_urls_time else None,
        "peak_memory_mb": round(peak / 1024 / 1024, 3),
        "manifest_summary": manifest_summary,
        "harmonization_summary": harmonization_summary,
        "adapter_counts": adapter_counts(rows),
        **timer.timings,
        "total_time_seconds": total_time,
        "synthetic_performance": run_synthetic_performance(spec, registry, args.spec),
        "mixed_planning_benchmark": run_mixed_planning_benchmark(spec, registry, args.spec),
        "capability_validation_eval": run_capability_validation_eval(spec, registry),
        "docs_presence_check": presence_check(repo_dir, REQUIRED_DOCS),
        "golden_fixture_check": presence_check(repo_dir, REQUIRED_GOLDENS),
        "schema_presence_check": presence_check(repo_dir, REQUIRED_SCHEMAS),
        "schema_structural_status": schema_structural_status(schema_dir),
        "schema_hashes": schema_hashes,
        "inspect_contract_report": inspect_contract_report,
        "output_validation_eval": output_validation_eval,
        "execution_package_eval": {
            "package_id": execution_package["package_id"],
            "total_job_count": execution_package["total_job_count"],
            "request_count": execution_package["request_count"],
            "adapter_counts": execution_package["adapter_counts"],
            "source_counts": execution_package["source_counts"],
            "stage_counts": execution_package["stage_counts"],
            "validation_status": execution_package["validation_status"]["overall"],
            "hashes": package_hashes(execution_package_dir),
            "dag_validation_status": execution_package["dag_validation"]["status"],
            "dependency_count": execution_package["dependency_count"],
            "cache_extension_counts": json.loads((execution_package_dir / "cache_plan.json").read_text())["extension_counts"],
        },
        "scheduler_export_eval": {
            "slurm": slurm_summary,
            "local_dry_run": local_summary,
        },
        "pytest_durations": run_pytest_durations(repo_dir),
    }
    metrics["inspect_contract_status"] = metrics["inspect_contract_report"]["overall_status"]
    metrics["inspect_contract_golden_status"] = metrics["inspect_contract_report"]["golden_check"]["status"]
    metrics["inspect_contract_json_sane"] = json.loads(json.dumps(metrics["inspect_contract_report"]))["overall_status"] in {
        "PASS",
        "FAIL",
    }
    metrics["schema_structural_validation_status"] = (
        "PASS"
        if metrics["schema_structural_status"]["valid"] == len(SCHEMA_FILENAMES)
        and metrics["schema_presence_check"]["present"] == len(SCHEMA_FILENAMES)
        else "FAIL"
    )

    metrics.pop("write_manifest_time_seconds", None)
    metrics.pop("write_harmonization_time_seconds", None)

    json_path = reports_dir / "perf_diagnostics.json"
    md_path = reports_dir / "test_diagnostics.md"
    json_path.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(markdown_report(metrics), encoding="utf-8")

    print(json.dumps(metrics, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
