import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from faster_raster.cli import app
from faster_raster import task_compiler
from faster_raster import system_grade


TASK_ID = "example_wave1_climate_stack"
RUNNABLE_SOURCE_COUNT = 5
FIXTURE_SOURCE_COUNT = 0
JOBS_PER_RUNNABLE_SOURCE = 8
runner = CliRunner()


@pytest.fixture(autouse=True)
def isolate_compiler_reports(monkeypatch, tmp_path):
    report_root = tmp_path / "reports"
    monkeypatch.setattr(task_compiler, "REPORT_ROOT", report_root)
    monkeypatch.setattr(task_compiler, "TASK_COMPILE_ROOT", report_root / "task_compiles")
    monkeypatch.setattr(task_compiler, "EXECUTION_PACKAGE_ROOT", report_root / "execution_packages")
    monkeypatch.setattr(system_grade, "SYSTEM_GRADE_DIR", report_root / "system_grade")
    monkeypatch.setattr(system_grade, "RUN_ROOT", report_root / "runs")
    monkeypatch.setattr(system_grade, "MATERIALIZATION_ROOT", report_root / "materializations")


def test_task_compile_static_range_manifest_counts():
    report = task_compiler.compile_task(TASK_ID)
    rows = report["manifest_rows"]

    assert report["manifest_row_count"] == 5
    assert report["executable_request_count"] == RUNNABLE_SOURCE_COUNT
    assert report["fixture_request_count"] == FIXTURE_SOURCE_COUNT
    assert report["validation_status"] == "PASS"
    assert report["determinism_status"] == "PASS"
    assert report["adapter_counts"] == {"static_http_range": 5}
    assert sum(1 for row in rows if row["fixture_only"]) == FIXTURE_SOURCE_COUNT
    runnable = [row for row in rows if not row["fixture_only"]]
    assert {row["source_id"] for row in runnable} == {
        "chirps_daily_precipitation",
        "gridmet_daily",
        "prism_daily_ppt_static_zip",
        "terraclimate_monthly",
        "worldclim_bioclim_normals",
    }
    assert all(row["request_headers_redacted"]["Range"] == "bytes=0-65535" for row in runnable)
    assert all("Authorization" not in row["request_headers_redacted"] for row in rows)


def test_prism_compiles_as_runnable_bounded_fetch_pipeline():
    task_compiler.package_task(TASK_ID)
    jobs = json.loads(
        (
            task_compiler.EXECUTION_PACKAGE_ROOT
            / TASK_ID
            / "execution_jobs.json"
        ).read_text()
    )["jobs"]
    prism_jobs = [
        job
        for job in jobs
        if job["source_id"] == "prism_daily_ppt_static_zip"
    ]

    assert len(prism_jobs) == JOBS_PER_RUNNABLE_SOURCE
    assert {job["stage"] for job in prism_jobs} == {
        "resolve_request",
        "bounded_fetch",
        "validate_http_status",
        "validate_byte_cap",
        "validate_magic",
        "validate_content_family",
        "compute_checksum",
        "record_source_evidence",
    }
    assert all(job["fixture_only"] is False for job in prism_jobs)
    assert sum(job["network_required"] is True for job in prism_jobs) == 1


def test_execution_package_stage_counts_and_dag_pass():
    package = task_compiler.package_task(TASK_ID)

    assert package["executable_request_count"] == RUNNABLE_SOURCE_COUNT
    assert package["fixture_request_count"] == FIXTURE_SOURCE_COUNT
    assert package["total_job_count"] == RUNNABLE_SOURCE_COUNT * JOBS_PER_RUNNABLE_SOURCE
    assert package["dag_validation_status"] == "PASS"
    assert package["stage_counts"]["bounded_fetch"] == RUNNABLE_SOURCE_COUNT
    assert package["stage_counts"].get("record_fixture_evidence", 0) == FIXTURE_SOURCE_COUNT


def test_compile_and_package_are_deterministic():
    first_compile = task_compiler.compile_task(TASK_ID)
    first_package = task_compiler.package_task(TASK_ID)
    second_compile = task_compiler.compile_task(TASK_ID)
    second_package = task_compiler.package_task(TASK_ID)

    assert first_compile["acquisition_manifest_sha256"] == second_compile["acquisition_manifest_sha256"]
    assert first_compile["compile_report_contract_sha256"] == second_compile["compile_report_contract_sha256"]
    assert first_compile["determinism_status"] == "PASS"
    assert second_compile["determinism_status"] == "PASS"
    assert first_package["execution_package_contract_sha256"] == second_package["execution_package_contract_sha256"]
    assert first_package["jobs_sha256"] == second_package["jobs_sha256"]
    assert first_package["cache_plan_sha256"] == second_package["cache_plan_sha256"]
    assert first_package["failure_policy_sha256"] == second_package["failure_policy_sha256"]
    assert first_package["dag_sha256"] == second_package["dag_sha256"]


def test_task_compile_cli_no_network_defaults():
    result = runner.invoke(app, ["task", "compile", TASK_ID, "--plain"])

    assert result.exit_code == 0
    assert f"executable_request_count: {RUNNABLE_SOURCE_COUNT}" in result.output
    assert f"fixture_request_count: {FIXTURE_SOURCE_COUNT}" in result.output
    assert "network_run: False" in result.output


def test_task_package_and_inspect_cli():
    package = runner.invoke(app, ["task", "package", TASK_ID, "--plain"])
    inspect = runner.invoke(app, ["task", "inspect-compile", TASK_ID, "--plain"])

    assert package.exit_code == 0
    assert "dag_validation_status: PASS" in package.output
    assert f"total_job_count: {RUNNABLE_SOURCE_COUNT * JOBS_PER_RUNNABLE_SOURCE}" in package.output
    assert inspect.exit_code == 0
    assert f"fixture_request_count: {FIXTURE_SOURCE_COUNT}" in inspect.output


def test_system_grade_cli():
    result = runner.invoke(app, ["grade", "system", "--plain"])

    assert result.exit_code == 0
    assert "safety_score: 100" in result.output
    assert "release_decision:" in result.output


def _mock_grade_inputs(monkeypatch, compile_report):
    package = {
        "dag_validation_status": "PASS",
        "execution_package_contract_sha256": "package-hash",
        "jobs_sha256": "jobs-hash",
    }

    def read_json(path):
        if "static_http_range_wave1_results.json" in str(path):
            return {
                "runnable_source_count": 4,
                "fixture_source_count": 1,
                "pass_count": 4,
                "fail_count": 0,
            }
        return {"overall_status": "PASS"}

    monkeypatch.setattr(system_grade, "compile_task", lambda task_id: dict(compile_report))
    monkeypatch.setattr(system_grade, "package_task", lambda task_id: dict(package))
    monkeypatch.setattr(system_grade, "_read_json", read_json)


def _valid_compile_report():
    return {
        "task_id": TASK_ID,
        "validation_status": "PASS",
        "manifest_row_count": 5,
        "executable_request_count": 4,
        "fixture_request_count": 1,
        "determinism_status": "PASS",
        "acquisition_manifest_sha256": "manifest-hash",
    }


def test_system_grade_missing_determinism_status_holds_release(monkeypatch):
    report = _valid_compile_report()
    report.pop("determinism_status")
    _mock_grade_inputs(monkeypatch, report)

    grade = system_grade.grade_system(TASK_ID)

    assert grade["release_decision"] == "hold_release"
    assert "compile report determinism_status missing" in grade["blocking_failures"]


def test_system_grade_incorrect_manifest_count_holds_release(monkeypatch):
    report = _valid_compile_report()
    report["manifest_row_count"] = 4
    _mock_grade_inputs(monkeypatch, report)

    grade = system_grade.grade_system(TASK_ID)

    assert grade["release_decision"] == "hold_release"
    assert "compile report manifest_row_count does not equal executable_request_count + fixture_request_count" in grade["blocking_failures"]


def test_system_grade_valid_compile_report_without_live_receipt_has_cautions(monkeypatch):
    _mock_grade_inputs(monkeypatch, _valid_compile_report())

    grade = system_grade.grade_system(TASK_ID)

    assert grade["release_decision"] == "release_ready_with_cautions"
    assert grade["blocking_failures"] == []
    assert grade["expected_runnable_source_count"] == 4
    assert grade["expected_fixture_source_count"] == 1
    assert "no_live_local_execution_receipt" in grade["warnings"]
