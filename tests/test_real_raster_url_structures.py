from __future__ import annotations

import json
from pathlib import Path

from faster_raster.harmonization_planner import build_harmonization_plan, write_harmonization_plan
from faster_raster.manifest import read_manifest, write_manifest
from faster_raster.schemas import ResearchSpec, SourceRegistry
from faster_raster.source_registry import load_registry
from faster_raster.url_planner import plan_urls
from faster_raster.validation import validate_spec


GOLDEN = Path("/home/dmsrsic/raster-work/faster-raster/tests/golden")


def load_case(name: str, registry_key: str):
    spec = ResearchSpec.model_validate(json.loads((GOLDEN / f"research_spec_{name}.json").read_text()))
    registry = load_registry(GOLDEN / f"source_registry_{registry_key}.yaml")
    return spec, registry


def test_nlcd_tile_url_exact_documented_structure():
    row = read_manifest(GOLDEN / "acquisition_manifest_nlcd_aws_tile.jsonl")[0]

    assert (
        row["url"]
        == "https://usgs-landcover.s3.us-west-2.amazonaws.com/annual-nlcd/c1/v0/cu/tile/h14v15/Annual_NLCD_H14V15_FctImp_1985_CU_C1V0.tif"
    )


def test_nlcd_mosaic_url_exact_documented_structure():
    row = read_manifest(GOLDEN / "acquisition_manifest_nlcd_aws_mosaic.jsonl")[0]

    assert (
        row["url"]
        == "https://usgs-landcover.s3.us-west-2.amazonaws.com/annual-nlcd/c1/v0/cu/mosaic/Annual_NLCD_FctImp_1985_CU_C1V0.tif"
    )


def test_prism_daily_url_exact_documented_structure():
    row = read_manifest(GOLDEN / "acquisition_manifest_prism_daily_zip.jsonl")[0]

    assert (
        row["url"]
        == "https://data.prism.oregonstate.edu/time_series/us/an/4km/ppt/daily/2026/prism_ppt_us_25m_20260101.zip"
    )


def test_real_template_missing_source_specific_field_fails_clearly():
    spec, registry = load_case("nlcd_aws_tile", "annual_nlcd_aws_tile")
    raw = registry.model_dump()
    raw["sources"]["annual_nlcd_aws_tile"]["product_code"] = None

    errors = validate_spec(spec, SourceRegistry.model_validate(raw))

    assert "Source nlcd_tile missing template field: product_code" in errors


def test_nlcd_categorical_rejects_unsafe_resampling():
    spec, registry = load_case("nlcd_aws_tile", "annual_nlcd_aws_tile")
    raw_spec = spec.model_dump()
    raw_spec["sources"][0]["semantic_type"] = "categorical"
    raw_spec["sources"][0]["resampling"] = "bilinear"
    raw_registry = registry.model_dump()
    raw_registry["sources"]["annual_nlcd_aws_tile"]["semantic_type"] = "categorical"

    errors = validate_spec(ResearchSpec.model_validate(raw_spec), SourceRegistry.model_validate(raw_registry))

    assert "Categorical source nlcd_tile cannot use bilinear resampling" in errors


def test_prism_continuous_bilinear_permitted():
    spec, registry = load_case("prism_daily_zip", "prism_time_series_daily_zip")

    assert validate_spec(spec, registry) == []


def test_real_template_no_network_access(monkeypatch):
    def fail_network(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr("urllib.request.urlopen", fail_network)
    spec, registry = load_case("prism_daily_zip", "prism_time_series_daily_zip")

    assert len(plan_urls(spec, registry, GOLDEN / "research_spec_prism_daily_zip.json")) == 1


def test_real_url_golden_byte_stability(tmp_path):
    cases = [
        ("nlcd_aws_tile", "annual_nlcd_aws_tile"),
        ("nlcd_aws_mosaic", "annual_nlcd_aws_mosaic"),
        ("prism_daily_zip", "prism_time_series_daily_zip"),
    ]
    for name, registry_key in cases:
        spec, registry = load_case(name, registry_key)
        rows = plan_urls(spec, registry, GOLDEN / f"research_spec_{name}.json")
        manifest = tmp_path / f"acquisition_manifest_{name}.jsonl"
        plan = tmp_path / f"harmonization_plan_{name}.json"
        write_manifest(rows, manifest)
        write_harmonization_plan(build_harmonization_plan(spec, rows), plan)

        assert manifest.read_bytes() == (GOLDEN / f"acquisition_manifest_{name}.jsonl").read_bytes()
        assert plan.read_bytes() == (GOLDEN / f"harmonization_plan_{name}.json").read_bytes()
