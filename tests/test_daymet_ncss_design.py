from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from faster_raster.adapters.thredds_ncss import ThreddsNcssAdapter
from faster_raster.url_planner import ADAPTERS


ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = ROOT / "research" / "daymet_ncss_probe_spec.yaml"


def load_probe_spec() -> dict:
    return yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))


def test_daymet_probe_spec_yaml_parses():
    config = load_probe_spec()
    assert config["source_id"] == "ornl_daymet_daily_ncss_service_aware"
    assert config["network_default"] == "disabled"
    assert config["scenario"]["max_bytes"] == 65536


def test_thredds_ncss_adapter_is_disabled_by_default():
    adapter = ThreddsNcssAdapter()
    assert adapter.adapter_name == "thredds_ncss"
    assert adapter.runtime_enabled is False
    assert "thredds_ncss" not in ADAPTERS


def test_thredds_ncss_probe_request_is_deterministic():
    adapter = ThreddsNcssAdapter()
    config = load_probe_spec()
    first = adapter.plan_probe_request(config)
    second = adapter.plan_probe_request(config)
    assert first == second
    assert first["request_id"] == "daymet_prcp_20230101_probe_bbox_000001"
    assert first["params"] == dict(sorted(first["params"].items()))
    assert first["network_default"] == "disabled"
    assert first["extraction"] is False


def test_thredds_ncss_probe_request_has_expected_fields():
    request = ThreddsNcssAdapter().plan_probe_request(load_probe_spec())
    assert {
        "request_id",
        "source_id",
        "adapter",
        "experimental",
        "runtime_enabled",
        "discovery_mechanism",
        "credential_scope",
        "method",
        "endpoint",
        "params",
        "variables",
        "time_range",
        "bbox",
        "target_grid_crs",
        "semantic_type",
        "resampling",
        "expected_format",
        "max_probe_bytes",
        "status",
    } <= set(request)
    assert request["variables"] == ["prcp"]
    assert request["expected_format"] == "netcdf"
    assert request["bbox"]["crs"] == "EPSG:4326"


def test_thredds_ncss_validation_errors_are_clear():
    config = load_probe_spec()
    del config["scenario"]["bbox"]
    with pytest.raises(ValueError, match="scenario missing required field: bbox"):
        ThreddsNcssAdapter().plan_probe_request(config)


def test_thredds_ncss_adapter_does_not_use_network(monkeypatch):
    def fail_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr("urllib.request.urlopen", fail_network)
    request = ThreddsNcssAdapter().plan_probe_request(load_probe_spec())
    assert request["status"] == "planned_experimental"
