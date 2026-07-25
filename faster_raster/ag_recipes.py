from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from faster_raster.ag_classification_contracts import (
    CLASSIFICATION_SCIENTIFIC_CLAIM,
    CLASSIFICATION_UNSUPPORTED_CLAIMS,
    CDL_SURFACE_SUPERCLASSES_MAPPING_ID,
    classification_mapping,
)


V2AssetName = Literal[
    "natural",
    "ndvi",
    "cdl_classes",
    "cdl_color",
    "hillshade",
]
V3AssetName = Literal["naip_multispectral", "cdl_classes"]
V2PreviewType = Literal[
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


class AgriculturalRecipeV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2]
    recipe_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    required_assets: list[V2AssetName] = Field(min_length=1)
    maximum_naip_pixel_size_m: float = Field(gt=0, le=100)
    resampling: RecipeResampling
    preview: V2PreviewType
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
    def preview_dependencies_exist(self) -> "AgriculturalRecipeV2":
        required = set(self.required_assets)
        if not {"natural", "ndvi", "cdl_classes"}.issubset(required):
            raise ValueError(
                "agricultural previews require natural, ndvi, and cdl_classes"
            )
        if self.preview == "crop_terrain" and "hillshade" not in required:
            raise ValueError("crop_terrain preview requires hillshade")
        return self


ClassificationFeature = Literal[
    "red",
    "green",
    "blue",
    "nir",
    "ndvi",
    "gndvi",
    "vari",
    "excess_green",
    "brightness",
    "saturation",
]


class ClassificationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mapping_id: Literal[CDL_SURFACE_SUPERCLASSES_MAPPING_ID]
    backend: Literal["random_forest"]
    random_seed: int = Field(ge=0, le=2_147_483_647)
    training_core_radius_cdl_cells: int = Field(ge=1, le=8)
    maximum_samples_per_class: int = Field(gt=0, le=1_000_000)
    minimum_training_samples_per_class: int = Field(gt=0, le=1_000_000)
    spatial_holdout_folds: int = Field(ge=2, le=20)
    spatial_holdout_fold: int = Field(default=0, ge=0, le=19)
    inference_window_size: int = Field(ge=16, le=4096)
    confidence_threshold: float = Field(ge=0.0, le=1.0)
    sieve_minimum_pixels: int = Field(ge=0, le=100_000)
    features: list[ClassificationFeature] = Field(min_length=1)
    n_estimators: int = Field(default=192, ge=1, le=4096)
    max_depth: int = Field(default=20, ge=1, le=256)
    min_samples_leaf: int = Field(default=5, ge=1, le=100_000)
    max_features: Literal["sqrt"] = "sqrt"
    class_weight: Literal["balanced_subsample"] = "balanced_subsample"
    n_jobs: Literal[1] = 1

    @field_validator("features")
    @classmethod
    def feature_order_is_unique(
        cls, values: list[ClassificationFeature]
    ) -> list[ClassificationFeature]:
        if len(values) != len(set(values)):
            raise ValueError("classification features must be unique")
        return values

    @model_validator(mode="after")
    def sampling_and_holdout_are_coherent(self) -> "ClassificationSpec":
        if self.minimum_training_samples_per_class > self.maximum_samples_per_class:
            raise ValueError(
                "minimum_training_samples_per_class cannot exceed "
                "maximum_samples_per_class"
            )
        if self.spatial_holdout_fold >= self.spatial_holdout_folds:
            raise ValueError(
                "spatial_holdout_fold must be less than spatial_holdout_folds"
            )
        classification_mapping(self.mapping_id)
        return self


class AgriculturalRecipeV3(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[3]
    recipe_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    analysis_type: Literal["weak_supervised_classification"]
    scientific_claim: Literal[CLASSIFICATION_SCIENTIFIC_CLAIM]
    unsupported_claims: list[str] = Field(min_length=1)
    required_assets: list[V3AssetName] = Field(min_length=1)
    maximum_naip_pixel_size_m: float = Field(gt=0, le=100)
    resampling: RecipeResampling
    classification: ClassificationSpec
    preview: Literal["classification_audit"]
    inspection_focus: list[str] = Field(min_length=1)
    defaults: RecipeDefaults
    required_output_artifacts: list[str] = Field(min_length=1)

    @field_validator(
        "required_assets",
        "required_output_artifacts",
        "unsupported_claims",
    )
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
    def classification_contract_is_complete(self) -> "AgriculturalRecipeV3":
        if set(self.required_assets) != {"naip_multispectral", "cdl_classes"}:
            raise ValueError(
                "classification recipes require exactly naip_multispectral and "
                "cdl_classes"
            )
        if set(self.unsupported_claims) != set(CLASSIFICATION_UNSUPPORTED_CLAIMS):
            raise ValueError(
                "classification recipe must declare the complete unsupported-claims contract"
            )
        return self


AgriculturalRecipe: TypeAlias = Annotated[
    AgriculturalRecipeV2 | AgriculturalRecipeV3,
    Field(discriminator="schema_version"),
]
_AGRICULTURAL_RECIPE_ADAPTER = TypeAdapter(AgriculturalRecipe)


class RecipeLoadError(ValueError):
    """Raised when a recipe cannot be loaded deterministically."""


def load_recipe(path: Path) -> AgriculturalRecipe:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecipeLoadError(f"Unable to read recipe {path}: {exc}") from exc

    try:
        recipe = _AGRICULTURAL_RECIPE_ADAPTER.validate_python(raw)
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
    schema = _AGRICULTURAL_RECIPE_ADAPTER.json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "FasterRaster agricultural recipe v2-v3"
    return schema


def agricultural_recipe_v2_schema() -> dict:
    schema = AgriculturalRecipeV2.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "FasterRaster agricultural recipe v2"
    return schema


def agricultural_recipe_v3_schema() -> dict:
    schema = AgriculturalRecipeV3.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "FasterRaster agricultural recipe v3"
    return schema
