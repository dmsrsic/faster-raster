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


def make_task(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ok([
        "task", "new",
        "--id", "cli_sentinel_task",
        "--name", "CLI Sentinel Task",
        "--bbox=-83.2,39.8,-83.19,39.81",
        "--bbox-crs", "EPSG:4326",
        "--target-crs", "EPSG:5070",
        "--years", "2023",
        "--theme", "sentinel2",
        "--source", "copernicus_sentinel2_l2a_cdse_stac",
        "--plain",
    ])


def test_copernicus_auth_check_plain_and_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    plain = ok(["copernicus", "auth-check", "--plain"])
    assert "auth_present:" in plain
    payload = json.loads(ok(["copernicus", "auth-check", "--json"]))
    assert payload["network_run"] is False


def test_sentinel_search_plan_cli(tmp_path, monkeypatch):
    make_task(tmp_path, monkeypatch)
    output = ok(["copernicus", "sentinel", "search-plan", "cli_sentinel_task", "--plain"])
    assert "network_run: False" in output
    assert Path("reports/copernicus/cli_sentinel_task_sentinel2_l2a_search_plan.json").exists()


def test_sentinel_search_live_requires_allow_network(tmp_path, monkeypatch):
    make_task(tmp_path, monkeypatch)
    result = runner.invoke(app, ["copernicus", "sentinel", "search-live", "cli_sentinel_task", "--plain"])
    assert result.exit_code != 0
    assert "allow-network" in result.output


def test_preview_real_layout_cli_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ok([
        "task", "new",
        "--id", "layout_task",
        "--name", "Layout Task",
        "--bbox=-83.2,39.8,-83.19,39.81",
        "--bbox-crs", "EPSG:4326",
        "--target-crs", "EPSG:5070",
        "--years", "2023",
        "--theme", "landcover",
        "--source", "cdl_arcgis_tiny_export",
        "--plain",
    ])
    payload = json.loads(ok(["task", "preview-real", "layout_task", "--layout", "report", "--json"]))
    assert payload["preview_layout"] == "report"
    assert payload["base_raster_was_tiled"] is False
