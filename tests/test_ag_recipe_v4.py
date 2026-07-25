from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from faster_raster.ag_recipes import (
    AgriculturalRecipeV3,
    AgriculturalRecipeV4,
    HybridClassificationSpec,
    load_named_recipe,
)
from faster_raster.workfiles import WorkfileSpec


ROOT = Path(__file__).resolve().parents[1]
V3_PATH = ROOT / "recipes/ag/naip_cdl_classification_audit.json"
V4_PATH = (
    ROOT / "recipes/ag/naip_cdl_index_hybrid_classification_audit.json"
)


def _raw_v4() -> dict:
    return json.loads(V4_PATH.read_text(encoding="utf-8"))


def test_builtin_v4_recipe_is_complete_and_v3_is_unchanged() -> None:
    v3 = load_named_recipe(ROOT, "naip_cdl_classification_audit")
    v4 = load_named_recipe(
        ROOT, "naip_cdl_index_hybrid_classification_audit"
    )
    assert isinstance(v3, AgriculturalRecipeV3)
    assert isinstance(v4, AgriculturalRecipeV4)
    assert v3.model_dump(mode="json", exclude_none=True) == json.loads(
        V3_PATH.read_text(encoding="utf-8")
    )
    assert v4.classification.general.requested_class_count == 6
    assert v4.classification.general.class_codes == (1, 2, 3, 4, 5, 6)
    assert v4.classification.specialists.requested_class_count == 2
    assert {
        item.output_code for item in v4.classification.specialists.classes
    } == {20, 21}
    assert {
        item.index_id
        for item in v4.classification.indices
        if item.persist
    } == {"ndvi", "gndvi", "green_nir_water_proxy"}


@pytest.mark.parametrize(
    ("mutator", "message"),
    (
        (
            lambda raw: raw["classification"]["general"].update(
                requested_class_count=5
            ),
            "requested general class count",
        ),
        (
            lambda raw: raw["classification"]["specialists"].update(
                requested_class_count=1
            ),
            "requested specialist class count",
        ),
        (
            lambda raw: raw["classification"]["specialists"]["classes"][1].update(
                output_code=20
            ),
            "specialist output codes",
        ),
        (
            lambda raw: raw["classification"]["specialists"]["classes"][1].update(
                eligible_parent_general_classes=["unknown_parent"]
            ),
            "eligible_parent_general_classes",
        ),
        (
            lambda raw: raw["classification"]["specialists"]["classes"][0][
                "strategy"
            ]["conditions"][0].update(index_id="undeclared"),
            "undeclared index",
        ),
        (
            lambda raw: raw["classification"]["indices"][0].update(
                persist=False
            ),
            "must be persisted",
        ),
    ),
)
def test_v4_rejects_incoherent_class_and_index_contracts(
    mutator,
    message: str,
) -> None:
    raw = _raw_v4()
    mutator(raw)
    with pytest.raises(ValidationError, match=message):
        AgriculturalRecipeV4.model_validate(raw)


def test_automatic_selection_requires_explicit_authorization_and_evidence() -> None:
    raw = _raw_v4()
    specialists = raw["classification"]["specialists"]
    specialists["selection_mode"] = "automatic"
    specialists["search"]["candidate_indices"] = ["ndvi", "gndvi"]
    with pytest.raises(ValidationError, match="explicit authorization"):
        AgriculturalRecipeV4.model_validate(raw)

    specialists["automatic_authorized"] = True
    with pytest.raises(ValidationError, match="CDL weak labels"):
        AgriculturalRecipeV4.model_validate(raw)

    for specialist in specialists["classes"]:
        specialist["calibration"]["source"] = "cdl_weak_labels"
        specialist["calibration"]["positive_general_classes"] = list(
            specialist["eligible_parent_general_classes"]
        )
    validated = AgriculturalRecipeV4.model_validate(raw)
    assert validated.classification.specialists.automatic_authorized is True


def test_recommendation_requires_bounded_candidate_indices() -> None:
    raw = _raw_v4()
    specialists = raw["classification"]["specialists"]
    specialists["selection_mode"] = "recommendation"
    with pytest.raises(ValidationError, match="candidate_indices"):
        AgriculturalRecipeV4.model_validate(raw)


def test_v4_rejects_duplicate_rules_points_and_parameterized_search() -> None:
    duplicate_rule = _raw_v4()
    conditions = duplicate_rule["classification"]["specialists"]["classes"][
        0
    ]["strategy"]["conditions"]
    conditions.append(deepcopy(conditions[0]))
    with pytest.raises(ValidationError, match="must not contain duplicates"):
        AgriculturalRecipeV4.model_validate(duplicate_rule)

    duplicate_point = _raw_v4()
    calibration = duplicate_point["classification"]["specialists"][
        "classes"
    ][0]["calibration"]
    calibration.update(
        source="user_points",
        minimum_positive_support=1,
        minimum_negative_support=1,
        points=[
            {
                "longitude": -83.05,
                "latitude": 40.05,
                "class_id": "vigorous_vegetation_candidate",
            },
            {
                "longitude": -83.05,
                "latitude": 40.05,
                "class_id": "background",
            },
        ],
    )
    with pytest.raises(
        ValidationError,
        match="duplicate or contradictory calibration coordinates",
    ):
        AgriculturalRecipeV4.model_validate(duplicate_point)

    parameterized = _raw_v4()
    specialists = parameterized["classification"]["specialists"]
    specialists["selection_mode"] = "recommendation"
    specialists["search"]["candidate_indices"] = ["normalized_difference"]
    for specialist in specialists["classes"]:
        specialist["calibration"]["source"] = "cdl_weak_labels"
        specialist["calibration"]["positive_general_classes"] = list(
            specialist["eligible_parent_general_classes"]
        )
    with pytest.raises(
        ValidationError,
        match="explicit parameterized specialist contract",
    ):
        AgriculturalRecipeV4.model_validate(parameterized)


def test_point_estimated_target_is_not_allowed_in_learned_selection() -> None:
    raw = _raw_v4()
    specialists = raw["classification"]["specialists"]
    specialists.update(
        selection_mode="automatic",
        automatic_authorized=True,
    )
    specialists["search"]["candidate_indices"] = ["ndvi"]
    target = specialists["classes"][0]
    target["strategy"] = {
        "type": "target_signature_similarity",
        "target_bands": {"red": 0.0, "nir": 0.0},
        "weights": {},
        "threshold": 0.8,
        "target_source": "positive_calibration_points",
    }
    target["calibration"].update(
        source="user_points",
        minimum_positive_support=1,
        minimum_negative_support=1,
        points=[
            {
                "longitude": -83.05,
                "latitude": 40.05,
                "class_id": target["class_id"],
            },
            {
                "longitude": -83.04,
                "latitude": 40.04,
                "class_id": "background",
            },
        ],
    )
    other = specialists["classes"][1]
    other["calibration"]["source"] = "cdl_weak_labels"
    other["calibration"]["positive_general_classes"] = list(
        other["eligible_parent_general_classes"]
    )
    with pytest.raises(
        ValidationError,
        match="outer holdout isolated",
    ):
        AgriculturalRecipeV4.model_validate(raw)


def test_custom_expression_is_validated_at_recipe_load() -> None:
    raw = _raw_v4()
    raw["classification"]["indices"].append(
        {
            "index_id": "custom_ratio",
            "expression": "normalized_difference(nir, red)",
            "persist": True,
            "display": False,
        }
    )
    condition = raw["classification"]["specialists"]["classes"][1][
        "strategy"
    ]["condition"]
    condition["index_id"] = "custom_ratio"
    validated = AgriculturalRecipeV4.model_validate(raw)
    request = validated.classification.indices[-1]
    assert request.index_id == "custom_ratio"

    invalid = deepcopy(raw)
    invalid["classification"]["indices"][-1][
        "expression"
    ] = "__import__('os').system('unsafe')"
    with pytest.raises(ValidationError, match="arbitrary function"):
        AgriculturalRecipeV4.model_validate(invalid)


def test_v1_workfile_accepts_hybrid_override_only_for_v4_workflow() -> None:
    classification = HybridClassificationSpec.model_validate(
        _raw_v4()["classification"]
    )
    base = {
        "schema_version": "fasterraster.work/v1",
        "name": "hybrid-test",
        "workflow": "naip-cdl-index-hybrid-classification-audit",
        "area": {"bbox": [-83.1, 40.0, -83.0, 40.1]},
        "time": {
            "start": "2023-01-01",
            "end": "2023-12-31",
            "crop_year": 2023,
        },
        "classification": classification.model_dump(mode="json"),
    }
    validated = WorkfileSpec.model_validate(base)
    assert validated.workflow_id == (
        "naip_cdl_index_hybrid_classification_audit"
    )
    assert validated.classification is not None

    base["workflow"] = "naip-cdl-classification-audit"
    with pytest.raises(ValidationError, match="only by the index-guided"):
        WorkfileSpec.model_validate(base)
