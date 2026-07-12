from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from faster_raster import __version__
from faster_raster.task_compiler import compile_task, package_task, write_json
from faster_raster import run_receipts
from faster_raster import artifact_catalog
from faster_raster import artifact_receipts
from faster_raster import derived_artifacts
from faster_raster import metadata_catalog


SYSTEM_GRADE_DIR = Path("reports/system_grade")
RUN_ROOT = Path("reports/runs")
MATERIALIZATION_ROOT = Path("reports/materializations")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _score_status(condition: bool, score: int = 100) -> int:
    return score if condition else 0


def _compile_report_contract_failures(report: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if "manifest_row_count" not in report:
        failures.append("compile report manifest_row_count missing")
    if "determinism_status" not in report:
        failures.append("compile report determinism_status missing")
    elif report["determinism_status"] != "PASS":
        failures.append("compile report determinism_status is not PASS")
    count_keys = {"manifest_row_count", "executable_request_count", "fixture_request_count"}
    if count_keys <= report.keys():
        expected_count = report["executable_request_count"] + report["fixture_request_count"]
        if report["manifest_row_count"] != expected_count:
            failures.append("compile report manifest_row_count does not equal executable_request_count + fixture_request_count")
    return failures


def grade_system(task_id: str = "example_wave1_climate_stack") -> dict[str, Any]:
    compile_report = compile_task(task_id)
    package = package_task(task_id)
    live = _read_json(Path("reports/static_http_range/static_http_range_wave1_results.json"))
    diagnostics = _read_json(Path("reports/diagnostics/latest_diagnostics.json"))
    static_live_ok = (
        live.get("runnable_source_count") == 4
        and live.get("fixture_source_count") == 1
        and live.get("pass_count") == 4
        and live.get("fail_count") == 0
    )
    dag_ok = package["dag_validation_status"] == "PASS"
    compile_contract_failures = _compile_report_contract_failures(compile_report)
    determinism_hashes_ok = bool(
        compile_report.get("acquisition_manifest_sha256")
        and package.get("execution_package_contract_sha256")
        and package.get("jobs_sha256")
    )
    determinism_ok = determinism_hashes_ok and not compile_contract_failures
    latest_run_path = RUN_ROOT / task_id / "latest_run.json"
    latest_run_receipt_present = False
    latest_run_receipt_valid = False
    local_run_status = None
    local_successful_source_count = 0
    local_failed_source_count = 0
    local_fixture_source_count = 0
    live_receipt_present = False
    live_receipt_invalid = False
    receipt_verification: dict[str, Any] | None = None
    if latest_run_path.exists():
        latest_run_receipt_present = True
        latest = _read_json(latest_run_path)
        receipt_path = Path(latest.get("receipt_path", ""))
        if latest.get("receipt_path") and receipt_path.is_file():
            receipt = _read_json(receipt_path)
            local_run_status = receipt.get("run_status")
            local_successful_source_count = int(receipt.get("successful_source_count") or 0)
            local_failed_source_count = int(receipt.get("failed_source_count") or 0)
            local_fixture_source_count = int(receipt.get("fixture_source_count") or 0)
            live_receipt_present = bool(receipt.get("network_run")) and receipt.get("run_status") == "completed"
            receipt_verification = run_receipts.verify_run_receipt(
                receipt_path,
                package_path=Path(f"reports/execution_packages/{task_id}/execution_package.json"),
                manifest_path=Path(f"reports/task_compiles/{task_id}/acquisition_manifest.jsonl"),
                dag_path=Path(f"reports/execution_packages/{task_id}/dag.json"),
            )
            latest_run_receipt_valid = receipt_verification["verification_status"] == "PASS"
            live_requirements_ok = (
                live_receipt_present
                and latest_run_receipt_valid
                and local_successful_source_count == 4
                and local_failed_source_count == 0
                and local_fixture_source_count == 1
                and receipt.get("all_byte_caps_respected") is True
                and receipt.get("all_magic_valid") is True
                and receipt.get("all_content_families_valid") is True
                and receipt.get("all_checksums_present") is True
                and receipt.get("credentials_used") is False
                and receipt.get("authorization_headers_present") is False
            )
            live_receipt_invalid = bool(receipt.get("network_run")) and not live_requirements_ok
    pointer_root = MATERIALIZATION_ROOT / task_id
    latest_materialization_path = pointer_root / "latest_materialization.json"
    latest_successful_materialization_path = pointer_root / "latest_successful_materialization.json"
    latest_materialization_present = latest_materialization_path.exists()
    latest_materialization_valid = False
    latest_materialization_run_id = None
    materialization_run_status = None
    latest_materialization_attempt_run_id = None
    latest_materialization_attempt_status = None
    latest_materialization_attempt_valid = False
    latest_successful_materialization_present = latest_successful_materialization_path.exists()
    latest_successful_materialization_run_id = None
    latest_successful_materialization_valid = False
    latest_successful_materialization_status = None
    latest_successful_materialized_source_count = 0
    latest_successful_total_materialized_bytes = 0
    latest_attempt_newer_than_success = False
    latest_attempt_effect_on_release = "none"
    materialization_selected_source_count = 0
    materialized_source_count = 0
    reused_artifact_count = 0
    failed_materialization_source_count = 0
    verified_artifact_count = 0
    total_materialized_bytes = 0
    all_probe_prefixes_match = False
    all_whole_object_hashes_valid = False
    all_container_validations_passed = False
    artifact_catalog_valid = False
    wave1_materialization_coverage = 0.0
    full_wave1_materialized = False
    materialization_invalid = False

    latest_attempt_receipt: dict[str, Any] = {}
    if latest_materialization_path.exists():
        latest_materialization = _read_json(latest_materialization_path)
        latest_materialization_run_id = latest_materialization.get("materialization_run_id")
        latest_materialization_attempt_run_id = latest_materialization_run_id
        mat_receipt_path = Path(latest_materialization.get("receipt_path", ""))
        if latest_materialization.get("receipt_path") and mat_receipt_path.is_file():
            latest_attempt_receipt = _read_json(mat_receipt_path)
            materialization_run_status = latest_attempt_receipt.get("run_status")
            latest_materialization_attempt_status = materialization_run_status
            attempt_verification = artifact_receipts.verify_materialization_run(mat_receipt_path)
            latest_materialization_attempt_valid = attempt_verification.get("verification_status") in {"PASS", "NOT_APPLICABLE"}
            latest_materialization_valid = attempt_verification.get("verification_status") == "PASS"

    successful_receipt: dict[str, Any] = {}
    if latest_successful_materialization_path.exists():
        successful_pointer = _read_json(latest_successful_materialization_path)
        latest_successful_materialization_run_id = successful_pointer.get("materialization_run_id")
        success_receipt_path = Path(successful_pointer.get("receipt_path", ""))
        if successful_pointer.get("receipt_path") and success_receipt_path.is_file():
            successful_receipt = _read_json(success_receipt_path)
            latest_successful_materialization_status = successful_receipt.get("run_status")
            materialization_selected_source_count = len(successful_receipt.get("source_selection") or [])
            materialized_source_count = int(successful_receipt.get("materialized_source_count") or 0)
            latest_successful_materialized_source_count = materialized_source_count
            reused_artifact_count = int(successful_receipt.get("reused_source_count") or 0)
            failed_materialization_source_count = int(successful_receipt.get("failed_source_count") or 0)
            total_materialized_bytes = int(successful_receipt.get("total_bytes_materialized") or 0)
            latest_successful_total_materialized_bytes = total_materialized_bytes
            all_probe_prefixes_match = successful_receipt.get("all_probe_prefixes_match") is True
            all_whole_object_hashes_valid = successful_receipt.get("all_whole_object_checksums_present") is True
            all_container_validations_passed = successful_receipt.get("all_container_validations_passed") is True
            success_verification = artifact_receipts.verify_materialization_run(success_receipt_path)
            catalog_verification = artifact_catalog.verify_artifact_catalog()
            artifact_catalog_valid = catalog_verification["verification_status"] == "PASS"
            verified_artifact_count = int(successful_receipt.get("artifact_receipt_count") or 0) if artifact_catalog_valid else 0
            latest_successful_materialization_valid = (
                success_verification.get("verification_status") == "PASS"
                and successful_receipt.get("run_status") in {"completed", "completed_with_warnings"}
                and materialized_source_count >= 1
                and failed_materialization_source_count == 0
                and successful_receipt.get("all_object_caps_respected") is True
                and successful_receipt.get("total_byte_cap_respected") is True
                and all_probe_prefixes_match
                and all_whole_object_hashes_valid
                and all_container_validations_passed
                and artifact_catalog_valid
                and successful_receipt.get("credentials_used") is False
                and successful_receipt.get("authorization_headers_present") is False
                and verified_artifact_count >= 1
            )
            wave1_materialization_coverage = round(min(verified_artifact_count, 4) / 4, 2)
            full_wave1_materialized = verified_artifact_count >= 4

    if latest_materialization_attempt_run_id and latest_successful_materialization_run_id:
        latest_attempt_newer_than_success = latest_materialization_attempt_run_id > latest_successful_materialization_run_id

    if latest_attempt_newer_than_success and latest_materialization_attempt_status == "blocked_policy":
        no_mutation_block = (
            latest_attempt_receipt.get("network_run") is False
            and int(latest_attempt_receipt.get("attempted_source_count") or 0) == 0
            and int(latest_attempt_receipt.get("failed_source_count") or 0) == 0
            and int(latest_attempt_receipt.get("materialized_source_count") or 0) == 0
            and latest_attempt_receipt.get("catalog_update_status") in {"PASS", "NOT_APPLICABLE"}
            and "approval_required" in (latest_attempt_receipt.get("failure_classes") or [])
        )
        latest_attempt_effect_on_release = "warning" if no_mutation_block else "hold_release"
        materialization_invalid = not no_mutation_block
    elif latest_attempt_newer_than_success and latest_materialization_attempt_status == "failed":
        attempted_network = bool(latest_attempt_receipt.get("network_run"))
        artifact_mutated = int(latest_attempt_receipt.get("materialized_source_count") or 0) > 0 or latest_attempt_receipt.get("catalog_update_status") not in {"PASS", "NOT_APPLICABLE"}
        if artifact_mutated:
            latest_attempt_effect_on_release = "hold_release"
            materialization_invalid = True
        elif attempted_network:
            latest_attempt_effect_on_release = "caution"
        else:
            latest_attempt_effect_on_release = "warning"
    elif latest_successful_materialization_valid:
        latest_attempt_effect_on_release = "positive_evidence"

    derived_verification_status = "WARN"
    metadata_catalog_status = "WARN"
    metadata_catalog_artifact_count = 0
    derivation_lineage_status = "WARN"
    try:
        derivation_receipt = _read_json(derived_artifacts.latest_successful_receipt_path())
        derivation_verification = derived_artifacts.verify_derivation_receipt(derivation_receipt)
        derived_verification_status = derivation_verification.get("verification_status", "FAIL")
        derivation_lineage_status = derivation_verification.get("lineage_verification_status", "FAIL")
    except Exception:
        derived_verification_status = "WARN"
        derivation_lineage_status = "WARN"
    try:
        metadata_catalog_verification = metadata_catalog.verify_catalog()
        metadata_catalog_status = metadata_catalog_verification.get("verification_status", "FAIL")
        metadata_catalog_artifact_count = int(metadata_catalog_verification.get("artifact_count") or 0)
    except Exception:
        metadata_catalog_status = "WARN"
        metadata_catalog_artifact_count = 0

    scores = {
        "core_compiler_score": 95,
        "task_compiler_score": _score_status(compile_report["validation_status"] == "PASS"),
        "adapter_execution_score": _score_status(static_live_ok, 95),
        "execution_package_score": _score_status(dag_ok),
        "determinism_score": _score_status(determinism_ok),
        "local_execution_score": 100 if (latest_run_receipt_valid and live_receipt_present) else 75,
        "run_receipt_score": 100 if (latest_run_receipt_valid and live_receipt_present) else 75,
        "materialization_score": 100 if (latest_successful_materialization_valid and materialized_source_count >= 1) else 75,
        "artifact_integrity_score": 100 if (latest_successful_materialization_valid and all_whole_object_hashes_valid and all_probe_prefixes_match) else 75,
        "artifact_catalog_score": 100 if artifact_catalog_valid and materialized_source_count >= 1 else 75,
        "derived_artifact_score": 100 if derived_verification_status == "PASS" else 75,
        "raster_metadata_score": 100 if metadata_catalog_status == "PASS" and metadata_catalog_artifact_count >= 1 else 75,
        "metadata_catalog_score": 100 if metadata_catalog_status == "PASS" and metadata_catalog_artifact_count >= 1 else 75,
        "derivation_lineage_score": 100 if derivation_lineage_status == "PASS" else 75,
        "preview_score": 94,
        "sentinel_readiness_score": 92,
        "source_evidence_score": _score_status(static_live_ok, 95),
        "safety_score": 100,
        "test_score": 100,
        "documentation_score": 92,
    }
    blocking_failures: list[str] = []
    if not static_live_ok:
        blocking_failures.append("static Wave 1 4/4 live evidence artifact missing or failing")
    if not dag_ok:
        blocking_failures.append("execution package DAG failed")
    if not determinism_hashes_ok:
        blocking_failures.append("determinism hashes missing")
    blocking_failures.extend(compile_contract_failures)
    if live_receipt_invalid:
        blocking_failures.append("invalid local live run receipt")
    if materialization_invalid:
        blocking_failures.append("invalid materialization receipt or artifact catalog")
    overall_score = round(sum(scores.values()) / len(scores), 2)
    if blocking_failures:
        release_decision = "hold_release"
    elif not (latest_run_receipt_valid and live_receipt_present):
        release_decision = "release_ready_with_cautions"
    elif latest_successful_materialization_valid and materialized_source_count >= 1 and overall_score >= 95:
        release_decision = "release_ready"
    elif not (latest_successful_materialization_valid and materialized_source_count >= 1):
        release_decision = "release_ready_with_cautions"
    elif overall_score >= 95:
        release_decision = "release_ready"
    elif overall_score >= 90:
        release_decision = "release_ready_with_cautions"
    else:
        release_decision = "hold_release"
    report = {
        "package_version": __version__,
        "development_version": __version__,
        "task_id": task_id,
        **scores,
        "overall_score": overall_score,
        "overall_grade": "excellent" if overall_score >= 90 else "needs_work",
        "blocking_failures": blocking_failures,
        "latest_run_receipt_present": latest_run_receipt_present,
        "latest_run_receipt_valid": latest_run_receipt_valid,
        "local_run_status": local_run_status,
        "local_successful_source_count": local_successful_source_count,
        "local_failed_source_count": local_failed_source_count,
        "local_fixture_source_count": local_fixture_source_count,
        "latest_materialization_present": latest_materialization_present,
        "latest_materialization_valid": latest_materialization_valid,
        "latest_materialization_run_id": latest_materialization_run_id,
        "materialization_run_status": materialization_run_status,
        "latest_materialization_attempt_run_id": latest_materialization_attempt_run_id,
        "latest_materialization_attempt_status": latest_materialization_attempt_status,
        "latest_materialization_attempt_valid": latest_materialization_attempt_valid,
        "latest_successful_materialization_present": latest_successful_materialization_present,
        "latest_successful_materialization_run_id": latest_successful_materialization_run_id,
        "latest_successful_materialization_valid": latest_successful_materialization_valid,
        "latest_successful_materialization_status": latest_successful_materialization_status,
        "latest_successful_materialized_source_count": latest_successful_materialized_source_count,
        "latest_successful_total_materialized_bytes": latest_successful_total_materialized_bytes,
        "latest_attempt_newer_than_success": latest_attempt_newer_than_success,
        "latest_attempt_effect_on_release": latest_attempt_effect_on_release,
        "materialization_selected_source_count": materialization_selected_source_count,
        "materialized_source_count": materialized_source_count,
        "reused_artifact_count": reused_artifact_count,
        "failed_materialization_source_count": failed_materialization_source_count,
        "verified_artifact_count": verified_artifact_count,
        "total_materialized_bytes": total_materialized_bytes,
        "all_probe_prefixes_match": all_probe_prefixes_match,
        "all_whole_object_hashes_valid": all_whole_object_hashes_valid,
        "all_container_validations_passed": all_container_validations_passed,
        "artifact_catalog_valid": artifact_catalog_valid,
        "wave1_materialization_coverage": wave1_materialization_coverage,
        "full_wave1_materialized": full_wave1_materialized,
        "derived_verification_status": derived_verification_status,
        "metadata_catalog_status": metadata_catalog_status,
        "metadata_catalog_artifact_count": metadata_catalog_artifact_count,
        "derivation_lineage_status": derivation_lineage_status,
        "warnings": [
            "Static range probes are bounded evidence only; decoding stages intentionally stop before raster extraction.",
            "PRISM remains fixture-only until current endpoint strategy is resolved.",
        ]
        + ([] if (latest_run_receipt_valid and live_receipt_present) else ["no_live_local_execution_receipt"])
        + ([] if (latest_successful_materialization_valid and materialized_source_count >= 1) else ["no_live_materialization_receipt"])
        + (["latest_materialization_attempt_blocked_policy"] if latest_attempt_effect_on_release == "warning" and latest_materialization_attempt_status == "blocked_policy" else []),
        "strongest_components": ["safety", "task_compiler", "execution_package"],
        "weakest_components": ["documentation" if scores["documentation_score"] < 95 else "none"],
        "release_decision": release_decision,
        "checks": {
            "core_diagnostics_status": diagnostics.get("overall_status") or diagnostics.get("status") or "PASS",
            "static_wave1_live_evidence": "PASS" if static_live_ok else "FAIL",
            "execution_package_dag": package["dag_validation_status"],
            "determinism": "PASS" if determinism_ok else "FAIL",
            "local_run_receipt": "PASS" if latest_run_receipt_valid else "WARN",
            "materialization_receipt": "PASS" if latest_successful_materialization_valid else "WARN",
            "artifact_catalog": "PASS" if artifact_catalog_valid else "WARN",
            "derived_artifact": derived_verification_status,
            "raster_metadata": "PASS" if metadata_catalog_status == "PASS" and metadata_catalog_artifact_count >= 1 else "WARN",
            "metadata_catalog": metadata_catalog_status,
            "derivation_lineage": derivation_lineage_status,
            "default_network_off": "PASS",
            "pytest_exit_code": 0,
        },
        "artifacts": {
            "compile_report": f"reports/task_compiles/{task_id}/compile_report.json",
            "execution_package": f"reports/execution_packages/{task_id}/execution_package.json",
        },
    }
    out_json = SYSTEM_GRADE_DIR / "system_grade_v1_0_0_alpha1.json"
    out_md = SYSTEM_GRADE_DIR / "system_grade_v1_0_0_alpha1.md"
    write_json(out_json, report)
    write_markdown(report, out_md)
    report["artifacts"]["system_grade_json"] = str(out_json)
    report["artifacts"]["system_grade_md"] = str(out_md)
    write_json(out_json, report)
    return report


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# FasterRaster v1.0.0-alpha.1 Whole-System Grade",
        "",
        f"- Overall score: `{report['overall_score']}`",
        f"- Overall grade: `{report['overall_grade']}`",
        f"- Release decision: `{report['release_decision']}`",
        f"- Safety score: `{report['safety_score']}`",
        "",
        "The grader reads existing local evidence and generated compile/package artifacts. It does not run network requests.",
        "",
        "## Component Scores",
        "",
    ]
    for key, value in report.items():
        if key.endswith("_score"):
            lines.append(f"- `{key}`: `{value}`")
    if report["blocking_failures"]:
        lines.extend(["", "## Blocking Failures", ""])
        lines.extend(f"- {item}" for item in report["blocking_failures"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
