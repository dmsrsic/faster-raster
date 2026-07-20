from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "daymet_single_pixel_probe.py"
SPEC_PATH = ROOT / "research" / "daymet_single_pixel_probe_spec.yaml"


def load_script_module():
    spec = importlib.util.spec_from_file_location("daymet_single_pixel_probe", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeHeaders(dict):
    def items(self):
        return super().items()


class FakeResponse:
    status = 200
    headers = FakeHeaders({"Content-Type": "text/csv", "Content-Length": "100", "Accept-Ranges": "bytes"})

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


def test_url_construction_is_deterministic():
    module = load_script_module()
    config = module.load_probe_spec(SPEC_PATH)
    assert module.build_url(config) == (
        "https://daymet.ornl.gov/single-pixel/api/data?"
        "lat=39.805&lon=-83.195&vars=prcp&start=2023-01-01&end=2023-01-01"
    )


def test_refuses_without_allow_network(monkeypatch):
    module = load_script_module()
    monkeypatch.setattr(module, "parse_args", lambda: type("Args", (), {
        "allow_network": False,
        "spec": str(SPEC_PATH),
        "max_bytes": 64,
        "chunk_size": 16,
        "timeout_seconds": 1,
        "out": "/tmp/out.json",
        "markdown": "/tmp/out.md",
    })())
    with pytest.raises(SystemExit, match="without --allow-network"):
        module.main()


def test_max_bytes_cap_helper():
    module = load_script_module()
    response = FakeResponse(b"abcdefghijklmnopqrstuvwxyz")
    result = module.read_bounded_response(response, max_bytes=10, chunk_size=4, start=module.time.perf_counter())
    assert result["bytes_read"] == 10
    assert result["truncated"] is True
    assert result["sha256"]


def test_report_writers_shape(tmp_path):
    module = load_script_module()
    report = {
        "probe_status": "PASS",
        "http_status": 200,
        "content_type": "text/csv",
        "bytes_read": 12,
        "truncated": False,
        "sha256": "abc",
        "elapsed_seconds": 0.1,
        "first_byte_seconds": 0.01,
        "error": None,
        "url": "https://example.invalid",
        "first_response_lines": ["a,b"],
    }
    json_path = tmp_path / "report.json"
    md_path = tmp_path / "report.md"
    module.write_json_report(json_path, report)
    module.write_markdown_report(md_path, report)
    assert json.loads(json_path.read_text())["probe_status"] == "PASS"
    assert "Daymet Single-Pixel Probe Report" in md_path.read_text()


def test_no_network_with_mocked_opener():
    module = load_script_module()
    config = module.load_probe_spec(SPEC_PATH)

    def opener(request, timeout):
        return FakeResponse(b"year,yday,prcp\n2023,1,0.0\n")

    report = module.probe(config, max_bytes=64, chunk_size=16, timeout_seconds=1, opener=opener)
    assert report["probe_status"] == "PASS"
    assert report["http_status"] == 200
    assert report["first_response_lines"][0] == "year,yday,prcp"
