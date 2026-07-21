from __future__ import annotations

import json
from pathlib import Path

from click import unstyle
from typer.main import get_command
from typer.testing import CliRunner

from faster_raster.cli import app

runner = CliRunner()
HELP_ENV = {"COLUMNS": "120"}
REQUIRED_PREVIEW_OPTIONS = {
    "--visibility-mode", "--sample-grid-size", "--grid-size",
    "--preview-expand-factor", "--cdl-render-mode", "--cdl-verify-samples",
}


def command_option_names(*command_path):
    command = get_command(app)
    for command_name in command_path:
        command = command.commands[command_name]
    return {
        option_name
        for parameter in command.params
        for option_name in (*parameter.opts, *parameter.secondary_opts)
    }



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
    assert "allow-network" in unstyle(result.output)


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



def test_auth_check_help_includes_live_and_allow_network():
    assert {"--live", "--allow-network"} <= command_option_names("copernicus", "auth-check")
    assert {"--allow-network"} <= command_option_names("copernicus", "sentinel", "search-live")
    result = runner.invoke(app, ["copernicus", "auth-check", "--help"], env=HELP_ENV)
    assert result.exit_code == 0
    output = unstyle(result.output)
    assert "--live" in output
    assert "--allow-network" in output


def test_auth_check_live_requires_allow_network(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["copernicus", "auth-check", "--live", "--plain"])
    assert result.exit_code != 0
    assert "allow-network" in unstyle(result.output)


def test_auth_check_live_mocked_redacts_token(tmp_path, monkeypatch):
    from faster_raster import copernicus_auth

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CDSE_ACCESS_TOKEN", "fake-token-value")
    class FakeHeaders(dict):
        def get(self, key, default=None):
            return super().get(key, default)
    class FakeResponse:
        def __init__(self):
            self.data = b'{"type":"Catalog"}'
            self.headers = FakeHeaders({"Content-Type": "application/json"})
            self.status = 200
            self.code = 200
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def read(self, size=-1):
            return self.data if size < 0 else self.data[:size]
    monkeypatch.setattr(copernicus_auth.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    payload = json.loads(ok(["copernicus", "auth-check", "--live", "--allow-network", "--json"]))
    assert payload["network_run"] is True
    assert payload["live_probe_attempted"] is True
    assert payload["token_redacted"] is True
    assert payload["no_downloads"] is True
    assert "fake-token-value" not in json.dumps(payload)


def test_preview_real_help_contains_visibility_options():
    assert REQUIRED_PREVIEW_OPTIONS <= command_option_names("task", "preview-real")
    assert REQUIRED_PREVIEW_OPTIONS <= command_option_names("stack", "preview-real")
    result = runner.invoke(app, ["task", "preview-real", "--help"], env=HELP_ENV)
    assert result.exit_code == 0
    output = unstyle(result.output)
    assert "--visibility-mode" in output
    assert "--overlay-strength" in output
