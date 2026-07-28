from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from faster_raster import preview_contracts
from faster_raster.preview_templates import (
    get_template,
    load_registry,
    template_catalog,
    validate_audit_evidence,
    validate_template,
    validate_template_path,
)


ROOT = Path(__file__).resolve().parent.parent


def _allowlist() -> dict:
    return yaml.safe_load((ROOT / "configs" / "source_allowlist.yaml").read_text())


def test_registry_templates_are_valid_and_content_bound():
    first = load_registry()
    second = load_registry()
    assert first["registry_sha256"] == second["registry_sha256"]
    items = template_catalog()
    assert {item["template_id"] for item in items} >= {
        "ag_classification_audit_v1",
        "general_multisource_v1",
    }
    assert all(item["template_sha256"] for item in items)


def test_existing_preview_tasks_compile_through_template_registry():
    for task_id, template_id in (
        ("example_imagery_first_multipreview", "imagery_first_multipreview_v1"),
        ("example_imagery_first_balanced_stack", "imagery_first_balanced_stack_v1"),
    ):
        first = preview_contracts.build_render_contract(task_id, _allowlist())
        second = preview_contracts.build_render_contract(task_id, _allowlist())
        assert first["preview_template_id"] == template_id
        assert first["preview_template_contract_sha256"]
        assert first["preview_render_contract_sha256"] == second["preview_render_contract_sha256"]
        assert preview_contracts.contract_hash(first) == first["preview_render_contract_sha256"]


def test_user_template_rejects_unknown_role(tmp_path):
    value = deepcopy(get_template("general_multisource_v1"))
    value.pop("template_sha256")
    value["template_id"] = "user-template"
    value["panels"][0]["role"] = "run_python"
    path = tmp_path / "template.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    result = validate_template_path(path)
    assert result["status"] == "FAIL"
    assert any("unknown role" in item for item in result["errors"])


def test_categorical_role_rejects_non_nearest_resampling():
    registry = load_registry()
    roles = deepcopy(registry["roles"])
    roles["classification"]["resampling"] = "bilinear"
    template = get_template("ag_classification_audit_v1")
    result = validate_template(
        template,
        template_id="ag_classification_audit_v1",
        roles=roles,
    )
    assert result["status"] == "FAIL"
    assert any("unsafe resampling" in item for item in result["errors"])


def _valid_audit_evidence() -> dict:
    return {
        "panel_titles": ["Natural color", "Classification", "Confidence"],
        "legends_present": {"broad_classes", "confidence_states"},
        "explanations_present": {
            "unknown_uncertain",
            "confidence_threshold",
            "decision_states",
        },
        "class_codes": [0, 1, 2, 3, 4, 5, 6],
        "supported_class_codes": [0, 1, 2, 3, 4, 5, 6],
        "confidence_provenance": {
            "confidence_metric": "maximum_class_probability",
            "confidence_threshold": 0.6,
            "unknown_class_code": 0,
            "threshold_source": "recipe_default",
        },
        "provenance_footer": "Receipt-bound provenance.",
    }


def test_classification_audit_layout_contract_is_complete():
    result = validate_audit_evidence(
        "ag_classification_audit_v1",
        **_valid_audit_evidence(),
    )
    assert result["status"] == "PASS"
    assert result["minimum_font_size"] >= 18
    assert result["documentation_derivative"] == {
        "width": 1920,
        "height": 1080,
    }


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (
            {"legends_present": set()},
            "missing legend",
        ),
        (
            {"panel_titles": ["x" * 120]},
            "title overflow",
        ),
        (
            {"class_codes": [0, 99]},
            "unsupported class codes",
        ),
        (
            {"confidence_provenance": {}},
            "missing confidence threshold",
        ),
        (
            {"provenance_footer": ""},
            "missing provenance footer",
        ),
    ],
)
def test_classification_audit_layout_rejects_missing_evidence(
    change,
    expected,
):
    evidence = _valid_audit_evidence()
    evidence.update(change)
    result = validate_audit_evidence(
        "ag_classification_audit_v1",
        **evidence,
    )
    assert result["status"] == "FAIL"
    assert any(expected in item for item in result["errors"])
