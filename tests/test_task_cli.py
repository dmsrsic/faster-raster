from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from faster_raster.cli import app

runner = CliRunner()


def ok(args):
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return result.output


def test_task_cli_create_list_show_validate_preview(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    output = ok([
        "task", "new",
        "--id", "cli_task",
        "--name", "CLI Task",
        "--bbox=-83.2,39.8,-83.19,39.81",
        "--bbox-crs", "EPSG:4326",
        "--target-crs", "EPSG:5070",
        "--years", "2023",
        "--theme", "precipitation",
        "--theme", "landcover",
        "--source", "prism_daily_ppt_static_zip",
        "--source", "cdl_arcgis_tiny_export",
        "--plain",
    ])
    assert "created task" in output
    assert Path("tasks/cli_task.yaml").exists()
    assert "cli_task" in ok(["task", "list", "--plain"])
    assert "target_crs: EPSG:5070" in ok(["task", "show", "cli_task", "--plain"])
    assert "status: PASS" in ok(["task", "validate", "cli_task", "--plain"])
    preview = ok(["task", "preview", "cli_task", "--plain"])
    assert "network_run: False" in preview
    assert Path("reports/task_previews/cli_task_stack_preview.png").exists()
    alias = ok(["stack", "preview", "cli_task", "--json"])
    assert json.loads(alias)["network_run"] is False


def test_task_validate_nonzero_on_invalid(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("tasks").mkdir()
    Path("tasks/bad.yaml").write_text(
        "task_id: bad\n"
        "aoi:\n"
        "  bbox: [0, 1]\n"
        "target_grid: {}\n"
        "time:\n"
        "  years: []\n"
        "themes: []\n"
        "sources: []\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["task", "validate", "bad", "--plain"])
    assert result.exit_code != 0
    assert "FAIL" in result.output


def test_task_preview_open_fallback(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ok(["task", "new", "--id", "open_task", "--name", "Open Task", "--bbox=0,0,1,1", "--bbox-crs", "EPSG:4326", "--target-crs", "EPSG:5070", "--theme", "landcover", "--plain"])
    output = ok(["task", "preview", "open_task", "--open", "--plain"])
    assert "preview_png" in output
