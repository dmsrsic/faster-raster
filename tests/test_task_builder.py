from __future__ import annotations

import json
from pathlib import Path

import pytest

from faster_raster import task_builder, stack_preview


def test_default_task_validate_and_reports(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    task = task_builder.default_task(
        "demo_task",
        "Demo Task",
        [-83.2, 39.8, -83.19, 39.81],
        "EPSG:4326",
        "EPSG:5070",
        [2023],
        ["precipitation", "landcover"],
        ["prism_daily_ppt_static_zip", "cdl_arcgis_tiny_export"],
    )
    assert task_builder.validate_task(task) == []
    path = task_builder.save_task(task)
    assert path.exists()
    report = task_builder.write_task_reports(task)
    assert report["validation_status"] == "PASS"
    assert report["source_count"] == 2
    assert Path(report["output_artifacts"]["task_json"]).exists()


def test_invalid_task_errors():
    task = {"task_id": "Bad ID", "aoi": {"bbox": [0, 1], "bbox_crs": "EPSG:4326"}, "target_grid": {}, "time": {"years": [2024, 2023]}, "themes": [], "sources": []}
    errors = task_builder.validate_task(task)
    assert any("task_id" in error for error in errors)
    assert any("bbox" in error for error in errors)
    assert any("target_grid.crs" in error for error in errors)
    assert any("at least one source" in error for error in errors)


def test_task_rejects_secret_like_values():
    task = task_builder.default_task("secret_task", "Secret", [0, 0, 1, 1], "EPSG:4326", "EPSG:5070", [], ["x"], [])
    task["notes"] = ["token=abcdefghijk12345"]
    assert any("secret-looking" in error for error in task_builder.validate_task(task))


def test_preview_png_json_markdown(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    task = task_builder.default_task("preview_task", "Preview", [-83.2, 39.8, -83.19, 39.81], "EPSG:4326", "EPSG:5070", [2023], ["precipitation"], ["prism_daily_ppt_static_zip"])
    task_builder.save_task(task)
    report = task_builder.create_preview(task)
    png = Path(report["preview_png"])
    assert png.exists()
    assert png.read_bytes().startswith(b"\x89PNG")
    assert Path(report["preview_json"]).exists()
    assert json.loads(Path(report["preview_json"]).read_text())["network_run"] is False
    assert Path(report["preview_md"]).exists()


def test_open_preview_fallback_does_not_fail(tmp_path, monkeypatch):
    png = tmp_path / "x.png"
    png.write_bytes(b"not really")
    monkeypatch.setattr(stack_preview.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 1, "stdout": ""})())
    result = stack_preview.open_preview(png)
    assert "open skipped" in result


def test_parse_years_preserves_invalid_duplicates_for_validation():
    assert task_builder.parse_years("2023,2023") == [2023, 2023]
    task = task_builder.default_task("dup_year", "Dup", [0, 0, 1, 1], "EPSG:4326", "EPSG:5070", [2023, 2023], ["x"], [])
    assert any("years" in error for error in task_builder.validate_task(task))


def test_data_task_schema_exists_and_has_required_fields():
    import json
    schema = json.loads(Path("schemas/data_task.schema.json").read_text())
    assert "task_id" in schema["required"]
    assert schema["properties"]["aoi"]["properties"]["bbox"]["minItems"] == 4
