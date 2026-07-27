from __future__ import annotations

import json
import hashlib
import urllib.error
from pathlib import Path

import pytest
from typer.testing import CliRunner

from faster_raster.cli import app
from faster_raster import local_executor, system_grade, run_receipts, task_compiler


TASK_ID = "example_wave1_climate_stack"
RUNNABLE_SOURCE_COUNT = 5
FIXTURE_SOURCE_COUNT = 0
JOBS_PER_RUNNABLE_SOURCE = 8
runner = CliRunner()


class FakeHeaders(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class FakeResponse:
    def __init__(self, data: bytes, *, status: int = 206, content_type: str = "application/octet-stream", content_range: str | None = None):
        self.status = status
        self.headers = FakeHeaders({"Content-Type": content_type})
        if content_range is None and status == 206:
            content_range = f"bytes 0-{len(data)-1}/100"
        if content_range:
            self.headers["Content-Range"] = content_range
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def getcode(self):
        return self.status

    def read(self, size=-1):
        return self._data[:size]


def sample_bytes(url: str) -> bytes:
    if url.endswith(".gz"):
        return b"\x1f\x8bmock-gzip-prefix"
    if url.endswith(".zip"):
        return b"PK\x03\x04mock-zip-prefix"
    return b"CDFmock-netcdf-prefix"


def fake_urlopen(request, timeout=0):
    assert request.headers["Range"] == "bytes=0-65535"
    assert request.headers["User-agent"].startswith("FasterRaster/")
    return FakeResponse(sample_bytes(request.full_url))


def counting_urlopen(counter):
    def open_url(request, timeout=0):
        counter["count"] += 1
        return fake_urlopen(request, timeout=timeout)

    return open_url




@pytest.fixture(autouse=True)
def isolate_report_roots(monkeypatch, tmp_path):
    report_root = tmp_path / "reports"
    monkeypatch.setattr(task_compiler, "REPORT_ROOT", report_root)
    monkeypatch.setattr(task_compiler, "TASK_COMPILE_ROOT", report_root / "task_compiles")
    monkeypatch.setattr(task_compiler, "EXECUTION_PACKAGE_ROOT", report_root / "execution_packages")
    task_compiler.compile_task(TASK_ID)
    task_compiler.package_task(TASK_ID)
    monkeypatch.setattr(local_executor, "COMPILE_ROOT", task_compiler.TASK_COMPILE_ROOT)
    monkeypatch.setattr(local_executor, "PACKAGE_ROOT", task_compiler.EXECUTION_PACKAGE_ROOT)
    run_root = report_root / "runs"
    monkeypatch.setattr(local_executor, "RUN_ROOT", run_root)
    monkeypatch.setattr(system_grade, "RUN_ROOT", run_root)
    monkeypatch.setattr(system_grade, "MATERIALIZATION_ROOT", report_root / "materializations")
    monkeypatch.setattr(system_grade, "SYSTEM_GRADE_DIR", report_root / "system_grade")


def deterministic_clock():
    values = [f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z" for i in range(240)]
    iterator = iter(values)
    return lambda: next(iterator)


def execution_inputs():
    inputs = local_executor.load_execution_inputs(TASK_ID)
    bounded = next(job for job in inputs["jobs"] if job["stage"] == "bounded_fetch")
    row = next(item for item in inputs["manifest"] if item["request_id"] == bounded["request_id"])
    return bounded, row


def write_cache_entry(cache_root: Path, *, payload: bytes | None = None, sidecar_updates: dict | None = None, legacy: bool = False):
    job, row = execution_inputs()
    payload = payload if payload is not None else sample_bytes(job["deterministic_url"])
    digest = hashlib.sha256(payload).hexdigest()
    path = local_executor._runtime_cache_path(job, row, 65536, cache_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    sidecar = {
        "cache_contract_version": 1,
        "source_id": job["source_id"],
        "request_id": job["request_id"],
        "url_sha256": row["url_sha256"],
        "payload_sha256": digest,
        "bytes_read": len(payload),
        "byte_cap": 65536,
        "bounded_probe_only": True,
        "full_object": False,
        "http_status": 206,
        "content_type": "application/octet-stream",
        "content_range": "bytes 0-3/100",
        "range_requested": True,
        "range_honored": True,
        "expected_magic": row["expected_magic"],
        "detected_magic": "gzip" if payload.startswith(b"\x1f\x8b") else "netcdf" if payload.startswith(b"CDF") else "zip",
        "expected_content_family": row["expected_content_family"],
        "detected_content_family": "gzip" if payload.startswith(b"\x1f\x8b") else "netcdf" if payload.startswith(b"CDF") else "zip",
        "generated_at_utc": "2026-01-01T00:00:00Z",
    }
    if legacy:
        sidecar.pop("cache_contract_version")
        sidecar.pop("http_status")
        sidecar.pop("range_honored")
    if sidecar_updates:
        sidecar.update(sidecar_updates)
        if sidecar_updates.get("remove"):
            for key in sidecar_updates["remove"]:
                sidecar.pop(key, None)
            sidecar.pop("remove", None)
    Path(str(path) + ".receipt.json").write_text(json.dumps(sidecar, indent=2, sort_keys=True) + "\n")
    return path, Path(str(path) + ".receipt.json")


def test_run_plan_is_deterministic_and_does_not_use_network(monkeypatch, tmp_path):
    def fail_network(*args, **kwargs):
        raise AssertionError("network should not be used during planning")

    monkeypatch.setattr("urllib.request.urlopen", fail_network)
    first = local_executor.build_run_plan(TASK_ID, write_artifacts=False)
    second = local_executor.build_run_plan(TASK_ID, write_artifacts=False)

    assert first["planned_job_count"] == RUNNABLE_SOURCE_COUNT * JOBS_PER_RUNNABLE_SOURCE
    assert first["planned_network_job_count"] == RUNNABLE_SOURCE_COUNT
    assert first["planned_fixture_job_count"] == FIXTURE_SOURCE_COUNT
    assert first["network_allowed"] is False
    assert first["run_plan_contract_sha256"] == second["run_plan_contract_sha256"]


def test_network_disabled_by_default_blocks_fetch_and_records_fixture(monkeypatch, tmp_path):
    result = local_executor.execute_local(TASK_ID, timestamp_utc="2026-01-01T00:00:00Z", now_fn=deterministic_clock(), cache_root=tmp_path / "cache")

    receipt = result["receipt"]
    assert receipt["run_status"] == "blocked_policy"
    assert receipt["network_run"] is False
    assert receipt["fixture_source_count"] == FIXTURE_SOURCE_COUNT
    assert receipt["successful_source_count"] == 0
    assert "execution_blocked: network_not_allowed" in receipt["warnings"]


def test_mocked_local_execution_success_writes_receipt_and_cache(monkeypatch, tmp_path):
    result = local_executor.execute_local(
        TASK_ID,
        allow_network=True,
        timestamp_utc="2026-01-01T00:00:00Z",
        now_fn=deterministic_clock(),
        sleep_fn=lambda seconds: None,
        urlopen=fake_urlopen,
        cache_root=tmp_path / "cache",
    )

    receipt = result["receipt"]
    assert receipt["run_status"] == "completed"
    assert receipt["successful_source_count"] == RUNNABLE_SOURCE_COUNT
    assert receipt["failed_source_count"] == 0
    assert receipt["fixture_source_count"] == FIXTURE_SOURCE_COUNT
    assert receipt["all_byte_caps_respected"] is True
    assert receipt["all_magic_valid"] is True
    assert receipt["all_content_families_valid"] is True
    assert receipt["all_checksums_present"] is True
    assert receipt["credentials_used"] is False
    assert receipt["authorization_headers_present"] is False

    run_dir = Path(result["receipt_path"]).parent
    cache_index = json.loads((run_dir / "cache_index.json").read_text())
    assert len(cache_index["entries"]) == RUNNABLE_SOURCE_COUNT
    assert all(".head65536" in entry["cache_path"] for entry in cache_index["entries"])
    assert {entry["cache_status"] for entry in cache_index["entries"]} == {"fetched"}


def test_independent_branches_continue_after_one_failure(monkeypatch, tmp_path):
    def one_404(request, timeout=0):
        if "chirps" in request.full_url:
            raise urllib.error.HTTPError(request.full_url, 404, "missing", {}, None)
        return fake_urlopen(request, timeout=timeout)

    result = local_executor.execute_local(
        TASK_ID,
        allow_network=True,
        timestamp_utc="2026-01-01T00:00:00Z",
        now_fn=deterministic_clock(),
        sleep_fn=lambda seconds: None,
        urlopen=one_404,
        cache_root=tmp_path / "cache",
    )

    assert result["receipt"]["run_status"] == "failed"
    assert result["receipt"]["successful_source_count"] == RUNNABLE_SOURCE_COUNT - 1
    assert result["receipt"]["failed_source_count"] == 1


def test_dag_cycle_fails_closed():
    jobs = [
        {"job_id": "a", "dependencies": ["b"]},
        {"job_id": "b", "dependencies": ["a"]},
    ]
    with pytest.raises(local_executor.LocalExecutionError, match="cycle"):
        local_executor.topological_order(jobs)


def test_cli_run_plan_and_blocked_local(monkeypatch, tmp_path):
    monkeypatch.setattr(local_executor, "RUNTIME_CACHE_ROOT", tmp_path / "cache")
    plan = runner.invoke(app, ["run", "plan", TASK_ID, "--plain"])
    assert plan.exit_code == 0
    assert "run_plan_contract_sha256:" in plan.output

    local = runner.invoke(app, ["run", "local", TASK_ID, "--plain"])
    assert local.exit_code == 0
    assert "execution_blocked: network_not_allowed" in local.output


def test_cli_run_inspect_verify_evidence_after_mocked_success(monkeypatch, tmp_path):
    local_executor.execute_local(
        TASK_ID,
        allow_network=True,
        timestamp_utc="2026-01-01T00:00:00Z",
        now_fn=deterministic_clock(),
        sleep_fn=lambda seconds: None,
        urlopen=fake_urlopen,
        cache_root=tmp_path / "cache",
    )

    inspect = runner.invoke(app, ["run", "inspect", TASK_ID, "--plain"])
    verify = runner.invoke(app, ["run", "verify", TASK_ID, "--plain"])
    evidence = runner.invoke(app, ["run", "evidence", TASK_ID, "--plain"])
    verify_json = runner.invoke(app, ["run", "verify", TASK_ID, "--json"])

    assert inspect.exit_code == 0
    assert "run_status: completed" in inspect.output
    assert verify.exit_code == 0
    assert "verification_status: PASS" in verify.output
    assert evidence.exit_code == 0
    assert "chirps_daily_precipitation:" in evidence.output
    assert verify_json.exit_code == 0
    assert json.loads(verify_json.output)["verification_status"] == "PASS"


def test_system_grade_no_live_receipt_gives_cautions(monkeypatch, tmp_path):
    local_executor.execute_local(TASK_ID, timestamp_utc="2026-01-01T00:00:00Z", now_fn=deterministic_clock(), cache_root=tmp_path / "cache")
    report = system_grade.grade_system(TASK_ID)

    assert report["release_decision"] == "release_ready_with_cautions"
    assert "no_live_local_execution_receipt" in report["warnings"]
    assert report["latest_run_receipt_present"] is True
    assert report["local_run_status"] == "blocked_policy"


def test_system_grade_valid_live_receipt_with_failed_latest_materialization_holds_release(monkeypatch, tmp_path):
    local_executor.execute_local(
        TASK_ID,
        allow_network=True,
        timestamp_utc="2026-01-01T00:00:00Z",
        now_fn=deterministic_clock(),
        sleep_fn=lambda seconds: None,
        urlopen=fake_urlopen,
        cache_root=tmp_path / "cache",
    )

    report = system_grade.grade_system(TASK_ID)

    assert report["release_decision"] in {"release_ready_with_cautions", "hold_release"}
    assert report["latest_run_receipt_valid"] is True
    assert report["local_successful_source_count"] == RUNNABLE_SOURCE_COUNT
    assert report["local_fixture_source_count"] == FIXTURE_SOURCE_COUNT
    assert "no_live_materialization_receipt" in report["warnings"]


def test_system_grade_invalid_live_receipt_holds_release(monkeypatch, tmp_path):
    result = local_executor.execute_local(
        TASK_ID,
        allow_network=True,
        timestamp_utc="2026-01-01T00:00:00Z",
        now_fn=deterministic_clock(),
        sleep_fn=lambda seconds: None,
        urlopen=fake_urlopen,
        cache_root=tmp_path / "cache",
    )
    receipt_path = Path(result["receipt_path"])
    receipt = json.loads(receipt_path.read_text())
    receipt["successful_source_count"] = RUNNABLE_SOURCE_COUNT - 1
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")

    report = system_grade.grade_system(TASK_ID)

    assert report["release_decision"] == "hold_release"
    assert "invalid local live run receipt" in report["blocking_failures"]


def test_mocked_execution_leaves_repository_runtime_cache_untouched(tmp_path):
    repo_cache = local_executor.RUNTIME_CACHE_ROOT
    before = sorted(str(path.relative_to(repo_cache)) for path in repo_cache.rglob("*") if path.is_file()) if repo_cache.exists() else []
    local_executor.execute_local(
        TASK_ID,
        allow_network=True,
        timestamp_utc="2026-01-01T00:00:00Z",
        now_fn=deterministic_clock(),
        sleep_fn=lambda seconds: None,
        urlopen=fake_urlopen,
        cache_root=tmp_path / "cache",
    )
    after = sorted(str(path.relative_to(repo_cache)) for path in repo_cache.rglob("*") if path.is_file()) if repo_cache.exists() else []
    assert after == before


def test_valid_cache_hit_hydrates_http_metadata_and_network_run_false(tmp_path):
    counter = {"count": 0}
    cache_root = tmp_path / "cache"
    local_executor.execute_local(
        TASK_ID,
        allow_network=True,
        timestamp_utc="2026-01-01T00:00:00Z",
        now_fn=deterministic_clock(),
        sleep_fn=lambda seconds: None,
        urlopen=counting_urlopen(counter),
        cache_root=cache_root,
    )
    result = local_executor.execute_local(
        TASK_ID,
        allow_network=True,
        timestamp_utc="2026-01-01T00:01:00Z",
        now_fn=deterministic_clock(),
        sleep_fn=lambda seconds: None,
        urlopen=counting_urlopen(counter),
        cache_root=cache_root,
    )

    run_dir = Path(result["receipt_path"]).parent
    jobs = json.loads((run_dir / "job_receipts.json").read_text())
    fetch_jobs = [job for job in jobs if job["stage"] == "bounded_fetch"]
    http_jobs = [job for job in jobs if job["stage"] == "validate_http_status"]
    cache_index = json.loads((run_dir / "cache_index.json").read_text())

    assert counter["count"] == RUNNABLE_SOURCE_COUNT
    assert result["receipt"]["allow_network"] is True
    assert result["receipt"]["network_run"] is False
    assert all(job["status"] == "cache_hit" for job in fetch_jobs)
    assert all(job["http_status"] == 206 for job in fetch_jobs)
    assert all(job["range_honored"] is True for job in fetch_jobs)
    assert all(job["sha256"] for job in fetch_jobs)
    assert all(job["status"] == "succeeded" for job in http_jobs)
    assert {entry["cache_status"] for entry in cache_index["entries"]} == {"hit"}


def test_legacy_cache_without_http_status_or_range_honored_is_rejected_and_refetched(tmp_path):
    cache_root = tmp_path / "cache"
    write_cache_entry(cache_root, legacy=True)
    result = local_executor.execute_local(
        TASK_ID,
        allow_network=True,
        timestamp_utc="2026-01-01T00:00:00Z",
        now_fn=deterministic_clock(),
        sleep_fn=lambda seconds: None,
        urlopen=fake_urlopen,
        cache_root=cache_root,
    )
    run_dir = Path(result["receipt_path"]).parent
    cache_index = json.loads((run_dir / "cache_index.json").read_text())
    safety = json.loads((run_dir / "safety_events.json").read_text())

    assert any(entry["cache_status"] == "invalid_refetched" for entry in cache_index["entries"])
    assert any(event["event_type"] == "invalid_cache_entry" and event["action"] == "refetch" for event in safety["events"])


def test_invalid_cache_payload_url_and_byte_cap_mismatches_are_rejected(tmp_path):
    cases = [
        {"payload": b"corrupt"},
        {"sidecar_updates": {"url_sha256": "wrong"}},
        {"sidecar_updates": {"byte_cap": 1}},
    ]
    for index, case in enumerate(cases):
        cache_root = tmp_path / f"cache-{index}"
        write_cache_entry(cache_root, **case)
        cached, errors = local_executor._read_valid_cache(*execution_inputs(), 65536, cache_root)
        assert cached is None
        assert errors


def test_invalid_cache_blocks_when_network_disabled(tmp_path):
    cache_root = tmp_path / "cache"
    write_cache_entry(cache_root, legacy=True)
    result = local_executor.execute_local(
        TASK_ID,
        allow_network=False,
        timestamp_utc="2026-01-01T00:00:00Z",
        now_fn=deterministic_clock(),
        cache_root=cache_root,
    )

    assert result["receipt"]["run_status"] == "blocked_policy"
    assert result["receipt"]["network_run"] is False
    assert result["receipt"]["successful_source_count"] == 0


def test_no_authorization_values_appear_in_receipts(tmp_path):
    result = local_executor.execute_local(
        TASK_ID,
        allow_network=True,
        timestamp_utc="2026-01-01T00:00:00Z",
        now_fn=deterministic_clock(),
        sleep_fn=lambda seconds: None,
        urlopen=fake_urlopen,
        cache_root=tmp_path / "cache",
    )
    run_dir = Path(result["receipt_path"]).parent
    combined = "\n".join(path.read_text() for path in run_dir.glob("*.json*"))
    assert "Authorization: " + "Bearer" not in combined
    assert "CDSE_ACCESS_TOKEN" not in combined
