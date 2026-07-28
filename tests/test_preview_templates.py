from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from faster_raster import preview_contracts
from faster_raster.preview_templates import (
    get_template,
    load_registry,
    template_catalog,
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
