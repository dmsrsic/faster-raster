from __future__ import annotations

import json
from pathlib import Path

from click import unstyle
from typer.testing import CliRunner

from faster_raster.cli import app

runner = CliRunner()


def ok(args):
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return result.output


def make_task(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ok([
        "task", "new",
        "--id", "cli_real_task",
        "--name", "CLI Real Task",
        "--bbox=-83.2,39.8,-83.19,39.81",
        "--bbox-crs", "EPSG:4326",
        "--target-crs", "EPSG:5070",
        "--years", "2023",
        "--theme", "landcover",
        "--source", "cdl_arcgis_tiny_export",
        "--plain",
    ])


def test_task_preview_real_dry_run_cli(tmp_path, monkeypatch):
    make_task(tmp_path, monkeypatch)
    output = ok(["task", "preview-real", "cli_real_task", "--plain"])
    assert "network_run: False" in output
    assert "real_fetch_attempted: False" in output
    assert Path("reports/task_previews/cli_real_task_real_preview_plan.json").exists()
    payload = json.loads(Path("reports/task_previews/cli_real_task_real_preview_plan.json").read_text())
    assert payload["network_run"] is False
    assert payload["real_fetch_attempted"] is False


def test_stack_preview_real_dry_run_cli_json(tmp_path, monkeypatch):
    make_task(tmp_path, monkeypatch)
    payload = json.loads(ok(["stack", "preview-real", "cli_real_task", "--json"]))
    assert payload["real_data_preview"] is True
    assert payload["network_run"] is False
    assert payload["source_results"][0]["source_id"] == "cdl_arcgis_tiny_export"


def test_task_preview_real_dry_run_preview_size_flag(tmp_path, monkeypatch):
    make_task(tmp_path, monkeypatch)
    payload = json.loads(ok(["task", "preview-real", "cli_real_task", "--preview-size", "512", "--max-pixels", "100", "--json"]))
    assert payload["preview_size"] == 10
    assert payload["network_run"] is False


def test_task_preview_real_debug_no_cache_flags_dry_run(tmp_path, monkeypatch):
    make_task(tmp_path, monkeypatch)
    output = ok(["task", "preview-real", "cli_real_task", "--debug-artifacts", "--no-cache-raw", "--plain"])
    assert "dry_run: True" in output



def assert_help_has(command_prefix):
    # Keep the public help contract stable at a typical wide-terminal width.
    result = runner.invoke(app, command_prefix + ["--help"], env={"COLUMNS": "120"})
    assert result.exit_code == 0, result.output
    output = unstyle(result.output)
    for option in ["--sample-grid-size", "--grid-size", "--preview-expand-factor", "--cdl-render-mode", "--cdl-verify-samples"]:
        assert option in output


def test_preview_real_help_includes_cdl_sample_options():
    assert_help_has(["task", "preview-real"])
    assert_help_has(["stack", "preview-real"])


def test_task_preview_real_dry_run_cdl_sample_flags(tmp_path, monkeypatch):
    make_task(tmp_path, monkeypatch)
    before = Path("tasks/cli_real_task.yaml").read_text()
    payload = json.loads(ok(["task", "preview-real", "cli_real_task", "--sample-grid-size", "5", "--preview-expand-factor", "10", "--cdl-render-mode", "auto", "--json"]))
    assert Path("tasks/cli_real_task.yaml").read_text() == before
    assert payload["cdl_verification_run"] is False
    assert payload["sample_grid_size"] == 5
    assert payload["preview_expand_factor"] == 10
    assert payload["cdl_render_mode"] == "auto"
    assert payload["preview_fetch_bbox"] != payload["bbox"]


def test_grid_size_alias_sets_sample_grid_size(tmp_path, monkeypatch):
    make_task(tmp_path, monkeypatch)
    payload = json.loads(ok(["stack", "preview-real", "cli_real_task", "--grid-size", "4", "--json"]))
    assert payload["sample_grid_size"] == 4
