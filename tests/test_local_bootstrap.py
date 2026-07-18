from __future__ import annotations

import json
import shutil
import socket
import subprocess
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from faster_raster.local_config import ConfigDocument
from faster_raster.local_diagnostics import run_doctor
from faster_raster.local_paths import resolve_local_paths
from faster_raster.source_capabilities import (
    BoundedHTTPTransport,
    SourceDefinition,
    evaluate_sources,
    probe_source,
    source_evidence_state,
    write_profile_atomic,
)


def local_paths(tmp_path: Path):
    return resolve_local_paths(
        tmp_path / "project",
        environ={
            "FASTERRASTER_CONFIG_HOME": str(tmp_path / "config"),
            "FASTERRASTER_STATE_HOME": str(tmp_path / "state"),
            "FASTERRASTER_CACHE_HOME": str(tmp_path / "cache"),
            "FASTERRASTER_TEMP_HOME": str(tmp_path / "temp"),
        },
        home=tmp_path,
    )


def fake_doctor() -> dict:
    return {
        "status": "PASS",
        "machine": {"operating_system": "Linux", "architecture": "x86_64", "python_version": "3.12", "python_implementation": "CPython", "is_wsl": False},
        "gdal": {"version": "GDAL 3.8", "drivers": ["GTiff", "COG", "HDF5"]},
        "resources": {"cpu_count": 4, "available_memory_bytes": 8_000_000_000},
        "checks": {},
        "recommendations": {"maximum_parallel_tasks": {"value": 2, "reason": "test"}},
        "warnings": [],
    }


class FakeTransport:
    def __init__(self, *, body: bytes = b"{}", status: int = 200, error: Exception | None = None):
        self.body = body
        self.status = status
        self.error = error
        self.bytes_transferred = 0
        self.requests_made = 0
        self.calls: list[dict] = []

    def request(self, url: str, **kwargs):
        self.requests_made += 1
        self.calls.append({"url": url, **kwargs})
        if self.error:
            raise self.error
        self.bytes_transferred += len(self.body)
        return {"status_code": self.status, "body": self.body, "headers": {}, "bytes": len(self.body)}


def definition(**updates) -> SourceDefinition:
    values = {
        "source_id": "source_a",
        "provider": "Provider",
        "product": "Product",
        "access_category": "service_discovered",
        "probe_strategy": "service_metadata",
        "endpoint": "https://example.test/service",
        "logical_assets": ("natural",),
        "aliases": ("a",),
        "required_driver": "GTiff",
    }
    values.update(updates)
    return SourceDefinition(**values)


def doctor_runner(command, **kwargs):
    if command[:2] == ["gdalinfo", "--formats"]:
        return subprocess.CompletedProcess(command, 0, "Supported Formats:\n  GTiff -raster- (rw+vs): GeoTIFF\n  COG -raster- (wv): COG\n", "")
    if command[:2] == ["gdalinfo", "--version"]:
        return subprocess.CompletedProcess(command, 0, "GDAL 3.8.0\n", "")
    if command[0] == "gdal_translate":
        Path(command[-1]).write_bytes(b"tiny-raster")
        return subprocess.CompletedProcess(command, 0, "", "")
    return subprocess.CompletedProcess(command, 0, "{}", "")


def test_doctor_succeeds_with_mocked_linux_environment(tmp_path):
    report = run_doctor(
        local_paths(tmp_path),
        offline=True,
        runner=doctor_runner,
        which=lambda name: f"/usr/bin/{name}",
        environ={},
    )
    assert report["status"] == "PASS"
    assert report["network_requests"] == 0
    assert report["gdal"]["drivers"] == ["COG", "GTiff"]


def test_doctor_reports_missing_gdal_cleanly(tmp_path):
    report = run_doctor(local_paths(tmp_path), runner=doctor_runner, which=lambda name: None, environ={})
    assert report["status"] == "FAIL"
    assert "missing required GDAL command: gdalinfo" in report["failures"]


def test_doctor_temporary_fixtures_removed_after_success(tmp_path):
    paths = local_paths(tmp_path)
    report = run_doctor(paths, runner=doctor_runner, which=lambda name: f"/bin/{name}", environ={})
    assert report["temporary_artifacts_removed"] is True
    assert not (paths.temporary_root / "doctor").exists()


def test_doctor_temporary_fixtures_removed_after_failure(tmp_path):
    paths = local_paths(tmp_path)

    def failing_runner(command, **kwargs):
        if command[0] == "gdal_translate":
            return subprocess.CompletedProcess(command, 1, "", "failed")
        return doctor_runner(command, **kwargs)

    report = run_doctor(paths, runner=failing_runner, which=lambda name: f"/bin/{name}", environ={})
    assert report["status"] == "FAIL"
    assert not (paths.temporary_root / "doctor").exists()


def test_source_evaluation_offline_performs_zero_network_work(tmp_path):
    transport = FakeTransport(error=AssertionError("network called"))
    profile = evaluate_sources(
        local_paths(tmp_path),
        ConfigDocument(),
        definitions={"source_a": definition()},
        transport=transport,
        doctor_report=fake_doctor(),
        offline=True,
        environ={},
    )
    assert profile["evaluation"]["requests_made"] == 0
    assert profile["sources"]["source_a"]["status"] == "skipped_offline"


class _Response:
    status = 200
    headers = {}

    def __init__(self, body: bytes):
        self.body = body

    def read(self, size: int) -> bytes:
        return self.body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_global_byte_ceiling_is_enforced():
    transport = BoundedHTTPTransport(3)
    transport._opener = type("Opener", (), {"open": lambda self, req, timeout: _Response(b"four")})()
    with pytest.raises(RuntimeError, match="byte ceiling"):
        transport.request("https://example.test", method="GET", timeout=1, byte_ceiling=9)
    assert transport.bytes_transferred == 3
    assert transport.requests_made == 1


def test_per_source_request_and_byte_limits_are_passed_to_transport():
    transport = FakeTransport(body=b"abc")
    item = definition(byte_ceiling=17, request_ceiling=1)
    result = probe_source(item, transport=transport, drivers=["GTiff"], offline=False, environ={})
    assert result["requests_made"] == 1
    assert transport.calls[0]["byte_ceiling"] == 17
    assert len(transport.calls) <= item.request_ceiling


@pytest.mark.parametrize(
    ("category", "strategy", "expected"),
    [
        ("static_verified", "http_range", "Range"),
        ("service_discovered", "service_metadata", "f=pjson"),
        ("api_discovered", "api_discovery", "https://example.test/service"),
    ],
)
def test_access_categories_use_source_aware_probe_strategies(category, strategy, expected):
    transport = FakeTransport(body=b"{}")
    result = probe_source(
        definition(access_category=category, probe_strategy=strategy),
        transport=transport,
        drivers=["GTiff"],
        offline=False,
        environ={},
    )
    assert result["status"] == "available"
    call = transport.calls[0]
    assert expected in call.get("headers", {}) or expected in call["url"]


def test_missing_credential_is_reported_without_network():
    transport = FakeTransport(error=AssertionError("authenticated acquisition attempted"))
    result = probe_source(
        definition(access_category="credential_gated", credential_env="SOURCE_TOKEN"),
        transport=transport,
        drivers=["GTiff"],
        offline=False,
        environ={},
    )
    assert result["status"] == "credential_missing"
    assert transport.requests_made == 0


def test_credential_values_never_appear_in_profile_or_evidence(tmp_path):
    secret = "super-secret-value"
    transport = FakeTransport(body=b"{}")
    profile = evaluate_sources(
        local_paths(tmp_path),
        ConfigDocument(),
        definitions={"source_a": definition(access_category="credential_gated", credential_env="SOURCE_TOKEN")},
        transport=transport,
        doctor_report=fake_doctor(),
        environ={"SOURCE_TOKEN": secret},
    )
    serialized = json.dumps(profile)
    assert secret not in serialized
    assert secret not in json.dumps(transport.calls[0]["url"])
    assert transport.calls[0]["headers"]["Authorization"].endswith(secret)


def test_future_unverified_source_remains_unavailable():
    result = probe_source(
        definition(access_category="future_unverified", probe_strategy="none", selectable=False),
        transport=FakeTransport(error=AssertionError("network called")),
        drivers=["GTiff"],
        offline=False,
        environ={},
    )
    assert result["status"] == "future_unverified"


def test_timeout_is_distinct_from_unsupported_driver():
    timeout = probe_source(
        definition(),
        transport=FakeTransport(error=socket.timeout()),
        drivers=["GTiff"],
        offline=False,
        environ={},
    )
    unsupported = probe_source(
        definition(required_driver="HDF5"),
        transport=FakeTransport(),
        drivers=["GTiff"],
        offline=False,
        environ={},
    )
    assert timeout["status"] == "timeout"
    assert unsupported["status"] == "unsupported_local_driver"


def test_rate_limited_source_has_explicit_status():
    error = urllib.error.HTTPError("https://example.test", 429, "slow down", {}, None)
    result = probe_source(definition(), transport=FakeTransport(error=error), drivers=["GTiff"], offline=False, environ={})
    assert result["status"] == "rate_limited"


def test_failed_profile_refresh_preserves_previous_profile(tmp_path, monkeypatch):
    paths = local_paths(tmp_path)
    previous = {"schema_version": "fasterraster.capabilities/v1", "sentinel": "keep"}
    paths.capability_profile.parent.mkdir(parents=True)
    paths.capability_profile.write_text(json.dumps(previous), encoding="utf-8")

    def fail_write(path, profile):
        raise OSError("disk full")

    monkeypatch.setattr("faster_raster.source_capabilities.write_profile_atomic", fail_write)
    with pytest.raises(OSError, match="disk full"):
        evaluate_sources(
            paths,
            ConfigDocument(),
            definitions={"source_a": definition()},
            transport=FakeTransport(),
            doctor_report=fake_doctor(),
            offline=True,
            environ={},
        )
    assert json.loads(paths.capability_profile.read_text(encoding="utf-8")) == previous


def test_capability_profile_write_is_atomic(tmp_path):
    path = tmp_path / "profile.json"
    write_profile_atomic(path, {"schema_version": "fasterraster.capabilities/v1", "value": 1})
    assert json.loads(path.read_text(encoding="utf-8"))["value"] == 1
    assert list(tmp_path.glob(".profile.json.*")) == []


def test_stale_evidence_is_deterministic():
    current = datetime(2026, 7, 17, tzinfo=timezone.utc)
    record = {
        "status": "available",
        "access_category": "service_discovered",
        "probe_timestamp": (current - timedelta(hours=73)).isoformat(),
    }
    state = source_evidence_state(record, ConfigDocument(), now=current)
    assert state == {"stale": True, "age_seconds": 73 * 3600, "ttl_hours": 72.0}


@pytest.mark.parametrize("failure", [socket.timeout(), ValueError("parse failure")])
def test_probe_directories_self_clean_after_failure_modes(tmp_path, failure):
    paths = local_paths(tmp_path)
    transport = FakeTransport(error=failure)
    if isinstance(failure, ValueError):
        transport = FakeTransport(body=b"not-json")
    evaluate_sources(
        paths,
        ConfigDocument(),
        definitions={"source_a": definition()},
        transport=transport,
        doctor_report=fake_doctor(),
        environ={},
    )
    assert not (paths.cache_home / "probes").exists()


def test_keep_probe_artifacts_retains_only_redacted_summary(tmp_path):
    paths = local_paths(tmp_path)
    profile = evaluate_sources(
        paths,
        ConfigDocument(),
        definitions={"source_a": definition()},
        transport=FakeTransport(),
        doctor_report=fake_doctor(),
        keep_probe_artifacts=True,
        environ={},
    )
    artifact = Path(profile["evaluation"]["artifact_directory"])
    assert [path.name for path in artifact.iterdir()] == ["probe_summary.json"]
    shutil.rmtree(paths.cache_home / "probes")
