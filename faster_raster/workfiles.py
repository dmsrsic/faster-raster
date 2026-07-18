from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from faster_raster.ag_geography import BBoxValidationError, validate_bbox


WORKFILE_SCHEMA_VERSION = "fasterraster.work/v1"
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
    open_when_complete: bool = False


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

    @model_validator(mode="after")
    def one_workflow(self) -> "WorkfileSpec":
        if bool(self.workflow) == bool(self.recipe):
            raise ValueError("specify exactly one of workflow or recipe")
        return self

    @property
    def workflow_id(self) -> str:
        return str(self.workflow or self.recipe).replace("-", "_")


@dataclass(frozen=True)
class Workfile:
    path: Path
    spec: WorkfileSpec
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
    try:
        spec = WorkfileSpec.model_validate(raw)
    except ValidationError as exc:
        raise WorkfileError(f"Invalid workfile {path}: {exc}") from exc
    if repository_root is not None:
        recipe_path = repository_root / "recipes" / "ag" / f"{spec.workflow_id}.json"
        if not recipe_path.is_file():
            raise WorkfileError(f"unsupported workflow or recipe: {spec.workflow or spec.recipe}")
    return Workfile(path=path.resolve(), spec=spec, prose=prose, front_matter=raw)


def workfile_schema() -> dict[str, Any]:
    schema = WorkfileSpec.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "FasterRaster Markdown workfile v1 front matter"
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
