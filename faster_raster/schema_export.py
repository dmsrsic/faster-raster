from __future__ import annotations

import json
from pathlib import Path


SCHEMA_FILENAMES = [
    "research_spec.schema.json",
    "source_registry.schema.json",
    "acquisition_manifest_row.schema.json",
    "harmonization_plan.schema.json",
    "inspect_contract_report.schema.json",
    "unified_acquisition_manifest_row.schema.json",
    "task_compile_report.schema.json",
    "execution_dag.schema.json",
    "run_plan.schema.json",
    "job_receipt.schema.json",
    "run_receipt.schema.json",
    "source_evidence.schema.json",
    "receipt_verification.schema.json",
    "materialization_plan.schema.json",
    "materialization_object_plan.schema.json",
    "transfer_receipt.schema.json",
    "artifact_receipt.schema.json",
    "materialization_run_receipt.schema.json",
    "artifact_catalog.schema.json",
    "materialization_verification.schema.json",
    "system_grade.schema.json",
    "agricultural_recipe_v2.schema.json",
    "agricultural_recipe_v3.schema.json",
    "agricultural_recipe_v4.schema.json",
    "workfile_v1.schema.json",
    "workfile_v2.schema.json",
    "source_pack.schema.json",
    "source_materialization_request.schema.json",
    "temporal_alternatives.schema.json",
    "temporal_resolution.schema.json",
    "classification_temporal_alternatives.schema.json",
    "classification_temporal_resolution.schema.json",
    "categorical_area_accounting.schema.json",
    "preview_template.schema.json",
    "capability_registry.schema.json",
    "credential_requirement.schema.json",
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


def unified_acquisition_manifest_row_schema() -> dict:
    required = [
        "request_id",
        "task_id",
        "source_id",
        "adapter",
        "acquisition_mode",
        "source_classification",
        "execution_status",
        "url_sha256",
        "request_method",
        "request_headers_redacted",
        "temporal_key",
        "spatial_key",
        "expected_content_family",
        "expected_magic",
        "expected_format",
        "bounded_request",
        "credential_required",
        "fixture_only",
        "network_required",
        "checksum_policy",
        "validation_steps",
        "harmonization_readiness",
    ]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "FasterRaster v0.7 unified acquisition manifest row",
        "type": "object",
        "additionalProperties": True,
        "required": required,
        "properties": {
            "request_id": string_schema(),
            "task_id": string_schema(),
            "source_id": string_schema(),
            "adapter": string_schema(enum=["arcgis_imageserver", "generic_https_template", "static_http_range", "stac_metadata"]),
            "acquisition_mode": string_schema(),
            "source_classification": string_schema(enum=["runnable", "fixture_only"]),
            "execution_status": string_schema(),
            "deterministic_url": {"type": ["string", "null"]},
            "url_sha256": string_schema(),
            "request_method": string_schema(),
            "request_headers_redacted": {"type": "object"},
            "temporal_key": string_schema(),
            "spatial_key": string_schema(),
            "expected_content_family": {},
            "expected_magic": {},
            "expected_format": string_schema(),
            "max_bytes": {"type": ["integer", "null"]},
            "bounded_request": {"type": "boolean"},
            "credential_required": {"type": "boolean"},
            "auth_profile": {"type": ["string", "null"]},
            "fixture_only": {"type": "boolean"},
            "network_required": {"type": "boolean"},
            "checksum_policy": string_schema(),
            "validation_steps": {"type": "array", "items": string_schema()},
            "harmonization_readiness": string_schema(),
        },
    }


def task_compile_report_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "FasterRaster v0.7 task compile report",
        "type": "object",
        "additionalProperties": True,
        "required": [
            "task_id",
            "manifest_row_count",
            "executable_request_count",
            "fixture_request_count",
            "validation_status",
            "determinism_status",
            "acquisition_manifest_sha256",
            "compile_report_contract_sha256",
        ],
        "properties": {
            "task_id": string_schema(),
            "validation_status": string_schema(enum=["PASS", "FAIL"]),
            "determinism_status": string_schema(enum=["PASS", "FAIL"]),
            "manifest_row_count": integer_schema(),
            "request_count": integer_schema(),
            "executable_request_count": integer_schema(),
            "fixture_request_count": integer_schema(),
            "acquisition_manifest_sha256": string_schema(),
            "compile_report_contract_sha256": string_schema(),
        },
    }


def execution_dag_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "FasterRaster v0.7 execution DAG",
        "type": "object",
        "additionalProperties": True,
        "required": ["status", "job_count", "dependency_count", "stage_counts", "errors"],
        "properties": {
            "status": string_schema(enum=["PASS", "FAIL"]),
            "job_count": integer_schema(),
            "dependency_count": integer_schema(),
            "stage_counts": {"type": "object"},
            "errors": {"type": "array", "items": string_schema()},
        },
    }


def system_grade_schema() -> dict:
    required = [
        "overall_score",
        "overall_grade",
        "blocking_failures",
        "safety_score",
        "release_decision",
        "local_execution_score",
        "run_receipt_score",
        "latest_run_receipt_present",
        "latest_run_receipt_valid",
        "local_run_status",
        "local_successful_source_count",
        "local_failed_source_count",
        "local_fixture_source_count",
        "materialization_score",
        "artifact_integrity_score",
        "artifact_catalog_score",
        "latest_materialization_present",
        "latest_materialization_valid",
        "materialization_run_status",
        "latest_materialization_attempt_run_id",
        "latest_materialization_attempt_status",
        "latest_materialization_attempt_valid",
        "latest_successful_materialization_present",
        "latest_successful_materialization_run_id",
        "latest_successful_materialization_valid",
        "latest_successful_materialization_status",
        "latest_successful_materialized_source_count",
        "latest_successful_total_materialized_bytes",
        "latest_attempt_newer_than_success",
        "latest_attempt_effect_on_release",
        "materialized_source_count",
        "verified_artifact_count",
        "artifact_catalog_valid",
    ]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "FasterRaster v0.9 system grade",
        "type": "object",
        "additionalProperties": True,
        "required": required,
        "properties": {
            "overall_score": number_schema(),
            "overall_grade": string_schema(),
            "blocking_failures": {"type": "array", "items": string_schema()},
            "safety_score": integer_schema(),
            "release_decision": string_schema(enum=["release_ready", "release_ready_with_cautions", "hold_release"]),
            "local_execution_score": integer_schema(),
            "run_receipt_score": integer_schema(),
            "latest_run_receipt_present": {"type": "boolean"},
            "latest_run_receipt_valid": {"type": "boolean"},
            "local_run_status": {"type": ["string", "null"]},
            "local_successful_source_count": integer_schema(),
            "local_failed_source_count": integer_schema(),
            "local_fixture_source_count": integer_schema(),
            "materialization_score": integer_schema(),
            "artifact_integrity_score": integer_schema(),
            "artifact_catalog_score": integer_schema(),
            "latest_materialization_attempt_run_id": {"type": ["string", "null"]},
            "latest_materialization_attempt_status": {"type": ["string", "null"]},
            "latest_materialization_attempt_valid": {"type": "boolean"},
            "latest_successful_materialization_present": {"type": "boolean"},
            "latest_successful_materialization_run_id": {"type": ["string", "null"]},
            "latest_successful_materialization_valid": {"type": "boolean"},
            "latest_successful_materialization_status": {"type": ["string", "null"]},
            "latest_successful_materialized_source_count": integer_schema(),
            "latest_successful_total_materialized_bytes": integer_schema(),
            "latest_attempt_newer_than_success": {"type": "boolean"},
            "latest_attempt_effect_on_release": string_schema(),
            "latest_materialization_present": {"type": "boolean"},
            "latest_materialization_valid": {"type": "boolean"},
            "latest_materialization_run_id": {"type": ["string", "null"]},
            "materialization_run_status": {"type": ["string", "null"]},
            "materialized_source_count": integer_schema(),
            "verified_artifact_count": integer_schema(),
            "artifact_catalog_valid": {"type": "boolean"},
        },
    }


def run_plan_schema() -> dict:
    required = [
        "task_id",
        "package_id",
        "package_version",
        "package_sha256",
        "manifest_sha256",
        "dag_sha256",
        "run_plan_contract_sha256",
        "planned_job_count",
        "planned_network_job_count",
        "planned_fixture_job_count",
        "max_bytes_per_source",
        "max_total_bytes",
        "network_required",
        "network_allowed",
    ]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "FasterRaster v0.8 run plan",
        "type": "object",
        "additionalProperties": True,
        "required": required,
        "properties": {
            "task_id": string_schema(),
            "package_id": string_schema(),
            "package_version": string_schema(),
            "package_sha256": string_schema(),
            "manifest_sha256": string_schema(),
            "dag_sha256": string_schema(),
            "run_plan_contract_sha256": string_schema(),
            "planned_job_count": integer_schema(),
            "planned_network_job_count": integer_schema(),
            "planned_fixture_job_count": integer_schema(),
            "max_bytes_per_source": integer_schema(),
            "max_total_bytes": integer_schema(),
            "network_required": {"type": "boolean"},
            "network_allowed": {"type": "boolean"},
        },
    }


def job_receipt_schema() -> dict:
    required = [
        "job_id",
        "request_id",
        "task_id",
        "source_id",
        "adapter",
        "stage",
        "status",
        "dependencies",
        "dependency_statuses",
        "network_attempted",
        "input_contract_sha256",
        "output_contract_sha256",
        "credentials_used",
        "authorization_redacted",
    ]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "FasterRaster v0.8 job receipt",
        "type": "object",
        "additionalProperties": True,
        "required": required,
        "properties": {
            "job_id": string_schema(),
            "request_id": {"type": ["string", "null"]},
            "task_id": string_schema(),
            "source_id": {"type": ["string", "null"]},
            "adapter": {"type": ["string", "null"]},
            "stage": string_schema(),
            "status": string_schema(enum=["pending", "running", "succeeded", "failed", "skipped_dependency_failed", "skipped_network_disabled", "fixture_recorded", "cache_hit", "unsupported"]),
            "dependencies": {"type": "array", "items": string_schema()},
            "dependency_statuses": {"type": "object"},
            "network_attempted": {"type": "boolean"},
            "input_contract_sha256": string_schema(),
            "output_contract_sha256": string_schema(),
            "credentials_used": {"type": "boolean"},
            "authorization_redacted": {"type": "boolean"},
        },
    }


def run_receipt_schema() -> dict:
    required = [
        "run_id",
        "task_id",
        "package_id",
        "package_version",
        "package_sha256",
        "manifest_sha256",
        "dag_sha256",
        "run_plan_contract_sha256",
        "receipt_contract_sha256",
        "run_status",
        "allow_network",
        "max_bytes_per_source",
        "max_total_bytes",
        "planned_job_count",
        "job_receipt_count",
        "safety_event_count",
        "all_byte_caps_respected",
        "credentials_used",
        "authorization_headers_present",
    ]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "FasterRaster v0.8 run receipt",
        "type": "object",
        "additionalProperties": True,
        "required": required,
        "properties": {
            "run_id": string_schema(),
            "task_id": string_schema(),
            "package_id": string_schema(),
            "package_version": string_schema(),
            "package_sha256": string_schema(),
            "manifest_sha256": string_schema(),
            "dag_sha256": string_schema(),
            "run_plan_contract_sha256": string_schema(),
            "receipt_contract_sha256": string_schema(),
            "run_status": string_schema(enum=["planned", "completed", "completed_with_warnings", "failed", "blocked_policy"]),
            "allow_network": {"type": "boolean"},
            "max_bytes_per_source": integer_schema(),
            "max_total_bytes": integer_schema(),
            "planned_job_count": integer_schema(),
            "job_receipt_count": integer_schema(),
            "safety_event_count": integer_schema(),
            "all_byte_caps_respected": {"type": "boolean"},
            "credentials_used": {"type": "boolean"},
            "authorization_headers_present": {"type": "boolean"},
        },
    }


def source_evidence_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "FasterRaster v0.8 source evidence",
        "type": "object",
        "additionalProperties": True,
        "required": ["task_id", "run_id", "sources"],
        "properties": {
            "task_id": string_schema(),
            "run_id": string_schema(),
            "sources": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        },
    }


def receipt_verification_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "FasterRaster v0.8 receipt verification",
        "type": "object",
        "additionalProperties": True,
        "required": ["verification_status", "checks", "failures", "warnings"],
        "properties": {
            "verification_status": string_schema(enum=["PASS", "FAIL"]),
            "checks": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
            "failures": {"type": "array", "items": string_schema()},
            "warnings": {"type": "array", "items": string_schema()},
        },
    }


def materialization_object_plan_schema() -> dict:
    required = [
        "request_id",
        "source_id",
        "adapter",
        "url_sha256",
        "expected_content_family",
        "expected_magic",
        "artifact_extension",
        "max_object_bytes",
        "materialization_eligible",
        "eligibility_status",
        "blocking_reasons",
    ]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "FasterRaster v0.9 materialization object plan",
        "type": "object",
        "additionalProperties": True,
        "required": required,
        "properties": {
            "request_id": string_schema(),
            "source_id": string_schema(),
            "adapter": string_schema(),
            "url_sha256": string_schema(),
            "expected_content_family": {},
            "expected_magic": {},
            "artifact_extension": string_schema(),
            "max_object_bytes": integer_schema(),
            "materialization_eligible": {"type": "boolean"},
            "eligibility_status": string_schema(),
            "blocking_reasons": {"type": "array", "items": string_schema()},
        },
    }


def materialization_plan_schema() -> dict:
    required = [
        "task_id",
        "package_id",
        "package_version",
        "package_sha256",
        "manifest_sha256",
        "execution_dag_sha256",
        "materialization_plan_contract_sha256",
        "source_selection",
        "planned_transfer_count",
        "max_object_bytes",
        "max_total_bytes",
        "network_required",
        "network_allowed",
        "approval_required",
        "approval_status",
        "object_plans",
        "validation_status",
    ]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "FasterRaster v0.9 materialization plan",
        "type": "object",
        "additionalProperties": True,
        "required": required,
        "properties": {
            "task_id": string_schema(),
            "package_id": string_schema(),
            "package_version": string_schema(),
            "package_sha256": string_schema(),
            "manifest_sha256": string_schema(),
            "execution_dag_sha256": string_schema(),
            "materialization_plan_contract_sha256": string_schema(),
            "source_selection": {"type": "array", "items": string_schema()},
            "planned_transfer_count": integer_schema(),
            "max_object_bytes": integer_schema(),
            "max_total_bytes": integer_schema(),
            "network_required": {"type": "boolean"},
            "network_allowed": {"type": "boolean"},
            "approval_required": {"type": "boolean"},
            "approval_status": string_schema(),
            "object_plans": {"type": "array", "items": materialization_object_plan_schema()},
            "validation_status": string_schema(),
        },
    }


def transfer_receipt_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "FasterRaster v0.9 transfer receipt",
        "type": "object",
        "additionalProperties": True,
        "required": ["request_id", "source_id", "status"],
        "properties": {"request_id": string_schema(), "source_id": string_schema(), "status": string_schema()},
    }


def artifact_receipt_schema() -> dict:
    required = [
        "artifact_receipt_version",
        "artifact_id",
        "task_id",
        "materialization_run_id",
        "request_id",
        "source_id",
        "object_status",
        "complete_object",
        "bounded_probe_only",
        "whole_object_sha256",
        "artifact_path",
        "content_addressed",
        "prefix_match",
        "container_validation_status",
        "credentials_used",
        "authorization_headers_present",
        "artifact_receipt_contract_sha256",
    ]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "FasterRaster v0.9 artifact receipt",
        "type": "object",
        "additionalProperties": True,
        "required": required,
        "properties": {
            "artifact_receipt_version": integer_schema(),
            "artifact_id": string_schema(),
            "task_id": string_schema(),
            "materialization_run_id": string_schema(),
            "request_id": string_schema(),
            "source_id": string_schema(),
            "object_status": string_schema(),
            "complete_object": {"type": "boolean"},
            "bounded_probe_only": {"type": "boolean"},
            "whole_object_sha256": string_schema(),
            "artifact_path": string_schema(),
            "content_addressed": {"type": "boolean"},
            "prefix_match": {"type": "boolean"},
            "container_validation_status": string_schema(),
            "credentials_used": {"type": "boolean"},
            "authorization_headers_present": {"type": "boolean"},
            "artifact_receipt_contract_sha256": string_schema(),
        },
    }


def materialization_run_receipt_schema() -> dict:
    required = [
        "materialization_run_id",
        "task_id",
        "package_id",
        "package_version",
        "materialization_plan_contract_sha256",
        "materialization_run_receipt_contract_sha256",
        "run_status",
        "execution_blocked",
        "allow_network",
        "allow_materialization",
        "approval_hash_valid",
        "network_run",
        "materialized_source_count",
        "failed_source_count",
        "all_probe_prefixes_match",
        "all_whole_object_checksums_present",
        "all_container_validations_passed",
        "credentials_used",
        "authorization_headers_present",
        "artifact_receipt_count",
    ]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "FasterRaster v0.9 materialization run receipt",
        "type": "object",
        "additionalProperties": True,
        "required": required,
        "properties": {
            "materialization_run_id": string_schema(),
            "task_id": string_schema(),
            "package_id": string_schema(),
            "package_version": string_schema(),
            "materialization_plan_contract_sha256": string_schema(),
            "materialization_run_receipt_contract_sha256": string_schema(),
            "run_status": string_schema(enum=["planned", "blocked_policy", "completed", "completed_with_warnings", "failed"]),
            "execution_blocked": {},
            "allow_network": {"type": "boolean"},
            "allow_materialization": {"type": "boolean"},
            "approval_hash_valid": {"type": "boolean"},
            "network_run": {"type": "boolean"},
            "materialized_source_count": integer_schema(),
            "failed_source_count": integer_schema(),
            "all_probe_prefixes_match": {"type": "boolean"},
            "all_whole_object_checksums_present": {"type": "boolean"},
            "all_container_validations_passed": {"type": "boolean"},
            "credentials_used": {"type": "boolean"},
            "authorization_headers_present": {"type": "boolean"},
            "artifact_receipt_count": integer_schema(),
        },
    }


def artifact_catalog_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "FasterRaster v0.9 artifact catalog",
        "type": "object",
        "additionalProperties": True,
        "required": ["catalog_version", "artifact_count", "total_materialized_bytes", "entries", "catalog_contract_sha256"],
        "properties": {
            "catalog_version": integer_schema(),
            "artifact_count": integer_schema(),
            "total_materialized_bytes": integer_schema(),
            "entries": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
            "catalog_contract_sha256": string_schema(),
        },
    }


def materialization_verification_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "FasterRaster v0.9 materialization verification",
        "type": "object",
        "additionalProperties": True,
        "required": ["verification_status", "checks", "failures", "warnings"],
        "properties": {
            "contract_verification_status": string_schema(enum=["PASS", "FAIL"]),
            "execution_outcome_status": string_schema(enum=["PASS", "FAILED", "BLOCKED", "NOT_APPLICABLE"]),
            "artifact_verification_status": string_schema(enum=["PASS", "FAIL", "NOT_APPLICABLE"]),
            "catalog_verification_status": string_schema(enum=["PASS", "FAIL", "NOT_APPLICABLE"]),
            "release_evidence_status": string_schema(enum=["PASS", "FAIL", "NOT_APPLICABLE"]),
            "verification_status": string_schema(enum=["PASS", "FAIL", "WARN", "NOT_APPLICABLE"]),
            "target_selection": string_schema(),
            "materialization_run_id": string_schema(),
            "run_status": string_schema(enum=["completed", "completed_with_warnings", "failed", "blocked_policy"]),
            "blocking_reasons": {"type": "array", "items": string_schema()},
            "informational_reasons": {"type": "array", "items": string_schema()},
            "checks": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
            "failures": {"type": "array", "items": string_schema()},
            "warnings": {"type": "array", "items": string_schema()},
        },
    }


def all_schemas() -> dict[str, dict]:
    from faster_raster.ag_recipes import (
        agricultural_recipe_v2_schema,
        agricultural_recipe_v3_schema,
        agricultural_recipe_v4_schema,
    )
    from faster_raster.workfiles import (
        human_development_workfile_schema,
        workfile_schema,
    )
    from faster_raster.public_contract_schemas import (
        capability_registry_schema,
        categorical_area_accounting_schema,
        classification_temporal_alternatives_schema,
        classification_temporal_resolution_schema,
        credential_requirement_schema,
        preview_template_schema,
        source_materialization_request_schema,
        source_pack_schema,
        temporal_alternatives_schema,
        temporal_resolution_schema,
    )

    return {
        "research_spec.schema.json": research_spec_schema(),
        "source_registry.schema.json": source_registry_schema(),
        "acquisition_manifest_row.schema.json": acquisition_manifest_row_schema(),
        "harmonization_plan.schema.json": harmonization_plan_schema(),
        "inspect_contract_report.schema.json": inspect_contract_report_schema(),
        "unified_acquisition_manifest_row.schema.json": unified_acquisition_manifest_row_schema(),
        "task_compile_report.schema.json": task_compile_report_schema(),
        "execution_dag.schema.json": execution_dag_schema(),
        "run_plan.schema.json": run_plan_schema(),
        "job_receipt.schema.json": job_receipt_schema(),
        "run_receipt.schema.json": run_receipt_schema(),
        "source_evidence.schema.json": source_evidence_schema(),
        "receipt_verification.schema.json": receipt_verification_schema(),
        "materialization_plan.schema.json": materialization_plan_schema(),
        "materialization_object_plan.schema.json": materialization_object_plan_schema(),
        "transfer_receipt.schema.json": transfer_receipt_schema(),
        "artifact_receipt.schema.json": artifact_receipt_schema(),
        "materialization_run_receipt.schema.json": materialization_run_receipt_schema(),
        "artifact_catalog.schema.json": artifact_catalog_schema(),
        "materialization_verification.schema.json": materialization_verification_schema(),
        "system_grade.schema.json": system_grade_schema(),
        "agricultural_recipe_v2.schema.json": agricultural_recipe_v2_schema(),
        "agricultural_recipe_v3.schema.json": agricultural_recipe_v3_schema(),
        "agricultural_recipe_v4.schema.json": agricultural_recipe_v4_schema(),
        "workfile_v1.schema.json": workfile_schema(),
        "workfile_v2.schema.json": human_development_workfile_schema(),
        "source_pack.schema.json": source_pack_schema(),
        "source_materialization_request.schema.json": (
            source_materialization_request_schema()
        ),
        "temporal_alternatives.schema.json": temporal_alternatives_schema(),
        "temporal_resolution.schema.json": temporal_resolution_schema(),
        "classification_temporal_alternatives.schema.json": (
            classification_temporal_alternatives_schema()
        ),
        "classification_temporal_resolution.schema.json": (
            classification_temporal_resolution_schema()
        ),
        "categorical_area_accounting.schema.json": (
            categorical_area_accounting_schema()
        ),
        "preview_template.schema.json": preview_template_schema(),
        "capability_registry.schema.json": capability_registry_schema(),
        "credential_requirement.schema.json": credential_requirement_schema(),
    }


def write_json_deterministic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


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
