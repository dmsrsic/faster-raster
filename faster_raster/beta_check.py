from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from faster_raster.release_inventory import build_release_inventory, inventory_markdown


def repository_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tracked_report_hashes(root: Path) -> dict[str, str]:
    result = subprocess.run(
        ["git", "ls-files", "reports"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    hashes: dict[str, str] = {}
    for value in result.stdout.splitlines():
        path = root / value
        hashes[value] = _sha256(path) if path.is_file() else "MISSING"
    return hashes


def _step(
    name: str,
    action: Callable[[], Any],
    steps: list[dict[str, Any]],
) -> Any:
    started = time.monotonic()
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = action()
        if isinstance(result, dict):
            for key in ("stdout_tail", "stderr_tail"):
                value = result.get(key)
                if value:
                    output.write(str(value))
        if isinstance(result, int) and result != 0:
            raise RuntimeError(f"exit code {result}")
        status = "PASS"
        error = None
        return result
    except Exception as exc:
        status = "FAIL"
        error = f"{type(exc).__name__}: {exc}"
        return None
    finally:
        steps.append(
            {
                "name": name,
                "status": status,
                "duration_seconds": round(time.monotonic() - started, 3),
                "error": error,
                "output_tail": output.getvalue()[-12_000:],
            }
        )
        print(f"[{status}] {name} ({steps[-1]['duration_seconds']:.3f}s)")
        if error:
            print(f"  {error}")


def _subprocess(command: list[str], root: Path, environment: dict[str, str]) -> dict[str, Any]:
    result = subprocess.run(
        command,
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            + (result.stdout + result.stderr)[-8_000:]
        )
    return {
        "command": command,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-8_000:],
        "stderr_tail": result.stderr[-8_000:],
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# FasterRaster Beta Gate 1 check",
        "",
        f"- Final status: **{report['final_status']}**",
        f"- Python: `{report['runtime']['python_version']}`",
        f"- Tracked reports unchanged: `{report['tracked_reports_unchanged']}`",
        f"- Cached zero-network cook: `{report['cached_cook']['status']}`",
        "",
        "| Check | Status | Seconds |",
        "|---|:---:|---:|",
    ]
    for step in report["steps"]:
        lines.append(f"| {step['name']} | {step['status']} | {step['duration_seconds']:.3f} |")
    if report["failures"]:
        lines.extend(["", "## Failures", ""])
        lines.extend(f"- {value}" for value in report["failures"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_beta_check(
    *,
    root: Path,
    output: Path,
    run_tests: bool = True,
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    before = _tracked_report_hashes(root)
    steps: list[dict[str, Any]] = []
    failures: list[str] = []
    inventory = build_release_inventory(root)
    (output / "release_inventory.json").write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output / "release_inventory.md").write_text(
        inventory_markdown(inventory),
        encoding="utf-8",
    )

    managed_environment = (
        "FASTERRASTER_REPORT_ROOT",
        "FASTERRASTER_HANDOFF_ROOT",
        "FASTERRASTER_AG_CACHE_ROOT",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
    )
    original_environment = {
        key: os.environ.get(key) for key in managed_environment
    }

    with tempfile.TemporaryDirectory(prefix="fasterraster-beta-") as temporary:
        temp = Path(temporary)
        environment = dict(os.environ)
        environment.update(
            {
                "FASTERRASTER_REPORT_ROOT": str(temp / "reports"),
                "FASTERRASTER_HANDOFF_ROOT": str(temp / "handoffs"),
                "FASTERRASTER_AG_CACHE_ROOT": str(root / "outputs" / "handoffs"),
                "XDG_CONFIG_HOME": str(temp / "xdg-config"),
                "XDG_CACHE_HOME": str(temp / "xdg-cache"),
                "XDG_STATE_HOME": str(temp / "xdg-state"),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        os.environ.update({key: environment[key] for key in environment if key.startswith("FASTERRASTER_") or key.startswith("XDG_")})

        from faster_raster import system_grade, task_compiler
        from faster_raster.ag_assets import compile_asset_plan, discover_cached_assets
        from faster_raster.adapters import static_http_range
        from faster_raster.ag_recipes import load_named_recipe
        from faster_raster.fr_cli import main as fr_main
        from faster_raster.workfiles import load_workfile

        original_report_roots = (
            task_compiler.TASK_COMPILE_ROOT,
            task_compiler.EXECUTION_PACKAGE_ROOT,
            system_grade.SYSTEM_GRADE_DIR,
            system_grade.RUN_ROOT,
            system_grade.MATERIALIZATION_ROOT,
            static_http_range.DEFAULT_REPORT_DIR,
            system_grade.preview_alpha2.SOURCE_REPORT_ROOT,
        )
        task_compiler.TASK_COMPILE_ROOT = temp / "reports" / "task_compiles"
        task_compiler.EXECUTION_PACKAGE_ROOT = temp / "reports" / "execution_packages"
        system_grade.SYSTEM_GRADE_DIR = temp / "reports" / "system_grade"
        system_grade.RUN_ROOT = temp / "reports" / "runs"
        system_grade.MATERIALIZATION_ROOT = temp / "reports" / "materializations"
        static_http_range.DEFAULT_REPORT_DIR = temp / "reports" / "static_http_range"
        system_grade.preview_alpha2.SOURCE_REPORT_ROOT = (
            temp / "reports" / "sources"
        )

        workfile_path = root / "examples" / "colby-study.fr.md"
        _step("fr doctor --offline", lambda: fr_main(["doctor", "--offline", "--json"]), steps)
        _step("fr validate", lambda: fr_main(["validate", str(workfile_path), "--json"]), steps)
        _step(
            "fr plan --reuse only fails closed without compatible cache",
            lambda: 0
            if fr_main(
                [
                    "plan",
                    str(workfile_path),
                    "--reuse",
                    "only",
                    "--out",
                    str(temp / "plan"),
                    "--json",
                ]
            )
            == 2
            else 1,
            steps,
        )

        compile_report = _step(
            "compile example task",
            lambda: task_compiler.compile_task("example_wave1_climate_stack"),
            steps,
        )
        package = _step(
            "package and validate execution DAG",
            lambda: task_compiler.package_task("example_wave1_climate_stack"),
            steps,
        )
        grade = _step("full system grader", system_grade.grade_system, steps)

        workfile = load_workfile(workfile_path, repository_root=root)
        recipe = load_named_recipe(root, workfile.spec.workflow_id)
        inventory_assets = discover_cached_assets(root / "outputs" / "handoffs")
        decisions = compile_asset_plan(
            recipe,
            inventory_assets,
            tuple(workfile.spec.area.bbox),
            workfile.spec.time.crop_year,
            "only",
        )
        compatible_cache = bool(decisions) and all(
            decision.action.startswith("reuse_") for decision in decisions
        )
        cached_cook_status = "SKIP_NO_COMPATIBLE_CACHE"
        if compatible_cache:
            cook_result = _step(
                "cached zero-network cook",
                lambda: fr_main(["cook", str(workfile_path), "--reuse", "only", "--no-open"]),
                steps,
            )
            if cook_result == 0:
                _step("inspect cached cook", lambda: fr_main(["inspect", "latest", "--json"]), steps)
                cached_cook_status = "PASS"
            else:
                cached_cook_status = "FAIL"

        if run_tests:
            test_environment = dict(environment)
            test_environment.pop("FASTERRASTER_HANDOFF_ROOT", None)
            test_environment.pop("FASTERRASTER_AG_CACHE_ROOT", None)
            _step(
                "complete pytest suite",
                lambda: _subprocess([sys.executable, "-m", "pytest", "-q"], root, test_environment),
                steps,
            )

        contract = {
            "compile_determinism_status": (compile_report or {}).get("determinism_status"),
            "compile_manifest_rows": (compile_report or {}).get("manifest_row_count"),
            "package_dag_validation_status": (package or {}).get("dag_validation_status"),
            "package_contract_sha256": (package or {}).get("execution_package_contract_sha256"),
            "grader_score": (grade or {}).get("overall_score"),
            "grader_decision": (grade or {}).get("release_decision"),
        }

    (
        task_compiler.TASK_COMPILE_ROOT,
        task_compiler.EXECUTION_PACKAGE_ROOT,
        system_grade.SYSTEM_GRADE_DIR,
        system_grade.RUN_ROOT,
        system_grade.MATERIALIZATION_ROOT,
        static_http_range.DEFAULT_REPORT_DIR,
        system_grade.preview_alpha2.SOURCE_REPORT_ROOT,
    ) = original_report_roots
    for key, value in original_environment.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    after = _tracked_report_hashes(root)
    changed_reports = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
    for step in steps:
        if step["status"] != "PASS":
            failures.append(f"{step['name']}: {step['error']}")
    if changed_reports:
        failures.append("tracked reports changed: " + ", ".join(changed_reports))
    if contract["compile_determinism_status"] != "PASS":
        failures.append("compile determinism contract did not pass")
    if contract["package_dag_validation_status"] != "PASS":
        failures.append("execution DAG contract did not pass")
    if contract["grader_decision"] not in {"release_ready", "release_ready_with_cautions"}:
        failures.append("system grader did not return a release-ready offline decision")
    report = {
        "schema_version": "fasterraster.beta-check/v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "final_status": "PASS" if not failures else "FAIL",
        "runtime": {
            "python_executable": sys.executable,
            "python_version": sys.version.split()[0],
        },
        "contract_verification": contract,
        "cached_cook": {
            "compatible_local_handoff_available": compatible_cache,
            "status": cached_cook_status,
        },
        "tracked_reports_unchanged": not changed_reports,
        "changed_tracked_reports": changed_reports,
        "inventory_summary": inventory["summary"],
        "steps": steps,
        "failures": failures,
    }
    (output / "beta_check.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_markdown(output / "beta_check.md", report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fr-beta-check")
    parser.add_argument("--root", type=Path, default=repository_root())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--skip-tests", action="store_true", help="developer-only fast path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    output = args.output or root / "outputs" / "beta_gate_1" / "latest"
    report = run_beta_check(root=root, output=output.resolve(), run_tests=not args.skip_tests)
    print(f"Beta check: {report['final_status']}")
    print(f"Report: {output.resolve() / 'beta_check.json'}")
    return 0 if report["final_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
