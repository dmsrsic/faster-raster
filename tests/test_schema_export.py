from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from faster_raster.cli import app
from faster_raster.contract import inspect_contract
from faster_raster.manifest import read_manifest
from faster_raster.schema_export import SCHEMA_FILENAMES, export_schemas, schema_structural_status


runner = CliRunner()
SCHEMA_DIR = Path("/home/dmsrsic/raster-work/faster-raster/schemas")
PROJECT_SPEC = Path("/home/dmsrsic/raster-work/projects/ohio_cdl_edges/research_spec.json")
MANIFEST = Path("/home/dmsrsic/raster-work/projects/ohio_cdl_edges/manifests/acquisition_manifest.jsonl")
PLAN = Path("/home/dmsrsic/raster-work/projects/ohio_cdl_edges/plans/harmonization_plan.json")


def load_schema(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def assert_required_fields(instance: dict, schema: dict) -> None:
    missing = sorted(set(schema["required"]) - set(instance))
    assert missing == []


def test_schema_export_writes_expected_files(tmp_path):
    paths = export_schemas(tmp_path)

    assert [path.name for path in paths] == SCHEMA_FILENAMES
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(SCHEMA_FILENAMES)


def test_schema_export_is_byte_stable(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    export_schemas(first)
    export_schemas(second)

    for filename in SCHEMA_FILENAMES:
        assert (first / filename).read_bytes() == (second / filename).read_bytes()


def test_committed_schema_files_are_current(tmp_path):
    export_schemas(tmp_path)

    for filename in SCHEMA_FILENAMES:
        assert (tmp_path / filename).read_bytes() == (SCHEMA_DIR / filename).read_bytes()


def test_schemas_have_required_fields_and_enum_like_contracts():
    research = load_schema(SCHEMA_DIR / "research_spec.schema.json")
    registry = load_schema(SCHEMA_DIR / "source_registry.schema.json")
    manifest = load_schema(SCHEMA_DIR / "acquisition_manifest_row.schema.json")
    report = load_schema(SCHEMA_DIR / "inspect_contract_report.schema.json")

    assert "project" in research["required"]
    source_props = research["properties"]["sources"]["items"]["properties"]
    assert source_props["acquisition_mode"]["enum"] == ["arcgis_export_image", "https_template"]
    assert source_props["semantic_type"]["enum"] == ["categorical", "continuous"]
    assert source_props["resampling"]["enum"] == ["nearest", "mode", "bilinear", "cubic", "lanczos", "average"]
    entry_props = registry["properties"]["sources"]["additionalProperties"]["properties"]
    assert entry_props["adapter"]["enum"] == ["arcgis_imageserver", "generic_https_template"]
    assert entry_props["bbox_request_policy"]["enum"] == [
        "preserve_input_bbox_with_bboxsr",
        "project_bbox_to_service_crs",
        "no_bbox_url_template",
    ]
    assert entry_props["year_parameter_strategy"]["enum"] == ["time_value", "mosaic_rule_by_attribute"]
    assert "tile_width_pixels" in manifest["required"]
    assert report["properties"]["overall_status"]["enum"] == ["PASS", "FAIL"]


def test_current_examples_match_schema_required_fields():
    research = json.loads(PROJECT_SPEC.read_text(encoding="utf-8"))
    manifest_row = read_manifest(MANIFEST)[0]
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    report = inspect_contract(PROJECT_SPEC)

    assert_required_fields(research, load_schema(SCHEMA_DIR / "research_spec.schema.json"))
    assert_required_fields(manifest_row, load_schema(SCHEMA_DIR / "acquisition_manifest_row.schema.json"))
    assert_required_fields(plan, load_schema(SCHEMA_DIR / "harmonization_plan.schema.json"))
    assert_required_fields(report, load_schema(SCHEMA_DIR / "inspect_contract_report.schema.json"))


def test_schema_structural_status_passes():
    status = schema_structural_status(SCHEMA_DIR)

    assert status["present"] == len(SCHEMA_FILENAMES)
    assert status["valid"] == len(SCHEMA_FILENAMES)


def test_cli_export_schemas_writes_only_schema_files(tmp_path):
    result = runner.invoke(app, ["export-schemas", "--out", str(tmp_path)])

    assert result.exit_code == 0
    assert "wrote 5 schema files" in result.output
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(SCHEMA_FILENAMES)


def test_cli_export_schemas_no_network_access(monkeypatch, tmp_path):
    def fail_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr("urllib.request.urlopen", fail_network)
    result = runner.invoke(app, ["export-schemas", "--out", str(tmp_path)])

    assert result.exit_code == 0
