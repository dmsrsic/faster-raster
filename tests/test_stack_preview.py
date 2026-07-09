from __future__ import annotations

import json
from pathlib import Path

from faster_raster import stack_preview, task_builder


def example_task():
    return task_builder.default_task(
        "stack_preview_task",
        "Stack Preview Task",
        [-83.2, 39.8, -83.19, 39.81],
        "EPSG:4326",
        "EPSG:5070",
        [2023],
        ["precipitation", "landcover", "elevation"],
        ["prism_daily_ppt_static_zip", "cdl_arcgis_tiny_export", "usgs_3dep_dem"],
    )


def test_status_inference_for_example_sources():
    summary = stack_preview.build_preview_summary(example_task())
    statuses = {layer["source_id"]: layer["status"] for layer in summary["layers"]}
    assert statuses["prism_daily_ppt_static_zip"] == "verified_now"
    assert statuses["cdl_arcgis_tiny_export"] == "verified_now"
    assert statuses["usgs_3dep_dem"] == "adapter_needed"
    assert "usgs_3dep_dem is adapter_needed" in summary["warnings"]


def test_preview_json_contract(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    report = stack_preview.create_preview(example_task())
    assert Path(report["png_path"]).read_bytes().startswith(b"\x89PNG")
    payload = json.loads(Path(report["preview_json"]).read_text())
    assert payload["task_id"] == "stack_preview_task"
    assert payload["bbox"] == [-83.2, 39.8, -83.19, 39.81]
    assert payload["target_crs"] == "EPSG:5070"
    assert payload["source_count"] == 3
    assert payload["theme_count"] == 3
    assert payload["network_run"] is False
    assert payload["warnings"]


def test_preview_does_not_mutate_registry_or_atlas(tmp_path, monkeypatch):
    registry = Path("configs/source_registry.yaml").read_bytes()
    atlas = Path("research/source_atlas_v0_4.yaml").read_bytes()
    monkeypatch.chdir(tmp_path)
    stack_preview.create_preview(example_task())
    assert Path("/home/dmsrsic/raster-work/faster-raster/configs/source_registry.yaml").read_bytes() == registry
    assert Path("/home/dmsrsic/raster-work/faster-raster/research/source_atlas_v0_4.yaml").read_bytes() == atlas
