from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from faster_raster import artifact_receipts, local_executor, materialization, prism_raster, task_compiler
from faster_raster.prism_product import PRISM_SOURCE_ID


DEFAULT_TASK_ID = "example_wave1_climate_stack"
DEFAULT_PROBE_BYTES = 65_536
DEFAULT_OBJECT_CAP = 16 * 1024 * 1024
DEFAULT_RASTER_CAP = 64 * 1024 * 1024


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _configure_workspace(workspace: Path) -> dict[str, Path]:
    report_root = workspace / "reports"
    compile_root = report_root / "task_compiles"
    package_root = report_root / "execution_packages"
    run_root = report_root / "runs"
    materialization_root = report_root / "materializations"

    task_compiler.REPORT_ROOT = report_root
    task_compiler.TASK_COMPILE_ROOT = compile_root
    task_compiler.EXECUTION_PACKAGE_ROOT = package_root
    local_executor.COMPILE_ROOT = compile_root
    local_executor.PACKAGE_ROOT = package_root
    local_executor.RUN_ROOT = run_root
    materialization.COMPILE_ROOT = compile_root
    materialization.PACKAGE_ROOT = package_root
    materialization.MATERIALIZATION_ROOT = materialization_root

    return {
        "report_root": report_root,
        "compile_root": compile_root,
        "package_root": package_root,
        "run_root": run_root,
        "materialization_root": materialization_root,
        "runtime_cache_root": workspace / "cache" / "runtime",
        "artifact_root": workspace / "cache" / "artifacts" / "sha256",
        "staging_root": workspace / "cache" / "staging" / "materialization",
        "catalog_root": workspace / "cache" / "catalog",
        "raster_artifact_root": workspace / "cache" / "derived" / "prism" / "sha256",
        "raster_staging_root": workspace / "cache" / "staging" / "prism-raster",
        "raster_receipt_path": workspace / "prism_raster_receipt.json",
    }


def run_canary(
    *,
    repo_root: Path,
    workspace: Path,
    task_id: str = DEFAULT_TASK_ID,
    execute: bool = False,
    allow_network: bool = False,
    allow_materialization: bool = False,
    probe_bytes: int = DEFAULT_PROBE_BYTES,
    max_object_bytes: int = DEFAULT_OBJECT_CAP,
    max_total_bytes: int = DEFAULT_OBJECT_CAP,
    max_raster_bytes: int = DEFAULT_RASTER_CAP,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    workspace = workspace.resolve()
    if probe_bytes <= 0:
        raise ValueError("probe_bytes must be positive")
    if max_object_bytes <= 0 or max_total_bytes <= 0:
        raise ValueError("materialization byte caps must be positive")
    if max_object_bytes > max_total_bytes:
        raise ValueError("max_object_bytes must not exceed max_total_bytes")
    if max_raster_bytes <= 0:
        raise ValueError("max_raster_bytes must be positive")
    if not allow_network:
        raise ValueError("bounded canary requires --allow-network")
    if execute and not (allow_network and allow_materialization):
        raise ValueError("execution requires --allow-network and --allow-materialization")

    previous_cwd = Path.cwd()
    workspace.mkdir(parents=True, exist_ok=True)
    roots = _configure_workspace(workspace)
    try:
        os.chdir(repo_root)
        compile_report = task_compiler.compile_task(task_id, max_bytes_per_source=probe_bytes)
        package = task_compiler.package_task(task_id, max_bytes_per_source=probe_bytes)

        probe = local_executor.execute_local(
            task_id,
            allow_network=allow_network,
            max_bytes_per_source=probe_bytes,
            max_total_bytes=probe_bytes * max(int(package["executable_request_count"]), 1),
            timeout_seconds=timeout_seconds,
            cache_root=roots["runtime_cache_root"],
            reports_root=roots["report_root"],
        )
        probe_receipt = probe["receipt"]
        if probe_receipt.get("run_status") not in {"completed", "completed_with_warnings"}:
            raise RuntimeError(f"bounded probe did not complete: {probe_receipt.get('run_status')}")
        if probe_receipt.get("failed_source_count") != 0:
            raise RuntimeError("bounded probe contains failed sources")

        plan = materialization.build_materialization_plan(
            task_id,
            sources=[PRISM_SOURCE_ID],
            max_object_bytes=max_object_bytes,
            max_total_bytes=max_total_bytes,
            timeout_seconds=timeout_seconds,
            write_artifacts=True,
            artifact_root=roots["artifact_root"],
            staging_root=roots["staging_root"],
            catalog_root=roots["catalog_root"],
            materializations_root=roots["materialization_root"],
            probe_runs_root=roots["run_root"],
        )
        prism_plan = next(item for item in plan["object_plans"] if item["source_id"] == PRISM_SOURCE_ID)
        if plan.get("validation_status") != "PASS" or not prism_plan.get("materialization_eligible"):
            raise RuntimeError(f"PRISM materialization plan is not eligible: {plan.get('blocking_reasons')}")

        summary: dict[str, Any] = {
            "canary_version": 2,
            "task_id": task_id,
            "source_id": PRISM_SOURCE_ID,
            "workspace": str(workspace),
            "execution_requested": execute,
            "compile_validation_status": compile_report.get("validation_status"),
            "dag_validation_status": package.get("dag_validation_status"),
            "probe_run_id": probe_receipt.get("run_id"),
            "probe_evidence_class": probe_receipt.get("evidence_class"),
            "probe_status": probe_receipt.get("run_status"),
            "probe_bytes_per_source": probe_bytes,
            "materialization_plan_contract_sha256": plan["materialization_plan_contract_sha256"],
            "expected_object_size_bytes": prism_plan.get("expected_object_size_bytes"),
            "max_object_bytes": max_object_bytes,
            "max_total_bytes": max_total_bytes,
            "max_raster_bytes": max_raster_bytes,
            "status": "PLAN_READY",
        }

        if execute:
            result = materialization.execute_materialization(
                task_id,
                sources=[PRISM_SOURCE_ID],
                allow_network=True,
                allow_materialization=True,
                approve_plan_sha256=plan["materialization_plan_contract_sha256"],
                max_object_bytes=max_object_bytes,
                max_total_bytes=max_total_bytes,
                timeout_seconds=timeout_seconds,
                artifact_root=roots["artifact_root"],
                staging_root=roots["staging_root"],
                catalog_root=roots["catalog_root"],
                materializations_root=roots["materialization_root"],
                probe_runs_root=roots["run_root"],
            )
            receipt = result["receipt"]
            verification = artifact_receipts.verify_materialization_run(Path(result["receipt_path"]), repo_root=repo_root)
            if result.get("run_status") != "completed" or verification.get("verification_status") != "PASS":
                raise RuntimeError(
                    f"PRISM full-object materialization failed: status={result.get('run_status')} verification={verification.get('verification_status')}"
                )
            artifacts = receipt.get("artifact_receipts") or []
            if len(artifacts) != 1:
                raise RuntimeError("PRISM canary expected exactly one artifact receipt")
            artifact = artifacts[0]
            profile = (artifact.get("container_metadata") or {}).get("product_profile") or {}
            if profile.get("product_validation_status") != "PASS":
                raise RuntimeError("PRISM product profile validation did not pass")

            raster_receipt = prism_raster.materialize_prism_primary_raster(
                Path(artifact["artifact_path"]),
                temporal_key=str(artifact["temporal_key"]),
                product_profile=profile,
                artifact_root=roots["raster_artifact_root"],
                staging_root=roots["raster_staging_root"],
                receipt_path=roots["raster_receipt_path"],
                max_extracted_raster_bytes=max_raster_bytes,
            )
            raster_verification = prism_raster.verify_prism_raster_receipt(roots["raster_receipt_path"])
            raster_profile = raster_receipt.get("raster_profile") or {}
            if raster_receipt.get("validation_status") != "PASS" or raster_verification.get("verification_status") != "PASS":
                raise RuntimeError(
                    "PRISM decoded-raster validation failed: "
                    f"receipt={raster_receipt.get('validation_status')} "
                    f"verification={raster_verification.get('verification_status')}"
                )

            summary.update(
                {
                    "status": "PASS",
                    "materialization_run_id": result["materialization_run_id"],
                    "materialization_receipt_path": result["receipt_path"],
                    "materialization_verification_status": verification.get("verification_status"),
                    "artifact_id": artifact.get("artifact_id"),
                    "artifact_path": artifact.get("artifact_path"),
                    "object_size_bytes": artifact.get("object_size_bytes"),
                    "whole_object_sha256": artifact.get("whole_object_sha256"),
                    "product_profile": profile,
                    "raster_receipt_path": str(roots["raster_receipt_path"]),
                    "raster_receipt_contract_sha256": raster_receipt.get("raster_receipt_contract_sha256"),
                    "raster_receipt_verification_status": raster_verification.get("verification_status"),
                    "raster_artifact_id": raster_receipt.get("raster_artifact_id"),
                    "raster_artifact_path": raster_receipt.get("raster_artifact_path"),
                    "raster_sha256": raster_receipt.get("raster_sha256"),
                    "raster_size_bytes": raster_receipt.get("raster_size_bytes"),
                    "raster_profile": raster_profile,
                }
            )

        output = workspace / "prism_product_canary.json"
        _write_json(output, summary)
        summary["canary_summary_path"] = str(output)
        _write_json(output, summary)
        return summary
    finally:
        os.chdir(previous_cwd)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fr-prism-canary",
        description="Plan or execute a guarded PRISM daily precipitation full-object canary.",
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--workspace", type=Path, default=Path.home() / ".local" / "state" / "faster-raster" / "prism-canary" / _utc_stamp())
    parser.add_argument("--task-id", default=DEFAULT_TASK_ID)
    parser.add_argument("--probe-bytes", type=int, default=DEFAULT_PROBE_BYTES)
    parser.add_argument("--max-object-bytes", type=int, default=DEFAULT_OBJECT_CAP)
    parser.add_argument("--max-total-bytes", type=int, default=DEFAULT_OBJECT_CAP)
    parser.add_argument("--max-raster-bytes", type=int, default=DEFAULT_RASTER_CAP)
    parser.add_argument("--timeout-seconds", type=int, default=60)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--allow-materialization", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main() -> None:
    args = _parser().parse_args()
    summary = run_canary(
        repo_root=args.repo,
        workspace=args.workspace,
        task_id=args.task_id,
        execute=args.execute,
        allow_network=args.allow_network,
        allow_materialization=args.allow_materialization,
        probe_bytes=args.probe_bytes,
        max_object_bytes=args.max_object_bytes,
        max_total_bytes=args.max_total_bytes,
        max_raster_bytes=args.max_raster_bytes,
        timeout_seconds=args.timeout_seconds,
    )
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"status: {summary['status']}")
        print(f"task_id: {summary['task_id']}")
        print(f"source_id: {summary['source_id']}")
        print(f"workspace: {summary['workspace']}")
        print(f"plan_sha256: {summary['materialization_plan_contract_sha256']}")
        print(f"expected_object_size_bytes: {summary.get('expected_object_size_bytes')}")
        if summary["status"] == "PASS":
            print(f"object_size_bytes: {summary['object_size_bytes']}")
            print(f"whole_object_sha256: {summary['whole_object_sha256']}")
            print(f"primary_raster_member: {summary['product_profile']['primary_raster_member']}")
            print(f"inventory_sha256: {summary['product_profile']['inventory_sha256']}")
            print(f"raster_artifact_path: {summary['raster_artifact_path']}")
            print(f"raster_sha256: {summary['raster_sha256']}")
            print(f"raster_profile_sha256: {summary['raster_profile']['raster_profile_sha256']}")
            print(f"raster_dimensions: {summary['raster_profile']['width']}x{summary['raster_profile']['height']}")
            print(f"raster_epsg: {summary['raster_profile']['epsg']}")
            print(f"cog_validation: {summary['raster_profile']['cog_structure_validation_status']}")
        print(f"summary_json: {summary['canary_summary_path']}")


if __name__ == "__main__":
    main()
