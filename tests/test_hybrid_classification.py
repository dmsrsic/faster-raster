from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest
from affine import Affine

from faster_raster.ag_recipes import (
    AgriculturalRecipeV4,
    CalibrationPointSpec,
    CandidateSearchBoundsSpec,
    HybridArbitrationSpec,
    IndexConditionSpec,
    MultiIndexBooleanStrategy,
    MultiIndexWeightedStrategy,
    SingleIndexThresholdStrategy,
    SpecialistClassSpec,
    TargetSignatureStrategy,
)
from faster_raster.hybrid_classification import (
    IndexArray,
    arbitrate_hybrid,
    evaluate_condition,
    evaluate_specialist,
    generate_bounded_candidates,
    nested_spatial_select,
    recommendation_outcome,
    validate_calibration_points,
)


ROOT = Path(__file__).resolve().parents[1]
V4_PATH = (
    ROOT / "recipes/ag/naip_cdl_index_hybrid_classification_audit.json"
)


def _recipe() -> AgriculturalRecipeV4:
    return AgriculturalRecipeV4.model_validate_json(
        V4_PATH.read_text(encoding="utf-8")
    )


def _specialist(index: int = 0) -> SpecialistClassSpec:
    return _recipe().classification.specialists.classes[index]


@pytest.mark.parametrize(
    ("condition", "expected"),
    (
        (
            IndexConditionSpec(
                index_id="test", direction="high", threshold=0.5
            ),
            [[False, True, True, False]],
        ),
        (
            IndexConditionSpec(
                index_id="test", direction="low", threshold=0.5
            ),
            [[True, True, False, False]],
        ),
        (
            IndexConditionSpec(
                index_id="test",
                direction="range",
                minimum=0.4,
                maximum=0.8,
            ),
            [[False, True, True, False]],
        ),
    ),
)
def test_condition_directions_and_invalid_mask(condition, expected) -> None:
    index = IndexArray(
        np.array([[0.2, 0.5, 0.8, 0.9]], dtype=np.float32),
        np.array([[True, True, True, False]]),
    )
    candidate, margin = evaluate_condition(index, condition)
    assert candidate.tolist() == expected
    assert np.isnan(margin[0, 3])


@pytest.mark.parametrize(
    ("operator", "k", "expected"),
    (
        ("all", None, [[False, True, False]]),
        ("any", None, [[True, True, True]]),
        ("at_least_k", 2, [[False, True, False]]),
    ),
)
def test_boolean_all_any_and_at_least_k(operator, k, expected) -> None:
    raw = _specialist().model_dump(mode="json")
    raw["strategy"] = {
        "type": "multi_index_boolean",
        "operator": operator,
        "conditions": [
            {"index_id": "ndvi", "direction": "high", "threshold": 0.5},
            {"index_id": "gndvi", "direction": "high", "threshold": 0.5},
        ],
    }
    if k is not None:
        raw["strategy"]["k"] = k
    specialist = SpecialistClassSpec.model_validate(raw)
    indices = {
        "ndvi": IndexArray(
            np.array([[0.9, 0.9, 0.1]], dtype=np.float32),
            np.ones((1, 3), dtype=bool),
        ),
        "gndvi": IndexArray(
            np.array([[0.1, 0.9, 0.9]], dtype=np.float32),
            np.ones((1, 3), dtype=bool),
        ),
    }
    general = np.array([[1, 1, 5]], dtype=np.uint8)
    result = evaluate_specialist(specialist, general, indices)
    assert result.candidate.tolist() == expected
    np.testing.assert_allclose(result.score, [[0.5, 1.0, 0.5]])
    assert "not a probability" in result.score_semantics


def test_weighted_score_normalizes_inputs_before_combining() -> None:
    raw = _specialist().model_dump(mode="json")
    raw["strategy"] = {
        "type": "multi_index_weighted_score",
        "inputs": [
            {
                "index_id": "ndvi",
                "normalization_minimum": -1,
                "normalization_maximum": 1,
                "weight": 0.75,
            },
            {
                "index_id": "gndvi",
                "normalization_minimum": 0,
                "normalization_maximum": 100,
                "weight": 0.25,
            },
        ],
        "intercept": 0,
        "direction": "high",
        "threshold": 0.6,
        "weights_source": "user_provided",
    }
    specialist = SpecialistClassSpec.model_validate(raw)
    result = evaluate_specialist(
        specialist,
        np.array([[1, 1]], dtype=np.uint8),
        {
            "ndvi": IndexArray(
                np.array([[1.0, -1.0]], dtype=np.float32),
                np.ones((1, 2), dtype=bool),
            ),
            "gndvi": IndexArray(
                np.array([[0.0, 100.0]], dtype=np.float32),
                np.ones((1, 2), dtype=bool),
            ),
        },
    )
    np.testing.assert_allclose(result.score, [[0.75, 0.25]])
    assert result.candidate.tolist() == [[True, False]]
    assert result.contract["strategy_contract"]["score_range"] == [0.0, 1.0]


def test_target_signature_similarity_and_parent_restriction() -> None:
    raw = _specialist().model_dump(mode="json")
    raw["strategy"] = {
        "type": "target_signature_similarity",
        "target_bands": {
            "red": 0.4,
            "green": 0.4,
            "blue": 0.4,
            "nir": 0.3,
        },
        "weights": {"green": 1.2},
        "threshold": 0.9,
        "target_source": "user_provided",
    }
    raw["calibration"]["source"] = "target_vector"
    raw["eligible_parent_general_classes"] = ["fallow_or_barren"]
    specialist = SpecialistClassSpec.model_validate(raw)
    source = {
        "red": np.array([[0.4, 0.4, 0.9]], dtype=np.float32),
        "green": np.array([[0.4, 0.4, 0.1]], dtype=np.float32),
        "blue": np.array([[0.4, 0.4, 0.2]], dtype=np.float32),
        "nir": np.array([[0.3, 0.3, 0.8]], dtype=np.float32),
    }
    result = evaluate_specialist(
        specialist,
        np.array([[2, 1, 2]], dtype=np.uint8),
        {},
        source_bands=source,
        source_valid=np.ones((1, 3), dtype=bool),
    )
    assert result.candidate_before_parent.tolist() == [[True, True, False]]
    assert result.candidate.tolist() == [[True, False, False]]
    assert "land use" not in result.score_semantics.lower()


def test_minimum_support_disables_specialist_without_erasing_evidence() -> None:
    raw = _specialist(1).model_dump(mode="json")
    raw["minimum_support_pixels"] = 3
    specialist = SpecialistClassSpec.model_validate(raw)
    result = evaluate_specialist(
        specialist,
        np.array([[6, 6]], dtype=np.uint8),
        {
            "green_nir_water_proxy": IndexArray(
                np.array([[0.2, -0.2]], dtype=np.float32),
                np.ones((1, 2), dtype=bool),
            )
        },
    )
    assert result.support_pixels == 1
    assert result.enabled is False
    assert not result.candidate.any()
    assert result.contract["support_pixels"] == 1


def _evaluation(
    specialist: SpecialistClassSpec,
    candidate: list[list[bool]],
) -> object:
    values = np.where(candidate, 1.0, 0.0).astype(np.float32)
    index_id = (
        specialist.strategy.condition.index_id
        if isinstance(specialist.strategy, SingleIndexThresholdStrategy)
        else "ndvi"
    )
    raw = specialist.model_dump(mode="json")
    raw["strategy"] = {
        "type": "single_index_threshold",
        "condition": {
            "index_id": index_id,
            "direction": "high",
            "threshold": 0.5,
        },
    }
    raw["eligible_parent_general_classes"] = [
        "cropland",
        "noncrop_vegetation",
        "water",
    ]
    spec = SpecialistClassSpec.model_validate(raw)
    general = np.array([[1, 1, 5, 6]], dtype=np.uint8)
    return evaluate_specialist(
        spec,
        general,
        {index_id: IndexArray(values, np.ones(values.shape, dtype=bool))},
    )


def test_hybrid_arbitration_preserves_general_and_records_overlap() -> None:
    first = _specialist(0)
    second_raw = _specialist(1).model_dump(mode="json")
    second_raw["eligible_parent_general_classes"] = [
        "cropland",
        "noncrop_vegetation",
        "water",
    ]
    second = SpecialistClassSpec.model_validate(second_raw)
    left = _evaluation(first, [[True, True, False, False]])
    right = _evaluation(second, [[False, True, True, False]])
    result = arbitrate_hybrid(
        np.array([[1, 1, 5, 6]], dtype=np.uint8),
        [left, right],
        _recipe().classification.arbitration,
    )
    assert result["final_classes"].tolist() == [[20, 21, 21, 6]]
    assert result["decision_state"].tolist() == [[2, 2, 2, 1]]
    overlap = result["evidence"]["overlap_matrix"]
    assert overlap[0]["pixel_count"] == 1
    assert "raw unrelated index scores are never compared" in result[
        "evidence"
    ]["winner_reason"]


def test_equal_priority_overlap_can_be_unresolved() -> None:
    first_raw = _specialist(0).model_dump(mode="json")
    second_raw = _specialist(1).model_dump(mode="json")
    first_raw["priority"] = second_raw["priority"] = 100
    first = _evaluation(
        SpecialistClassSpec.model_validate(first_raw),
        [[True, False, False, False]],
    )
    second = _evaluation(
        SpecialistClassSpec.model_validate(second_raw),
        [[True, False, False, False]],
    )
    arbitration = HybridArbitrationSpec(
        policy="priority_then_class_code",
        equal_priority_tie="mark_unresolved",
        unresolved_code=255,
        preserve_general_output=True,
        compare_unscaled_scores=False,
    )
    result = arbitrate_hybrid(
        np.array([[1, 1, 5, 6]], dtype=np.uint8),
        [first, second],
        arbitration,
    )
    assert result["final_classes"][0, 0] == 255
    assert result["decision_state"][0, 0] == 3
    assert result["evidence"]["unresolved_pixels"] == 1


def test_calibration_points_are_private_deterministic_and_validated() -> None:
    aoi = {
        "type": "Polygon",
        "coordinates": [
            [
                [-83.1, 40.0],
                [-83.0, 40.0],
                [-83.0, 40.1],
                [-83.1, 40.1],
                [-83.1, 40.0],
            ]
        ],
    }
    points = [
        CalibrationPointSpec(
            longitude=-83.08, latitude=40.08, class_id="target"
        ),
        CalibrationPointSpec(
            longitude=-83.02, latitude=40.02, class_id="background"
        ),
    ]
    kwargs = {
        "aoi_epsg_4326": aoi,
        "raster_crs": "EPSG:4326",
        "raster_transform": Affine(0.01, 0, -83.1, 0, -0.01, 40.1),
        "width": 10,
        "height": 10,
        "valid_mask": np.ones((10, 10), dtype=bool),
    }
    first = validate_calibration_points(points, **kwargs)
    second = validate_calibration_points(points, **kwargs)
    assert first == second
    assert first["raw_coordinates_published"] is False
    public_text = json.dumps(first)
    assert "-83.08" not in public_text
    assert "40.08" not in public_text

    duplicate = [points[0], points[0].model_copy(update={"class_id": "other"})]
    with pytest.raises(ValueError, match="duplicate calibration coordinates"):
        validate_calibration_points(duplicate, **kwargs)
    outside = [
        CalibrationPointSpec(
            longitude=-82.9, latitude=40.05, class_id="target"
        )
    ]
    with pytest.raises(ValueError, match="outside the analysis AOI"):
        validate_calibration_points(outside, **kwargs)
    invalid_kwargs = dict(kwargs)
    invalid_kwargs["valid_mask"] = np.zeros((10, 10), dtype=bool)
    with pytest.raises(ValueError, match="source-invalid"):
        validate_calibration_points([points[0]], **invalid_kwargs)


def test_candidate_generation_is_bounded_and_deterministic() -> None:
    bounds = CandidateSearchBoundsSpec(
        candidate_indices=["ndvi", "gndvi", "vari", "brightness"],
        maximum_pairs=2,
        maximum_triples=1,
        maximum_candidate_models=6,
    )
    first = generate_bounded_candidates(
        ["vari", "ndvi", "brightness", "gndvi"],
        bounds,
    )
    second = generate_bounded_candidates(
        ["brightness", "gndvi", "ndvi", "vari"],
        bounds,
    )
    assert first == second
    assert len(first) == 6
    assert [item.complexity for item in first] == [1, 1, 1, 1, 2, 2]


def test_nested_spatial_selection_is_deterministic_and_holds_outer_fold() -> None:
    folds = np.repeat(np.arange(5), 20)
    target = np.tile(np.array([False] * 10 + [True] * 10), 5)
    signal = target.astype(np.float64)
    signal += np.tile(np.linspace(-0.08, 0.08, 20), 5)
    weak = np.tile(np.array([0.0, 1.0] * 10), 5)
    bounds = CandidateSearchBoundsSpec(
        candidate_indices=["ndvi", "gndvi"],
        maximum_pairs=1,
        maximum_triples=0,
        maximum_candidate_models=3,
        maximum_calibration_samples=200,
        inner_spatial_folds=3,
        minimum_selection_metric=0.5,
        minimum_complexity_improvement=0.05,
    )
    kwargs = {
        "index_values": {"ndvi": signal, "gndvi": weak},
        "target": target,
        "spatial_folds": folds,
        "outer_holdout_fold": 0,
        "bounds": bounds,
        "automatic_authorized": True,
    }
    first = nested_spatial_select(**kwargs)
    second = nested_spatial_select(**kwargs)
    assert first == second
    assert first["status"] == "SELECTED"
    assert first["selected"]["candidate_id"] == "ndvi"
    assert first["outer_holdout"]["used_for_candidate_selection"] is False
    assert first["outer_holdout"]["sample_count"] == 20
    assert first["candidate_count"] <= 3
    with pytest.raises(ValueError, match="explicitly authorized"):
        nested_spatial_select(**{**kwargs, "automatic_authorized": False})


def test_outer_only_signal_cannot_change_candidate_selection() -> None:
    folds = np.repeat(np.arange(5), 40)
    target = np.tile(
        np.array([False] * 20 + [True] * 20),
        5,
    )
    selection = folds != 0
    inner_signal = np.where(target, 0.9, 0.1).astype(float)
    outer_only = np.tile(np.array([0.0, 1.0] * 20), 5)
    inner_signal[~selection] = np.tile(
        np.array([0.0, 1.0] * 20),
        1,
    )
    outer_only[~selection] = np.where(
        target[~selection],
        0.9,
        0.1,
    )
    bounds = CandidateSearchBoundsSpec(
        candidate_indices=["gndvi", "ndvi"],
        maximum_pairs=0,
        maximum_triples=0,
        maximum_candidate_models=2,
        maximum_calibration_samples=500,
        inner_spatial_folds=3,
        minimum_selection_metric=0.5,
        tie_tolerance=1e-9,
    )
    first = nested_spatial_select(
        {"gndvi": outer_only, "ndvi": inner_signal},
        target,
        folds,
        outer_holdout_fold=0,
        bounds=bounds,
        automatic_authorized=True,
    )
    altered_inner = inner_signal.copy()
    altered_outer = outer_only.copy()
    altered_inner[~selection] = np.where(
        target[~selection],
        0.0,
        1.0,
    )
    altered_outer[~selection] = np.where(
        target[~selection],
        -1000.0,
        1000.0,
    )
    second = nested_spatial_select(
        {"gndvi": altered_outer, "ndvi": altered_inner},
        target,
        folds,
        outer_holdout_fold=0,
        bounds=bounds,
        automatic_authorized=True,
    )
    assert first["selected"]["candidate_id"] == "ndvi"
    assert second["selected"]["candidate_id"] == "ndvi"
    assert first["candidate_ranking"] == second["candidate_ranking"]
    assert first["selected"] == second["selected"]
    assert (
        first["outer_holdout"]["metrics"]
        != second["outer_holdout"]["metrics"]
    )


def test_tie_tolerance_prefers_simpler_then_lexical_candidate() -> None:
    folds = np.repeat(np.arange(5), 20)
    target = np.tile(np.array([False] * 10 + [True] * 10), 5)
    identical = target.astype(float)
    bounds = CandidateSearchBoundsSpec(
        candidate_indices=["ndvi", "gndvi"],
        maximum_pairs=1,
        maximum_triples=0,
        maximum_candidate_models=3,
        maximum_calibration_samples=200,
        inner_spatial_folds=3,
        minimum_selection_metric=0.5,
        minimum_complexity_improvement=0.0,
        tie_tolerance=1e-6,
    )
    result = nested_spatial_select(
        {"ndvi": identical, "gndvi": identical},
        target,
        folds,
        outer_holdout_fold=0,
        bounds=bounds,
        automatic_authorized=True,
    )
    assert result["selected"]["candidate_id"] == "gndvi"
    assert result["selected"]["complexity"] == 1
    assert result["tie_tolerance"] == 1e-6
    assert "within tie_tolerance" in result["tie_break_rule"]


def test_automatic_selection_stops_when_no_candidate_meets_guard() -> None:
    folds = np.repeat(np.arange(5), 20)
    target = np.tile(np.array([False] * 10 + [True] * 10), 5)
    values = np.tile(np.arange(20) % 2, 5).astype(float)
    bounds = CandidateSearchBoundsSpec(
        candidate_indices=["ndvi"],
        maximum_pairs=0,
        maximum_triples=0,
        maximum_candidate_models=1,
        maximum_calibration_samples=200,
        inner_spatial_folds=3,
        minimum_selection_metric=0.99,
    )
    result = nested_spatial_select(
        {"ndvi": values},
        target,
        folds,
        outer_holdout_fold=0,
        bounds=bounds,
        automatic_authorized=True,
    )
    assert result["status"] == "NO_CANDIDATE_MEETS_GUARD"


def test_recommendation_noninteractive_never_finalizes() -> None:
    ranking = [
        {"candidate_id": "gndvi", "selection_metric": 0.7, "complexity": 1},
        {"candidate_id": "ndvi", "selection_metric": 0.8, "complexity": 1},
    ]
    awaiting = recommendation_outcome(ranking, interactive=False)
    assert awaiting["status"] == "AWAITING_INDEX_SELECTION"
    assert awaiting["finalized"] is False
    assert awaiting["prompted"] is False
    accepted = recommendation_outcome(
        ranking,
        interactive=True,
        accepted_candidate_id="ndvi",
    )
    assert accepted["status"] == "SELECTED"
    assert accepted["selected"]["candidate_id"] == "ndvi"
    rejected = recommendation_outcome(ranking, interactive=True)
    assert rejected["status"] == "INDEX_SELECTION_CANCELLED"
