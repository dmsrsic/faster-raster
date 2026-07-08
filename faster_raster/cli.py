from __future__ import annotations

import json
from pathlib import Path

import typer

from faster_raster.contract import inspect_contract as build_contract_report
from faster_raster.execution_package import build_execution_package, package_hashes
from faster_raster.harmonization_planner import (
    plan_from_manifest,
    read_harmonization_plan,
    summarize_harmonization_plan,
    write_harmonization_plan,
)
from faster_raster.manifest import read_manifest, summarize_manifest, write_manifest
from faster_raster.output_validation import validate_harmonization as validate_harmonization_output
from faster_raster.output_validation import validate_manifest as validate_manifest_output
from faster_raster.schema_export import export_schemas
from faster_raster.scheduler_export import export_scheduler_package
from faster_raster.source_registry import load_registry
from faster_raster.url_planner import plan_urls
from faster_raster.validation import load_spec, validate_or_raise, validate_spec

app = typer.Typer(no_args_is_help=True)


def _print_json(value: dict) -> None:
    typer.echo(json.dumps(value, indent=2, sort_keys=True))


@app.command()
def validate(spec: Path) -> None:
    research_spec = load_spec(spec)
    registry = load_registry()
    errors = validate_spec(research_spec, registry)
    if errors:
        for error in errors:
            typer.echo(f"ERROR: {error}")
        raise typer.Exit(code=1)
    typer.echo(f"valid: {spec}")


@app.command("resolve-sources")
def resolve_sources(spec: Path) -> None:
    research_spec = load_spec(spec)
    registry = load_registry()
    validate_or_raise(research_spec, registry)
    resolved = []
    for source in sorted(research_spec.sources, key=lambda item: item.id):
        entry = registry.sources[source.registry_key]
        resolved.append(
            {
                "source_id": source.id,
                "registry_key": source.registry_key,
                "adapter": entry.adapter,
                "provider": entry.provider,
                "product": entry.product,
                "years": sorted(source.years),
                "thematic_layers": sorted(source.thematic_layers),
            }
        )
    _print_json({"sources": resolved})


@app.command("plan-urls")
def plan_urls_command(spec: Path, out: Path = typer.Option(..., "--out")) -> None:
    research_spec = load_spec(spec)
    registry = load_registry()
    rows = plan_urls(research_spec, registry, spec)
    write_manifest(rows, out)
    summary = summarize_manifest(rows)
    typer.echo(f"wrote: {out}")
    _print_json(summary)


@app.command("plan-harmonization")
def plan_harmonization(spec: Path, manifest: Path = typer.Option(..., "--manifest"), out: Path = typer.Option(..., "--out")) -> None:
    research_spec = load_spec(spec)
    plan = plan_from_manifest(research_spec, manifest)
    write_harmonization_plan(plan, out)
    typer.echo(f"wrote: {out}")
    _print_json(summarize_harmonization_plan(plan))


@app.command("validate-manifest")
def validate_manifest_command(manifest: Path, json_output: bool = typer.Option(False, "--json")) -> None:
    report = validate_manifest_output(manifest)
    if json_output:
        _print_json(report)
    else:
        typer.echo(f"Manifest validation: {report['status']}")
        typer.echo(f"Rows checked: {report['row_count']}")
        typer.echo(f"Errors: {report['error_count']}")
        for error in report["errors"][:20]:
            typer.echo(f"ERROR: {error}")
    if report["status"] != "PASS":
        raise typer.Exit(code=1)


@app.command("validate-harmonization")
def validate_harmonization_command(
    plan: Path,
    manifest: Path | None = typer.Option(None, "--manifest"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    report = validate_harmonization_output(plan, manifest)
    if json_output:
        _print_json(report)
    else:
        typer.echo(f"Harmonization validation: {report['status']}")
        typer.echo(f"Inputs checked: {report['input_count']}")
        if manifest is not None:
            typer.echo(f"Manifest rows checked: {report.get('manifest_row_count', 0)}")
        typer.echo(f"Errors: {report['error_count']}")
        for error in report["errors"][:20]:
            typer.echo(f"ERROR: {error}")
    if report["status"] != "PASS":
        raise typer.Exit(code=1)


@app.command("inspect-manifest")
def inspect_manifest(manifest: Path) -> None:
    _print_json(summarize_manifest(read_manifest(manifest)))


@app.command("inspect-harmonization")
def inspect_harmonization(plan: Path) -> None:
    _print_json(summarize_harmonization_plan(read_harmonization_plan(plan)))


@app.command("inspect-contract")
def inspect_contract(
    spec: Path,
    registry: Path | None = typer.Option(None, "--registry"),
    json_output: bool = typer.Option(False, "--json"),
    check_goldens: bool = typer.Option(False, "--check-goldens"),
    golden_dir: Path | None = typer.Option(None, "--golden-dir", hidden=True),
) -> None:
    report = build_contract_report(spec, registry, check_goldens, golden_dir=golden_dir) if golden_dir else build_contract_report(spec, registry, check_goldens)
    if json_output:
        _print_json(report)
    else:
        typer.echo(f"FasterRaster {report['package_version']}")
        typer.echo(f"Project: {report['project_id']}")
        typer.echo(f"Sources: {report['source_count']}")
        typer.echo(f"Overall status: {report['overall_status']}")
        for source in report["sources"]:
            typer.echo(
                f"- {source['source_id']} [{source['capability_status']}]: "
                f"{source.get('adapter', 'missing')} {source.get('provider', '')} {source.get('product', '')}"
            )
            if "bbox_request_policy" in source:
                typer.echo(f"  bbox_request_policy: {source['bbox_request_policy']}")
                typer.echo(f"  export_image_crs: {source['default_export_image_crs']}")
                typer.echo(f"  target_grid_crs: {source['target_grid_crs']}")
                typer.echo(f"  year_parameter_strategy: {source['year_parameter_strategy']}")
                typer.echo(f"  crs_transform: {source['supported_crs_transform_status']}")
        if report["errors"]:
            typer.echo("Errors:")
            for error in report["errors"]:
                typer.echo(f"  - {error}")
        if check_goldens:
            golden = report["golden_check"]
            typer.echo(f"Golden fixtures: {golden['status']} ({golden['matches']}/{len(golden['comparisons'])} matched)")
    if report["overall_status"] != "PASS":
        raise typer.Exit(code=1)
    if check_goldens and report["golden_check"]["status"] != "PASS":
        raise typer.Exit(code=1)


@app.command("compile-execution-package")
def compile_execution_package_command(
    manifest: Path = typer.Option(..., "--manifest"),
    harmonization: Path = typer.Option(..., "--harmonization"),
    out: Path = typer.Option(..., "--out"),
    execution_profile: Path | None = typer.Option(None, "--execution-profile"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    try:
        package = build_execution_package(
            manifest_path=manifest,
            harmonization_path=harmonization,
            out_dir=out,
            execution_profile=execution_profile,
        )
    except ValueError as exc:
        typer.echo(f"ERROR: execution package validation failed: {exc}")
        raise typer.Exit(code=1)
    summary = {
        "status": "PASS",
        "out": str(out),
        "package_id": package["package_id"],
        "total_job_count": package["total_job_count"],
        "request_count": package["request_count"],
        "adapter_counts": package["adapter_counts"],
        "source_counts": package["source_counts"],
        **package_hashes(out),
    }
    if json_output:
        _print_json(summary)
    else:
        typer.echo(f"wrote execution package: {out}")
        typer.echo(f"Package ID: {summary['package_id']}")
        typer.echo(f"Jobs: {summary['total_job_count']}")
        typer.echo(f"Validation: {package['validation_status']['overall']}")


@app.command("export-scheduler")
def export_scheduler_command(
    package: Path = typer.Option(..., "--package"),
    scheduler: str = typer.Option(..., "--scheduler"),
    out: Path = typer.Option(..., "--out"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    try:
        summary = export_scheduler_package(package, scheduler, out)
    except ValueError as exc:
        typer.echo(f"ERROR: scheduler export failed: {exc}")
        raise typer.Exit(code=1)
    if json_output:
        _print_json(summary)
    else:
        typer.echo(f"wrote scheduler export: {out}")
        typer.echo(f"Scheduler: {summary['scheduler']}")
        typer.echo(f"Jobs: {summary['job_count']}")
        typer.echo(f"DAG validation: {summary['dag_validation_status']}")


@app.command("export-schemas")
def export_schemas_command(out: Path = typer.Option(..., "--out")) -> None:
    paths = export_schemas(out)
    typer.echo(f"wrote {len(paths)} schema files to {out}")
    for path in paths:
        typer.echo(str(path))


if __name__ == "__main__":
    app()


from faster_raster.cli_app import register_product_commands
register_product_commands(app)
