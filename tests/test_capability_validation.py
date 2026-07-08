from __future__ import annotations

from faster_raster.schemas import ResearchSpec, SourceRegistry
from faster_raster.url_planner import plan_urls
from faster_raster.validation import validate_spec


def registry_mutation(registry, **updates):
    raw = registry.model_dump()
    raw["sources"]["usda_nass_cdl_imageserver"].update(updates)
    return SourceRegistry.model_validate(raw)


def test_unsupported_adapter_rejected_before_planning(valid_spec, registry):
    mutated = registry_mutation(registry, adapter="stac")

    errors = validate_spec(valid_spec, mutated)

    assert "Unsupported adapter for v0: stac" in errors


def test_missing_bboxsr_support_rejected_before_planning(valid_spec, registry):
    mutated = registry_mutation(registry, supports_bbox_crs_param=False)

    errors = validate_spec(valid_spec, mutated)

    assert "Source cdl must support bbox CRS parameter for v0 ArcGIS planning" in errors


def test_missing_url_param_names_rejected_before_planning(valid_spec, registry):
    mutated = registry_mutation(registry, bbox_param="", bbox_crs_param="", image_crs_param="")

    errors = validate_spec(valid_spec, mutated)

    assert "Source cdl missing bbox_param" in errors
    assert "Source cdl missing bbox_crs_param" in errors
    assert "Source cdl missing image_crs_param" in errors


def test_unsupported_year_strategy_rejected_before_planning(valid_spec, registry):
    mutated = registry_mutation(registry, year_parameter_strategy="mosaic_rule_by_attribute")

    errors = validate_spec(valid_spec, mutated)

    assert "Unsupported year_parameter_strategy for source cdl: mosaic_rule_by_attribute" in errors


def test_unsupported_bbox_transform_rejected_before_planning(valid_spec_raw, registry):
    valid_spec_raw["aoi"]["input_crs"] = "EPSG:5070"
    spec = ResearchSpec.model_validate(valid_spec_raw)
    mutated = registry_mutation(registry, bbox_request_policy="project_bbox_to_service_crs")

    errors = validate_spec(spec, mutated)

    assert any("UnsupportedCRSTransform: EPSG:5070 -> EPSG:3857" in error for error in errors)


def test_capability_validation_runs_before_url_rows(valid_spec, registry, project_spec_path):
    mutated = registry_mutation(registry, year_parameter_strategy="mosaic_rule_by_attribute")

    try:
        plan_urls(valid_spec, mutated, project_spec_path)
    except ValueError as exc:
        assert "Unsupported year_parameter_strategy" in str(exc)
    else:
        raise AssertionError("planning should fail before rows are generated")