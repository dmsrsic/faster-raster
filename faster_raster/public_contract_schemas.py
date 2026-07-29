from __future__ import annotations

from typing import Any


DRAFT = "https://json-schema.org/draft/2020-12/schema"


def source_pack_schema() -> dict[str, Any]:
    from faster_raster.source_pack import SourcePackManifest

    schema = SourcePackManifest.model_json_schema()
    schema["$schema"] = DRAFT
    schema["title"] = "FasterRaster declarative Source Pack v1"
    return schema


def temporal_alternatives_schema() -> dict[str, Any]:
    return {
        "$schema": DRAFT,
        "title": "FasterRaster temporal alternatives v1",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "source_id",
            "requested_time",
            "status",
            "search_contract_sha256",
            "selection_required",
            "original_request_unchanged",
            "candidate_count",
            "candidates",
            "ranking_policy",
            "temporal_alternatives_sha256",
        ],
        "properties": {
            "schema_version": {"const": "fasterraster.temporal-alternatives/v1"},
            "source_id": {"type": "string"},
            "requested_time": {"type": "string"},
            "status": {
                "enum": [
                    "AWAITING_TEMPORAL_SELECTION",
                    "NO_TEMPORAL_ALTERNATIVES",
                ]
            },
            "search_contract_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
            "selection_required": {"type": "boolean"},
            "original_request_unchanged": {"const": True},
            "candidate_count": {"type": "integer", "minimum": 0},
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "candidate_time",
                        "distance_days",
                        "coverage_fraction",
                        "rank",
                        "reason_codes",
                    ],
                    "properties": {
                        "candidate_time": {"type": "string"},
                        "distance_days": {"type": "integer", "minimum": 0},
                        "coverage_fraction": {
                            "oneOf": [
                                {"type": "number", "minimum": 0, "maximum": 1},
                                {"const": "unknown"},
                            ]
                        },
                        "rank": {"type": "integer", "minimum": 1},
                        "reason_codes": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "ranking_policy": {"type": "array", "items": {"type": "string"}},
            "temporal_alternatives_sha256": {
                "type": "string",
                "pattern": "^[a-f0-9]{64}$",
            },
        },
    }

def temporal_resolution_schema() -> dict[str, Any]:
    return {
        "$schema": DRAFT,
        "title": "FasterRaster explicit temporal resolution v1",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "status",
            "source_id",
            "requested_time",
            "selected_time",
            "selected_candidate",
            "search_contract_sha256",
            "temporal_alternatives_sha256",
            "selection_method",
            "original_request_unchanged",
            "resolved_contract_sha256",
        ],
        "properties": {
            "schema_version": {"const": "fasterraster.temporal-resolution/v1"},
            "status": {"const": "RESOLVED"},
            "source_id": {"type": "string"},
            "requested_time": {"type": "string"},
            "selected_time": {"type": "string"},
            "selected_candidate": {"type": "object"},
            "search_contract_sha256": {"type": "string"},
            "temporal_alternatives_sha256": {"type": "string"},
            "selection_method": {"const": "explicit_user_selection"},
            "original_request_unchanged": {"const": True},
            "resolved_contract_sha256": {"type": "string"},
        },
    }


def classification_temporal_alternatives_schema() -> dict[str, Any]:
    year_pair = {
        "type": "object",
        "additionalProperties": False,
        "required": ["imagery_year", "cdl_year"],
        "properties": {
            "imagery_year": {"type": "integer"},
            "cdl_year": {"type": "integer"},
        },
    }
    return {
        "$schema": DRAFT,
        "title": "FasterRaster classification temporal alternatives v1",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "status",
            "coherent_pair_status",
            "requested_pair",
            "original_request_unchanged",
            "selection_required",
            "raster_acquisition_authorized",
            "network_bytes",
            "search_contract_sha256",
            "candidate_count",
            "candidates",
            "ranking_policy",
            "temporal_alternatives_sha256",
        ],
        "properties": {
            "schema_version": {
                "const": "fasterraster.classification-temporal-alternatives/v1"
            },
            "status": {
                "enum": [
                    "EXACT_TIME_AVAILABLE",
                    "AWAITING_TEMPORAL_SELECTION",
                    "NO_COHERENT_ALTERNATIVE",
                ]
            },
            "coherent_pair_status": {
                "enum": [
                    "EXACT_TIME_AVAILABLE",
                    "AWAITING_TEMPORAL_SELECTION",
                    "NO_COHERENT_ALTERNATIVE",
                ]
            },
            "requested_pair": year_pair,
            "original_request_unchanged": {"const": True},
            "selection_required": {"type": "boolean"},
            "raster_acquisition_authorized": {"const": False},
            "network_bytes": {"const": 0},
            "search_contract_sha256": {
                "type": "string",
                "pattern": "^[a-f0-9]{64}$",
            },
            "candidate_count": {"type": "integer", "minimum": 0},
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "candidate_id",
                        "repair_mode",
                        "imagery_year",
                        "cdl_year",
                        "distance_years",
                        "coverage_fraction",
                        "coherent_pair",
                        "rank",
                        "reason_codes",
                    ],
                    "properties": {
                        "candidate_id": {"type": "string"},
                        "repair_mode": {
                            "enum": [
                                "coherent_imagery_and_weak_labels",
                                "imagery_only",
                            ]
                        },
                        "imagery_year": {"type": "integer"},
                        "cdl_year": {"type": "integer"},
                        "distance_years": {
                            "type": "integer",
                            "minimum": 0,
                        },
                        "coverage_fraction": {
                            "oneOf": [
                                {
                                    "type": "number",
                                    "minimum": 0,
                                    "maximum": 1,
                                },
                                {"const": "unknown"},
                            ]
                        },
                        "coherent_pair": {"type": "boolean"},
                        "rank": {"type": "integer", "minimum": 1},
                        "reason_codes": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
            },
            "ranking_policy": {
                "type": "array",
                "items": {"type": "string"},
            },
            "temporal_alternatives_sha256": {
                "type": "string",
                "pattern": "^[a-f0-9]{64}$",
            },
        },
    }


def classification_temporal_resolution_schema() -> dict[str, Any]:
    year_pair = {
        "type": "object",
        "additionalProperties": False,
        "required": ["imagery_year", "cdl_year"],
        "properties": {
            "imagery_year": {"type": "integer"},
            "cdl_year": {"type": "integer"},
        },
    }
    return {
        "$schema": DRAFT,
        "title": "FasterRaster classification temporal resolution v1",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "status",
            "requested_pair",
            "resolved_pair",
            "selected_candidate",
            "selection_method",
            "original_request_unchanged",
            "raster_acquisition_during_selection",
            "search_contract_sha256",
            "temporal_alternatives_sha256",
            "resolved_contract_sha256",
        ],
        "properties": {
            "schema_version": {
                "const": "fasterraster.classification-temporal-resolution/v1"
            },
            "status": {"const": "TEMPORAL_SELECTION_RESOLVED"},
            "requested_pair": year_pair,
            "resolved_pair": year_pair,
            "selected_candidate": {"type": "object"},
            "selection_method": {
                "enum": [
                    "explicit_user_selection",
                    "explicit_cli_year_arguments",
                ]
            },
            "original_request_unchanged": {"const": True},
            "raster_acquisition_during_selection": {"const": False},
            "search_contract_sha256": {
                "type": "string",
                "pattern": "^[a-f0-9]{64}$",
            },
            "temporal_alternatives_sha256": {
                "type": "string",
                "pattern": "^[a-f0-9]{64}$",
            },
            "resolved_contract_sha256": {
                "type": "string",
                "pattern": "^[a-f0-9]{64}$",
            },
        },
    }


def categorical_area_accounting_schema() -> dict[str, Any]:
    numeric_map = {
        "type": "object",
        "additionalProperties": {"type": "number"},
    }
    integer_map = {
        "type": "object",
        "additionalProperties": {"type": "integer", "minimum": 0},
    }
    grid = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "crs",
            "transform",
            "width",
            "height",
            "pixel_area_square_meters",
        ],
        "properties": {
            "crs": {"type": "string"},
            "transform": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 6,
                "maxItems": 6,
            },
            "width": {"type": "integer", "minimum": 1},
            "height": {"type": "integer", "minimum": 1},
            "pixel_area_square_meters": {
                "type": "number",
                "exclusiveMinimum": 0,
            },
        },
    }
    return {
        "$schema": DRAFT,
        "title": "FasterRaster categorical area accounting v1",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "area_method",
            "area_units",
            "area_reference_crs",
            "categorical_resampling",
            "native_class_counts_preserved",
            "source_grid",
            "area_grid",
            "native_valid_pixel_count",
            "native_class_pixel_counts",
            "equal_area_class_pixel_counts",
            "class_area_square_meters",
            "class_area_hectares",
            "area_reconciliation_tolerance_fraction",
            "valid_footprint_area_square_meters",
            "valid_footprint_area_hectares",
            "summed_class_area_square_meters",
            "summed_class_area_hectares",
            "area_reconciliation_difference_fraction",
            "area_reconciliation_status",
            "area_accounting_sha256",
        ],
        "properties": {
            "schema_version": {
                "const": "fasterraster.categorical-area-accounting/v1"
            },
            "area_method": {
                "enum": [
                    "native_declared_equal_area_grid",
                    "nearest_neighbor_reprojection_to_equal_area_grid",
                ]
            },
            "area_units": {"const": "hectares"},
            "area_reference_crs": {"type": "string"},
            "categorical_resampling": {"const": "nearest"},
            "native_class_counts_preserved": {"const": True},
            "source_grid": grid,
            "area_grid": grid,
            "native_valid_pixel_count": {
                "type": "integer",
                "minimum": 0,
            },
            "native_class_pixel_counts": integer_map,
            "equal_area_class_pixel_counts": integer_map,
            "class_area_square_meters": numeric_map,
            "class_area_hectares": numeric_map,
            "area_reconciliation_tolerance_fraction": {
                "type": "number",
                "minimum": 0,
            },
            "valid_footprint_area_square_meters": {
                "type": "number",
                "minimum": 0,
            },
            "valid_footprint_area_hectares": {
                "type": "number",
                "minimum": 0,
            },
            "summed_class_area_square_meters": {
                "type": "number",
                "minimum": 0,
            },
            "summed_class_area_hectares": {
                "type": "number",
                "minimum": 0,
            },
            "area_reconciliation_difference_fraction": {
                "type": "number",
                "minimum": 0,
            },
            "area_reconciliation_status": {"const": "PASS"},
            "area_accounting_sha256": {
                "type": "string",
                "pattern": "^[a-f0-9]{64}$",
            },
        },
    }


def preview_template_schema() -> dict[str, Any]:
    return {
        "$schema": DRAFT,
        "title": "FasterRaster preview template v1",
        "type": "object",
        "additionalProperties": True,
        "required": [
            "schema_version",
            "template_id",
            "layout",
            "panels",
            "shared_extent",
            "include_scale_bar",
            "include_north_arrow",
            "include_provenance_footer",
        ],
        "properties": {
            "schema_version": {"const": "fasterraster.preview-template/v1"},
            "template_id": {"type": "string"},
            "layout": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "rows", "columns"],
                "properties": {
                    "type": {"const": "grid"},
                    "rows": {"type": "integer", "minimum": 1, "maximum": 4},
                    "columns": {"type": "integer", "minimum": 1, "maximum": 4},
                },
            },
            "panels": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["panel_id", "role"],
                    "properties": {
                        "panel_id": {"type": "string"},
                        "role": {"type": "string"},
                    },
                },
            },
            "shared_extent": {"type": "boolean"},
            "include_scale_bar": {"type": "boolean"},
            "include_north_arrow": {"type": "boolean"},
            "include_provenance_footer": {"type": "boolean"},
        },
    }


def capability_registry_schema() -> dict[str, Any]:
    row = {
        "type": "object",
        "required": [
            "capability_id",
            "label",
            "kind",
            "status",
            "planning",
            "preview",
            "materialization",
            "analysis",
            "credential_requirement",
            "public_execution",
            "notes",
        ],
        "properties": {
            "capability_id": {"type": "string"},
            "label": {"type": "string"},
            "kind": {"type": "string"},
            "status": {
                "enum": [
                    "released",
                    "experimental",
                    "private",
                    "planned",
                    "unsupported",
                ]
            },
            "planning": {"type": "boolean"},
            "preview": {"type": "boolean"},
            "materialization": {"type": "boolean"},
            "analysis": {"type": "boolean"},
            "credential_requirement": {"type": "string"},
            "public_execution": {"type": "string"},
            "notes": {"type": "string"},
        },
    }
    return {
        "$schema": DRAFT,
        "title": "FasterRaster public capability registry v1",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "registry_version",
            "release",
            "status_definitions",
            "capabilities",
            "sources",
        ],
        "properties": {
            "schema_version": {"const": "fasterraster.capability-registry/v1"},
            "registry_version": {"type": "string"},
            "release": {"type": "object"},
            "status_definitions": {"type": "object"},
            "capabilities": {"type": "array", "items": row},
            "sources": {"type": "array", "items": row},
        },
    }


def credential_requirement_schema() -> dict[str, Any]:
    return {
        "$schema": DRAFT,
        "title": "FasterRaster public credential requirement v1",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "authentication_scheme",
            "credential_ref",
            "allowed_hosts",
            "redirect_hosts",
            "asset_hosts",
            "resolver_capability_required",
            "resolved_secret_present",
            "credential_requirement_sha256",
        ],
        "properties": {
            "schema_version": {"const": "fasterraster.credential-requirement/v1"},
            "authentication_scheme": {"enum": ["bearer", "api_key", "oauth2"]},
            "credential_ref": {"type": "string"},
            "allowed_hosts": {"type": "array", "items": {"type": "string"}},
            "redirect_hosts": {"type": "array", "items": {"type": "string"}},
            "asset_hosts": {"type": "array", "items": {"type": "string"}},
            "resolver_capability_required": {"type": "string"},
            "resolved_secret_present": {"const": False},
            "credential_requirement_sha256": {"type": "string"},
        },
    }
