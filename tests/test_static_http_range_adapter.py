from __future__ import annotations

import hashlib
import json
from email.message import Message
from pathlib import Path

import pytest

from faster_raster.adapters import static_http_range


class FakeResponse:
    def __init__(self, data: bytes, *, status: int = 206, content_type: str = "application/octet-stream", content_range: str | None = "bytes 0-9/100"):
        self.data = data
        self.status = status
        self.read_limit = None
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(data))
        if content_range:
            self.headers["Content-Range"] = content_range

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def getcode(self):
        return self.status

    def read(self, limit: int = -1) -> bytes:
        self.read_limit = limit
        return self.data[:limit]


def spec_by_id(source_id: str) -> dict:
    return next(spec for spec in static_http_range.load_wave1_specs() if spec["source_id"] == source_id)


def test_wave1_loads_four_runnable_sources_and_one_fixture():
    runnable = static_http_range.load_runnable_specs()
    fixtures = static_http_range.load_fixture_specs()
    assert [spec["source_id"] for spec in runnable] == [
        "chirps_daily_precipitation",
        "gridmet_daily",
        "terraclimate_monthly",
        "worldclim_bioclim_normals",
    ]
    assert [spec["source_id"] for spec in fixtures] == ["prism_daily_ppt_static_zip"]
    assert fixtures[0]["historical_sha256_short"] == "cc89306d4d5b"


def test_url_rendering_is_deterministic():
    spec = spec_by_id("chirps_daily_precipitation")
    assert static_http_range.render_static_url(spec, {}) == static_http_range.render_static_url(spec, {})
    assert static_http_range.render_static_url(spec, {}).endswith("chirps-v2.0.2023.01.01.tif.gz")


def test_missing_required_params_fails_closed():
    spec = {"source_id": "bad", "source_label": "Bad", "url_template": "https://example.test/{year}.zip", "required_params": ["year"]}
    row = static_http_range.probe_static_http_range(spec, allow_network=False)
    assert row["status"] == "fail_policy"
    assert "missing required" in row["error"]


def test_range_header_is_correct():
    assert static_http_range.build_range_headers(4) == {"Range": "bytes=0-3"}


def test_dry_run_does_not_call_network(monkeypatch):
    def fail_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr("urllib.request.urlopen", fail_network)
    row = static_http_range.probe_static_http_range(spec_by_id("worldclim_bioclim_normals"), allow_network=False)
    assert row["status"] == "skipped_dry_run"
    assert row["attempted"] is False


@pytest.mark.parametrize(
    ("source_id", "payload", "expected_magic"),
    [
        ("chirps_daily_precipitation", b"\x1f\x8b\x08gzip", "gzip"),
        ("gridmet_daily", b"CDF\x01netcdf", "netcdf"),
        ("terraclimate_monthly", b"\x89HDF\r\n\x1a\nhdf", "hdf5"),
        ("worldclim_bioclim_normals", b"PK\x03\x04zip", "zip"),
    ],
)
def test_mocked_wave1_magic_passes(monkeypatch, source_id, payload, expected_magic):
    fake = FakeResponse(payload)
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: fake)
    row = static_http_range.probe_static_http_range(spec_by_id(source_id), allow_network=True, max_bytes=8)
    assert row["status"].startswith("pass_")
    assert row["detected_magic"] == expected_magic
    assert row["sha256"] == hashlib.sha256(payload[:8]).hexdigest()
    assert row["sha256_short"] == row["sha256"][:12]
    assert row["range_honored"] is True
    assert fake.read_limit == 8


def test_prism_fixture_never_calls_network(monkeypatch):
    def fail_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr("urllib.request.urlopen", fail_network)
    row = static_http_range.probe_static_http_range(spec_by_id("prism_daily_ppt_static_zip"), allow_network=True)
    assert row["status"] == "fixture_only"
    assert row["attempted"] is False
    assert row["network_run"] is False
    assert row["historical_sha256_short"] == "cc89306d4d5b"
    assert "Historical bounded ZIP evidence preserved" in row["warning"]


def test_wave1_dry_run_contains_four_results_and_one_fixture(monkeypatch):
    def fail_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr("urllib.request.urlopen", fail_network)
    payload = static_http_range.probe_wave1_sources(allow_network=False)
    assert len(payload["results"]) == 4
    assert len(payload["fixtures"]) == 1
    assert payload["runnable_source_count"] == 4
    assert payload["fixture_source_count"] == 1
    assert payload["fail_count"] == 0
    assert payload["fixtures"][0]["status"] == "fixture_only"


def test_wave1_mocked_live_executes_four_network_requests(monkeypatch):
    calls = []
    payload_by_source = {
        "chirps-v2.0": b"\x1f\x8b\x08gzip",
        "pr_2023.nc": b"CDF\x01netcdf",
        "TerraClimate_ppt_2023.nc": b"\x89HDF\r\n\x1a\nhdf",
        "wc2.1_10m_prec.zip": b"PK\x03\x04zip",
    }

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        for marker, payload in payload_by_source.items():
            if marker in request.full_url:
                return FakeResponse(payload)
        raise AssertionError(f"unexpected URL: {request.full_url}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    payload = static_http_range.probe_wave1_sources(allow_network=True, max_bytes=8)
    assert len(calls) == 4
    assert len(payload["results"]) == 4
    assert len(payload["fixtures"]) == 1
    assert payload["attempted_source_count"] == 4
    assert payload["pass_count"] == 4
    assert payload["fail_count"] == 0
    assert payload["decision"] == "wave1_adapter_live_validated"


def test_wrong_magic_fails_magic(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: FakeResponse(b"notzip"))
    row = static_http_range.probe_static_http_range(spec_by_id("worldclim_bioclim_normals"), allow_network=True)
    assert row["status"] == "fail_magic"


def test_http_404_fails_http(monkeypatch):
    from urllib.error import HTTPError

    def raise_404(request, timeout):
        raise HTTPError(request.full_url, 404, "not found", Message(), None)

    monkeypatch.setattr("urllib.request.urlopen", raise_404)
    row = static_http_range.probe_static_http_range(spec_by_id("chirps_daily_precipitation"), allow_network=True)
    assert row["status"] == "fail_http"
    assert row["http_status"] == 404


def test_range_honored_inferred_from_206_or_content_range(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: FakeResponse(b"PK\x03\x04zip", status=200, content_range="bytes 0-7/50"))
    row = static_http_range.probe_static_http_range(spec_by_id("worldclim_bioclim_normals"), allow_network=True)
    assert row["range_honored"] is True


def test_report_json_and_md_write(tmp_path):
    payload = static_http_range.probe_wave1_sources(source_ids=["chirps_daily_precipitation"], allow_network=False)
    out_json = tmp_path / "report.json"
    out_md = tmp_path / "report.md"
    static_http_range.write_static_range_report(payload, out_json, out_md)
    assert json.loads(out_json.read_text(encoding="utf-8"))["results"][0]["status"] == "skipped_dry_run"
    assert "Static HTTP Range Plan" in out_md.read_text(encoding="utf-8")
