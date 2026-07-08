from __future__ import annotations

from faster_raster.harmonization_planner import build_harmonization_plan, write_harmonization_plan
from faster_raster.url_planner import plan_urls


def test_harmonization_plan_is_deterministic(valid_spec, registry, project_spec_path, tmp_path):
    rows = plan_urls(valid_spec, registry, project_spec_path)
    first = build_harmonization_plan(valid_spec, rows)
    second = build_harmonization_plan(valid_spec, rows)
    assert first == second

    left = tmp_path / "left.json"
    right = tmp_path / "right.json"
    write_harmonization_plan(first, left)
    write_harmonization_plan(second, right)
    assert left.read_bytes() == right.read_bytes()


def test_golden_harmonization_plan_bytes_are_stable(valid_spec, registry, project_spec_path, tmp_path):
    rows = plan_urls(valid_spec, registry, project_spec_path)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    write_harmonization_plan(build_harmonization_plan(valid_spec, rows), first)
    write_harmonization_plan(build_harmonization_plan(valid_spec, rows), second)

    assert first.read_bytes() == second.read_bytes()


def test_every_manifest_request_id_appears_once_in_harmonization_inputs(valid_spec, registry, project_spec_path):
    rows = plan_urls(valid_spec, registry, project_spec_path)
    plan = build_harmonization_plan(valid_spec, rows)

    manifest_ids = sorted(row["request_id"] for row in rows)
    plan_ids = sorted(item["request_id"] for item in plan["inputs"])
    assert plan_ids == manifest_ids
    assert len(plan_ids) == len(set(plan_ids))


def test_harmonization_inputs_include_manifest_contract_fields(valid_spec, registry, project_spec_path):
    rows = plan_urls(valid_spec, registry, project_spec_path)
    plan = build_harmonization_plan(valid_spec, rows)
    item = plan["inputs"][0]

    assert item["source_bbox"] == rows[0]["bbox"]
    assert item["bbox_crs"] == rows[0]["bbox_crs"]
    assert item["export_image_crs"] == rows[0]["export_image_crs"]
    assert item["target_grid_crs"] == rows[0]["target_grid_crs"]
    assert item["tile_width_pixels"] == rows[0]["tile_width_pixels"]
    assert item["tile_height_pixels"] == rows[0]["tile_height_pixels"]


def test_target_crs_is_epsg5070_for_example(valid_spec, registry, project_spec_path):
    plan = build_harmonization_plan(valid_spec, plan_urls(valid_spec, registry, project_spec_path))
    assert plan["target_grid"]["crs"] == "EPSG:5070"


def test_categorical_source_uses_nearest_only(valid_spec, registry, project_spec_path):
    plan = build_harmonization_plan(valid_spec, plan_urls(valid_spec, registry, project_spec_path))
    for item in plan["inputs"]:
        assert item["semantic_type"] == "categorical"
        assert item["resampling"] == "nearest"


def test_forbidden_resampling_includes_unsafe_methods(valid_spec, registry, project_spec_path):
    plan = build_harmonization_plan(valid_spec, plan_urls(valid_spec, registry, project_spec_path))
    forbidden = set(plan["inputs"][0]["forbidden_resampling"])
    assert {"bilinear", "cubic", "lanczos"} <= forbidden


def test_planned_output_paths_are_deterministic(valid_spec, registry, project_spec_path):
    plan = build_harmonization_plan(valid_spec, plan_urls(valid_spec, registry, project_spec_path))
    assert [item["planned_output"] for item in plan["inputs"]] == [
        "data/grid/cdl_2023_crop_type_tile_000001_epsg5070_30m.tif",
        "data/grid/cdl_2024_crop_type_tile_000001_epsg5070_30m.tif",
    ]


def test_validation_checks_cover_required_categories(valid_spec, registry, project_spec_path):
    plan = build_harmonization_plan(valid_spec, plan_urls(valid_spec, registry, project_spec_path))
    checks = set(plan["validation_checks"])
    assert {
        "manifest_request_id_present",
        "bbox_crs_present",
        "export_image_crs_present",
        "target_grid_crs_present",
        "categorical_resampling_safe",
        "tile_pixel_size_present",
        "tile_alignment_policy_present",
    } <= checks