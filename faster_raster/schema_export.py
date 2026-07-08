from __future__ import annotations

import json
from pathlib import Path


SCHEMA_FILENAMES = [
    "research_spec.schema.json",
    "source_registry.schema.json",
    "acquisition_manifest_row.schema.json",
    "harmonization_plan.schema.json",
    "inspect_contract_report.schema.json",
]


def string_schema(description: str | None = None, enum: list[str] | None = None) -> dict:
    schema: dict = {"type": "string"}
    if description:
        schema["description"] = description
    if enum:
        schema["enum"] = enum
    return schema


def number_schema() -> dict:
    return {"type": "number"}


def integer_schema() -> dict:
    return {"type": "integer"}


def bbox_schema() -> dict:
    return {
        "type": "array",
        "items": {"type": "number"},
        "minItems": 4,
        "maxItems": 4,
    }


def research_spec_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "FasterRaster research_spec.json",
        "type": "object",
        "additionalProperties": False,
        "required": ["project", "aoi", "target_grid", "sources", "outputs"],
        "properties": {
            "project": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id"],
                "properties": {
                    "id": string_schema("Stable project identifier."),
                    "created": string_schema("Creation date or timestamp."),
                },
            },
            "aoi": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path"],
                "properties": {
                    "path": string_schema("Path to AOI GeoJSON."),
                    "input_crs": string_schema("CRS of AOI coordinates."),
                    "bbox_policy": string_schema("AOI-to-bbox policy."),
                },
            },
            "target_grid": {
                "type": "object",
                "additionalProperties": False,
                "required": ["crs", "resolution_m", "nodata"],
                "properties": {
                    "crs": string_schema("Target harmonization CRS."),
                    "resolution_m": number_schema(),
                    "snap": string_schema("Target grid snap policy."),
                    "nodata": number_schema(),
                },
            },
            "sources": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "id",
                        "registry_key",
                        "years",
                        "thematic_layers",
                        "acquisition_mode",
                        "semantic_type",
                        "resampling",
                    ],
                    "properties": {
                        "id": string_schema(),
                        "registry_key": string_schema(),
                        "years": {"type": "array", "items": integer_schema(), "minItems": 1},
                        "thematic_layers": {"type": "array", "items": string_schema(), "minItems": 1},
                        "acquisition_mode": string_schema(enum=["arcgis_export_image", "https_template"]),
                        "semantic_type": string_schema(enum=["categorical", "continuous"]),
                        "resampling": string_schema(enum=["nearest", "mode", "bilinear", "cubic", "lanczos", "average"]),
                    },
                },
            },
            "outputs": {
                "type": "object",
                "additionalProperties": False,
                "required": ["manifest_dir", "plan_dir", "raster_format"],
                "properties": {
                    "manifest_dir": string_schema(),
                    "plan_dir": string_schema(),
                    "raster_format": string_schema(),
                },
            },
        },
    }


def source_registry_schema() -> dict:
    entry_required = ["adapter", "provider", "product", "semantic_type"]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "FasterRaster source_registry.yaml",
        "type": "object",
        "additionalProperties": False,
        "required": ["sources"],
        "properties": {
            "sources": {
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "additionalProperties": True,
                    "required": entry_required,
                    "properties": {
                        "adapter": string_schema(enum=["arcgis_imageserver", "generic_https_template"]),
                        "provider": string_schema(),
                        "product": string_schema(),
                        "base_url": string_schema(),
                        "operation": string_schema(),
                        "bbox_param": string_schema(),
                        "bbox_crs_param": string_schema(),
                        "image_crs_param": string_schema(),
                        "size_param": string_schema(),
                        "format_param": string_schema(),
                        "response_format_param": string_schema(),
                        "default_image_format": string_schema(),
                        "default_response_format": string_schema(),
                        "max_width": integer_schema(),
                        "max_height": integer_schema(),
                        "service_crs": string_schema(),
                        "default_export_image_crs": string_schema(),
                        "bbox_request_policy": string_schema(
                            enum=[
                                "preserve_input_bbox_with_bboxsr",
                                "project_bbox_to_service_crs",
                                "no_bbox_url_template",
                            ]
                        ),
                        "supports_bbox_crs_param": {"type": "boolean"},
                        "url_template": string_schema(),
                        "product_slug": string_schema(),
                        "region": string_schema(),
                        "h": string_schema(),
                        "v": string_schema(),
                        "template_tile_id": string_schema(),
                        "product_code": string_schema(),
                        "collection": string_schema(),
                        "version": string_schema(),
                        "variable": string_schema(),
                        "yyyymmdd": string_schema(),
                        "resolution": string_schema(),
                        "temporal_frequency": string_schema(),
                        "native_crs": string_schema(),
                        "supports_tiling": {"type": "boolean"},
                        "default_format": string_schema(),
                        "semantic_type": string_schema(enum=["categorical", "continuous"]),
                        "supported_years": {"type": "array", "items": integer_schema()},
                        "native_pixel_type": string_schema(),
                        "time_parameter_strategy": string_schema(),
                        "year_parameter_strategy": string_schema(enum=["time_value", "mosaic_rule_by_attribute"]),
                        "time_param": string_schema(),
                        "time_value": string_schema(),
                        "mosaic_rule_param": string_schema(),
                    },
                },
            }
        },
    }


def acquisition_manifest_row_schema() -> dict:
    required = [
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
        "tile_width_pixels",
        "tile_height_pixels",
        "tile_planning_crs",
        "semantic_type",
        "resampling",
        "url",
        "status",
    ]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "FasterRaster acquisition_manifest.jsonl row",
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": {
            "request_id": string_schema(),
            "source_id": string_schema(),
            "registry_key": string_schema(),
            "adapter": string_schema(enum=["arcgis_imageserver", "generic_https_template"]),
            "provider": string_schema(),
            "product": string_schema(),
            "year": integer_schema(),
            "thematic_layer": string_schema(),
            "tile_id": string_schema(),
            "tile_row": integer_schema(),
            "tile_col": integer_schema(),
            "aoi_path": string_schema(),
            "source_aoi_bbox": bbox_schema(),
            "source_aoi_crs": string_schema(),
            "bbox": bbox_schema(),
            "bbox_crs": string_schema(),
            "export_image_crs": string_schema(),
            "target_grid_crs": string_schema(),
            "target_resolution_m": number_schema(),
            "tile_width_pixels": integer_schema(),
            "tile_height_pixels": integer_schema(),
            "tile_planning_crs": string_schema(),
            "semantic_type": string_schema(enum=["categorical", "continuous"]),
            "resampling": string_schema(enum=["nearest", "mode", "bilinear", "cubic", "lanczos", "average"]),
            "url": string_schema(),
            "status": string_schema(enum=["planned"]),
        },
    }


def harmonization_plan_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "FasterRaster harmonization_plan.json",
        "type": "object",
        "additionalProperties": False,
        "required": ["project_id", "target_grid", "inputs", "validation_checks"],
        "properties": {
            "project_id": string_schema(),
            "target_grid": {
                "type": "object",
                "additionalProperties": False,
                "required": ["crs", "resolution_m", "nodata", "snap"],
                "properties": {
                    "crs": string_schema(),
                    "resolution_m": number_schema(),
                    "nodata": number_schema(),
                    "snap": string_schema(),
                },
            },
            "inputs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "request_id",
                        "source_bbox",
                        "bbox_crs",
                        "source_crs",
                        "export_image_crs",
                        "target_crs",
                        "target_grid_crs",
                        "tile_width_pixels",
                        "tile_height_pixels",
                        "tile_planning_crs",
                        "semantic_type",
                        "resampling",
                        "forbidden_resampling",
                        "planned_output",
                    ],
                    "properties": {
                        "request_id": string_schema(),
                        "source_bbox": bbox_schema(),
                        "bbox_crs": string_schema(),
                        "source_crs": string_schema(),
                        "export_image_crs": string_schema(),
                        "target_crs": string_schema(),
                        "target_grid_crs": string_schema(),
                        "tile_width_pixels": integer_schema(),
                        "tile_height_pixels": integer_schema(),
                        "tile_planning_crs": string_schema(),
                        "semantic_type": string_schema(enum=["categorical", "continuous"]),
                        "resampling": string_schema(enum=["nearest", "mode", "bilinear", "cubic", "lanczos", "average"]),
                        "forbidden_resampling": {"type": "array", "items": string_schema()},
                        "planned_output": string_schema(),
                    },
                },
            },
            "validation_checks": {"type": "array", "items": string_schema()},
        },
    }


def inspect_contract_report_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "FasterRaster inspect-contract report",
        "type": "object",
        "additionalProperties": True,
        "required": ["package_version", "project_id", "source_count", "sources", "overall_status", "errors"],
        "properties": {
            "package_version": string_schema(),
            "project_id": string_schema(),
            "source_count": integer_schema(),
            "overall_status": string_schema(enum=["PASS", "FAIL"]),
            "errors": {"type": "array", "items": string_schema()},
            "sources": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": True,
                    "required": ["source_id", "registry_key", "capability_status", "errors"],
                    "properties": {
                        "source_id": string_schema(),
                        "registry_key": string_schema(),
                        "adapter": string_schema(enum=["arcgis_imageserver", "generic_https_template"]),
                        "provider": string_schema(),
                        "product": string_schema(),
                        "acquisition_mode": string_schema(),
                        "bbox_request_policy": string_schema(
                            enum=[
                                "preserve_input_bbox_with_bboxsr",
                                "project_bbox_to_service_crs",
                                "no_bbox_url_template",
                            ]
                        ),
                        "supports_bbox_crs_param": {"type": "boolean"},
                        "service_crs": string_schema(),
                        "default_export_image_crs": string_schema(),
                        "target_grid_crs": string_schema(),
                        "year_parameter_strategy": string_schema(enum=["time_value", "mosaic_rule_by_attribute"]),
                        "supported_crs_transform_status": string_schema(),
                        "semantic_type": string_schema(enum=["categorical", "continuous"]),
                        "resampling": string_schema(enum=["nearest", "mode", "bilinear", "cubic", "lanczos", "average"]),
                        "max_width": integer_schema(),
                        "max_height": integer_schema(),
                        "capability_status": string_schema(enum=["PASS", "FAIL"]),
                        "errors": {"type": "array", "items": string_schema()},
                    },
                },
            },
        },
    }


def all_schemas() -> dict[str, dict]:
    return {
        "research_spec.schema.json": research_spec_schema(),
        "source_registry.schema.json": source_registry_schema(),
        "acquisition_manifest_row.schema.json": acquisition_manifest_row_schema(),
        "harmonization_plan.schema.json": harmonization_plan_schema(),
        "inspect_contract_report.schema.json": inspect_contract_report_schema(),
    }


def write_json_deterministic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def export_schemas(out_dir: Path) -> list[Path]:
    written = []
    for filename, schema in all_schemas().items():
        path = out_dir / filename
        write_json_deterministic(path, schema)
        written.append(path)
    return written


def schema_structural_status(schema_dir: Path) -> dict:
    items = []
    for filename in SCHEMA_FILENAMES:
        path = schema_dir / filename
        if not path.exists():
            items.append({"path": str(path), "present": False, "valid": False, "required_count": 0})
            continue
        schema = json.loads(path.read_text(encoding="utf-8"))
        required = schema.get("required", [])
        items.append(
            {
                "path": str(path),
                "present": True,
                "valid": schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema"
                and schema.get("type") == "object"
                and bool(required),
                "required_count": len(required),
            }
        )
    return {
        "expected": len(SCHEMA_FILENAMES),
        "present": sum(1 for item in items if item["present"]),
        "valid": sum(1 for item in items if item["valid"]),
        "items": items,
    }
