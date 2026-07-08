from __future__ import annotations

import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from faster_raster.cli import app


runner = CliRunner()


def test_cli_validate_success(project_spec_path):
    result = runner.invoke(app, ["validate", str(project_spec_path)])
    assert result.exit_code == 0
    assert "valid:" in result.output


def test_cli_resolve_sources_summary(project_spec_path):
    result = runner.invoke(app, ["resolve-sources", str(project_spec_path)])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["sources"][0]["registry_key"] == "usda_nass_cdl_imageserver"
    assert payload["sources"][0]["adapter"] == "arcgis_imageserver"


def test_cli_plan_urls_writes_manifest_and_summary(project_spec_path, tmp_path):
    manifest = tmp_path / "manifest.jsonl"
    result = runner.invoke(app, ["plan-urls", str(project_spec_path), "--out", str(manifest)])
    assert result.exit_code == 0
    assert manifest.exists()
    assert "wrote:" in result.output
    assert '"records": 2' in result.output


def test_cli_plan_harmonization_writes_plan_and_summary(project_spec_path, tmp_path):
    manifest = tmp_path / "manifest.jsonl"
    plan = tmp_path / "harmonization.json"
    runner.invoke(app, ["plan-urls", str(project_spec_path), "--out", str(manifest)])

    result = runner.invoke(
        app,
        ["plan-harmonization", str(project_spec_path), "--manifest", str(manifest), "--out", str(plan)],
    )

    assert result.exit_code == 0
    assert plan.exists()
    assert "wrote:" in result.output
    assert '"target_crs": "EPSG:5070"' in result.output


def test_cli_inspect_manifest_prints_summary(project_spec_path, tmp_path):
    manifest = tmp_path / "manifest.jsonl"
    runner.invoke(app, ["plan-urls", str(project_spec_path), "--out", str(manifest)])

    result = runner.invoke(app, ["inspect-manifest", str(manifest)])

    assert result.exit_code == 0
    assert '"records": 2' in result.output
    assert '"planned": 2' in result.output


def test_cli_inspect_harmonization_prints_summary(project_spec_path, tmp_path):
    manifest = tmp_path / "manifest.jsonl"
    plan = tmp_path / "harmonization.json"
    runner.invoke(app, ["plan-urls", str(project_spec_path), "--out", str(manifest)])
    runner.invoke(app, ["plan-harmonization", str(project_spec_path), "--manifest", str(manifest), "--out", str(plan)])

    result = runner.invoke(app, ["inspect-harmonization", str(plan)])

    assert result.exit_code == 0
    assert '"inputs": 2' in result.output


def test_cli_invalid_input_returns_nonzero(tmp_path):
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"project": {}}', encoding="utf-8")

    result = runner.invoke(app, ["validate", str(invalid)])

    assert result.exit_code != 0
    assert "Field required" in result.output or isinstance(result.exception, Exception)


def test_cli_inspect_contract_passes_for_example(project_spec_path):
    result = runner.invoke(app, ["inspect-contract", str(project_spec_path)])

    assert result.exit_code == 0
    assert "Overall status: PASS" in result.output
    assert "bbox_request_policy: preserve_input_bbox_with_bboxsr" in result.output


def test_cli_inspect_contract_invalid_capability_returns_nonzero(project_spec_path, tmp_path):
    registry = Path("/home/dmsrsic/raster-work/faster-raster/configs/source_registry.yaml")
    broken = tmp_path / "source_registry.yaml"
    broken.write_text(registry.read_text(encoding="utf-8").replace("adapter: arcgis_imageserver", "adapter: stac"), encoding="utf-8")

    result = runner.invoke(app, ["inspect-contract", str(project_spec_path), "--registry", str(broken)])

    assert result.exit_code != 0
    assert "Overall status: FAIL" in result.output
    assert "Unsupported adapter for v0: stac" in result.output


def test_cli_inspect_contract_json_emits_expected_fields(project_spec_path):
    result = runner.invoke(app, ["inspect-contract", str(project_spec_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["overall_status"] == "PASS"
    assert payload["package_version"]
    assert payload["project_id"] == "ohio_cdl_edge_dynamics_v001"
    assert payload["sources"][0]["registry_key"] == "usda_nass_cdl_imageserver"


def test_cli_inspect_contract_check_goldens_detects_present_goldens(project_spec_path):
    result = runner.invoke(app, ["inspect-contract", str(project_spec_path), "--json", "--check-goldens"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["golden_check"]["status"] == "PASS"
    assert payload["golden_check"]["present"] == payload["golden_check"]["expected"]


def test_cli_inspect_contract_check_goldens_detects_drift(project_spec_path, tmp_path):
    golden_dir = tmp_path / "golden"
    shutil.copytree("/home/dmsrsic/raster-work/faster-raster/tests/golden", golden_dir)
    manifest = golden_dir / "acquisition_manifest_preserve_bbox.jsonl"
    manifest.write_text(manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["inspect-contract", str(project_spec_path), "--json", "--check-goldens", "--golden-dir", str(golden_dir)],
    )

    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["golden_check"]["status"] == "FAIL"
    assert "acquisition_manifest_preserve_bbox.jsonl" in payload["golden_check"]["drift"]


def test_cli_inspect_contract_does_not_create_manifests(project_spec_path, tmp_path):
    before = sorted(path.name for path in tmp_path.iterdir())
    result = runner.invoke(app, ["inspect-contract", str(project_spec_path)], catch_exceptions=False)
    after = sorted(path.name for path in tmp_path.iterdir())

    assert result.exit_code == 0
    assert before == after


def test_cli_inspect_contract_no_network_access(monkeypatch, project_spec_path):
    def fail_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr("urllib.request.urlopen", fail_network)
    result = runner.invoke(app, ["inspect-contract", str(project_spec_path)])

    assert result.exit_code == 0
