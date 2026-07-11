from __future__ import annotations

import gzip
import hashlib
from pathlib import Path

from typer.testing import CliRunner
import pytest

from faster_raster import materialization, artifact_catalog, artifact_receipts, local_executor, system_grade, run_receipts
from faster_raster.cli import app


TASK_ID = "example_wave1_climate_stack"
SOURCE_ID = "chirps_daily_precipitation"
REQUEST_ID = "example_wave1_climate_stack__chirps_daily_precipitation__20230101"


class FakeHeaders(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class FakeResponse:
    def __init__(self, payload: bytes):
        self.status = 200
        self.headers = FakeHeaders({"Content-Length": str(len(payload)), "Content-Type": "application/gzip", "ETag": '"test"'})
        self._payload = payload
        self._offset = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def getcode(self):
        return self.status

    def read(self, size=-1):
        if self._offset >= len(self._payload):
            return b""
        if size < 0:
            size = len(self._payload) - self._offset
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk




@pytest.fixture(autouse=True)
def isolate_materialization_roots(monkeypatch, tmp_path):
    monkeypatch.setattr(materialization, "MATERIALIZATION_ROOT", tmp_path / "reports" / "materializations")
    monkeypatch.setattr(system_grade, "MATERIALIZATION_ROOT", tmp_path / "reports" / "materializations")


def _payload() -> bytes:
    return gzip.compress(b"synthetic chirps complete object")


def _clear_latest_materialization():
    latest = materialization.MATERIALIZATION_ROOT / TASK_ID / "latest_materialization.json"
    latest.unlink(missing_ok=True)


def _patch_probe(monkeypatch, payload: bytes, *, evidence_class: str = "live_network"):
    prefix = payload[: min(16, len(payload))]
    evidence = {
        SOURCE_ID: {
            "task_id": TASK_ID,
            "request_id": REQUEST_ID,
            "source_id": SOURCE_ID,
            "status": "succeeded",
            "network_attempted": True,
            "bytes_read": len(prefix),
            "sha256": hashlib.sha256(prefix).hexdigest(),
            "sha256_short": hashlib.sha256(prefix).hexdigest()[:12],
            "http_status": 206,
            "content_range": f"bytes 0-{len(prefix)-1}/{len(payload)}",
            "content_type": "application/gzip",
            "range_requested": True,
            "range_honored": True,
            "detected_magic": "gzip",
            "detected_content_family": "gzip",
            "expected_magic": "gzip",
            "expected_content_family": "gzip",
            "byte_cap": 65536,
            "credentials_used": False,
            "authorization_headers_present": False,
        }
    }
    receipt = {
        "run_id": "fr_run_test",
        "receipt_contract_sha256": "a" * 64,
        "evidence_class": evidence_class,
        "run_status": "completed",
        "network_run": evidence_class == "live_network",
        "allow_network": True,
        "failed_source_count": 0,
        "credentials_used": False,
        "authorization_headers_present": False,
        "all_byte_caps_respected": True,
        "all_magic_valid": True,
        "all_content_families_valid": True,
        "all_checksums_present": True,
    }
    metadata = {
        "probe_evidence_class": evidence_class,
        "probe_receipt_verification_status": "PASS" if evidence_class in {"live_network", "validated_cache_reuse"} else "FAIL",
        "probe_source_evidence_verification_status": "PASS" if evidence_class in {"live_network", "validated_cache_reuse"} else "FAIL",
        "probe_selected_explicitly": False,
        "probe_selection_method": "test",
        "rejected_latest_probe_run_id": None,
        "rejected_latest_probe_reasons": [],
        "selection_blocking_reasons": [] if evidence_class in {"live_network", "validated_cache_reuse"} else ["probe_receipt_test_fixture"],
    }
    monkeypatch.setattr(materialization, "_select_probe", lambda *args, **kwargs: ({"run_id": "fr_run_test"}, receipt, Path("probe.json"), evidence, metadata))


def test_materialization_plan_is_deterministic(monkeypatch, tmp_path):
    payload = _payload()
    _patch_probe(monkeypatch, payload)
    kwargs = {"sources": [SOURCE_ID], "artifact_root": tmp_path / "artifacts", "staging_root": tmp_path / "staging", "catalog_root": tmp_path / "catalog", "materializations_root": tmp_path / "materializations", "write_artifacts": False}
    plan1 = materialization.build_materialization_plan(TASK_ID, **kwargs)
    plan2 = materialization.build_materialization_plan(TASK_ID, **kwargs)
    assert plan1["materialization_plan_contract_sha256"] == plan2["materialization_plan_contract_sha256"]
    assert plan1["planned_transfer_count"] == 1


def test_fixture_only_prism_is_ineligible(monkeypatch, tmp_path):
    _patch_probe(monkeypatch, _payload())
    plan = materialization.build_materialization_plan(TASK_ID, artifact_root=tmp_path / "artifacts", staging_root=tmp_path / "staging", catalog_root=tmp_path / "catalog", write_artifacts=False)
    prism = next(item for item in plan["object_plans"] if item["source_id"] == "prism_daily_ppt_static_zip")
    assert prism["eligibility_status"] == "fixture_not_materializable"
    assert prism["materialization_eligible"] is False


def test_materialization_blocks_without_approval(monkeypatch, tmp_path):
    _patch_probe(monkeypatch, _payload())
    result = materialization.execute_materialization(
        TASK_ID,
        sources=[SOURCE_ID],
        artifact_root=tmp_path / "artifacts",
        staging_root=tmp_path / "staging",
        catalog_root=tmp_path / "catalog",
        materializations_root=tmp_path / "materializations",
        timestamp_utc="2026-01-01T00:00:00Z",
    )
    assert result["run_status"] == "blocked_policy"
    assert result["receipt"]["network_run"] is False
    assert result["receipt"]["materialized_source_count"] == 0
    _clear_latest_materialization()




def _create_success_then_blocked(monkeypatch, tmp_path):
    payload = _payload()
    _patch_probe(monkeypatch, payload)
    plan = materialization.build_materialization_plan(
        TASK_ID,
        sources=[SOURCE_ID],
        artifact_root=tmp_path / "artifacts",
        staging_root=tmp_path / "staging",
        catalog_root=tmp_path / "catalog",
        materializations_root=tmp_path / "materializations",
        write_artifacts=False,
    )
    success = materialization.execute_materialization(
        TASK_ID,
        sources=[SOURCE_ID],
        allow_network=True,
        allow_materialization=True,
        approve_plan_sha256=plan["materialization_plan_contract_sha256"],
        artifact_root=tmp_path / "artifacts",
        staging_root=tmp_path / "staging",
        catalog_root=tmp_path / "catalog",
        materializations_root=tmp_path / "materializations",
        timestamp_utc="2026-01-01T00:00:00Z",
        now_fn=iter(["2026-01-01T00:00:01Z"] * 100).__next__,
        sleep_fn=lambda _: None,
        urlopen=lambda request, timeout=0: FakeResponse(payload),
    )
    blocked = materialization.execute_materialization(
        TASK_ID,
        sources=[SOURCE_ID],
        artifact_root=tmp_path / "artifacts",
        staging_root=tmp_path / "staging",
        catalog_root=tmp_path / "catalog",
        materializations_root=tmp_path / "materializations",
        timestamp_utc="2026-01-01T00:01:00Z",
        now_fn=iter(["2026-01-01T00:01:01Z"] * 100).__next__,
    )
    return success, blocked

def test_mocked_complete_materialization_succeeds(monkeypatch, tmp_path):
    payload = _payload()
    _patch_probe(monkeypatch, payload)
    plan = materialization.build_materialization_plan(TASK_ID, sources=[SOURCE_ID], artifact_root=tmp_path / "artifacts", staging_root=tmp_path / "staging", catalog_root=tmp_path / "catalog", write_artifacts=False)
    seen_headers = {}

    def fake_urlopen(request, timeout=0):
        seen_headers.update(dict(request.header_items()))
        return FakeResponse(payload)

    result = materialization.execute_materialization(
        TASK_ID,
        sources=[SOURCE_ID],
        allow_network=True,
        allow_materialization=True,
        approve_plan_sha256=plan["materialization_plan_contract_sha256"],
        artifact_root=tmp_path / "artifacts",
        staging_root=tmp_path / "staging",
        catalog_root=tmp_path / "catalog",
        materializations_root=tmp_path / "materializations",
        timestamp_utc="2026-01-01T00:00:00Z",
        now_fn=iter(["2026-01-01T00:00:01Z"] * 100).__next__,
        sleep_fn=lambda _: None,
        urlopen=fake_urlopen,
    )
    assert result["run_status"] == "completed"
    assert result["receipt"]["materialized_source_count"] == 1
    assert result["receipt"]["all_probe_prefixes_match"] is True
    assert seen_headers["Accept-encoding"] == "identity"
    verification = artifact_receipts.verify_materialization_run(Path(result["receipt_path"]))
    assert verification["verification_status"] == "PASS"
    catalog_verification = artifact_catalog.verify_artifact_catalog(catalog_root=tmp_path / "catalog")
    assert catalog_verification["verification_status"] == "PASS"
    _clear_latest_materialization()


def test_wrong_approval_hash_blocks(monkeypatch, tmp_path):
    _patch_probe(monkeypatch, _payload())
    result = materialization.execute_materialization(
        TASK_ID,
        sources=[SOURCE_ID],
        allow_network=True,
        allow_materialization=True,
        approve_plan_sha256="0" * 64,
        artifact_root=tmp_path / "artifacts",
        staging_root=tmp_path / "staging",
        catalog_root=tmp_path / "catalog",
    )
    assert result["run_status"] == "blocked_policy"
    assert result["receipt"]["execution_blocked"] == "plan_hash_mismatch"
    _clear_latest_materialization()


def test_cli_materialize_plan_and_blocked_local():
    runner = CliRunner()
    plan = runner.invoke(app, ["materialize", "plan", TASK_ID, "--source", SOURCE_ID, "--max-object-bytes", "134217728", "--max-total-bytes", "134217728", "--plain"])
    assert plan.exit_code == 0
    assert "materialization_plan_contract_sha256" in plan.output
    local = runner.invoke(app, ["materialize", "local", TASK_ID, "--source", SOURCE_ID, "--max-object-bytes", "134217728", "--max-total-bytes", "134217728", "--plain"])
    assert local.exit_code == 0
    assert "run_status: blocked_policy" in local.output
    assert "network_run: False" in local.output




def test_deterministic_fixture_probe_cannot_authorize_materialization(monkeypatch, tmp_path):
    _patch_probe(monkeypatch, _payload(), evidence_class="deterministic_test_fixture")
    plan = materialization.build_materialization_plan(
        TASK_ID,
        sources=[SOURCE_ID],
        artifact_root=tmp_path / "artifacts",
        staging_root=tmp_path / "staging",
        catalog_root=tmp_path / "catalog",
        materializations_root=tmp_path / "materializations",
        write_artifacts=False,
    )
    assert plan["validation_status"] == "WARN"
    assert "probe_receipt_test_fixture" in plan["blocking_reasons"]
    assert plan["planned_transfer_count"] == 0


def test_changing_probe_run_changes_plan_hash(monkeypatch, tmp_path):
    payload = _payload()
    _patch_probe(monkeypatch, payload)
    plan1 = materialization.build_materialization_plan(
        TASK_ID,
        sources=[SOURCE_ID],
        artifact_root=tmp_path / "artifacts",
        staging_root=tmp_path / "staging",
        catalog_root=tmp_path / "catalog",
        materializations_root=tmp_path / "materializations",
        write_artifacts=False,
    )

    base_select = materialization._select_probe

    def select_other(*args, **kwargs):
        latest, receipt, receipt_path, evidence, metadata = base_select(*args, **kwargs)
        receipt = {**receipt, "run_id": "fr_run_other"}
        metadata = {**metadata, "probe_selection_method": "explicit", "probe_selected_explicitly": True}
        return latest, receipt, receipt_path, evidence, metadata

    monkeypatch.setattr(materialization, "_select_probe", select_other)
    plan2 = materialization.build_materialization_plan(
        TASK_ID,
        sources=[SOURCE_ID],
        artifact_root=tmp_path / "artifacts",
        staging_root=tmp_path / "staging",
        catalog_root=tmp_path / "catalog",
        materializations_root=tmp_path / "materializations",
        write_artifacts=False,
    )
    assert plan1["materialization_plan_contract_sha256"] != plan2["materialization_plan_contract_sha256"]

def test_system_grade_valid_canary_materialization_permits_release_ready(monkeypatch, tmp_path):
    payload = _payload()
    _patch_probe(monkeypatch, payload)

    class ProbeResponse(FakeResponse):
        def __init__(self, data: bytes, content_type: str = "application/octet-stream"):
            super().__init__(data)
            self.status = 206
            self.headers["Content-Range"] = f"bytes 0-{len(data)-1}/100"
            self.headers["Content-Type"] = content_type

    def fake_probe_urlopen(request, timeout=0):
        url = request.full_url
        if url.endswith(".gz"):
            return ProbeResponse(b"\x1f\x8bmock-gzip-prefix", "application/gzip")
        if url.endswith(".zip"):
            return ProbeResponse(b"PK\x03\x04mock-zip-prefix", "application/zip")
        return ProbeResponse(b"CDFmock-netcdf-prefix", "application/x-netcdf")

    local_executor.execute_local(
        TASK_ID,
        allow_network=True,
        timestamp_utc="2026-01-01T00:00:00Z",
        now_fn=iter([f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z" for i in range(300)]).__next__,
        sleep_fn=lambda _: None,
        urlopen=fake_probe_urlopen,
        cache_root=tmp_path / "probe-cache",
        reports_root=tmp_path / "reports",
    )
    _patch_probe(monkeypatch, payload)
    plan = materialization.build_materialization_plan(TASK_ID, sources=[SOURCE_ID], artifact_root=tmp_path / "artifacts", staging_root=tmp_path / "staging", catalog_root=tmp_path / "catalog", write_artifacts=False)
    materialization.execute_materialization(
        TASK_ID,
        sources=[SOURCE_ID],
        allow_network=True,
        allow_materialization=True,
        approve_plan_sha256=plan["materialization_plan_contract_sha256"],
        artifact_root=tmp_path / "artifacts",
        staging_root=tmp_path / "staging",
        catalog_root=tmp_path / "catalog",
        materializations_root=tmp_path / "materializations",
        timestamp_utc="2026-01-01T00:01:00Z",
        now_fn=iter(["2026-01-01T00:01:01Z"] * 100).__next__,
        sleep_fn=lambda _: None,
        urlopen=lambda request, timeout=0: FakeResponse(payload),
    )
    monkeypatch.setattr(system_grade.artifact_catalog, "verify_artifact_catalog", lambda: {"verification_status": "PASS"})
    monkeypatch.setattr(system_grade, "MATERIALIZATION_ROOT", tmp_path / "materializations")

    report = system_grade.grade_system(TASK_ID)

    assert report["release_decision"] == "release_ready"
    assert report["latest_materialization_valid"] is True
    assert report["materialized_source_count"] == 1
    assert report["full_wave1_materialized"] is False
    _clear_latest_materialization()


def test_artifact_store_preflight_failure_is_normalized(monkeypatch, tmp_path):
    _patch_probe(monkeypatch, _payload())
    plan = materialization.build_materialization_plan(
        TASK_ID,
        sources=[SOURCE_ID],
        artifact_root=tmp_path / "artifacts",
        staging_root=tmp_path / "staging",
        catalog_root=tmp_path / "catalog",
        materializations_root=tmp_path / "materializations",
        write_artifacts=False,
    )

    def fail_prepare(*args, **kwargs):
        raise materialization.artifact_store.ArtifactStoreError("artifact_store_error")

    monkeypatch.setattr(materialization.artifact_store, "prepare_artifact_store", fail_prepare)
    network_calls = []
    result = materialization.execute_materialization(
        TASK_ID,
        sources=[SOURCE_ID],
        allow_network=True,
        allow_materialization=True,
        approve_plan_sha256=plan["materialization_plan_contract_sha256"],
        artifact_root=tmp_path / "artifacts",
        staging_root=tmp_path / "staging",
        catalog_root=tmp_path / "catalog",
        materializations_root=tmp_path / "materializations",
        urlopen=lambda *args, **kwargs: network_calls.append(args),
        timestamp_utc="2026-01-01T00:00:00Z",
    )
    receipt = result["receipt"]
    transfer = result["transfer_receipts"][0]
    assert receipt["run_status"] == "failed"
    assert receipt["failure_classes"] == ["artifact_store_error"]
    assert receipt["attempted_source_count"] == 1
    assert receipt["failed_source_count"] == 1
    assert receipt["total_bytes_transferred"] == 0
    assert receipt["all_transfer_lengths_valid"] is False
    assert receipt["all_probe_prefixes_match"] is False
    assert receipt["all_whole_object_checksums_present"] is False
    assert receipt["all_container_validations_passed"] is False
    assert receipt["all_artifact_paths_content_addressed"] is False
    assert transfer["transfer_status"] == "failed"
    assert transfer["failure_class"] == "artifact_store_error"
    assert transfer["network_attempted"] is False
    assert transfer["bytes_transferred"] == 0
    assert transfer["artifact_promoted"] is False
    assert not network_calls
    assert not (tmp_path / "staging").exists()
    assert not (tmp_path / "catalog" / "artifact_catalog.json").exists()


def test_materialize_verify_cli_reports_failed_components(monkeypatch, tmp_path):
    _patch_probe(monkeypatch, _payload())
    plan = materialization.build_materialization_plan(
        TASK_ID,
        sources=[SOURCE_ID],
        artifact_root=tmp_path / "artifacts",
        staging_root=tmp_path / "staging",
        catalog_root=tmp_path / "catalog",
        materializations_root=tmp_path / "materializations",
        write_artifacts=False,
    )
    monkeypatch.setattr(materialization.artifact_store, "prepare_artifact_store", lambda *args, **kwargs: (_ for _ in ()).throw(materialization.artifact_store.ArtifactStoreError("artifact_store_error")))
    materialization.execute_materialization(
        TASK_ID,
        sources=[SOURCE_ID],
        allow_network=True,
        allow_materialization=True,
        approve_plan_sha256=plan["materialization_plan_contract_sha256"],
        artifact_root=tmp_path / "artifacts",
        staging_root=tmp_path / "staging",
        catalog_root=tmp_path / "catalog",
        materializations_root=tmp_path / "materializations",
        timestamp_utc="2026-01-01T00:00:00Z",
    )
    monkeypatch.setattr(materialization, "MATERIALIZATION_ROOT", tmp_path / "materializations")
    result = CliRunner().invoke(app, ["materialize", "verify", TASK_ID, "--plain"])
    assert result.exit_code != 0
    assert "contract_verification_status: PASS" in result.output
    assert "execution_outcome_status: FAILED" in result.output
    assert "release_evidence_status: FAIL" in result.output
    assert "verification_status: FAIL" in result.output
    assert "artifact_store_error" in result.output


def test_successful_materialization_followed_by_blocked_policy_still_grades_release_ready(monkeypatch, tmp_path):
    success, blocked = _create_success_then_blocked(monkeypatch, tmp_path)
    assert success["run_status"] == "completed"
    assert blocked["run_status"] == "blocked_policy"
    monkeypatch.setattr(system_grade.artifact_catalog, "verify_artifact_catalog", lambda: {"verification_status": "PASS"})
    monkeypatch.setattr(system_grade, "MATERIALIZATION_ROOT", tmp_path / "materializations")
    monkeypatch.setattr(system_grade, "RUN_ROOT", Path("reports/runs"))

    report = system_grade.grade_system(TASK_ID)

    assert report["release_decision"] == "release_ready"
    assert report["latest_successful_materialization_present"] is True
    assert report["latest_successful_materialization_valid"] is True
    assert report["latest_successful_materialization_run_id"] == success["materialization_run_id"]
    assert report["latest_materialization_attempt_run_id"] == blocked["materialization_run_id"]
    assert report["latest_materialization_attempt_status"] == "blocked_policy"
    assert report["latest_attempt_newer_than_success"] is True
    assert report["latest_attempt_effect_on_release"] == "warning"
    assert report["materialized_source_count"] == 1
    assert report["verified_artifact_count"] == 1
    assert report["wave1_materialization_coverage"] == 0.25
    assert report["full_wave1_materialized"] is False
    assert "no_live_materialization_receipt" not in report["warnings"]
    assert "latest_materialization_attempt_blocked_policy" in report["warnings"]


def test_blocked_run_verification_is_not_applicable_without_failures(monkeypatch, tmp_path):
    _, blocked = _create_success_then_blocked(monkeypatch, tmp_path)
    receipt_path = Path(blocked["receipt_path"])
    stored = run_receipts.read_json(receipt_path.parent / "materialization_verification.json")
    recomputed = artifact_receipts.verify_materialization_run(receipt_path)

    assert stored == recomputed
    assert recomputed["execution_outcome_status"] == "BLOCKED"
    assert recomputed["verification_status"] == "NOT_APPLICABLE"
    assert recomputed["release_evidence_status"] == "NOT_APPLICABLE"
    assert recomputed["failures"] == []
    assert recomputed["blocking_reasons"] == ["approval_required"]


def test_materialize_verify_cli_target_selection(monkeypatch, tmp_path):
    success, blocked = _create_success_then_blocked(monkeypatch, tmp_path)
    monkeypatch.setattr(materialization, "MATERIALIZATION_ROOT", tmp_path / "materializations")
    runner = CliRunner()

    successful = runner.invoke(app, ["materialize", "verify", TASK_ID, "--latest-successful", "--plain"])
    assert successful.exit_code == 0
    assert "target_selection: latest_successful" in successful.output
    assert f"materialization_run_id: {success['materialization_run_id']}" in successful.output
    assert "verification_status: PASS" in successful.output

    latest = runner.invoke(app, ["materialize", "verify", TASK_ID, "--latest-attempt", "--plain"])
    assert latest.exit_code == 0
    assert "target_selection: latest_attempt" in latest.output
    assert f"materialization_run_id: {blocked['materialization_run_id']}" in latest.output
    assert "verification_status: NOT_APPLICABLE" in latest.output
    assert "blocking_reasons: ['approval_required']" in latest.output
    assert "failures: []" in latest.output

    exact = runner.invoke(app, ["materialize", "verify", TASK_ID, "--run-id", success["materialization_run_id"], "--plain"])
    assert exact.exit_code == 0
    assert "target_selection: run_id" in exact.output
    assert f"materialization_run_id: {success['materialization_run_id']}" in exact.output
