from __future__ import annotations

import json
from pathlib import Path

import pytest

from faster_raster.ag_assets import AssetDecision
from faster_raster.ag_execution import (
    SelectionReviewReady,
    handoff_transaction,
)
from faster_raster.contract_repair import PromptSession
from faster_raster.fr_cli import (
    _interactive_recommendation_selector,
    main,
)
from faster_raster.local_paths import resolve_local_paths
from faster_raster.study_planning import compile_study_plan
from faster_raster.study_templates import (
    list_study_templates,
    render_study_template,
)
from faster_raster.workfiles import load_workfile


ROOT = Path(__file__).resolve().parents[1]


def _paths(tmp_path: Path):
    return resolve_local_paths(
        tmp_path,
        environ={
            "FASTERRASTER_CONFIG_HOME": str(tmp_path / "config"),
            "FASTERRASTER_STATE_HOME": str(tmp_path / "state"),
            "FASTERRASTER_CACHE_HOME": str(tmp_path / "cache"),
            "FASTERRASTER_TEMP_HOME": str(tmp_path / "temp"),
        },
        home=tmp_path,
    )


def _offline_local_plan(monkeypatch) -> None:
    def resolution(workfile, required_assets, *args, **kwargs):
        return {
            "schema_version": "fasterraster.source-resolution/v1",
            "decisions": [
                {
                    "logical_asset": asset,
                    "display_name": asset,
                    "candidates_considered": [],
                    "candidates_rejected": [],
                    "selected_source": None,
                    "selected_capability_status": None,
                    "selected_fallback": False,
                    "provisional": False,
                    "live_execution_must_revalidate": False,
                    "blocking_reason": "remote evidence not needed for local fixture",
                }
                for asset in required_assets
            ],
            "blocking": True,
        }

    def decisions(recipe, *args, **kwargs):
        return [
            AssetDecision(
                asset_name=asset,
                action="reuse_direct",
                reason="synthetic compatible local asset",
                candidate=None,
                spatial_relationship="exact",
                resampling=(
                    "nearest" if asset == "cdl_classes" else "bilinear"
                ),
                tolerance_degrees=1e-6,
            )
            for asset in recipe.required_assets
        ]

    monkeypatch.setattr(
        "faster_raster.study_planning.resolve_study_sources",
        resolution,
    )
    monkeypatch.setattr(
        "faster_raster.study_planning.discover_cached_assets",
        lambda *args: [],
    )
    monkeypatch.setattr(
        "faster_raster.study_planning.compile_asset_plan",
        decisions,
    )


def test_hybrid_template_is_deterministic_configurable_and_valid(
    tmp_path: Path,
) -> None:
    template_id = "ag-naip-index-hybrid-classification"
    assert template_id in {
        item["template_id"] for item in list_study_templates()
    }
    first = render_study_template(
        template_id,
        name="hybrid-demo",
        bbox=(-83.1, 40.0, -83.0, 40.1),
        years=(2023,),
    )
    assert first == render_study_template(
        template_id,
        name="hybrid-demo",
        bbox=(-83.1, 40.0, -83.0, 40.1),
        years=(2023,),
    )
    path = tmp_path / "hybrid.fr.md"
    path.write_text(first, encoding="utf-8")
    workfile = load_workfile(path, repository_root=ROOT)
    assert workfile.spec.workflow_id == (
        "naip_cdl_index_hybrid_classification_audit"
    )
    assert workfile.spec.classification is not None
    assert workfile.spec.classification.general.requested_class_count == 6
    assert (
        workfile.spec.classification.specialists.requested_class_count == 2
    )


def test_hybrid_plan_exposes_indices_bands_outputs_and_search_bounds(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _offline_local_plan(monkeypatch)
    path = tmp_path / "hybrid.fr.md"
    path.write_text(
        render_study_template(
            "ag-naip-index-hybrid-classification",
            name="hybrid-plan",
        ).replace("candidate_indices: []", "candidate_indices: [vari]"),
        encoding="utf-8",
    )
    workfile = load_workfile(path, repository_root=ROOT)
    plan = compile_study_plan(
        ROOT,
        workfile,
        _paths(tmp_path),
        output_dir=tmp_path / "plan",
    )
    hybrid = plan["index_guided_hybrid"]
    assert plan["blocking"] is False
    assert hybrid["capability_failures"] == []
    assert {
        item["index_id"] for item in hybrid["requested_indices"]
    } == {"ndvi", "gndvi", "green_nir_water_proxy", "vari"}
    candidate_only = next(
        item
        for item in hybrid["requested_indices"]
        if item["index_id"] == "vari"
    )
    assert candidate_only["required_by_candidate_search"] is True
    assert candidate_only["selection_candidate_only"] is True
    assert "vari" in hybrid["indices_to_persist"]
    assert "data/indices/vari.cog.tif" in hybrid["expected_output_rasters"]
    assert hybrid["source_band_capabilities"]["actual_band_order"] == [
        "red",
        "green",
        "blue",
        "nir",
    ]
    assert hybrid["general_classes"]["actual_class_count"] == 6
    assert len(hybrid["specialist_classes"]) == 2
    assert hybrid["expected_output_rasters"]
    assert hybrid["candidate_search_bounds"]["maximum_candidate_models"] == 128


def test_ndmi_workfile_override_fails_planning_before_transfer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _offline_local_plan(monkeypatch)
    text = render_study_template(
        "ag-naip-index-hybrid-classification",
        name="bad-ndmi",
    ).replace("green_nir_water_proxy", "ndmi")
    path = tmp_path / "bad-ndmi.fr.md"
    path.write_text(text, encoding="utf-8")
    workfile = load_workfile(path, repository_root=ROOT)
    plan = compile_study_plan(
        ROOT,
        workfile,
        _paths(tmp_path),
        output_dir=tmp_path / "plan",
    )
    assert plan["blocking"] is True
    failure = plan["index_guided_hybrid"]["capability_failures"][0]
    assert failure["requested_index"] == "ndmi"
    assert failure["missing_bands"] == ["swir1"]
    assert failure["source_asset"] == "naip_multispectral"


def test_hybrid_explain_reports_indices_specialists_and_bounds(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _offline_local_plan(monkeypatch)
    monkeypatch.setenv(
        "FASTERRASTER_CONFIG_HOME",
        str(tmp_path / "config"),
    )
    monkeypatch.setenv(
        "FASTERRASTER_STATE_HOME",
        str(tmp_path / "state"),
    )
    monkeypatch.setenv(
        "FASTERRASTER_CACHE_HOME",
        str(tmp_path / "cache"),
    )
    monkeypatch.setenv(
        "FASTERRASTER_TEMP_HOME",
        str(tmp_path / "temp"),
    )
    path = tmp_path / "hybrid.fr.md"
    path.write_text(
        render_study_template(
            "ag-naip-index-hybrid-classification",
            name="hybrid-explain",
        ),
        encoding="utf-8",
    )
    assert main(["explain", str(path), "--offline"]) == 0
    output = capsys.readouterr().out
    assert "Index-guided hybrid:" in output
    assert (
        "Requested indices: ndvi, gndvi, green_nir_water_proxy"
        in output
    )
    assert "Source compatibility: COMPATIBLE" in output
    assert (
        "Specialist classes: vigorous_vegetation_candidate, "
        "wet_surface_candidate" in output
    )
    assert "Candidate bound: 128 models; 100000 samples" in output


def test_indices_cli_human_and_json_are_coherent(capsys) -> None:
    assert main(["indices", "list", "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["index_count"] == 15
    ndmi = next(
        item for item in document["indices"] if item["index_id"] == "ndmi"
    )
    assert ndmi["naip_compatibility"]["missing_bands"] == ["swir1"]

    assert main(["indices", "show", "ndvi", "--json"]) == 0
    ndvi = json.loads(capsys.readouterr().out)
    assert ndvi["formula"] == "(nir - red) / (nir + red + epsilon)"
    assert ndvi["naip_compatibility"]["status"] == "COMPATIBLE"
    assert len(ndvi["registry_sha256"]) == 64

    assert main(["indices", "show", "ndvi"]) == 0
    human = capsys.readouterr().out
    assert "Formula:" in human
    assert "Raw-DN caveat:" in human
    assert "Definition / registry hash:" in human


def test_interactive_recommendation_accepts_choice_and_eof_fails_closed() -> None:
    ranking = [
        {
            "candidate_id": "ndvi",
            "selection_metric": 0.8,
            "complexity": 1,
        },
        {
            "candidate_id": "gndvi",
            "selection_metric": 0.7,
            "complexity": 1,
        },
    ]
    answers = iter(["2", "yes"])
    output: list[str] = []
    selector = _interactive_recommendation_selector(
        PromptSession(
            reader=lambda prompt: next(answers),
            writer=output.append,
        )
    )
    assert selector("vegetation", ranking) == "gndvi"
    assert any("not independent accuracy" in line for line in output)

    def eof_reader(prompt: str) -> str:
        raise EOFError

    cancelled = _interactive_recommendation_selector(
        PromptSession(reader=eof_reader, writer=lambda value: None)
    )
    assert cancelled("vegetation", ranking) is None


def test_selection_review_transaction_is_preserved_but_not_finalized(
    tmp_path: Path,
) -> None:
    final = tmp_path / "hybrid-run"
    with pytest.raises(SelectionReviewReady) as raised:
        with handoff_transaction(final) as staging:
            (staging / "manifest.json").write_text(
                json.dumps(
                    {
                        "operation_status": "AWAITING_INDEX_SELECTION",
                        "finalized": False,
                    }
                ),
                encoding="utf-8",
            )
            raise SelectionReviewReady(
                "AWAITING_INDEX_SELECTION",
                {"candidate_count": 3},
            )
    review = raised.value.package_path
    assert review is not None
    assert review.name.endswith("_review")
    assert not final.exists()
    manifest = json.loads((review / "manifest.json").read_text())
    assert manifest["finalized"] is False
    assert not (review / "failure_report.json").exists()
