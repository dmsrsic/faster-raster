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
            "resolver_capability_required": {"type": "string"},
            "resolved_secret_present": {"const": False},
            "credential_requirement_sha256": {"type": "string"},
        },
    }
