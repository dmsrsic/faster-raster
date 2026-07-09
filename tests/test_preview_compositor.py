from __future__ import annotations

from pathlib import Path

from PIL import Image

from faster_raster import preview_compositor


def test_opacity_decreases_as_layer_count_increases():
    one = preview_compositor.compute_layer_opacity("semantic_overlay", 1)
    many = preview_compositor.compute_layer_opacity("semantic_overlay", 5)
    assert many["opacity"] < one["opacity"]
    assert many["transparency_pct"] > one["transparency_pct"]


def test_real_base_layer_minimum_opacity():
    result = preview_compositor.compute_layer_opacity("real_raster_base", 8, first_meaningful_real_base=True)
    assert result["opacity"] >= 0.72


def test_stack_opacity_plan_counts_layers():
    plan = preview_compositor.compute_stack_opacity_plan([
        {"source_id": "cdl_arcgis_tiny_export", "status": "real_raster_rendered", "render_kind": "real_raster", "rendered": True, "real_raster_rendered": True},
        {"source_id": "prism_daily_ppt_static_zip", "status": "semantic_fallback", "render_kind": "semantic_fallback", "rendered": False},
        {"source_id": "usgs_3dep_dem", "status": "adapter_needed", "render_kind": "semantic_fallback", "rendered": False},
    ])
    assert plan["active_visual_layer_count"] == 3
    assert len(plan["layer_opacity_plan"]) == 3
    assert plan["adapter_needed_layer_count"] == 1


def test_composite_rgba_layers_writes_png(tmp_path):
    base = Image.new("RGBA", (8, 8), (255, 0, 0, 255))
    overlay = Image.new("RGBA", (8, 8), (0, 0, 255, 255))
    image = preview_compositor.composite_rgba_layers([base, overlay], [0.8, 0.2], size=(8, 8))
    path = tmp_path / "stack.png"
    image.save(path)
    assert path.read_bytes().startswith(bytes([137]) + b"PNG")



def test_opacity_ledger_is_deterministic_and_uses_visual_roles():
    results = [
        {"source_id": "cdl_arcgis_tiny_export", "status": "real_raster_rendered", "render_kind": "real_raster", "rendered": True, "real_raster_rendered": True},
        {"source_id": "prism_daily_ppt_static_zip", "status": "semantic_fallback", "render_kind": "semantic_fallback", "rendered": False},
        {"source_id": "copernicus_sentinel2_l2a_cdse_stac", "status": "planned", "render_kind": "semantic_fallback", "rendered": False},
    ]
    labels = {"cdl_arcgis_tiny_export": "CDL", "prism_daily_ppt_static_zip": "PRISM", "copernicus_sentinel2_l2a_cdse_stac": "Sentinel-2"}
    first = preview_compositor.compute_stack_opacity_plan(results, labels)
    second = preview_compositor.compute_stack_opacity_plan(results, labels)
    assert first["opacity_ledger_text"] == second["opacity_ledger_text"]
    assert first["layer_roles"]["cdl_arcgis_tiny_export"] == "real_base"
    assert first["layer_roles"]["prism_daily_ppt_static_zip"] == "climate_signal"
    assert first["layer_roles"]["copernicus_sentinel2_l2a_cdse_stac"] == "credential_gated_context"
    assert "CDL real_base visible" in first["opacity_ledger_text"][0]



def test_typed_visibility_model_outputs_required_layer_fields():
    results = [
        {"source_id": "cdl_arcgis_tiny_export", "status": "real_raster_rendered", "render_kind": "real_raster", "rendered": True, "real_raster_rendered": True},
        {"source_id": "prism_daily_ppt_static_zip", "status": "semantic_fallback", "render_kind": "semantic_fallback", "rendered": False},
        {"source_id": "usgs_3dep_dem", "status": "adapter_needed", "render_kind": "semantic_fallback", "rendered": False},
        {"source_id": "copernicus_sentinel2_l2a_cdse_stac", "status": "planned", "render_kind": "semantic_fallback", "rendered": False},
    ]
    labels = {"cdl_arcgis_tiny_export": "CDL", "prism_daily_ppt_static_zip": "PRISM", "usgs_3dep_dem": "3DEP", "copernicus_sentinel2_l2a_cdse_stac": "Sentinel-2"}
    plan = preview_compositor.compute_stack_opacity_plan(results, labels, sentinel_live_summary={"sentinel_stac_live_result_present": True})
    assert plan["typed_visibility_model_version"] == "0.5.10"
    assert plan["visibility_mode"] == "typed-log"
    assert plan["visual_layers"][0]["visual_role"] == "real_base"
    assert plan["visual_layers"][0]["opacity"] >= 0.72
    assert plan["visual_layers"][1]["data_type"] == "climate_overlay"
    assert plan["visual_layers"][2]["visual_role"] == "terrain_context"
    assert plan["visual_layers"][3]["status"] == "stac_discovered_no_pixels"
    assert plan["visual_layers"][3]["data_type"] == "credential_gated_scene"
    assert "credential_gated_scene" in plan["visibility_by_data_type"]
    assert plan["visibility_ledger"] == preview_compositor.opacity_ledger_lines(plan["visual_layers"])


def test_base_dominant_and_overlay_strength_affect_overlays_not_base_floor():
    results = [
        {"source_id": "cdl_arcgis_tiny_export", "status": "real_raster_rendered", "render_kind": "real_raster", "rendered": True, "real_raster_rendered": True},
        {"source_id": "prism_daily_ppt_static_zip", "status": "semantic_fallback", "render_kind": "semantic_fallback", "rendered": False},
    ]
    typed = preview_compositor.compute_stack_opacity_plan(results, visibility_mode="typed-log", overlay_strength=1.0)
    weak = preview_compositor.compute_stack_opacity_plan(results, visibility_mode="base-dominant", overlay_strength=0.75)
    assert typed["visual_layers"][0]["opacity"] >= 0.72
    assert weak["visual_layers"][0]["opacity"] >= 0.72
    assert weak["visual_layers"][1]["opacity"] < typed["visual_layers"][1]["opacity"]
    assert weak["overlay_strength"] == 0.75
