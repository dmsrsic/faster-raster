from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal, TypeAlias

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from faster_raster.ag_geography import BBoxValidationError, validate_bbox
from faster_raster.ag_recipes import HybridClassificationSpec
from faster_raster.development_sources import (
    ANNUAL_NLCD_MAPPING_ID,
    ANNUAL_NLCD_SOURCE_ID,
    USDA_CDL_MAPPING_ID,
    USDA_CDL_SOURCE_ID,
    USGS_NAIP_SOURCE_ID,
    validate_source_mapping,
)


WORKFILE_SCHEMA_VERSION = "fasterraster.work/v1"
HUMAN_DEVELOPMENT_WORKFILE_SCHEMA_VERSION = "fasterraster.work/v2"
HUMAN_DEVELOPMENT_WORKFLOW_ID = "human_development_change"
FORBIDDEN_KEY_PARTS = {
    "password",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "secret",
    "shell",
    "command",
    "exec",
}


class WorkfileError(ValueError):
    pass


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise WorkfileError(f"duplicate YAML key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


class AreaSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bbox: tuple[float, float, float, float]

    @field_validator("bbox")
    @classmethod
    def valid_bbox(cls, value: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        try:
            return validate_bbox(value)
        except BBoxValidationError as exc:
            raise ValueError(str(exc)) from exc


class TimeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: date
    end: date
    crop_year: int = Field(ge=1900, le=2200)

    @model_validator(mode="after")
    def dates_are_ordered(self) -> "TimeSpec":
        if self.end <= self.start:
            raise ValueError("time.end must be after time.start")
        if self.start.year != self.crop_year or self.end.year != self.crop_year:
            raise ValueError("time.start and time.end must match time.crop_year")
        return self


class SourcesSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy: Literal["auto", "pinned", "preferred"] = "auto"
    prefer: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    natural_imagery: str | None = None
    crop_classes: str | None = None
    terrain: str | None = None
    natural: str | None = None
    ndvi: str | None = None
    cdl_classes: str | None = None
    cdl_color: str | None = None
    hillshade: str | None = None

    @field_validator("prefer", "deny")
    @classmethod
    def unique_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("source preference lists must not contain duplicates")
        return values

    @model_validator(mode="after")
    def policy_fields_match(self) -> "SourcesSpec":
        pinned = any(
            getattr(self, name) is not None
            for name in (
                "natural_imagery",
                "crop_classes",
                "terrain",
                "natural",
                "ndvi",
                "cdl_classes",
                "cdl_color",
                "hillshade",
            )
        )
        if self.policy == "pinned" and not pinned:
            raise ValueError("pinned source policy requires at least one logical source mapping")
        if self.policy != "pinned" and pinned:
            raise ValueError("logical source mappings require sources.policy: pinned")
        if self.policy != "preferred" and self.prefer:
            raise ValueError("sources.prefer requires sources.policy: preferred")
        return self


class DataSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reuse: Literal["auto", "only", "never"] = "auto"
    allow_network: bool = False


class ProcessingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resolution_m: float | None = Field(default=None, gt=0, le=100_000)
    service_tile_size: int | None = Field(default=None, ge=64, le=10_000)
    maximum_parallel_tasks: int | None = Field(default=None, ge=1, le=256)


class LimitsSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    maximum_download_mb: float = Field(default=250.0, gt=0, le=20_000)


class OutputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview: bool = True
    include_context_imagery: bool = False
    open_when_complete: bool = False
    preview_emphasis: Literal["development", "all_transitions"] = "development"


class WorkfileSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[WORKFILE_SCHEMA_VERSION]
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    workflow: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]*$")
    recipe: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]*$")
    area: AreaSpec
    time: TimeSpec
    sources: SourcesSpec = Field(default_factory=SourcesSpec)
    data: DataSpec = Field(default_factory=DataSpec)
    processing: ProcessingSpec = Field(default_factory=ProcessingSpec)
    limits: LimitsSpec = Field(default_factory=LimitsSpec)
    outputs: OutputSpec = Field(default_factory=OutputSpec)
    classification: HybridClassificationSpec | None = None

    @model_validator(mode="after")
    def one_workflow(self) -> "WorkfileSpec":
        if bool(self.workflow) == bool(self.recipe):
            raise ValueError("specify exactly one of workflow or recipe")
        if (
            self.classification is not None
            and self.workflow_id
            != "naip_cdl_index_hybrid_classification_audit"
        ):
            raise ValueError(
                "classification override is supported only by the "
                "index-guided hybrid classification workflow"
            )
        return self

    @property
    def workflow_id(self) -> str:
        return str(self.workflow or self.recipe).replace("-", "_")


class HumanDevelopmentEpochSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    year: int = Field(ge=1985, le=2200)
    land_cover_path: str | None = Field(default=None, min_length=1)
    imperviousness_path: str | None = Field(default=None, min_length=1)

    @field_validator("land_cover_path", "imperviousness_path")
    @classmethod
    def local_raster_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "://" in value:
            raise ValueError("epoch raster paths must be local pinned paths, not URLs")
        if Path(value).suffix.lower() not in {".tif", ".tiff"}:
            raise ValueError("epoch raster paths must name GeoTIFF files")
        return value


class HumanDevelopmentSourcesSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy: Literal["pinned", "service_discovered"] = "pinned"
    source_id: Literal["usgs_annual_nlcd", "usda_nass_cdl_imageserver"] = ANNUAL_NLCD_SOURCE_ID
    mapping_id: Literal["annual_nlcd_development_v1", "usda_cdl_development_proxy_v1"] = ANNUAL_NLCD_MAPPING_ID
    collection: Literal[1] | None = 1
    version: Literal[2] | None = 2
    region: Literal["CU"] | None = "CU"
    context_imagery_source_id: Literal["usgs_naip_imageserver"] | None = None
    context_year: int | None = Field(default=None, ge=1985, le=2200)

    @model_validator(mode="after")
    def source_contract_is_compatible(self) -> "HumanDevelopmentSourcesSpec":
        validate_source_mapping(self.source_id, self.mapping_id)
        if self.policy == "pinned":
            if self.source_id != ANNUAL_NLCD_SOURCE_ID or self.mapping_id != ANNUAL_NLCD_MAPPING_ID:
                raise ValueError("pinned human-development inputs currently require the Annual NLCD mapping")
            if (self.collection, self.version, self.region) != (1, 2, "CU"):
                raise ValueError("pinned Annual NLCD requires collection 1, version 2, region CU")
            if self.context_imagery_source_id is not None or self.context_year is not None:
                raise ValueError("context imagery is supported only for service_discovered CDL studies")
        else:
            if self.source_id != USDA_CDL_SOURCE_ID or self.mapping_id != USDA_CDL_MAPPING_ID:
                raise ValueError("service_discovered human development requires the USDA CDL proxy mapping")
            if any(name in self.model_fields_set for name in ("collection", "version", "region")):
                raise ValueError("Annual NLCD collection fields do not apply to service_discovered CDL")
            self.collection = None
            self.version = None
            self.region = None
            if self.context_imagery_source_id not in {None, USGS_NAIP_SOURCE_ID}:
                raise ValueError("unknown context imagery source")
            if self.context_imagery_source_id is None and self.context_year is not None:
                raise ValueError("context_year requires context_imagery_source_id")
        return self


class HumanDevelopmentProcessingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_crs: Literal["EPSG:5070"] = "EPSG:5070"
    resolution_m: Literal[30.0] = 30.0
    window_size: int = Field(default=512, ge=16, le=4096)
    service_tile_size: int = Field(default=2048, ge=64, le=4097)


class HumanDevelopmentWorkfileSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[HUMAN_DEVELOPMENT_WORKFILE_SCHEMA_VERSION]
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    workflow: Literal[HUMAN_DEVELOPMENT_WORKFLOW_ID]
    area: AreaSpec
    epochs: list[HumanDevelopmentEpochSpec] = Field(min_length=2)
    sources: HumanDevelopmentSourcesSpec = Field(default_factory=HumanDevelopmentSourcesSpec)
    data: DataSpec = Field(default_factory=DataSpec)
    processing: HumanDevelopmentProcessingSpec = Field(default_factory=HumanDevelopmentProcessingSpec)
    limits: LimitsSpec = Field(default_factory=LimitsSpec)
    outputs: OutputSpec = Field(default_factory=OutputSpec)

    @model_validator(mode="after")
    def epochs_are_unique_and_ordered(self) -> "HumanDevelopmentWorkfileSpec":
        years = [epoch.year for epoch in self.epochs]
        if years != sorted(years):
            raise ValueError("epochs must be ordered by ascending year")
        if len(years) != len(set(years)):
            raise ValueError("epoch years must be unique")
        if self.sources.policy == "pinned":
            if any(epoch.land_cover_path is None for epoch in self.epochs):
                raise ValueError("pinned source policy requires land_cover_path for every epoch")
        else:
            if any(epoch.land_cover_path is not None or epoch.imperviousness_path is not None for epoch in self.epochs):
                raise ValueError("service_discovered CDL epochs must not provide local raster paths")
            if self.data.reuse in {"auto", "never"} and not self.data.allow_network:
                raise ValueError("live CDL acquisition requires explicit data.allow_network: true")
            if self.sources.context_year is not None and self.sources.context_year not in years:
                raise ValueError("context_year must be one of the declared epochs")
            if self.outputs.include_context_imagery and self.sources.context_imagery_source_id is None:
                raise ValueError("include_context_imagery requires a context imagery source")
        return self

    @property
    def workflow_id(self) -> str:
        return HUMAN_DEVELOPMENT_WORKFLOW_ID

    @property
    def comparison_mode(self) -> Literal["paired_comparison", "multi_epoch_time_series"]:
        return "paired_comparison" if len(self.epochs) == 2 else "multi_epoch_time_series"


AnyWorkfileSpec: TypeAlias = WorkfileSpec | HumanDevelopmentWorkfileSpec


@dataclass(frozen=True)
class Workfile:
    path: Path
    spec: AnyWorkfileSpec
    prose: str
    front_matter: dict[str, Any]


def _reject_dangerous_keys(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(part in normalized for part in FORBIDDEN_KEY_PARTS):
                raise WorkfileError(f"credentials and executable commands are forbidden: {'.'.join(path + (str(key),))}")
            _reject_dangerous_keys(child, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_dangerous_keys(child, path + (str(index),))


def _split_front_matter(text: str) -> tuple[str, str]:
    normalized = text.replace("\r\n", "\n")
    lines = normalized.splitlines()
    if not lines or lines[0].strip() != "---":
        raise WorkfileError("workfile must begin with YAML front matter delimited by ---")
    try:
        closing = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as exc:
        raise WorkfileError("workfile front matter is missing its closing --- delimiter") from exc
    return "\n".join(lines[1:closing]), "\n".join(lines[closing + 1 :]).lstrip("\n")


def load_workfile(path: Path, *, repository_root: Path | None = None) -> Workfile:
    try:
        front_text, prose = _split_front_matter(path.read_text(encoding="utf-8"))
        raw = yaml.load(front_text, Loader=_UniqueKeyLoader)
    except WorkfileError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise WorkfileError(f"Unable to read workfile {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise WorkfileError("workfile front matter must be a YAML mapping")
    _reject_dangerous_keys(raw)
    schema_version = raw.get("schema_version")
    model: type[WorkfileSpec] | type[HumanDevelopmentWorkfileSpec]
    if schema_version == WORKFILE_SCHEMA_VERSION:
        model = WorkfileSpec
    elif schema_version == HUMAN_DEVELOPMENT_WORKFILE_SCHEMA_VERSION:
        model = HumanDevelopmentWorkfileSpec
    else:
        raise WorkfileError(f"unsupported workfile schema_version: {schema_version}")
    try:
        spec = model.model_validate(raw)
    except ValidationError as exc:
        raise WorkfileError(f"Invalid workfile {path}: {exc}") from exc
    if repository_root is not None:
        recipe_path = repository_root / "recipes" / "ag" / f"{spec.workflow_id}.json"
        if spec.workflow_id != HUMAN_DEVELOPMENT_WORKFLOW_ID and not recipe_path.is_file():
            raise WorkfileError(f"unsupported workflow or recipe: {spec.workflow or spec.recipe}")
    return Workfile(path=path.resolve(), spec=spec, prose=prose, front_matter=raw)


def workfile_schema() -> dict[str, Any]:
    schema = WorkfileSpec.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "FasterRaster Markdown workfile v1 front matter"
    return schema


def human_development_workfile_schema() -> dict[str, Any]:
    schema = HumanDevelopmentWorkfileSpec.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "FasterRaster human-development Markdown workfile v2 front matter"
    return schema


def workfile_template(name: str = "colby-irrigation-2023") -> str:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-.") or "fasterraster-study"
    return f"""---
schema_version: fasterraster.work/v1
name: {safe_name}
workflow: irrigation-field-structure

area:
  bbox:
    - -101.065
    - 39.360
    - -101.045
    - 39.380

time:
  start: 2023-04-01
  end: 2023-10-31
  crop_year: 2023

sources:
  policy: auto

data:
  reuse: auto

processing:
  resolution_m: 1.2

limits:
  maximum_download_mb: 250

outputs:
  preview: true
  open_when_complete: false
---

# {safe_name.replace('-', ' ').title()}

Describe the research question, methods, observations, citations, and interpretation here.
Only the validated YAML front matter above controls FasterRaster execution.
"""
