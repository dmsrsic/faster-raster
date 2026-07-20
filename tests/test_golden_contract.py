from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from faster_raster.harmonization_planner import build_harmonization_plan, write_harmonization_plan
from faster_raster.manifest import read_manifest, write_manifest
from faster_raster.source_registry import load_registry
from faster_raster.url_planner import plan_urls
from faster_raster.validation import load_spec


ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = ROOT / "tests" / "golden"
REQUIRED_MANIFEST_FIELDS = {
    "bbox_crs",
    "export_image_crs",
    "target_grid_crs",
    "tile_planning_crs",
    "tile_width_pixels",
    "tile_height_pixels",
}


def generate_manifest_and_plan(spec_name: str, tmp_path: Path) -> tuple[Path, Path]:
    spec_path = GOLDEN_DIR / spec_name
    registry = load_registry(GOLDEN_DIR / "source_registry_cdl.yaml")
    if "project_bbox" in spec_name:
        raw = registry.model_dump()
        raw["sources"]["usda_nass_cdl_imageserver"]["bbox_request_policy"] = "project_bbox_to_service_crs"
        from faster_raster.schemas import SourceRegistry

        registry = SourceRegistry.model_validate(raw)
    spec = load_spec(spec_path)
    rows = plan_urls(spec, registry, spec_path)
    manifest_path = tmp_path / spec_name.replace("research_spec", "acquisition_manifest").replace(".json", ".jsonl")
    plan_path = tmp_path / spec_name.replace("research_spec", "harmonization_plan")
    write_manifest(rows, manifest_path)
    write_harmonization_plan(build_harmonization_plan(spec, rows), plan_path)
    return manifest_path, plan_path


def test_generated_preserve_bbox_manifest_matches_golden(tmp_path):
    manifest_path, _ = generate_manifest_and_plan("research_spec_preserve_bbox.json", tmp_path)

    assert manifest_path.read_bytes() == (GOLDEN_DIR / "acquisition_manifest_preserve_bbox.jsonl").read_bytes()


def test_generated_project_bbox_manifest_matches_golden(tmp_path):
    manifest_path, _ = generate_manifest_and_plan("research_spec_project_bbox.json", tmp_path)

    assert manifest_path.read_bytes() == (GOLDEN_DIR / "acquisition_manifest_project_bbox.jsonl").read_bytes()


def test_generated_harmonization_plans_match_golden(tmp_path):
    _, preserve_plan = generate_manifest_and_plan("research_spec_preserve_bbox.json", tmp_path)
    _, project_plan = generate_manifest_and_plan("research_spec_project_bbox.json", tmp_path)

    assert preserve_plan.read_bytes() == (GOLDEN_DIR / "harmonization_plan_preserve_bbox.json").read_bytes()
    assert project_plan.read_bytes() == (GOLDEN_DIR / "harmonization_plan_project_bbox.json").read_bytes()


def test_golden_manifest_url_params_match_expected():
    preserve = read_manifest(GOLDEN_DIR / "acquisition_manifest_preserve_bbox.jsonl")[0]
    project = read_manifest(GOLDEN_DIR / "acquisition_manifest_project_bbox.jsonl")[0]

    assert parse_qs(urlparse(preserve["url"]).query) == {
        "bbox": ["-83.20000000,39.80000000,-82.90000000,40.10000000"],
        "bboxSR": ["4326"],
        "f": ["image"],
        "format": ["tiff"],
        "imageSR": ["3857"],
        "size": ["1114,1453"],
        "time": ["2023"],
    }
    assert parse_qs(urlparse(project["url"]).query) == {
        "bbox": ["-9261781.63400036,4836921.24639985,-9228385.78676238,4880484.66566228"],
        "bboxSR": ["3857"],
        "f": ["image"],
        "format": ["tiff"],
        "imageSR": ["3857"],
        "size": ["1114,1453"],
        "time": ["2023"],
    }


def test_golden_manifest_rows_have_explicit_contract_fields():
    for path in [
        GOLDEN_DIR / "acquisition_manifest_preserve_bbox.jsonl",
        GOLDEN_DIR / "acquisition_manifest_project_bbox.jsonl",
    ]:
        for row in read_manifest(path):
            assert REQUIRED_MANIFEST_FIELDS <= set(row)


def test_golden_harmonization_request_ids_match_manifest_ids():
    for name in ["preserve_bbox", "project_bbox", "generic_https"]:
        rows = read_manifest(GOLDEN_DIR / f"acquisition_manifest_{name}.jsonl")
        plan = json.loads((GOLDEN_DIR / f"harmonization_plan_{name}.json").read_text(encoding="utf-8"))

        assert sorted(row["request_id"] for row in rows) == sorted(item["request_id"] for item in plan["inputs"])


def test_generated_generic_https_manifest_matches_golden(tmp_path):
    spec_path = GOLDEN_DIR / "research_spec_generic_https.json"
    registry = load_registry(GOLDEN_DIR / "source_registry_generic.yaml")
    spec = load_spec(spec_path)
    rows = plan_urls(spec, registry, spec_path)
    manifest_path = tmp_path / "acquisition_manifest_generic_https.jsonl"
    write_manifest(rows, manifest_path)

    assert manifest_path.read_bytes() == (GOLDEN_DIR / "acquisition_manifest_generic_https.jsonl").read_bytes()


def test_generated_generic_https_harmonization_matches_golden(tmp_path):
    spec_path = GOLDEN_DIR / "research_spec_generic_https.json"
    registry = load_registry(GOLDEN_DIR / "source_registry_generic.yaml")
    spec = load_spec(spec_path)
    rows = plan_urls(spec, registry, spec_path)
    plan_path = tmp_path / "harmonization_plan_generic_https.json"
    write_harmonization_plan(build_harmonization_plan(spec, rows), plan_path)

    assert plan_path.read_bytes() == (GOLDEN_DIR / "harmonization_plan_generic_https.json").read_bytes()
