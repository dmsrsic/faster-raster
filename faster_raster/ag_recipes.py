from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


AssetName = Literal[
    "natural",
    "ndvi",
    "cdl_classes",
    "cdl_color",
    "hillshade",
]
PreviewType = Literal[
    "ndvi_cdl_boundaries",
    "field_structure",
    "class_inventory",
    "crop_terrain",
]
ResamplingMethod = Literal[
    "nearest",
    "mode",
    "bilinear",
    "cubic",
    "lanczos",
    "average",
]


class RecipeDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_total_bytes: int = Field(gt=0, le=20_000_000_000)
    naip_resolution_meters: float = Field(gt=0, le=100)
    portion: Literal["native", "overview"]
    preview_width: int = Field(ge=640, le=16_384)
    service_tile_size: int = Field(ge=64, le=10_000)


class RecipeResampling(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categorical: ResamplingMethod
    imagery: ResamplingMethod
    terrain: ResamplingMethod | None = None

    @model_validator(mode="after")
    def semantic_methods_are_safe(self) -> "RecipeResampling":
        if self.categorical not in {"nearest", "mode"}:
            raise ValueError("categorical resampling must be nearest or mode")
        if self.imagery in {"nearest", "mode"}:
            raise ValueError("imagery resampling must be continuous-data resampling")
        if self.terrain in {"nearest", "mode"}:
            raise ValueError("terrain resampling must be continuous-data resampling")
        return self


class AgriculturalRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2]
    recipe_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    required_assets: list[AssetName] = Field(min_length=1)
    maximum_naip_pixel_size_m: float = Field(gt=0, le=100)
    resampling: RecipeResampling
    preview: PreviewType
    inspection_focus: list[str] = Field(min_length=1)
    defaults: RecipeDefaults
    required_output_artifacts: list[str] = Field(min_length=1)

    @field_validator("required_assets", "required_output_artifacts")
    @classmethod
    def values_are_unique(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("values must be unique")
        return values

    @field_validator("inspection_focus")
    @classmethod
    def focus_is_nonempty(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("inspection focus entries must be nonempty")
        return values

    @model_validator(mode="after")
    def preview_dependencies_exist(self) -> "AgriculturalRecipe":
        required = set(self.required_assets)
        if not {"natural", "ndvi", "cdl_classes"}.issubset(required):
            raise ValueError(
                "agricultural previews require natural, ndvi, and cdl_classes"
            )
        if self.preview == "crop_terrain" and "hillshade" not in required:
            raise ValueError("crop_terrain preview requires hillshade")
        return self


class RecipeLoadError(ValueError):
    """Raised when a recipe cannot be loaded deterministically."""


def load_recipe(path: Path) -> AgriculturalRecipe:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecipeLoadError(f"Unable to read recipe {path}: {exc}") from exc

    try:
        recipe = AgriculturalRecipe.model_validate(raw)
    except Exception as exc:
        raise RecipeLoadError(f"Invalid recipe {path}: {exc}") from exc

    expected_id = path.stem
    if recipe.recipe_id != expected_id:
        raise RecipeLoadError(
            f"recipe_id {recipe.recipe_id!r} does not match filename {expected_id!r}"
        )
    return recipe


def load_named_recipe(root: Path, recipe_id: str) -> AgriculturalRecipe:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", recipe_id):
        raise RecipeLoadError(f"Invalid agricultural recipe id: {recipe_id!r}")
    path = root / "recipes" / "ag" / f"{recipe_id}.json"
    if not path.is_file():
        raise RecipeLoadError(f"Unknown agricultural recipe: {recipe_id}")
    return load_recipe(path)


def agricultural_recipe_schema() -> dict:
    schema = AgriculturalRecipe.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "FasterRaster agricultural recipe v2"
    return schema
