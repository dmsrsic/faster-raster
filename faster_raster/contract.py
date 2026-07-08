from __future__ import annotations

import tempfile
from pathlib import Path

from faster_raster import __version__
from faster_raster.crs import UnsupportedCRSTransform, transform_bbox
from faster_raster.harmonization_planner import build_harmonization_plan, write_harmonization_plan
from faster_raster.manifest import write_manifest
from faster_raster.schemas import ResearchSpec, SourceRegistry
from faster_raster.source_registry import load_registry
from faster_raster.url_planner import plan_urls
from faster_raster.validation import load_spec, validate_spec


GOLDEN_DIR = Path(__file__).resolve().parent.parent / "tests" / "golden"
GOLDEN_FIXTURES = [
    "source_registry_cdl.yaml",
    "research_spec_preserve_bbox.json",
    "research_spec_project_bbox.json",
    "acquisition_manifest_preserve_bbox.jsonl",
    "acquisition_manifest_project_bbox.jsonl",
    "harmonization_plan_preserve_bbox.json",
    "harmonization_plan_project_bbox.json",
    "source_registry_generic.yaml",
    "research_spec_generic_https.json",
    "acquisition_manifest_generic_https.jsonl",
    "harmonization_plan_generic_https.json",
    "source_registry_annual_nlcd_aws_tile.yaml",
    "source_registry_annual_nlcd_aws_mosaic.yaml",
    "source_registry_prism_time_series_daily_zip.yaml",
    "research_spec_nlcd_aws_tile.json",
    "research_spec_nlcd_aws_mosaic.json",
    "research_spec_prism_daily_zip.json",
    "acquisition_manifest_nlcd_aws_tile.jsonl",
    "acquisition_manifest_nlcd_aws_mosaic.jsonl",
    "acquisition_manifest_prism_daily_zip.jsonl",
    "harmonization_plan_nlcd_aws_tile.json",
    "harmonization_plan_nlcd_aws_mosaic.json",
    "harmonization_plan_prism_daily_zip.json",
]


def transform_status(spec: ResearchSpec, entry) -> str:
    if entry.bbox_request_policy != "project_bbox_to_service_crs":
        return "not_required"
    try:
        transform_bbox([0.0, 0.0, 1.0, 1.0], spec.aoi.input_crs, entry.service_crs)
    except UnsupportedCRSTransform as exc:
        return str(exc)
    return "supported"


def inspect_contract(
    spec_path: Path,
    registry_path: Path | None = None,
    check_goldens: bool = False,
    golden_dir: Path = GOLDEN_DIR,
) -> dict:
    spec = load_spec(spec_path)
    registry = load_registry(registry_path)
    spec_errors = validate_spec(spec, registry)
    sources = []
    for source in sorted(spec.sources, key=lambda item: item.id):
        entry = registry.sources.get(source.registry_key)
        if entry is None:
            sources.append(
                {
                    "source_id": source.id,
                    "registry_key": source.registry_key,
                    "capability_status": "FAIL",
                    "errors": [f"Unknown registry_key: {source.registry_key}"],
                }
            )
            continue
        source_errors = [error for error in spec_errors if f" {source.id} " in error or error.endswith(f": {entry.adapter}")]
        sources.append(
            {
                "source_id": source.id,
                "registry_key": source.registry_key,
                "adapter": entry.adapter,
                "provider": entry.provider,
                "product": entry.product,
                "acquisition_mode": source.acquisition_mode,
                "bbox_request_policy": entry.bbox_request_policy,
                "supports_bbox_crs_param": entry.supports_bbox_crs_param,
                "service_crs": entry.service_crs,
                "default_export_image_crs": entry.default_export_image_crs or entry.service_crs,
                "target_grid_crs": spec.target_grid.crs,
                "year_parameter_strategy": entry.year_parameter_strategy,
                "supported_crs_transform_status": transform_status(spec, entry),
                "semantic_type": source.semantic_type,
                "resampling": source.resampling,
                "max_width": entry.max_width,
                "max_height": entry.max_height,
                "capability_status": "PASS" if not spec_errors else "FAIL",
                "errors": source_errors,
            }
        )
    result = {
        "package_version": __version__,
        "project_id": spec.project.id,
        "source_count": len(spec.sources),
        "sources": sources,
        "overall_status": "PASS" if not spec_errors else "FAIL",
        "errors": spec_errors,
    }
    if check_goldens:
        result["golden_check"] = check_golden_fixtures(golden_dir)
    return result


def check_golden_fixtures(golden_dir: Path = GOLDEN_DIR) -> dict:
    presence = [{"path": str(golden_dir / name), "present": (golden_dir / name).exists()} for name in GOLDEN_FIXTURES]
    comparisons = []
    if all(item["present"] for item in presence):
        registry = load_registry(golden_dir / "source_registry_cdl.yaml")
        with tempfile.TemporaryDirectory(prefix="faster_raster_goldens_") as tmp:
            tmp_dir = Path(tmp)
            for name in ["preserve_bbox", "project_bbox"]:
                spec_path = golden_dir / f"research_spec_{name}.json"
                local_registry = registry
                if name == "project_bbox":
                    raw = registry.model_dump()
                    raw["sources"]["usda_nass_cdl_imageserver"]["bbox_request_policy"] = "project_bbox_to_service_crs"
                    local_registry = SourceRegistry.model_validate(raw)
                spec = load_spec(spec_path)
                rows = plan_urls(spec, local_registry, spec_path)
                manifest_path = tmp_dir / f"acquisition_manifest_{name}.jsonl"
                plan_path = tmp_dir / f"harmonization_plan_{name}.json"
                write_manifest(rows, manifest_path)
                write_harmonization_plan(build_harmonization_plan(spec, rows), plan_path)
                for generated, golden in [
                    (manifest_path, golden_dir / f"acquisition_manifest_{name}.jsonl"),
                    (plan_path, golden_dir / f"harmonization_plan_{name}.json"),
                ]:
                    comparisons.append(
                        {
                            "fixture": golden.name,
                            "matches": generated.read_bytes() == golden.read_bytes(),
                        }
                    )
            generic_registry = load_registry(golden_dir / "source_registry_generic.yaml")
            generic_spec_path = golden_dir / "research_spec_generic_https.json"
            generic_spec = load_spec(generic_spec_path)
            generic_rows = plan_urls(generic_spec, generic_registry, generic_spec_path)
            generic_manifest = tmp_dir / "acquisition_manifest_generic_https.jsonl"
            generic_plan = tmp_dir / "harmonization_plan_generic_https.json"
            write_manifest(generic_rows, generic_manifest)
            write_harmonization_plan(build_harmonization_plan(generic_spec, generic_rows), generic_plan)
            for generated, golden in [
                (generic_manifest, golden_dir / "acquisition_manifest_generic_https.jsonl"),
                (generic_plan, golden_dir / "harmonization_plan_generic_https.json"),
            ]:
                comparisons.append(
                    {
                        "fixture": golden.name,
                        "matches": generated.read_bytes() == golden.read_bytes(),
                    }
                )
            for name, registry_name in [
                ("nlcd_aws_tile", "annual_nlcd_aws_tile"),
                ("nlcd_aws_mosaic", "annual_nlcd_aws_mosaic"),
                ("prism_daily_zip", "prism_time_series_daily_zip"),
            ]:
                registry = load_registry(golden_dir / f"source_registry_{registry_name}.yaml")
                spec_path = golden_dir / f"research_spec_{name}.json"
                spec = load_spec(spec_path)
                rows = plan_urls(spec, registry, spec_path)
                manifest = tmp_dir / f"acquisition_manifest_{name}.jsonl"
                plan = tmp_dir / f"harmonization_plan_{name}.json"
                write_manifest(rows, manifest)
                write_harmonization_plan(build_harmonization_plan(spec, rows), plan)
                for generated, golden in [
                    (manifest, golden_dir / f"acquisition_manifest_{name}.jsonl"),
                    (plan, golden_dir / f"harmonization_plan_{name}.json"),
                ]:
                    comparisons.append(
                        {
                            "fixture": golden.name,
                            "matches": generated.read_bytes() == golden.read_bytes(),
                        }
                    )
    return {
        "expected": len(GOLDEN_FIXTURES),
        "present": sum(1 for item in presence if item["present"]),
        "missing": [item["path"] for item in presence if not item["present"]],
        "comparisons": comparisons,
        "matches": sum(1 for item in comparisons if item["matches"]),
        "drift": [item["fixture"] for item in comparisons if not item["matches"]],
        "status": "PASS" if all(item["present"] for item in presence) and all(item["matches"] for item in comparisons) else "FAIL",
    }
