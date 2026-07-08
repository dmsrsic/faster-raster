from __future__ import annotations

import pytest
from pydantic import ValidationError

from faster_raster.schemas import SourceRegistry
from faster_raster.source_registry import get_registry_entry, load_registry


REQUIRED_PARAM_FIELDS = [
    "bbox_param",
    "bbox_crs_param",
    "image_crs_param",
    "size_param",
    "format_param",
    "response_format_param",
    "time_param",
]


def test_source_registry_loads(registry):
    assert "usda_nass_cdl_imageserver" in registry.sources


def test_usda_cdl_registry_entry_resolves(registry):
    entry = get_registry_entry("usda_nass_cdl_imageserver", registry)

    assert entry.adapter == "arcgis_imageserver"
    assert entry.provider == "USDA_NASS"
    assert entry.product == "Cropland Data Layer"
    assert entry.service_crs == "EPSG:3857"
    assert entry.default_export_image_crs == "EPSG:3857"
    assert entry.bbox_request_policy == "preserve_input_bbox_with_bboxsr"
    assert entry.supports_bbox_crs_param is True
    assert entry.semantic_type == "categorical"
    assert entry.year_parameter_strategy == "time_value"
    assert entry.time_value == "{year}"


def test_missing_registry_key_gives_clear_error(registry):
    with pytest.raises(ValueError) as excinfo:
        get_registry_entry("missing_key", registry)

    assert "Unknown registry_key: missing_key" in str(excinfo.value)


def test_adapter_type_is_required(registry):
    raw = registry.model_dump()
    del raw["sources"]["usda_nass_cdl_imageserver"]["adapter"]

    with pytest.raises(ValidationError) as excinfo:
        SourceRegistry.model_validate(raw)

    assert "adapter" in str(excinfo.value)


def test_url_parameter_field_names_are_present(registry):
    entry = get_registry_entry("usda_nass_cdl_imageserver", registry)

    for field in REQUIRED_PARAM_FIELDS:
        assert getattr(entry, field)
