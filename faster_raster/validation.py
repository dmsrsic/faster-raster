from __future__ import annotations

import json
from pathlib import Path

from faster_raster.adapters.generic_https_template import template_placeholders, ALLOWED_PLACEHOLDERS
from faster_raster.crs import UnsupportedCRSTransform, transform_bbox
from faster_raster.schemas import ResearchSpec, SourceRegistry


CATEGORICAL_FORBIDDEN_RESAMPLING = {"bilinear", "cubic", "lanczos", "average"}
SUPPORTED_ADAPTERS = {"arcgis_imageserver", "generic_https_template"}
SUPPORTED_BBOX_REQUEST_POLICIES = {
    "preserve_input_bbox_with_bboxsr",
    "project_bbox_to_service_crs",
    "no_bbox_url_template",
}
SUPPORTED_YEAR_PARAMETER_STRATEGIES = {"time_value"}
CONTINUOUS_ALLOWED_RESAMPLING = {"nearest", "bilinear", "cubic"}
CATEGORICAL_ALLOWED_RESAMPLING = {"nearest", "mode"}


def load_spec(path: Path) -> ResearchSpec:
    with path.open("r", encoding="utf-8") as handle:
        return ResearchSpec.model_validate(json.load(handle))


def validate_spec(spec: ResearchSpec, registry: SourceRegistry) -> list[str]:
    errors: list[str] = []
    for source in spec.sources:
        entry = registry.sources.get(source.registry_key)
        if entry is None:
            errors.append(f"Unknown registry_key: {source.registry_key}")
            continue
        if source.semantic_type != entry.semantic_type:
            errors.append(
                f"Source {source.id} semantic_type {source.semantic_type} does not match registry {entry.semantic_type}"
            )
        if source.semantic_type == "categorical" and source.resampling not in CATEGORICAL_ALLOWED_RESAMPLING:
            errors.append(f"Categorical source {source.id} cannot use {source.resampling} resampling")
        if source.semantic_type == "continuous" and source.resampling not in CONTINUOUS_ALLOWED_RESAMPLING:
            errors.append(f"Continuous source {source.id} cannot use {source.resampling} resampling")
        if entry.supported_years:
            missing = sorted(set(source.years) - set(entry.supported_years))
            if missing:
                errors.append(f"Source {source.id} requested unsupported years: {missing}")
        if entry.adapter not in SUPPORTED_ADAPTERS:
            errors.append(f"Unsupported adapter for v0: {entry.adapter}")
            continue
        if entry.bbox_request_policy not in SUPPORTED_BBOX_REQUEST_POLICIES:
            errors.append(f"Unsupported bbox_request_policy for source {source.id}: {entry.bbox_request_policy}")
        if entry.adapter == "arcgis_imageserver":
            if source.acquisition_mode != "arcgis_export_image":
                errors.append(f"Unsupported acquisition_mode for v0: {source.acquisition_mode}")
            if entry.year_parameter_strategy not in SUPPORTED_YEAR_PARAMETER_STRATEGIES:
                errors.append(
                    f"Unsupported year_parameter_strategy for source {source.id}: {entry.year_parameter_strategy}"
                )
            if not entry.supports_bbox_crs_param:
                errors.append(f"Source {source.id} must support bbox CRS parameter for v0 ArcGIS planning")
            if not entry.bbox_param:
                errors.append(f"Source {source.id} missing bbox_param")
            if not entry.bbox_crs_param:
                errors.append(f"Source {source.id} missing bbox_crs_param")
            if not entry.image_crs_param:
                errors.append(f"Source {source.id} missing image_crs_param")
        if entry.adapter == "generic_https_template":
            if source.acquisition_mode != "https_template":
                errors.append(f"Unsupported acquisition_mode for generic_https_template: {source.acquisition_mode}")
            if not entry.url_template:
                errors.append(f"Source {source.id} missing url_template")
            else:
                placeholders = template_placeholders(entry.url_template)
                unknown = sorted(placeholders - ALLOWED_PLACEHOLDERS)
                if unknown:
                    errors.append(f"Unknown URL template placeholder(s): {unknown}")
                for placeholder in sorted(placeholders):
                    if placeholder in {
                        "product_slug",
                        "region",
                        "h",
                        "v",
                        "template_tile_id",
                        "product_code",
                        "collection",
                        "version",
                        "variable",
                        "yyyymmdd",
                        "resolution",
                        "temporal_frequency",
                    } and getattr(entry, placeholder) in (None, ""):
                        errors.append(f"Source {source.id} missing template field: {placeholder}")
            if entry.bbox_request_policy != "no_bbox_url_template":
                errors.append(
                    f"Unsupported bbox_request_policy for generic_https_template source {source.id}: {entry.bbox_request_policy}"
                )
        export_image_crs = entry.default_export_image_crs or entry.service_crs or entry.native_crs
        if not export_image_crs:
            errors.append(f"Source {source.id} missing export image CRS")
        if entry.bbox_request_policy == "project_bbox_to_service_crs" and entry.service_crs:
            try:
                # Validate transform support before URL planning. The actual AOI bbox is read later.
                transform_bbox([0.0, 0.0, 1.0, 1.0], spec.aoi.input_crs, entry.service_crs)
            except UnsupportedCRSTransform as exc:
                errors.append(str(exc))
    return errors


def validate_or_raise(spec: ResearchSpec, registry: SourceRegistry) -> None:
    errors = validate_spec(spec, registry)
    if errors:
        raise ValueError("; ".join(errors))
