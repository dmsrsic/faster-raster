from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Project(BaseModel):
    id: str = Field(min_length=1)
    created: str | None = None


class Aoi(BaseModel):
    path: str = Field(min_length=1)
    input_crs: str = "EPSG:4326"
    bbox_policy: str = "tile_to_source_limits"


class TargetGrid(BaseModel):
    crs: str = Field(min_length=1)
    resolution_m: int | float = Field(gt=0)
    snap: str = "aoi_bounds"
    nodata: int | float


class SourceSpec(BaseModel):
    id: str = Field(min_length=1)
    registry_key: str = Field(min_length=1)
    years: list[int] = Field(min_length=1)
    thematic_layers: list[str] = Field(min_length=1)
    acquisition_mode: str
    semantic_type: Literal["categorical", "continuous"]
    resampling: str

    @field_validator("years")
    @classmethod
    def years_are_sorted_unique(cls, years: list[int]) -> list[int]:
        if len(set(years)) != len(years):
            raise ValueError("years must be unique")
        return years


class Outputs(BaseModel):
    manifest_dir: str = "manifests"
    plan_dir: str = "plans"
    raster_format: str = "COG"


class ResearchSpec(BaseModel):
    project: Project
    aoi: Aoi
    target_grid: TargetGrid
    sources: list[SourceSpec] = Field(min_length=1)
    outputs: Outputs


class RegistryEntry(BaseModel):
    adapter: str
    provider: str
    product: str
    base_url: str | None = None
    operation: str | None = None
    bbox_param: str | None = None
    bbox_crs_param: str | None = None
    image_crs_param: str | None = None
    size_param: str | None = None
    format_param: str | None = None
    response_format_param: str | None = None
    default_image_format: str | None = None
    default_response_format: str | None = None
    max_width: int = 4097
    max_height: int = 4097
    service_crs: str | None = None
    default_export_image_crs: str | None = None
    bbox_request_policy: Literal[
        "preserve_input_bbox_with_bboxsr", "project_bbox_to_service_crs", "no_bbox_url_template"
    ] = (
        "preserve_input_bbox_with_bboxsr"
    )
    supports_bbox_crs_param: bool = True
    semantic_type: Literal["categorical", "continuous"]
    url_template: str | None = None
    product_slug: str | None = None
    region: str | None = None
    h: str | None = None
    v: str | None = None
    template_tile_id: str | None = None
    product_code: str | None = None
    collection: str | None = None
    version: str | None = None
    variable: str | None = None
    yyyymmdd: str | None = None
    resolution: str | None = None
    temporal_frequency: str | None = None
    native_crs: str | None = None
    supports_tiling: bool = False
    default_format: str | None = None
    supported_years: list[int] | None = None
    native_pixel_type: str | None = None
    time_parameter_strategy: str | None = None
    year_parameter_strategy: str = "time_value"
    time_param: str = "time"
    time_value: str = "{year}"
    mosaic_rule_param: str = "mosaicRule"


class SourceRegistry(BaseModel):
    sources: dict[str, RegistryEntry]
