from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

STACK_COMPOSITOR_VERSION = "0.5.8"
MIN_OPACITY = 0.18
MAX_OPACITY = 0.92
BASE_OPACITY_BY_ROLE = {
    "real_raster_base": 0.92,
    "categorical_raster": 0.78,
    "continuous_overlay": 0.62,
    "semantic_overlay": 0.50,
    "point_or_sample": 0.90,
    "warning_mask": 0.35,
}
COMPOSITING_FORMULA = "opacity = clamp(0.18, 0.92, base_opacity / log2(n + 1)); first meaningful real raster base >= 0.72"


def clamp(value: float, min_value: float = MIN_OPACITY, max_value: float = MAX_OPACITY) -> float:
    return max(min_value, min(max_value, value))


def compute_layer_opacity(role: str, active_layer_count: int, *, first_meaningful_real_base: bool = False) -> dict[str, Any]:
    n = max(1, int(active_layer_count))
    base = BASE_OPACITY_BY_ROLE.get(role, BASE_OPACITY_BY_ROLE["semantic_overlay"])
    opacity = clamp(base / math.log2(n + 1))
    if first_meaningful_real_base:
        opacity = max(opacity, 0.72)
    if role in {"semantic_overlay", "warning_mask"}:
        opacity = min(opacity, 0.50 if role == "semantic_overlay" else 0.35)
    opacity = round(opacity, 3)
    return {
        "role": role,
        "base_opacity": base,
        "opacity": opacity,
        "transparency_pct": round((1.0 - opacity) * 100),
    }


def role_for_result(result: dict[str, Any]) -> str:
    render_kind = result.get("render_kind")
    status = result.get("status")
    if result.get("real_raster_rendered") or render_kind == "real_raster":
        return "real_raster_base"
    if render_kind == "real_categorical_samples":
        return "point_or_sample"
    if render_kind == "real_point":
        return "point_or_sample"
    if status == "adapter_needed":
        return "semantic_overlay"
    if status in {"no_data_or_placeholder", "fetch_failed"}:
        return "warning_mask"
    return "semantic_overlay"


def visible_layer_results(source_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [result for result in source_results if result.get("source_id")]


def compute_stack_opacity_plan(source_results: list[dict[str, Any]]) -> dict[str, Any]:
    layers = visible_layer_results(source_results)
    active_count = len(layers)
    first_real_seen = False
    plan = []
    for result in layers:
        role = role_for_result(result)
        first_real = role == "real_raster_base" and not first_real_seen and bool(result.get("rendered"))
        if first_real:
            first_real_seen = True
        opacity = compute_layer_opacity(role, active_count, first_meaningful_real_base=first_real)
        plan.append({
            "source_id": result.get("source_id"),
            "status": result.get("status"),
            "render_kind": result.get("render_kind"),
            **opacity,
        })
    return {
        "stack_compositor_version": STACK_COMPOSITOR_VERSION,
        "active_visual_layer_count": active_count,
        "layer_opacity_plan": plan,
        "compositing_formula": COMPOSITING_FORMULA,
        "real_layer_count": sum(1 for item in plan if item["role"] in {"real_raster_base", "categorical_raster", "continuous_overlay", "point_or_sample"} and next((r for r in layers if r.get("source_id") == item["source_id"]), {}).get("rendered")),
        "semantic_layer_count": sum(1 for item in plan if item["role"] == "semantic_overlay"),
        "fallback_layer_count": sum(1 for result in layers if result.get("render_kind") == "semantic_fallback" or not result.get("rendered")),
        "adapter_needed_layer_count": sum(1 for result in layers if result.get("status") == "adapter_needed"),
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
    draw.text((x, y), "LAYER STACK", fill=(35, 52, 72))
    y += 20
    draw.text((x, y), "opacity = base / log2(n + 1)", fill=(75, 88, 105))
    y += 24
    for item in opacity_plan[:10]:
        label = f"{item['source_id']} | {item['role']} | {item['render_kind']} | opacity {item['opacity']} | transparent {item['transparency_pct']}% | {item['status']}"
        draw.text((x, y), label[:92], fill=(35, 52, 72))
        y += 18


def write_stack_transparency_ledger(report: dict[str, Any], path: Path) -> None:
    import json
    payload = {key: report[key] for key in ["task_id", "stack_compositor_version", "active_visual_layer_count", "layer_opacity_plan", "compositing_formula", "real_layer_count", "semantic_layer_count", "fallback_layer_count", "adapter_needed_layer_count"] if key in report}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
