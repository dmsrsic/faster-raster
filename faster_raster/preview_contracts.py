from __future__ import annotations

import hashlib
from typing import Any
from faster_raster.adapter_contract import stable_json
from faster_raster import preview_profiles, preview_templates, preview_themes
from faster_raster.adapters.capabilities import adapter_capability_catalog

IMPLEMENTATION_VERSION = "preview-renderer-v1-alpha3"
RENDER_PROFILES = {
    "natural_color": {"required_bands": ["red", "green", "blue"], "band_order": ["red", "green", "blue"], "scale_source": "service_png_or_reflectance", "percentile_range": [2, 98], "gamma": 1.0, "clipping_policy": "clip_to_percentiles", "output_bit_depth": 8, "nodata_behavior": "transparent"},
    "natural_color_mild_stretch": {"required_bands": ["red", "green", "blue"], "band_order": ["red", "green", "blue"], "scale_source": "service_png_rgb", "percentile_range": [2, 98], "gamma": 0.98, "shadow_lift": 0.015, "highlight_control": 0.995, "saturation_multiplier": 1.02, "clipping_policy": "clip_to_percentiles", "output_bit_depth": 8, "nodata_behavior": "transparent", "random_sampling": False, "false_color_claim": False},
    "color_infrared": {"required_bands": ["nir", "red", "green"], "band_order": ["nir", "red", "green"], "scale_source": "reflectance", "percentile_range": [2, 98], "gamma": 1.0, "clipping_policy": "clip_to_percentiles", "output_bit_depth": 8, "nodata_behavior": "transparent"},
    "vegetation_emphasis": {"required_bands": ["nir", "red"], "band_order": ["nir", "red", "green"], "scale_source": "reflectance_or_ndvi", "percentile_range": [2, 98], "gamma": 0.9, "clipping_policy": "clip_to_percentiles", "output_bit_depth": 8, "nodata_behavior": "transparent"},
    "grayscale_single_band": {"required_bands": ["band_1"], "band_order": ["band_1"], "scale_source": "single_band", "percentile_range": [2, 98], "gamma": 1.0, "clipping_policy": "clip_to_percentiles", "output_bit_depth": 8, "nodata_behavior": "transparent"},
    "categorical_overlay": {"required_bands": ["class"], "band_order": ["class"], "scale_source": "service_palette", "percentile_range": None, "gamma": 1.0, "clipping_policy": "none", "output_bit_depth": 8, "nodata_behavior": "transparent"},
    "radar_db_grayscale": {"required_bands": ["vv_or_vh"], "band_order": ["vv"], "scale_source": "decibel", "percentile_range": [2, 98], "gamma": 1.0, "clipping_policy": "documented_db_range", "output_bit_depth": 8, "nodata_behavior": "transparent", "natural_color_claim": False},
}


def contract_hash(contract: dict[str, Any]) -> str:
    payload = {k: v for k, v in contract.items() if k not in {"preview_render_contract_sha256", "generated_at_utc"}}
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


def source_allowlist_hash(allowlist: dict[str, Any]) -> str:
    payload = {k: v for k, v in allowlist.items() if k != "source_allowlist_sha256"}
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


def _layer(entry: dict[str, Any], *, theme_id: str | None = None, render_role: str | None = None, opacity: float | None = None, z_order: int | None = None, profile_id: str | None = None) -> dict[str, Any]:
    theme = preview_themes.get_theme(theme_id or entry["semantic_theme"])
    color_profile = profile_id or entry["default_render_profile"]
    requested = float(opacity if opacity is not None else theme["default_opacity"])
    return {
        "source_id": entry["source_id"],
        "adapter_id": entry["adapter_id"],
        "theme": theme_id or entry["semantic_theme"],
        "render_role": render_role or entry.get("default_render_role") or theme["render_role"],
        "z_order": int(z_order if z_order is not None else theme["default_z_order"]),
        "opacity": requested,
        "requested_opacity": requested,
        "compiled_opacity": requested,
        "effective_alpha": requested,
        "opacity_adjustment_reason": "profile_default",
        "blend_mode": theme["default_blend_mode"],
        "resampling_method": theme["default_resampling"],
        "color_profile": color_profile,
        "band_mapping": RENDER_PROFILES[color_profile]["band_order"],
        "contrast_stretch_policy": RENDER_PROFILES[color_profile],
        "nodata_policy": theme["nodata_render_policy"],
        "mask_policy": "transparent_nodata_only",
        "legend_order": theme["legend_priority"],
        "real_raster_pixels_required": theme["real_raster_pixels_required"],
        "include_in_alpha_budget": True,
    }


def _build_template_layers(
    entries: dict[str, dict[str, Any]],
    template: dict[str, Any],
    profile: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    layers: list[dict[str, Any]] = []
    for declaration in template.get("source_layers") or []:
        source_id = declaration["source_id"]
        if source_id not in entries:
            raise ValueError(
                f"preview template {template['template_id']} references "
                f"unregistered source {source_id}"
            )
        layer = _layer(
            entries[source_id],
            theme_id=declaration.get("theme_id"),
            render_role=declaration.get("render_role"),
            opacity=declaration.get("opacity"),
            z_order=declaration.get("z_order"),
            profile_id=declaration.get("profile_id"),
        )
        for key in (
            "compiled_opacity",
            "blend_mode",
            "resampling_method",
            "coverage_dependent_opacity",
            "include_in_alpha_budget",
        ):
            if key in declaration:
                layer[key] = declaration[key]
        if layer.get("render_role") == "primary_imagery":
            layer["effective_alpha"] = float(layer["compiled_opacity"])
        if layer.get("coverage_dependent_opacity"):
            if profile is None:
                raise ValueError("coverage-dependent opacity requires a preview profile")
            layer.update(
                preview_profiles.compile_categorical_opacity(
                    float(declaration.get("categorical_coverage_fraction", 1.0)),
                    float(layer["requested_opacity"]),
                    profile,
                )
            )
        layers.append(layer)
    layers.sort(
        key=lambda layer: (
            0
            if layer["source_id"] == template.get("primary_imagery_selection")
            else 1,
            layer["z_order"],
            preview_themes.get_theme(layer["theme"])["visual_priority"],
            layer["source_id"],
        )
    )
    if template.get("alpha_budget_profile"):
        if profile is None:
            raise ValueError("alpha-budget template requires a preview profile")
        alpha_budget = preview_profiles.compile_overlay_alpha_budget(layers, profile)
    else:
        alpha_budget = {
            "overlay_alpha_budget_limit": 1.0,
            "requested_overlay_alpha_budget": 0.0,
            "compiled_overlay_alpha_budget": 0.0,
            "overlay_alpha_budget_status": "PASS",
            "overlay_adjustments": [],
        }
    return layers, alpha_budget


def build_render_contract(task_id: str, allowlist: dict[str, Any], *, width: int | None = None, height: int | None = None, max_total_bytes: int = 25_000_000, network_policy: str = "disabled", explicit_profile_id: str | None = None) -> dict[str, Any]:
    entries = {entry["source_id"]: entry for entry in allowlist["entries"]}
    template = preview_templates.template_for_task(task_id)
    selected_profile_id = explicit_profile_id or template.get("preview_profile_id")
    profile = preview_profiles.select_default_profile(
        task_id,
        explicit_profile_id=selected_profile_id,
    )
    layers, alpha_budget = _build_template_layers(entries, template, profile)
    aoi = template["task_aoi"]
    width = width or int(template["default_width"])
    height = height or int(template["default_height"])
    for idx, layer in enumerate(layers):
        layer["compiled_order"] = idx
    adapter_hashes = {item["adapter_id"]: item["adapter_capability_contract_sha256"] for item in adapter_capability_catalog()["adapters"]}
    contract = {
        "schema_version": 1,
        "task_id": task_id,
        "preview_template_id": template["template_id"],
        "preview_template_schema_version": template["schema_version"],
        "preview_template_contract_sha256": template["template_sha256"],
        "aoi": aoi,
        "target_preview_crs": "EPSG:4326",
        "preview_width": width,
        "preview_height": height,
        "primary_imagery_selection": template["primary_imagery_selection"],
        "preview_profile_id": profile["profile_id"] if profile else None,
        "preview_profile_version": profile["profile_version"] if profile else None,
        "preview_profile_contract_sha256": profile["default_profile_contract_sha256"] if profile else None,
        "preview_profile": profile,
        "source_artifact_or_asset_identifiers": [layer["source_id"] for layer in layers],
        "adapter_ids": sorted({layer["adapter_id"] for layer in layers}),
        "layers": layers,
        "pixel_zoom_location": {"x_fraction": 0.50, "y_fraction": 0.50, "window_pixels": 64},
        "output_format": "PNG",
        "byte_caps": {"max_total_bytes": int(max_total_bytes)},
        "network_policy": network_policy,
        "adapter_capability_hashes": adapter_hashes,
        "source_allowlist_hash": source_allowlist_hash(allowlist),
        "preview_theme_registry_sha256": preview_themes.theme_registry()["preview_theme_registry_sha256"],
        "renderer_implementation_version": IMPLEMENTATION_VERSION,
        "render_profiles": RENDER_PROFILES,
        "overlay_alpha_budget": alpha_budget,
        "source_request_parameters": template.get("source_request_parameters") or {},
        "approval_requirement": "require --allow-preview and --approve-plan-sha256; network also requires --allow-network",
        "generated_at_utc": "volatile_excluded_from_hash",
        "preview_render_contract_sha256": "",
    }
    contract["preview_render_contract_sha256"] = contract_hash(contract)
    return contract
