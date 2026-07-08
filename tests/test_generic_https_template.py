from __future__ import annotations

from pathlib import Path

import pytest

from faster_raster.harmonization_planner import build_harmonization_plan
from faster_raster.manifest import write_manifest
from faster_raster.schemas import ResearchSpec, SourceRegistry
from faster_raster.source_registry import load_registry
from faster_raster.url_planner import plan_urls
from faster_raster.validation import validate_spec


def generic_spec_raw(valid_spec_raw: dict) -> dict:
    raw = valid_spec_raw
    raw["project"]["id"] = "generic_demo_cog_v001"
    raw["sources"] = [
        {
            "id": "demo_cog",
            "registry_key": "generic_demo_cog",
            "years": [2023, 2024],
            "thematic_layers": ["ndvi", "elevation"],
            "acquisition_mode": "https_template",
            "semantic_type": "continuous",
            "resampling": "bilinear",
        }
    ]
    return raw


def test_generic_url_template_byte_stability(valid_spec_raw, project_spec_path, tmp_path):
    spec = ResearchSpec.model_validate(generic_spec_raw(valid_spec_raw))
    registry = load_registry()
    first = plan_urls(spec, registry, project_spec_path)
    second = plan_urls(spec, registry, project_spec_path)
    left = tmp_path / "left.jsonl"
    right = tmp_path / "right.jsonl"

    write_manifest(first, left)
    write_manifest(second, right)

    assert first == second
    assert left.read_bytes() == right.read_bytes()


def test_generic_placeholder_replacement_correctness(valid_spec_raw, project_spec_path):
    spec = ResearchSpec.model_validate(generic_spec_raw(valid_spec_raw))
    rows = plan_urls(spec, load_registry(), project_spec_path)

    assert rows[0]["request_id"] == "demo_cog_2023_elevation_tile_000001"
    assert rows[0]["url"] == "https://example.invalid/rasters/demo-cog/2023/elevation/000001.tif"
    assert rows[-1]["url"] == "https://example.invalid/rasters/demo-cog/2024/ndvi/000001.tif"


def test_generic_unknown_placeholder_fails_clearly(valid_spec_raw):
    spec = ResearchSpec.model_validate(generic_spec_raw(valid_spec_raw))
    raw = load_registry().model_dump()
    raw["sources"]["generic_demo_cog"]["url_template"] = "https://example.invalid/{unknown}.tif"
    registry = SourceRegistry.model_validate(raw)

    errors = validate_spec(spec, registry)

    assert "Unknown URL template placeholder(s): ['unknown']" in errors


def test_generic_missing_url_template_fails_clearly(valid_spec_raw):
    spec = ResearchSpec.model_validate(generic_spec_raw(valid_spec_raw))
    raw = load_registry().model_dump()
    raw["sources"]["generic_demo_cog"]["url_template"] = None
    registry = SourceRegistry.model_validate(raw)

    errors = validate_spec(spec, registry)

    assert "Source demo_cog missing url_template" in errors


def test_generic_continuous_bilinear_resampling_accepted(valid_spec_raw):
    spec = ResearchSpec.model_validate(generic_spec_raw(valid_spec_raw))

    assert validate_spec(spec, load_registry()) == []


def test_generic_categorical_bilinear_rejected(valid_spec_raw):
    raw = generic_spec_raw(valid_spec_raw)
    raw["sources"][0]["semantic_type"] = "categorical"
    raw["sources"][0]["resampling"] = "bilinear"
    spec = ResearchSpec.model_validate(raw)
    registry_raw = load_registry().model_dump()
    registry_raw["sources"]["generic_demo_cog"]["semantic_type"] = "categorical"
    registry = SourceRegistry.model_validate(registry_raw)

    errors = validate_spec(spec, registry)

    assert "Categorical source demo_cog cannot use bilinear resampling" in errors


def test_harmonization_accepts_generic_rows(valid_spec_raw, project_spec_path):
    spec = ResearchSpec.model_validate(generic_spec_raw(valid_spec_raw))
    rows = plan_urls(spec, load_registry(), project_spec_path)
    plan = build_harmonization_plan(spec, rows)

    assert len(plan["inputs"]) == 4
    assert plan["inputs"][0]["request_id"] == "demo_cog_2023_elevation_tile_000001"
    assert plan["inputs"][0]["export_image_crs"] == "EPSG:4326"


def test_generic_no_network_access(monkeypatch, valid_spec_raw, project_spec_path):
    def fail_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr("urllib.request.urlopen", fail_network)
    spec = ResearchSpec.model_validate(generic_spec_raw(valid_spec_raw))

    assert len(plan_urls(spec, load_registry(), project_spec_path)) == 4
