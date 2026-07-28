from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

import yaml

from faster_raster.adapter_contract import stable_json


REGISTRY_SCHEMA_VERSION = "fasterraster.preview-template-registry/v1"
TEMPLATE_SCHEMA_VERSION = "fasterraster.preview-template/v1"
DEFAULT_REGISTRY_PATH = (
    Path(__file__).resolve().parent.parent / "configs" / "preview_templates.yaml"
)
ALLOWED_STATUSES = {"released", "experimental", "private", "planned", "unsupported"}
ALLOWED_LAYOUTS = {"grid"}


def _hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(stable_json(dict(value)).encode("utf-8")).hexdigest()


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"unable to read preview template {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("preview template document must be an object")
    return value


def load_registry(path: Path | None = None) -> dict[str, Any]:
    registry = _load_yaml(path or DEFAULT_REGISTRY_PATH)
    if registry.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        raise ValueError("unsupported preview-template registry schema")
    roles = registry.get("roles")
    templates = registry.get("templates")
    if not isinstance(roles, dict) or not roles:
        raise ValueError("preview-template registry requires roles")
    if not isinstance(templates, dict) or not templates:
        raise ValueError("preview-template registry requires templates")
    for template_id, template in templates.items():
        validate_template(template, template_id=str(template_id), roles=roles)
    stable = deepcopy(registry)
    stable["registry_sha256"] = _hash(stable)
    return stable


def validate_template(
    template: Mapping[str, Any],
    *,
    template_id: str | None = None,
    roles: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    value = dict(template)
    role_registry = dict(roles or load_registry()["roles"])
    identifier = template_id or str(value.get("template_id") or "")
    if not identifier or not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
        errors.append("template_id must contain only letters, numbers, hyphens, or underscores")
    if value.get("schema_version") != TEMPLATE_SCHEMA_VERSION:
        errors.append(f"schema_version must be {TEMPLATE_SCHEMA_VERSION}")
    if value.get("status", "experimental") not in ALLOWED_STATUSES:
        errors.append("template status is invalid")
    layout = value.get("layout")
    if not isinstance(layout, Mapping):
        errors.append("layout is required")
        layout = {}
    if layout.get("type") not in ALLOWED_LAYOUTS:
        errors.append("layout.type must be grid")
    try:
        rows = int(layout.get("rows"))
        columns = int(layout.get("columns"))
        if not 1 <= rows <= 4 or not 1 <= columns <= 4:
            errors.append("layout rows and columns must be between 1 and 4")
    except (TypeError, ValueError):
        rows = columns = 0
        errors.append("layout rows and columns must be integers")
    panels = value.get("panels")
    if not isinstance(panels, list) or not panels:
        errors.append("at least one panel is required")
        panels = []
    if rows and columns and len(panels) > rows * columns:
        errors.append("panel count exceeds layout capacity")
    panel_ids: set[str] = set()
    for index, panel in enumerate(panels):
        if not isinstance(panel, Mapping):
            errors.append(f"panel {index + 1} must be an object")
            continue
        panel_id = str(panel.get("panel_id") or "")
        if not panel_id or panel_id in panel_ids:
            errors.append(f"panel {index + 1} has a missing or duplicate panel_id")
        panel_ids.add(panel_id)
        role = str(panel.get("role") or "")
        if role not in role_registry:
            errors.append(f"panel {panel_id or index + 1} references unknown role {role!r}")
            continue
        definition = role_registry[role]
        if (
            definition.get("semantic_type") == "categorical"
            and definition.get("resampling") not in {"nearest", "mode"}
        ):
            errors.append(f"categorical role {role} uses unsafe resampling")
    for dimension in ("default_width", "default_height"):
        if dimension in value:
            try:
                parsed = int(value[dimension])
                if not 64 <= parsed <= 4096:
                    errors.append(f"{dimension} must be between 64 and 4096")
            except (TypeError, ValueError):
                errors.append(f"{dimension} must be an integer")
    source_layers = value.get("source_layers") or []
    if not isinstance(source_layers, list):
        errors.append("source_layers must be an array")
        source_layers = []
    z_orders: list[int] = []
    for layer in source_layers:
        if not isinstance(layer, Mapping) or not layer.get("source_id"):
            errors.append("every source layer requires source_id")
            continue
        if "opacity" in layer and not 0.0 <= float(layer["opacity"]) <= 1.0:
            errors.append(f"source layer {layer.get('source_id')} opacity is outside 0..1")
        if "z_order" in layer:
            z_orders.append(int(layer["z_order"]))
        if (
            layer.get("profile_id") == "categorical_overlay"
            and layer.get("resampling_method", "nearest") not in {"nearest", "mode"}
        ):
            errors.append(
                f"source layer {layer.get('source_id')} uses unsafe categorical resampling"
            )
    if len(z_orders) != len(set(z_orders)):
        warnings.append("source layer z-order contains ties; source_id is the stable tie-breaker")
    stable_template = {"template_id": identifier, **value}
    return {
        "status": "PASS" if not errors else "FAIL",
        "template_id": identifier,
        "errors": errors,
        "warnings": warnings,
        "template_sha256": _hash(stable_template) if not errors else None,
    }


def template_catalog(path: Path | None = None) -> list[dict[str, Any]]:
    registry = load_registry(path)
    result = []
    for template_id in sorted(registry["templates"]):
        template = registry["templates"][template_id]
        validation = validate_template(
            template,
            template_id=template_id,
            roles=registry["roles"],
        )
        result.append(
            {
                "template_id": template_id,
                "title": template.get("title"),
                "description": template.get("description"),
                "status": template.get("status"),
                "layout": template.get("layout"),
                "panel_roles": [item["role"] for item in template["panels"]],
                "template_sha256": validation["template_sha256"],
            }
        )
    return result


def get_template(template_id: str, path: Path | None = None) -> dict[str, Any]:
    registry = load_registry(path)
    try:
        template = deepcopy(registry["templates"][template_id])
    except KeyError as exc:
        raise ValueError(f"unknown preview template: {template_id}") from exc
    template["template_id"] = template_id
    template["template_sha256"] = validate_template(
        template,
        template_id=template_id,
        roles=registry["roles"],
    )["template_sha256"]
    return template


def template_for_task(task_id: str, path: Path | None = None) -> dict[str, Any]:
    registry = load_registry(path)
    matches = [
        template_id
        for template_id, template in registry["templates"].items()
        if task_id in (template.get("task_ids") or [])
    ]
    if len(matches) != 1:
        raise ValueError(f"unknown or ambiguously mapped preview task: {task_id}")
    return get_template(matches[0], path)


def validate_template_path(path: Path) -> dict[str, Any]:
    value = _load_yaml(path)
    if value.get("schema_version") == REGISTRY_SCHEMA_VERSION:
        try:
            registry = load_registry(path)
        except ValueError as exc:
            return {"status": "FAIL", "errors": [str(exc)], "warnings": []}
        return {
            "status": "PASS",
            "registry_sha256": registry["registry_sha256"],
            "template_count": len(registry["templates"]),
            "errors": [],
            "warnings": [],
        }
    registry = load_registry()
    return validate_template(
        value,
        template_id=str(value.get("template_id") or path.stem),
        roles=registry["roles"],
    )
