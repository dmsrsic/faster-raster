from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from faster_raster.adapters import static_http_range
from faster_raster.cli import app


runner = CliRunner()
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def isolate_static_range_reports(monkeypatch, tmp_path):
    monkeypatch.setattr(static_http_range, "DEFAULT_REPORT_DIR", tmp_path / "reports")


def ok(args):
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return result.output


def test_range_sources_plain():
    output = ok(["range", "sources", "--plain"])
    assert "chirps_daily_precipitation" in output
    assert "prism_daily_ppt_static_zip" in output
    assert "fixture_only" in output


def test_range_plan_dry_run_writes_reports(monkeypatch, tmp_path):
    monkeypatch.chdir(ROOT)
    output = ok(["range", "plan", "--plain"])
    assert "skipped_dry_run" in output
    assert "runnable_source_count: 4" in output
    assert "fixture_source_count: 1" in output
    assert "network_run: False" in output
    assert (static_http_range.DEFAULT_REPORT_DIR / "static_http_range_wave1_plan.json").exists()


def test_range_probe_dry_run_no_network(monkeypatch):
    def fail_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr("urllib.request.urlopen", fail_network)
    output = ok(["range", "probe", "chirps_daily_precipitation", "--plain"])
    assert "network_run: False" in output


def test_range_wave1_dry_run_json():
    output = ok(["range", "wave1", "--json"])
    payload = json.loads(output)
    assert len(payload["results"]) == 4
    assert len(payload["fixtures"]) == 1
    assert payload["runnable_source_count"] == 4
    assert payload["fixture_source_count"] == 1
    assert all(row["status"] == "skipped_dry_run" for row in payload["results"])


def test_range_probe_prism_fixture_only_no_network(monkeypatch):
    def fail_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr("urllib.request.urlopen", fail_network)
    output = ok(["range", "probe", "prism_daily_ppt_static_zip", "--allow-network", "--plain"])
    assert "fixture_only" in output
    assert "historical zip evidence" in output
    assert "network_run: False" in output


def test_cook_wave1_alias_dry_run():
    output = ok(["cook", "wave1", "--plain"])
    assert "Static HTTP range probe" in output


def test_task_show_reports_static_range_availability(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ok([
        "task", "new",
        "--id", "range_task",
        "--name", "Range Task",
        "--bbox=0,0,1,1",
        "--bbox-crs", "EPSG:4326",
        "--target-crs", "EPSG:5070",
        "--source", "chirps_daily_precipitation",
        "--plain",
    ])
    output = ok(["task", "show", "range_task", "--plain"])
    assert "static_range_adapter_available: True" in output
    payload = json.loads(ok(["task", "show", "range_task", "--json"]))
    assert payload["static_range_wave1_available_sources"] == ["chirps_daily_precipitation"]
