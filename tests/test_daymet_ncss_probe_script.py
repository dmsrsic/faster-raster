from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT_PATH = Path("/home/dmsrsic/raster-work/faster-raster/scripts/daymet_ncss_probe.py")
SPEC_PATH = Path("/home/dmsrsic/raster-work/faster-raster/research/daymet_ncss_probe_spec.yaml")


def load_script_module():
    spec = importlib.util.spec_from_file_location("daymet_ncss_probe", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeHeaders(dict):
    def items(self):
        return super().items()


class FakeResponse:
    status = 200
    headers = FakeHeaders({"Content-Type": "application/xml", "Content-Length": "10", "Accept-Ranges": "bytes"})

    def __init__(self, body: bytes):
        self.body = body
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size: int) -> bytes:
        chunk = self.body[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


def test_script_refuses_without_allow_network():
    module = load_script_module()
    with pytest.raises(SystemExit, match="without --allow-network"):
        module.run_probe(
            spec_path=SPEC_PATH,
            allow_network=False,
            metadata_only=True,
            max_bytes=64,
            chunk_size=16,
            timeout_seconds=1,
        )


def test_spec_loads():
    module = load_script_module()
    config = module.load_probe_spec(SPEC_PATH)
    assert config["source_id"] == "ornl_daymet_daily_ncss_service_aware"
    assert config["network_default"] == "disabled"


def test_request_descriptor_is_deterministic():
    module = load_script_module()
    config = module.load_probe_spec(SPEC_PATH)
    first = module.ThreddsNcssAdapter().plan_probe_request(config)
    second = module.ThreddsNcssAdapter().plan_probe_request(config)
    assert first == second


def test_read_bounded_response_enforces_max_bytes():
    module = load_script_module()
    response = FakeResponse(b"abcdefghijklmnopqrstuvwxyz")
    result = module.read_bounded_response(response, max_bytes=10, chunk_size=4, start=module.time.perf_counter())
    assert result["bytes_read"] == 10
    assert result["truncated"] is True
    assert result["sha256"]


def test_report_writers_produce_json_and_markdown(tmp_path):
    module = load_script_module()
    report = {
        "probe_status": "FAIL",
        "source_id": "ornl_daymet_daily_ncss_service_aware",
        "request_id": "daymet_prcp_20230101_probe_bbox_000001",
        "network_opt_in": True,
        "metadata_only": True,
        "max_bytes": 64,
        "stage_results": [
            {
                "stage": "metadata",
                "stage_status": "SKIPPED",
                "http_status": None,
                "bytes_read": 0,
                "content_type": None,
                "elapsed_seconds": 0.0,
                "error": "endpoint unresolved",
                "endpoint": None,
            }
        ],
    }
    json_path = tmp_path / "probe.json"
    md_path = tmp_path / "probe.md"
    module.write_json_report(json_path, report)
    module.write_markdown_report(md_path, report)
    assert json.loads(json_path.read_text())["probe_status"] == "FAIL"
    assert "Daymet NCSS Probe Report" in md_path.read_text()


def test_no_network_guard_with_unresolved_metadata_endpoint(monkeypatch, tmp_path):
    module = load_script_module()
    config = module.load_probe_spec(SPEC_PATH)
    config["probe_sequence"][0]["endpoint"] = "needs_official_verification"
    unresolved_spec = tmp_path / "unresolved_daymet_probe.yaml"
    import yaml

    unresolved_spec.write_text(yaml.safe_dump(config), encoding="utf-8")

    def fail_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    report = module.run_probe(
        spec_path=unresolved_spec,
        allow_network=True,
        metadata_only=True,
        max_bytes=64,
        chunk_size=16,
        timeout_seconds=1,
        opener=fail_network,
    )
    assert report["probe_status"] == "FAIL"
    assert report["stage_results"][0]["stage_status"] == "SKIPPED"
    assert "needs_official_verification" in report["stage_results"][0]["error"]


def test_mocked_metadata_probe_can_pass(tmp_path):
    module = load_script_module()
    config = module.load_probe_spec(SPEC_PATH)
    config["probe_sequence"][0]["endpoint"] = "https://example.invalid/thredds/catalog.xml"
    spec_path = tmp_path / "spec.yaml"
    import yaml

    spec_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    def opener(request, timeout):
        return FakeResponse(b"<catalog />")

    report = module.run_probe(
        spec_path=spec_path,
        allow_network=True,
        metadata_only=True,
        max_bytes=64,
        chunk_size=16,
        timeout_seconds=1,
        opener=opener,
    )
    assert report["probe_status"] == "PASS"
    assert report["stage_results"][0]["http_status"] == 200
    assert report["stage_results"][0]["bytes_read"] == len(b"<catalog />")
    assert report["stage_results"][1]["stage_status"] == "SKIPPED"
