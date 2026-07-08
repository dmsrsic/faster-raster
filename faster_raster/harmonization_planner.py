from __future__ import annotations

import json
from pathlib import Path

from faster_raster.manifest import read_manifest
from faster_raster.schemas import ResearchSpec
from faster_raster.validation import CATEGORICAL_FORBIDDEN_RESAMPLING


VALIDATION_CHECKS = [
    "manifest_request_id_present",
    "bbox_crs_present",
    "export_image_crs_present",
    "target_grid_crs_present",
    "categorical_resampling_safe",
    "tile_pixel_size_present",
    "tile_alignment_policy_present",
]


def planned_output_for(row: dict) -> str:
    crs_slug = row["target_grid_crs"].lower().replace(":", "")
    return (
        f"data/grid/{row['source_id']}_{row['year']}_{row['thematic_layer']}"
        f"_tile_{row['tile_id']}_{crs_slug}_{row['target_resolution_m']}m.tif"
    )


def build_harmonization_plan(spec: ResearchSpec, manifest_rows: list[dict]) -> dict:
    inputs = []
    for row in manifest_rows:
        forbidden = sorted(CATEGORICAL_FORBIDDEN_RESAMPLING - {"average"})
        inputs.append(
            {
                "request_id": row["request_id"],
                "source_bbox": row["bbox"],
                "bbox_crs": row["bbox_crs"],
                "source_crs": row["export_image_crs"],
                "export_image_crs": row["export_image_crs"],
                "target_crs": row["target_grid_crs"],
                "target_grid_crs": row["target_grid_crs"],
                "tile_width_pixels": row["tile_width_pixels"],
                "tile_height_pixels": row["tile_height_pixels"],
                "tile_planning_crs": row["tile_planning_crs"],
                "semantic_type": row["semantic_type"],
                "resampling": row["resampling"],
                "forbidden_resampling": forbidden,
                "planned_output": planned_output_for(row),
            }
        )
    return {
        "project_id": spec.project.id,
        "target_grid": {
            "crs": spec.target_grid.crs,
            "resolution_m": spec.target_grid.resolution_m,
            "nodata": spec.target_grid.nodata,
            "snap": spec.target_grid.snap,
        },
        "inputs": sorted(inputs, key=lambda item: item["request_id"]),
        "validation_checks": VALIDATION_CHECKS,
    }


def write_harmonization_plan(plan: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(plan, handle, indent=2, sort_keys=True)
        handle.write("\n")


def read_harmonization_plan(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def summarize_harmonization_plan(plan: dict) -> dict:
    return {
        "project_id": plan["project_id"],
        "inputs": len(plan["inputs"]),
        "target_crs": plan["target_grid"]["crs"],
        "resolution_m": plan["target_grid"]["resolution_m"],
        "validation_checks": len(plan["validation_checks"]),
    }


def plan_from_manifest(spec: ResearchSpec, manifest_path: Path) -> dict:
    return build_harmonization_plan(spec, read_manifest(manifest_path))
