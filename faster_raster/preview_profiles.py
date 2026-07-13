from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any

from faster_raster.adapter_contract import stable_json

PROFILE_ID = "imagery_first_balanced_v1"
PROFILE_VERSION = "1.0.0-alpha.3"

_DEFAULT_PROFILE: dict[str, Any] = {
    "schema_version": 1,
    "profile_id": PROFILE_ID,
    "profile_version": PROFILE_VERSION,
    "primary_imagery_policy": {
        "opacity": 1.0,
        "z_order": 0,
        "blend_mode": "normal",
        "render_role": "primary_imagery",
        "nodata_policy": "transparent",
        "imagery_coverage_required": True,
        "real_pixels_required": True,
    },
    "imagery_enhancement_policy": {
        "profile_id": "natural_color_mild_stretch",
        "rgb_band_order": ["red", "green", "blue"],
        "percentile_range": [2, 98],
        "gamma": 0.98,
        "shadow_lift": 0.015,
        "highlight_control": 0.995,
        "saturation_multiplier": 1.02,
        "random_sampling": False,
        "over_sharpening": False,
        "false_color_claim": False,
    },
    "terrain_policy": {
        "requested_opacity": 0.12,
        "minimum_opacity": 0.10,
        "maximum_opacity": 0.18,
        "target_opacity": 0.12,
        "blend_mode": "multiply",
        "z_order": 10,
        "render_role": "terrain_context",
        "count_as_imagery": False,
        "nodata_policy": "transparent",
    },
    "categorical_policy": {
        "requested_opacity": 0.24,
        "minimum_opacity": 0.18,
        "maximum_opacity": 0.30,
        "full_coverage_threshold": 0.80,
        "medium_coverage_threshold": 0.35,
        "full_coverage_opacity": 0.20,
        "medium_coverage_opacity": 0.24,
        "sparse_coverage_opacity": 0.30,
        "resampling": "nearest",
        "blend_mode": "normal",
        "z_order": 20,
        "render_role": "thematic_overlay",
        "nodata_policy": "transparent",
        "class_aware_opacity": {
            "enabled": True,
            "emphasized_classes": [1, 5, 24, 36, 37, 41, 42, 43, 53, 54, 58, 59, 61, 176, 190, 195],
            "contextual_classes": [121, 122, 123, 124, 141, 142, 143, 152, 176],
            "muted_classes": [111, 112, 131, 190, 195],
            "transparent_classes": [0],
            "status_when_mapping_unavailable": "unavailable",
        },
    },
    "continuous_context_policy": {
        "fill_opacity": 0.0,
        "contour_opacity_range": [0.10, 0.16],
        "z_order": 30,
        "render_role": "environmental_context",
        "preferred_render_forms": ["sparse_contours", "comparison_panel", "thumbnail"],
    },
    "boundary_policy": {
        "preferred_source": "raw_categorical_class_id_grid",
        "main_composite_enabled": False,
        "diagnostic_panel_enabled": True,
        "default_opacity": 0.14,
        "maximum_opacity": 0.20,
        "width_pixels": 1,
        "render_role": "diagnostic_boundary",
        "nodata_transitions": "excluded",
    },
    "alpha_budget_policy": {
        "overlay_alpha_budget_limit": 0.42,
        "sparse_symbol_budget_separate": True,
        "adjustment_order": ["diagnostic_fills", "continuous_environmental_fills", "categorical_contextual_classes", "categorical_emphasized_classes", "terrain"],
        "never_reduce_primary_imagery": True,
    },
    "visual_authority_thresholds": {
        "primary_imagery_visible_fraction_min": 0.70,
        "imagery_contrast_retention_min": 0.65,
        "imagery_edge_retention_min": 0.65,
        "categorical_effective_coverage_max": 0.30,
        "boundary_pixel_fraction_max": 0.08,
        "overlay_alpha_budget_max": 0.42,
        "dominant_visual_role": "primary_imagery",
    },
    "comparison_panel_policy": {
        "dashboard_size": [1560, 980],
        "required_panels": ["imagery_only", "hillshade_only", "raw_cdl_palette", "naip_cdl_015", "naip_cdl_020", "naip_cdl_030", "selected_balanced_composite", "pixel_zoom"],
        "opacity_comparison_values": [0.15, 0.20, 0.30],
    },
    "legend_policy": {
        "source": "verified_cdl_class_metadata_when_lossless_mapping_available",
        "default_limit": 12,
        "order": "descending_visible_fraction_then_class_code",
        "overflow_label": "Other visible classes",
        "mapping_unavailable_text": "Categorical class mapping unavailable",
        "never_infer_class_names_from_rgb": True,
    },
}


def profile_contract_hash(profile: dict[str, Any]) -> str:
    payload = {k: v for k, v in profile.items() if k != "default_profile_contract_sha256"}
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


def imagery_first_balanced_v1() -> dict[str, Any]:
    profile = deepcopy(_DEFAULT_PROFILE)
    profile["default_profile_contract_sha256"] = profile_contract_hash(profile)
    return profile


def select_default_profile(task_id: str, *, explicit_profile_id: str | None = None, has_real_imagery: bool = True, has_thematic_overlays: bool = True) -> dict[str, Any] | None:
    if explicit_profile_id in {PROFILE_ID, "imagery_first_balanced_v1"}:
        return imagery_first_balanced_v1()
    if task_id == "example_imagery_first_balanced_stack":
        return imagery_first_balanced_v1()
    if has_real_imagery and has_thematic_overlays and explicit_profile_id is None and task_id.endswith("balanced_stack"):
        return imagery_first_balanced_v1()
    return None


def compile_categorical_opacity(coverage_fraction: float, requested_opacity: float | None = None, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = profile or imagery_first_balanced_v1()
    policy = profile["categorical_policy"]
    requested = float(requested_opacity if requested_opacity is not None else policy["requested_opacity"])
    if coverage_fraction >= policy["full_coverage_threshold"]:
        compiled = min(requested, policy["full_coverage_opacity"])
        reason = "full_coverage_reduction" if compiled < requested else "profile_default"
    elif coverage_fraction >= policy["medium_coverage_threshold"]:
        compiled = min(requested, policy["medium_coverage_opacity"])
        reason = "medium_coverage_balance"
    else:
        compiled = min(max(requested, policy["minimum_opacity"]), policy["sparse_coverage_opacity"])
        reason = "sparse_overlay_preserved" if compiled >= requested else "profile_default"
    compiled = min(max(compiled, policy["minimum_opacity"]), policy["maximum_opacity"])
    return {
        "requested_opacity": round(requested, 4),
        "compiled_opacity": round(compiled, 4),
        "effective_alpha": round(compiled * coverage_fraction, 6),
        "categorical_coverage_fraction": round(coverage_fraction, 6),
        "opacity_adjustment_reason": reason,
        "class_aware_opacity_status": "unavailable",
        "class_legend_provenance": "verified_cdl_metadata_unavailable_for_colorized_service_pixels",
    }


def compile_overlay_alpha_budget(layers: list[dict[str, Any]], profile: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = profile or imagery_first_balanced_v1()
    limit = float(profile["alpha_budget_policy"]["overlay_alpha_budget_limit"])
    compiled_layers = [dict(layer) for layer in layers]
    requested = sum(float(layer.get("requested_opacity", layer.get("opacity", 0.0))) for layer in compiled_layers if layer.get("render_role") != "primary_imagery" and layer.get("include_in_alpha_budget", True))
    priority = {"diagnostic_overlay": 0, "environmental_context": 1, "thematic_overlay": 2, "terrain_context": 3}
    def total() -> float:
        return sum(float(layer.get("compiled_opacity", layer.get("opacity", 0.0))) for layer in compiled_layers if layer.get("render_role") != "primary_imagery" and layer.get("include_in_alpha_budget", True))
    adjustments = []
    for layer in sorted(compiled_layers, key=lambda item: priority.get(item.get("render_role"), 9)):
        excess = total() - limit
        if excess <= 0:
            break
        if layer.get("render_role") == "primary_imagery" or not layer.get("include_in_alpha_budget", True):
            continue
        current = float(layer.get("compiled_opacity", layer.get("opacity", 0.0)))
        floor = 0.0 if layer.get("render_role") in {"diagnostic_overlay", "environmental_context"} else min(current, 0.18 if layer.get("render_role") == "thematic_overlay" else 0.10)
        reduction = min(excess, max(0.0, current - floor))
        if reduction > 0:
            layer["compiled_opacity"] = round(current - reduction, 6)
            layer["opacity_adjustment_reason"] = "alpha_budget_reduction"
    compiled = total()
    for layer in compiled_layers:
        if layer.get("render_role") != "primary_imagery" and layer.get("include_in_alpha_budget", True):
            adjustments.append({"source_id": layer.get("source_id"), "requested_opacity": layer.get("requested_opacity", layer.get("opacity")), "compiled_opacity": layer.get("compiled_opacity", layer.get("opacity")), "reason": layer.get("opacity_adjustment_reason", "profile_default")})
    return {"overlay_alpha_budget_limit": limit, "requested_overlay_alpha_budget": round(requested, 6), "compiled_overlay_alpha_budget": round(compiled, 6), "overlay_alpha_budget_status": "PASS" if compiled <= limit else "FAIL", "overlay_adjustments": adjustments, "compiled_layers": compiled_layers}
