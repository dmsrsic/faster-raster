from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

STACK_COMPOSITOR_VERSION = "0.5.10"
TYPED_VISIBILITY_MODEL_VERSION = "0.5.10"
MIN_OPACITY = 0.18
MAX_OPACITY = 0.92
VISIBILITY_FORMULA = "visibility = clamp(min_visibility, max_visibility, base_visibility / log2(log_depth + 2)); non-base overlays scaled by overlay_strength"
COMPOSITING_FORMULA = VISIBILITY_FORMULA

VISIBILITY_RULES: dict[str, dict[str, Any]] = {
    "real_base": {"base_visibility": 0.86, "min_visibility": 0.72, "max_visibility": 0.92, "blend_mode": "normal", "depth_offset": 0.0},
    "categorical_base": {"base_visibility": 0.82, "min_visibility": 0.72, "max_visibility": 0.90, "blend_mode": "normal", "depth_offset": 0.0},
    "optical_context": {"base_visibility": 0.68, "min_visibility": 0.24, "max_visibility": 0.74, "blend_mode": "screen_intent", "depth_offset": 1.0},
    "climate_signal": {"base_visibility": 0.42, "min_visibility": 0.20, "max_visibility": 0.46, "blend_mode": "screen_intent", "depth_offset": 2.0},
    "terrain_context": {"base_visibility": 0.32, "min_visibility": 0.18, "max_visibility": 0.36, "blend_mode": "multiply_intent", "depth_offset": 3.0},
    "hydrology_context": {"base_visibility": 0.50, "min_visibility": 0.22, "max_visibility": 0.54, "blend_mode": "screen_intent", "depth_offset": 2.0},
    "quality_mask": {"base_visibility": 0.36, "min_visibility": 0.18, "max_visibility": 0.40, "blend_mode": "mask_intent", "depth_offset": 3.0},
    "credential_gated_context": {"base_visibility": 0.28, "min_visibility": 0.16, "max_visibility": 0.32, "blend_mode": "outline_intent", "depth_offset": 4.0},
    "semantic_overlay": {"base_visibility": 0.30, "min_visibility": 0.18, "max_visibility": 0.35, "blend_mode": "screen_intent", "depth_offset": 3.0},
    "adapter_needed_overlay": {"base_visibility": 0.24, "min_visibility": 0.16, "max_visibility": 0.30, "blend_mode": "outline_intent", "depth_offset": 4.0},
    "warning_overlay": {"base_visibility": 0.22, "min_visibility": 0.14, "max_visibility": 0.30, "blend_mode": "mask_intent", "depth_offset": 5.0},
    "point_marker": {"base_visibility": 0.90, "min_visibility": 0.72, "max_visibility": 0.95, "blend_mode": "normal", "depth_offset": 0.5},
}

BASE_OPACITY_BY_ROLE = {role: rule["base_visibility"] for role, rule in VISIBILITY_RULES.items()}
BASE_OPACITY_BY_ROLE.update({
    "real_raster_base": 0.86,
    "categorical_raster": 0.82,
    "continuous_overlay": 0.42,
    "semantic_precip_overlay": 0.42,
    "semantic_elevation_overlay": 0.32,
    "credential_gated_scene_overlay": 0.28,
    "point_or_sample": 0.90,
    "warning_mask": 0.22,
})

SOURCE_HINTS = {
    "cdl_arcgis_tiny_export": ("categorical_raster", "real_base"),
    "prism_daily_ppt_static_zip": ("climate_overlay", "climate_signal"),
    "usgs_3dep_dem": ("hillshade_or_dem", "terrain_context"),
    "copernicus_sentinel2_l2a_cdse_stac": ("credential_gated_scene", "credential_gated_context"),
    "daymet_single_pixel_prcp_rest": ("point_or_sample", "point_marker"),
}


def clamp(value: float, min_value: float = MIN_OPACITY, max_value: float = MAX_OPACITY) -> float:
    return max(min_value, min(max_value, value))


def normalize_visibility_mode(value: str | None) -> str:
    mode = (value or "typed-log").strip().lower().replace("_", "-")
    if mode not in {"typed-log", "equal", "base-dominant"}:
        raise ValueError(f"invalid visibility_mode: {value}")
    return mode


def normalize_overlay_strength(value: float | int | None) -> float:
    strength = 1.0 if value is None else float(value)
    if not 0.25 <= strength <= 2.0:
        raise ValueError("overlay_strength must be between 0.25 and 2.0")
    return strength


def role_for_result(result: dict[str, Any]) -> str:
    source_id = result.get("source_id")
    render_kind = result.get("render_kind")
    status = result.get("status")
    if source_id in SOURCE_HINTS:
        data_type, role = SOURCE_HINTS[source_id]
        if source_id == "usgs_3dep_dem" and status == "adapter_needed":
            return "terrain_context"
        return role
    if result.get("real_raster_rendered") or render_kind == "real_raster":
        return "real_base"
    if render_kind in {"real_categorical_samples", "real_point"}:
        return "point_marker"
    if status == "adapter_needed":
        return "adapter_needed_overlay"
    if status in {"no_data_or_placeholder", "fetch_failed"}:
        return "warning_overlay"
    return "semantic_overlay"


def data_type_for_result(result: dict[str, Any], role: str) -> str:
    source_id = result.get("source_id")
    if source_id in SOURCE_HINTS:
        return SOURCE_HINTS[source_id][0]
    if role == "real_base":
        return "categorical_raster" if result.get("render_kind") == "real_raster" else "continuous_raster"
    if role == "point_marker":
        return "point_or_sample"
    if role == "adapter_needed_overlay":
        return "adapter_needed"
    if role == "warning_overlay":
        return "warning_mask"
    return "semantic_fallback"


def visible_layer_results(source_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [result for result in source_results if result.get("source_id")]


def compute_visibility(rule: dict[str, Any], log_depth: float, *, role: str, mode: str, overlay_strength: float, real_base_rendered: bool) -> float:
    base = float(rule["base_visibility"])
    min_visibility = float(rule["min_visibility"])
    max_visibility = float(rule["max_visibility"])
    is_base = role in {"real_base", "categorical_base"}
    if mode == "equal" and not is_base:
        base, min_visibility, max_visibility = 0.34, 0.18, 0.42
    elif mode == "base-dominant":
        if is_base:
            base, min_visibility = max(base, 0.90), max(min_visibility, 0.78)
        else:
            base, min_visibility, max_visibility = base * 0.72, 0.12, min(max_visibility, 0.30)
    visibility = base / math.log2(log_depth + 2)
    if not is_base:
        visibility *= overlay_strength
    visibility = clamp(visibility, min_visibility, max_visibility)
    if is_base and real_base_rendered:
        visibility = max(visibility, 0.72)
    return round(visibility, 3)


def layer_explanation(layer: dict[str, Any]) -> str:
    return (
        f"{layer['short_label']} uses {layer['visual_role']} visibility for {layer['data_type']} "
        f"at log_depth {layer['log_depth']} with {layer['blend_mode']} blend intent."
    )


def build_visual_layers(
    source_results: list[dict[str, Any]],
    visual_labels: dict[str, str] | None = None,
    *,
    visibility_mode: str = "typed-log",
    overlay_strength: float = 1.0,
    sentinel_live_summary: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    mode = normalize_visibility_mode(visibility_mode)
    strength = normalize_overlay_strength(overlay_strength)
    visual_labels = visual_labels or {}
    raw_layers = visible_layer_results(source_results)
    role_priority = {
        "real_base": 0,
        "categorical_base": 0,
        "optical_context": 1,
        "climate_signal": 2,
        "terrain_context": 3,
        "hydrology_context": 4,
        "quality_mask": 5,
        "credential_gated_context": 6,
        "semantic_overlay": 7,
        "adapter_needed_overlay": 8,
        "warning_overlay": 9,
        "point_marker": 10,
    }
    layers = sorted(raw_layers, key=lambda result: (role_priority.get(role_for_result(result), 99), raw_layers.index(result)))
    real_base_rendered = any(result.get("real_raster_rendered") for result in layers)
    visual_layers: list[dict[str, Any]] = []
    for index, result in enumerate(layers):
        source_id = result["source_id"]
        role = role_for_result(result)
        data_type = data_type_for_result(result, role)
        if source_id == "copernicus_sentinel2_l2a_cdse_stac" and sentinel_live_summary and sentinel_live_summary.get("sentinel_stac_live_result_present"):
            data_type = "credential_gated_scene"
            role = "credential_gated_context"
            status = "stac_discovered_no_pixels"
            render_kind = "stac_metadata_context"
        else:
            status = result.get("status")
            render_kind = result.get("render_kind")
        rule = VISIBILITY_RULES.get(role, VISIBILITY_RULES["semantic_overlay"])
        log_depth = round(index + float(rule.get("depth_offset", 0.0)), 3)
        visibility = compute_visibility(rule, log_depth, role=role, mode=mode, overlay_strength=strength, real_base_rendered=real_base_rendered)
        layer = {
            "source_id": source_id,
            "short_label": visual_labels.get(source_id, source_id),
            "visual_label": visual_labels.get(source_id, source_id),
            "data_type": data_type,
            "visual_role": role,
            "role": role,
            "render_kind": render_kind,
            "status": status,
            "z_order": index,
            "base_visibility": rule["base_visibility"],
            "min_visibility": rule["min_visibility"],
            "max_visibility": rule["max_visibility"],
            "log_depth": log_depth,
            "opacity": visibility,
            "visibility_pct": round(visibility * 100),
            "transparency_pct": round((1.0 - visibility) * 100),
            "blend_mode": rule["blend_mode"],
        }
        layer["explanation"] = layer_explanation(layer)
        visual_layers.append(layer)
    return visual_layers


def opacity_ledger_lines(plan: list[dict[str, Any]]) -> list[str]:
    return [
        f"{item.get('short_label') or item.get('visual_label') or item.get('source_id')} {item['visual_role']} visible {item['visibility_pct']}% transparent {item['transparency_pct']}% {item['status']}"
        for item in plan
    ]


def visibility_by_data_type(layers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[float]] = {}
    for layer in layers:
        grouped.setdefault(layer["data_type"], []).append(layer["opacity"])
    return {
        data_type: {
            "layer_count": len(values),
            "mean_visibility_pct": round(sum(values) / len(values) * 100),
            "min_visibility_pct": round(min(values) * 100),
            "max_visibility_pct": round(max(values) * 100),
        }
        for data_type, values in grouped.items()
    }


def compute_stack_opacity_plan(
    source_results: list[dict[str, Any]],
    visual_labels: dict[str, str] | None = None,
    *,
    visibility_mode: str = "typed-log",
    overlay_strength: float = 1.0,
    sentinel_live_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    visual_layers = build_visual_layers(
        source_results,
        visual_labels,
        visibility_mode=visibility_mode,
        overlay_strength=overlay_strength,
        sentinel_live_summary=sentinel_live_summary,
    )
    source_by_id = {result.get("source_id"): result for result in source_results}
    return {
        "stack_compositor_version": STACK_COMPOSITOR_VERSION,
        "typed_visibility_model_version": TYPED_VISIBILITY_MODEL_VERSION,
        "visibility_mode": normalize_visibility_mode(visibility_mode),
        "overlay_strength": normalize_overlay_strength(overlay_strength),
        "visibility_formula": VISIBILITY_FORMULA,
        "active_visual_layer_count": len(visual_layers),
        "visual_layers": visual_layers,
        "layer_opacity_plan": visual_layers,
        "layer_roles": {item["source_id"]: item["visual_role"] for item in visual_layers},
        "visibility_ledger": opacity_ledger_lines(visual_layers),
        "opacity_ledger_text": opacity_ledger_lines(visual_layers),
        "visibility_by_data_type": visibility_by_data_type(visual_layers),
        "compositing_formula": COMPOSITING_FORMULA,
        "real_layer_count": sum(1 for item in visual_layers if item["visual_role"] in {"real_base", "categorical_base", "point_marker"} and source_by_id.get(item["source_id"], {}).get("rendered")),
        "semantic_layer_count": sum(1 for item in visual_layers if item["data_type"] in {"semantic_fallback", "climate_overlay", "hillshade_or_dem", "credential_gated_scene"}),
        "fallback_layer_count": sum(1 for result in source_results if result.get("render_kind") == "semantic_fallback" or not result.get("rendered")),
        "adapter_needed_layer_count": sum(1 for result in source_results if result.get("status") == "adapter_needed"),
    }


def compute_layer_opacity(role: str, active_layer_count: int, *, first_meaningful_real_base: bool = False) -> dict[str, Any]:
    mapped = {
        "real_raster_base": "real_base",
        "categorical_raster": "categorical_base",
        "continuous_overlay": "climate_signal",
        "semantic_precip_overlay": "climate_signal",
        "semantic_elevation_overlay": "terrain_context",
        "credential_gated_scene_overlay": "credential_gated_context",
        "point_or_sample": "point_marker",
        "warning_mask": "warning_overlay",
    }.get(role, role)
    rule = VISIBILITY_RULES.get(mapped, VISIBILITY_RULES["semantic_overlay"])
    opacity = compute_visibility(rule, max(0, active_layer_count - 1), role=mapped, mode="typed-log", overlay_strength=1.0, real_base_rendered=first_meaningful_real_base)
    return {
        "role": mapped,
        "base_opacity": rule["base_visibility"],
        "opacity": opacity,
        "visibility_pct": round(opacity * 100),
        "transparency_pct": round((1.0 - opacity) * 100),
    }


def composite_rgba_layers(layers: list[Image.Image], opacities: list[float], *, size: tuple[int, int]) -> Image.Image:
    canvas = Image.new("RGBA", size, (245, 247, 250, 255))
    for layer, opacity in zip(layers, opacities):
        rgba = layer.convert("RGBA").resize(size)
        alpha = rgba.getchannel("A").point(lambda value: int(value * clamp(opacity)))
        rgba.putalpha(alpha)
        canvas = Image.alpha_composite(canvas, rgba)
    return canvas


def render_stack_legend(draw: ImageDraw.ImageDraw, opacity_plan: list[dict[str, Any]], *, x: int, y: int) -> None:
    draw.text((x, y), "TYPED VISIBILITY STACK", fill=(35, 52, 72))
    y += 20
    draw.text((x, y), "visibility = base / log2(log_depth + 2)", fill=(75, 88, 105))
    y += 24
    for item in opacity_plan[:10]:
        label = f"{item['source_id']} | {item.get('visual_role', item.get('role'))} | visible {item.get('visibility_pct')}% | transparent {item['transparency_pct']}% | {item['status']}"
        draw.text((x, y), label[:92], fill=(35, 52, 72))
        y += 18


def write_stack_transparency_ledger(report: dict[str, Any], path: Path) -> None:
    import json
    payload = {key: report[key] for key in ["task_id", "stack_compositor_version", "typed_visibility_model_version", "visibility_mode", "overlay_strength", "active_visual_layer_count", "visual_layers", "layer_opacity_plan", "layer_roles", "visibility_ledger", "opacity_ledger_text", "visibility_by_data_type", "visibility_formula", "compositing_formula", "real_layer_count", "semantic_layer_count", "fallback_layer_count", "adapter_needed_layer_count"] if key in report}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
