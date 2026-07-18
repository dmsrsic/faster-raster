from __future__ import annotations

from pathlib import Path

from faster_raster.ag_assets import AssetDecision
from faster_raster.ag_recipes import load_named_recipe
from faster_raster.beta_check import _tracked_report_hashes, run_beta_check
from faster_raster.local_diagnostics import recommend_execution
from faster_raster.local_paths import resolve_local_paths
from faster_raster.study_planning import compile_study_plan
from faster_raster.workfiles import load_workfile


ROOT = Path(__file__).resolve().parent.parent


def test_remote_failure_does_not_block_compatible_local_reuse(tmp_path, monkeypatch):
    workfile_path = ROOT / "examples" / "colby-study.fr.md"
    workfile = load_workfile(workfile_path, repository_root=ROOT)
    recipe = load_named_recipe(ROOT, workfile.spec.workflow_id)
    paths = resolve_local_paths(
        tmp_path,
        environ={
            "FASTERRASTER_CONFIG_HOME": str(tmp_path / "config"),
            "FASTERRASTER_STATE_HOME": str(tmp_path / "state"),
            "FASTERRASTER_CACHE_HOME": str(tmp_path / "cache"),
            "FASTERRASTER_TEMP_HOME": str(tmp_path / "temp"),
        },
        home=tmp_path,
    )

    def unavailable_resolution(*args, **kwargs):
        decisions = []
        for asset in recipe.required_assets:
            decisions.append(
                {
                    "logical_asset": asset,
                    "display_name": asset,
                    "candidates_considered": [
                        {"source_id": f"remote_{asset}", "capability_status": "timeout"}
                    ],
                    "candidates_rejected": [],
                    "selected_source": None,
                    "selected_capability_status": None,
                    "selected_fallback": False,
                    "provisional": False,
                    "live_execution_must_revalidate": False,
                    "blocking_reason": "remote timed out",
                }
            )
        return {
            "schema_version": "fasterraster.source-resolution/v1",
            "decisions": decisions,
            "blocking": True,
        }

    def local_reuse(*args, **kwargs):
        return [
            AssetDecision(
                asset_name=asset,
                action="reuse_direct",
                reason="compatible cached asset",
                candidate=None,
                spatial_relationship="exact",
                resampling="nearest" if asset.startswith("cdl") else "bilinear",
                tolerance_degrees=1e-6,
            )
            for asset in recipe.required_assets
        ]

    monkeypatch.setattr("faster_raster.study_planning.resolve_study_sources", unavailable_resolution)
    monkeypatch.setattr("faster_raster.study_planning.discover_cached_assets", lambda *args: [])
    monkeypatch.setattr("faster_raster.study_planning.compile_asset_plan", local_reuse)
    plan = compile_study_plan(ROOT, workfile, paths, output_dir=tmp_path / "plan")

    assert plan["blocking"] is False
    assert {row["local_asset_readiness"] for row in plan["rows"]} == {"ready_exact"}
    assert {row["remote_source_status"] for row in plan["rows"]} == {"timeout"}
    assert all(row["reused"] and not row["acquired"] for row in plan["rows"])


def test_doctor_recommendations_expose_facts_candidates_limits_and_non_application():
    recommendation = recommend_execution(16, 32 * 1024**3, 100_000_000_000)

    assert recommendation["heuristic_version"] == "beta-gate-1.1"
    assert recommendation["applied"] is False
    assert recommendation["observed_facts"] == {
        "cpu_threads": 16,
        "available_memory_bytes": 32 * 1024**3,
        "available_memory_gib": 32.0,
        "cache_free_bytes": 100_000_000_000,
    }
    assert recommendation["intermediate_candidates"]["maximum_parallel_tasks"] == {
        "half_cpu_threads": 8,
        "four_gib_per_task": 8,
        "hard_safety_cap": 8,
    }
    assert recommendation["limiting_factor"]["maximum_parallel_tasks"] == [
        "four_gib_per_task",
        "half_cpu_threads",
        "hard_safety_cap",
    ]
    assert "not performance" in recommendation["safety_note"]


def test_beta_validation_does_not_mutate_tracked_reports(tmp_path):
    before = _tracked_report_hashes(ROOT)
    report = run_beta_check(root=ROOT, output=tmp_path / "beta", run_tests=False)
    after = _tracked_report_hashes(ROOT)

    assert report["final_status"] == "PASS"
    assert report["tracked_reports_unchanged"] is True
    assert report["changed_tracked_reports"] == []
    assert after == before
