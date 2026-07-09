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
    assert first["layer_roles"]["prism_daily_ppt_static_zip"] == "semantic_precip_overlay"
    assert first["layer_roles"]["copernicus_sentinel2_l2a_cdse_stac"] == "credential_gated_scene_overlay"
    assert "CDL real_base op" in first["opacity_ledger_text"][0]
