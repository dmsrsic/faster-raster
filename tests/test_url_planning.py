from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from faster_raster.crs import transform_bbox
from faster_raster.manifest import write_manifest
from faster_raster.schemas import ResearchSpec, SourceRegistry
from faster_raster.url_planner import plan_urls


REQUIRED_MANIFEST_FIELDS = {
    "request_id",
    "source_id",
    "registry_key",
    "adapter",
    "provider",
    "product",
    "year",
    "thematic_layer",
    "tile_id",
    "tile_row",
    "tile_col",
    "aoi_path",
    "source_aoi_bbox",
    "source_aoi_crs",
    "bbox",
    "bbox_crs",
    "export_image_crs",
    "target_grid_crs",
    "target_resolution_m",
    "semantic_type",
    "resampling",
    "tile_width_pixels",
    "tile_height_pixels",
    "tile_planning_crs",
    "url",
    "status",
}


def registry_with_policy(registry, policy: str, service_crs: str = "EPSG:3857"):
    raw = registry.model_dump()
    entry = raw["sources"]["usda_nass_cdl_imageserver"]
    entry["bbox_request_policy"] = policy
    entry["service_crs"] = service_crs
    entry["default_export_image_crs"] = service_crs
    return SourceRegistry.model_validate(raw)


def parsed_params(row: dict) -> dict[str, list[str]]:
    return parse_qs(urlparse(row["url"]).query)


def test_manifest_crs_contract_fields_are_explicit(valid_spec, registry, project_spec_path):
    row = plan_urls(valid_spec, registry, project_spec_path)[0]

    assert row["source_aoi_crs"] == "EPSG:4326"
    assert row["bbox_crs"] == "EPSG:4326"
    assert row["export_image_crs"] == "EPSG:3857"
    assert row["target_grid_crs"] == "EPSG:5070"
    assert row["tile_planning_crs"] == "EPSG:3857"
    assert "request_crs" not in row
    assert "target_crs" not in row
    assert "size" not in row


def test_preserve_input_bbox_policy_exact_url_params(valid_spec, registry, project_spec_path):
    rows = plan_urls(valid_spec, registry_with_policy(registry, "preserve_input_bbox_with_bboxsr"), project_spec_path)
    row = rows[0]
    params = parsed_params(row)

    assert row["source_aoi_bbox"] == [-83.2, 39.8, -82.9, 40.1]
    assert row["bbox"] == [-83.2, 39.8, -82.9, 40.1]
    assert row["bbox_crs"] == "EPSG:4326"
    assert params == {
        "bbox": ["-83.20000000,39.80000000,-82.90000000,40.10000000"],
        "bboxSR": ["4326"],
        "f": ["image"],
        "format": ["tiff"],
        "imageSR": ["3857"],
        "size": [f"{row['tile_width_pixels']},{row['tile_height_pixels']}"],
        "time": ["2023"],
    }


def test_project_bbox_to_service_crs_policy_exact_url_params(valid_spec, registry, project_spec_path):
    rows = plan_urls(valid_spec, registry_with_policy(registry, "project_bbox_to_service_crs"), project_spec_path)
    row = rows[0]
    params = parsed_params(row)
    projected = transform_bbox([-83.2, 39.8, -82.9, 40.1], "EPSG:4326", "EPSG:3857")
    bbox_text = ",".join(f"{value:.8f}" for value in projected)

    assert row["source_aoi_bbox"] == [-83.2, 39.8, -82.9, 40.1]
    assert row["source_aoi_crs"] == "EPSG:4326"
    assert row["bbox"] == projected
    assert row["bbox_crs"] == "EPSG:3857"
    assert params == {
        "bbox": [bbox_text],
        "bboxSR": ["3857"],
        "f": ["image"],
        "format": ["tiff"],
        "imageSR": ["3857"],
        "size": [f"{row['tile_width_pixels']},{row['tile_height_pixels']}"],
        "time": ["2023"],
    }


def test_unsupported_crs_transform_fails_clearly(valid_spec_raw, registry, project_spec_path):
    valid_spec_raw["aoi"]["input_crs"] = "EPSG:5070"
    spec = ResearchSpec.model_validate(valid_spec_raw)
    projected_registry = registry_with_policy(registry, "project_bbox_to_service_crs")

    with pytest.raises(ValueError, match="UnsupportedCRSTransform: EPSG:5070 -> EPSG:3857"):
        plan_urls(spec, projected_registry, project_spec_path)


def test_url_planning_is_deterministic(valid_spec, registry, project_spec_path, tmp_path):
    first = plan_urls(valid_spec, registry, project_spec_path)
    second = plan_urls(valid_spec, registry, project_spec_path)
    assert first == second

    left = tmp_path / "left.jsonl"
    right = tmp_path / "right.jsonl"
    write_manifest(first, left)
    write_manifest(second, right)
    assert left.read_bytes() == right.read_bytes()


def test_golden_manifest_bytes_are_stable(valid_spec, registry, project_spec_path, tmp_path):
    rows = plan_urls(valid_spec, registry, project_spec_path)
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    write_manifest(rows, first)
    write_manifest(plan_urls(valid_spec, registry, project_spec_path), second)

    assert first.read_bytes() == second.read_bytes()
    assert first.read_text(encoding="utf-8").splitlines()[0].startswith('{"adapter":"arcgis_imageserver"')


def test_row_ordering_is_deterministic(valid_spec, registry, project_spec_path):
    rows = plan_urls(valid_spec, registry, project_spec_path)
    ordering = [(row["source_id"], row["year"], row["thematic_layer"], row["tile_id"]) for row in rows]
    assert ordering == sorted(ordering)


def test_request_ids_are_deterministic(valid_spec, registry, project_spec_path):
    rows = plan_urls(valid_spec, registry, project_spec_path)
    assert [row["request_id"] for row in rows] == [
        "cdl_2023_crop_type_tile_000001",
        "cdl_2024_crop_type_tile_000001",
    ]


def test_url_params_match_registry_defined_names(valid_spec, registry, project_spec_path):
    row = plan_urls(valid_spec, registry, project_spec_path)[0]
    entry = registry.sources["usda_nass_cdl_imageserver"]
    params = parsed_params(row)

    for name in [entry.bbox_param, entry.bbox_crs_param, entry.image_crs_param, entry.size_param, entry.format_param, entry.response_format_param, entry.time_param]:
        assert name in params


def test_manifest_row_has_all_required_fields(valid_spec, registry, project_spec_path):
    row = plan_urls(valid_spec, registry, project_spec_path)[0]
    assert REQUIRED_MANIFEST_FIELDS <= set(row)


def test_no_network_access_is_performed(monkeypatch, valid_spec, registry, project_spec_path):
    def fail_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr("urllib.request.urlopen", fail_network)
    rows = plan_urls(valid_spec, registry, project_spec_path)
    assert len(rows) == 2


def test_mosaic_rule_by_attribute_strategy_is_reserved(valid_spec, registry, project_spec_path):
    raw = registry.model_dump()
    raw["sources"]["usda_nass_cdl_imageserver"]["year_parameter_strategy"] = "mosaic_rule_by_attribute"
    reserved_registry = SourceRegistry.model_validate(raw)

    with pytest.raises(ValueError, match="Unsupported year_parameter_strategy"):
        plan_urls(valid_spec, reserved_registry, project_spec_path)