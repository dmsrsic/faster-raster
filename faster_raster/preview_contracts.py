from __future__ import annotations

import hashlib
from typing import Any
from faster_raster.adapter_contract import stable_json
from faster_raster import preview_themes
from faster_raster.adapters.capabilities import adapter_capability_catalog

IMPLEMENTATION_VERSION = "preview-renderer-v1-alpha2"
RENDER_PROFILES = {
    "natural_color": {"required_bands": ["red", "green", "blue"], "band_order": ["red", "green", "blue"], "scale_source": "service_png_or_reflectance", "percentile_range": [2, 98], "gamma": 1.0, "clipping_policy": "clip_to_percentiles", "output_bit_depth": 8, "nodata_behavior": "transparent"},
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

def build_render_contract(task_id: str, allowlist: dict[str, Any], *, width: int = 1200, height: int = 800, max_total_bytes: int = 25_000_000, network_policy: str = "disabled") -> dict[str, Any]:
    if task_id != "example_imagery_first_multipreview":
        raise ValueError(f"unknown preview task: {task_id}")
    entries = {entry["source_id"]: entry for entry in allowlist["entries"]}
    source_order = ["usgs_naip_imagery", "usgs_3dep_hillshade", "usda_cdl_imageserver", "chirps_alpha1_derived_geotiff", "sentinel_2_l2a_planetary_computer", "sentinel_1_radar_scaffold"]
    layers = []
    for source_id in source_order:
        entry = entries[source_id]
        theme = preview_themes.get_theme(entry["semantic_theme"])
        profile = entry["default_render_profile"]
        render_role = entry.get("default_render_role") or theme["render_role"]
        z_order = 0 if source_id == "usgs_naip_imagery" else (5 if render_role == "imagery_alternative" else theme["default_z_order"])
        opacity = theme["default_opacity"] if source_id == "usgs_naip_imagery" else min(theme["default_opacity"], 0.85 if render_role == "imagery_alternative" else theme["default_opacity"])
        layers.append({"source_id": source_id, "adapter_id": entry["adapter_id"], "theme": entry["semantic_theme"], "render_role": render_role, "z_order": z_order, "opacity": opacity, "blend_mode": theme["default_blend_mode"], "resampling_method": theme["default_resampling"], "color_profile": profile, "band_mapping": RENDER_PROFILES[profile]["band_order"], "contrast_stretch_policy": RENDER_PROFILES[profile], "nodata_policy": theme["nodata_render_policy"], "mask_policy": "transparent_nodata_only", "legend_order": theme["legend_priority"], "real_raster_pixels_required": theme["real_raster_pixels_required"]})
    layers = sorted(layers, key=lambda layer: (0 if layer["source_id"] == "usgs_naip_imagery" else 1, layer["z_order"], preview_themes.get_theme(layer["theme"])["visual_priority"], layer["source_id"]))
    for idx, layer in enumerate(layers):
        layer["compiled_order"] = idx
    adapter_hashes = {item["adapter_id"]: item["adapter_capability_contract_sha256"] for item in adapter_capability_catalog()["adapters"]}
    contract = {"schema_version": 1, "task_id": task_id, "aoi": {"bbox": [-83.20, 39.80, -83.18, 39.82], "crs": "EPSG:4326"}, "target_preview_crs": "EPSG:4326", "preview_width": width, "preview_height": height, "primary_imagery_selection": "usgs_naip_imagery", "source_artifact_or_asset_identifiers": [layer["source_id"] for layer in layers], "adapter_ids": sorted({layer["adapter_id"] for layer in layers}), "layers": layers, "pixel_zoom_location": {"x_fraction": 0.50, "y_fraction": 0.50, "window_pixels": 64}, "output_format": "PNG", "byte_caps": {"max_total_bytes": int(max_total_bytes)}, "network_policy": network_policy, "adapter_capability_hashes": adapter_hashes, "source_allowlist_hash": source_allowlist_hash(allowlist), "preview_theme_registry_sha256": preview_themes.theme_registry()["preview_theme_registry_sha256"], "renderer_implementation_version": IMPLEMENTATION_VERSION, "render_profiles": RENDER_PROFILES, "approval_requirement": "require --allow-preview and --approve-plan-sha256; network also requires --allow-network", "generated_at_utc": "volatile_excluded_from_hash", "preview_render_contract_sha256": ""}
    contract["preview_render_contract_sha256"] = contract_hash(contract)
    return contract
