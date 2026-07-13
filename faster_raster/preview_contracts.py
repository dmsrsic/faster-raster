from __future__ import annotations

import hashlib
from typing import Any
from faster_raster.adapter_contract import stable_json
from faster_raster import preview_themes, preview_profiles
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


def _build_alpha2_layers(entries: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    source_order = ["usgs_naip_imagery", "usgs_3dep_hillshade", "usda_cdl_imageserver", "chirps_alpha1_derived_geotiff", "sentinel_2_l2a_planetary_computer", "sentinel_1_radar_scaffold"]
    layers = []
    for source_id in source_order:
        entry = entries[source_id]
        render_role = entry.get("default_render_role")
        z_order = 0 if source_id == "usgs_naip_imagery" else (5 if render_role == "imagery_alternative" else None)
        opacity = 1.0 if source_id == "usgs_naip_imagery" else None
        layers.append(_layer(entry, render_role=render_role, z_order=z_order, opacity=opacity))
    return sorted(layers, key=lambda layer: (0 if layer["source_id"] == "usgs_naip_imagery" else 1, layer["z_order"], preview_themes.get_theme(layer["theme"])["visual_priority"], layer["source_id"]))


def _build_balanced_layers(entries: dict[str, dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    imagery = _layer(entries["usgs_naip_imagery"], render_role="primary_imagery", opacity=1.0, z_order=0, profile_id="natural_color_mild_stretch")
    imagery.update({"compiled_opacity": 1.0, "effective_alpha": 1.0, "blend_mode": "normal", "include_in_alpha_budget": False})
    terrain = _layer(entries["usgs_3dep_hillshade"], render_role="terrain_context", opacity=profile["terrain_policy"]["requested_opacity"], z_order=10, profile_id="grayscale_single_band")
    terrain.update({"compiled_opacity": profile["terrain_policy"]["target_opacity"], "effective_alpha": profile["terrain_policy"]["target_opacity"], "blend_mode": "multiply", "opacity_adjustment_reason": "profile_default"})
    cdl = _layer(entries["usda_cdl_imageserver"], render_role="thematic_overlay", opacity=profile["categorical_policy"]["requested_opacity"], z_order=20, profile_id="categorical_overlay")
    cdl_adjustment = preview_profiles.compile_categorical_opacity(1.0, profile["categorical_policy"]["requested_opacity"], profile)
    cdl.update({"resampling_method": "nearest", "blend_mode": "normal", "coverage_dependent_opacity": True, **cdl_adjustment})
    layers = [imagery, terrain, cdl]
    budget = preview_profiles.compile_overlay_alpha_budget(layers, profile)
    return [{**layer, "compiled_order": idx} for idx, layer in enumerate(sorted(layers, key=lambda layer: (layer["z_order"], layer["source_id"])))], budget


def build_render_contract(task_id: str, allowlist: dict[str, Any], *, width: int | None = None, height: int | None = None, max_total_bytes: int = 25_000_000, network_policy: str = "disabled", explicit_profile_id: str | None = None) -> dict[str, Any]:
    entries = {entry["source_id"]: entry for entry in allowlist["entries"]}
    profile = preview_profiles.select_default_profile(task_id, explicit_profile_id=explicit_profile_id)
    if task_id == "example_imagery_first_multipreview":
        layers = _build_alpha2_layers(entries)
        aoi = {"bbox": [-83.20, 39.80, -83.18, 39.82], "crs": "EPSG:4326"}
        width = width or 1200; height = height or 800
        alpha_budget = {"overlay_alpha_budget_limit": 1.0, "requested_overlay_alpha_budget": 0.0, "compiled_overlay_alpha_budget": 0.0, "overlay_alpha_budget_status": "PASS", "overlay_adjustments": []}
    elif task_id == "example_imagery_first_balanced_stack":
        if profile is None:
            profile = preview_profiles.imagery_first_balanced_v1()
        layers, alpha_budget = _build_balanced_layers(entries, profile)
        aoi = {"bbox": [-84.65, 40.15, -84.45, 40.35], "crs": "EPSG:4326"}
        width = width or 1560; height = height or 980
    else:
        raise ValueError(f"unknown preview task: {task_id}")
    for idx, layer in enumerate(layers):
        layer["compiled_order"] = idx
    adapter_hashes = {item["adapter_id"]: item["adapter_capability_contract_sha256"] for item in adapter_capability_catalog()["adapters"]}
    contract = {
        "schema_version": 1,
        "task_id": task_id,
        "aoi": aoi,
        "target_preview_crs": "EPSG:4326",
        "preview_width": width,
        "preview_height": height,
        "primary_imagery_selection": "usgs_naip_imagery",
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
        "source_request_parameters": {"usda_cdl_imageserver": {"time": "1704067200000"}} if task_id == "example_imagery_first_balanced_stack" else {},
        "approval_requirement": "require --allow-preview and --approve-plan-sha256; network also requires --allow-network",
        "generated_at_utc": "volatile_excluded_from_hash",
        "preview_render_contract_sha256": "",
    }
    contract["preview_render_contract_sha256"] = contract_hash(contract)
    return contract
