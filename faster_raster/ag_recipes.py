from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Annotated, Any, Literal, TypeAlias

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
from faster_raster.spectral_indices import (
    BUILTIN_INDEX_REGISTRY,
    SEMANTIC_BANDS,
    parse_index_expression,
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


HYBRID_SCIENTIFIC_CLAIM = (
    "Single-date high-resolution NAIP spectral surface classification weakly "
    "supervised by USDA CDL superclasses, with explicitly declared "
    "scene-relative spectral-index specialist refinements. Spatial holdout "
    "metrics measure agreement with weak labels, not independent ground-truth "
    "accuracy."
)
HYBRID_UNSUPPORTED_CLAIMS = (
    *CLASSIFICATION_UNSUPPORTED_CLAIMS,
    "surface reflectance from raw NAIP digital numbers",
    "physical causation from a selected spectral index",
    "threshold transferability across dates, mosaics, sensors, or radiometric products",
    "mining, abandonment, contamination, ownership, or safety from spectral similarity",
)
GeneralClassId = Literal[
    "cropland",
    "fallow_or_barren",
    "developed_open_or_low",
    "developed_medium_or_high",
    "noncrop_vegetation",
    "water",
]
GENERAL_CLASS_CODES: dict[str, int] = {
    output.name: output.code
    for output in classification_mapping(
        CDL_SURFACE_SUPERCLASSES_MAPPING_ID
    ).output_classes
    if output.code != 0
}


class GeneralClassificationSpec(ClassificationSpec):
    requested_class_count: int = Field(ge=1, le=6)
    class_ids: list[GeneralClassId] = Field(min_length=1, max_length=6)

    @field_validator("class_ids")
    @classmethod
    def general_classes_are_unique(
        cls, values: list[GeneralClassId]
    ) -> list[GeneralClassId]:
        if len(values) != len(set(values)):
            raise ValueError("general class IDs must be unique")
        return values

    @model_validator(mode="after")
    def general_count_matches_meanings(self) -> "GeneralClassificationSpec":
        if self.requested_class_count != len(self.class_ids):
            raise ValueError(
                "requested general class count must match explicit class_ids"
            )
        return self

    @property
    def class_codes(self) -> tuple[int, ...]:
        return tuple(GENERAL_CLASS_CODES[class_id] for class_id in self.class_ids)


class IndexRequestSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    expression: str | None = Field(default=None, min_length=1, max_length=512)
    persist: bool = False
    display: bool = False

    @model_validator(mode="after")
    def index_is_builtin_or_safe_custom(self) -> "IndexRequestSpec":
        if self.expression is None:
            definition = BUILTIN_INDEX_REGISTRY.get(self.index_id)
            if definition.parameterized:
                raise ValueError(
                    f"parameterized index {self.index_id!r} requires a "
                    "specialist strategy"
                )
        else:
            if self.index_id in BUILTIN_INDEX_REGISTRY.ids:
                raise ValueError(
                    "a custom expression cannot replace a built-in index ID"
                )
            parse_index_expression(self.expression)
        return self


class IndexConditionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    direction: Literal["high", "low", "range"]
    threshold: float | None = None
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def threshold_matches_direction(self) -> "IndexConditionSpec":
        values = (self.threshold, self.minimum, self.maximum)
        if any(value is not None and not math.isfinite(value) for value in values):
            raise ValueError("specialist thresholds must be finite")
        if self.direction in {"high", "low"}:
            if self.threshold is None or self.minimum is not None or self.maximum is not None:
                raise ValueError(
                    "high/low conditions require only a finite threshold"
                )
        elif (
            self.threshold is not None
            or self.minimum is None
            or self.maximum is None
            or self.minimum >= self.maximum
        ):
            raise ValueError(
                "range conditions require minimum < maximum and no threshold"
            )
        return self


class SingleIndexThresholdStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["single_index_threshold"]
    condition: IndexConditionSpec


class MultiIndexBooleanStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["multi_index_boolean"]
    operator: Literal["all", "any", "at_least_k"]
    conditions: list[IndexConditionSpec] = Field(min_length=2, max_length=12)
    k: int | None = Field(default=None, ge=1, le=12)

    @model_validator(mode="after")
    def boolean_operator_is_complete(self) -> "MultiIndexBooleanStrategy":
        if self.operator == "at_least_k":
            if self.k is None or self.k > len(self.conditions):
                raise ValueError("at_least_k requires k no greater than N")
        elif self.k is not None:
            raise ValueError("k applies only to at_least_k")
        condition_keys = [
            json.dumps(
                condition.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            )
            for condition in self.conditions
        ]
        if len(condition_keys) != len(set(condition_keys)):
            raise ValueError(
                "Boolean specialist conditions must not contain duplicates"
            )
        return self


class WeightedIndexInputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    normalization_minimum: float
    normalization_maximum: float
    weight: float = Field(ge=-1000.0, le=1000.0)

    @model_validator(mode="after")
    def normalization_is_finite(self) -> "WeightedIndexInputSpec":
        values = (
            self.normalization_minimum,
            self.normalization_maximum,
            self.weight,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("weighted-score values must be finite")
        if self.normalization_minimum >= self.normalization_maximum:
            raise ValueError(
                "weighted-score normalization minimum must be below maximum"
            )
        return self


class MultiIndexWeightedStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["multi_index_weighted_score"]
    inputs: list[WeightedIndexInputSpec] = Field(min_length=2, max_length=12)
    intercept: float = Field(default=0.0, ge=-1000.0, le=1000.0)
    direction: Literal["high", "low"] = "high"
    threshold: float
    weights_source: Literal["user_provided", "learned_spatial_calibration"]

    @model_validator(mode="after")
    def weighted_score_is_bounded(self) -> "MultiIndexWeightedStrategy":
        if not math.isfinite(self.intercept) or not math.isfinite(self.threshold):
            raise ValueError("weighted-score intercept and threshold must be finite")
        ids = [item.index_id for item in self.inputs]
        if len(ids) != len(set(ids)):
            raise ValueError("weighted-score index inputs must be unique")
        if all(item.weight == 0 for item in self.inputs):
            raise ValueError("weighted-score inputs cannot all have zero weight")
        return self


class TargetSignatureStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["target_signature_similarity"]
    target_bands: dict[str, float] = Field(min_length=2, max_length=7)
    weights: dict[str, float] = Field(default_factory=dict, max_length=7)
    threshold: float = Field(gt=0.0, le=1.0)
    target_source: Literal["user_provided", "positive_calibration_points"]

    @model_validator(mode="after")
    def signature_is_semantic_and_finite(self) -> "TargetSignatureStrategy":
        unknown = sorted(set(self.target_bands) - set(SEMANTIC_BANDS))
        if unknown:
            raise ValueError(
                "target signature has unknown band(s): " + ", ".join(unknown)
            )
        if not all(math.isfinite(value) for value in self.target_bands.values()):
            raise ValueError("target signature values must be finite")
        if set(self.weights) - set(self.target_bands):
            raise ValueError("target signature weights require a target band")
        if not all(
            math.isfinite(value) and value > 0
            for value in self.weights.values()
        ):
            raise ValueError(
                "target signature weights must be finite and positive"
            )
        return self


SpecialistStrategy: TypeAlias = Annotated[
    SingleIndexThresholdStrategy
    | MultiIndexBooleanStrategy
    | MultiIndexWeightedStrategy
    | TargetSignatureStrategy,
    Field(discriminator="type"),
]


class CalibrationPointSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    longitude: float = Field(ge=-180.0, le=180.0)
    latitude: float = Field(ge=-90.0, le=90.0)
    class_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")

    @model_validator(mode="after")
    def coordinates_are_finite(self) -> "CalibrationPointSpec":
        if not math.isfinite(self.longitude) or not math.isfinite(self.latitude):
            raise ValueError("calibration coordinates must be finite")
        return self


class CalibrationEvidenceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal[
        "fixed_rule",
        "cdl_weak_labels",
        "user_points",
        "target_vector",
    ]
    points: list[CalibrationPointSpec] = Field(default_factory=list, max_length=5000)
    positive_general_classes: list[GeneralClassId] = Field(
        default_factory=list,
        max_length=6,
    )
    minimum_positive_support: int = Field(default=20, ge=1, le=100_000)
    minimum_negative_support: int = Field(default=20, ge=1, le=100_000)
    publish_coordinates: Literal[False] = False

    @model_validator(mode="after")
    def point_source_matches_points(self) -> "CalibrationEvidenceSpec":
        if self.source == "user_points" and not self.points:
            raise ValueError("user_points calibration requires points")
        if self.source != "user_points" and self.points:
            raise ValueError("calibration points require source user_points")
        if (
            self.source == "cdl_weak_labels"
            and not self.positive_general_classes
        ):
            raise ValueError(
                "cdl_weak_labels calibration requires explicit "
                "positive_general_classes"
            )
        if (
            self.source != "cdl_weak_labels"
            and self.positive_general_classes
        ):
            raise ValueError(
                "positive_general_classes applies only to cdl_weak_labels"
            )
        if len(self.positive_general_classes) != len(
            set(self.positive_general_classes)
        ):
            raise ValueError(
                "positive calibration general classes must be unique"
            )
        coordinates = [
            (point.longitude, point.latitude) for point in self.points
        ]
        if len(coordinates) != len(set(coordinates)):
            raise ValueError(
                "duplicate or contradictory calibration coordinates are "
                "not allowed"
            )
        return self


class SpecialistClassSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=120)
    output_code: int = Field(ge=7, le=254)
    intended_interpretation: str = Field(min_length=1)
    unsupported_interpretations: list[str] = Field(min_length=1)
    strategy: SpecialistStrategy
    eligible_parent_general_classes: list[GeneralClassId] = Field(min_length=1)
    priority: int = Field(ge=0, le=10_000)
    minimum_support_pixels: int = Field(default=1, ge=1, le=100_000_000)
    uncertainty_behavior: Literal["keep_general", "mark_unresolved"]
    calibration: CalibrationEvidenceSpec

    @field_validator(
        "unsupported_interpretations",
        "eligible_parent_general_classes",
    )
    @classmethod
    def specialist_lists_are_unique(cls, values: list[Any]) -> list[Any]:
        if len(values) != len(set(values)):
            raise ValueError("specialist class lists must be unique")
        return values


class CandidateSearchBoundsSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_indices: list[str] = Field(default_factory=list, max_length=12)
    maximum_pairs: int = Field(default=36, ge=0, le=66)
    maximum_triples: int = Field(default=24, ge=0, le=220)
    maximum_candidate_models: int = Field(default=128, ge=1, le=512)
    maximum_calibration_samples: int = Field(
        default=100_000, ge=40, le=1_000_000
    )
    inner_spatial_folds: int = Field(default=3, ge=2, le=10)
    ranking_metric: Literal["macro_f1", "balanced_accuracy"] = "macro_f1"
    minimum_selection_metric: float = Field(default=0.55, ge=0.0, le=1.0)
    minimum_complexity_improvement: float = Field(
        default=0.01, ge=0.0, le=1.0
    )
    tie_tolerance: float = Field(default=1e-9, ge=0.0, le=0.01)

    @field_validator("candidate_indices")
    @classmethod
    def candidate_indices_are_unique(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("candidate index IDs must be unique")
        for value in values:
            definition = BUILTIN_INDEX_REGISTRY.get(value)
            if definition.parameterized:
                raise ValueError(
                    f"candidate index {value!r} requires an explicit "
                    "parameterized specialist contract"
                )
        return values


class SpecialistCollectionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_class_count: int = Field(ge=1, le=32)
    selection_mode: Literal["user_defined", "recommendation", "automatic"]
    automatic_authorized: bool = False
    classes: list[SpecialistClassSpec] = Field(min_length=1, max_length=32)
    search: CandidateSearchBoundsSpec = Field(
        default_factory=CandidateSearchBoundsSpec
    )

    @model_validator(mode="after")
    def collection_contract_is_complete(self) -> "SpecialistCollectionSpec":
        if self.requested_class_count != len(self.classes):
            raise ValueError(
                "requested specialist class count must match explicit classes"
            )
        ids = [item.class_id for item in self.classes]
        codes = [item.output_code for item in self.classes]
        if len(ids) != len(set(ids)):
            raise ValueError("specialist class IDs must be unique")
        if len(codes) != len(set(codes)):
            raise ValueError("specialist output codes must be unique")
        if self.selection_mode == "automatic" and not self.automatic_authorized:
            raise ValueError(
                "automatic index selection requires explicit authorization"
            )
        if self.selection_mode != "automatic" and self.automatic_authorized:
            raise ValueError(
                "automatic_authorized applies only to automatic selection"
            )
        if self.selection_mode in {"recommendation", "automatic"}:
            if not self.search.candidate_indices:
                raise ValueError(
                    "recommendation and automatic selection require bounded "
                    "candidate_indices"
                )
            if any(
                item.calibration.source
                not in {"cdl_weak_labels", "user_points"}
                for item in self.classes
            ):
                raise ValueError(
                    "recommendation and automatic selection require "
                    "CDL weak labels or user calibration points"
                )
        return self


class HybridArbitrationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: Literal["priority_then_class_code"]
    equal_priority_tie: Literal["mark_unresolved", "lowest_class_code"]
    unresolved_code: Literal[255] = 255
    preserve_general_output: Literal[True] = True
    compare_unscaled_scores: Literal[False] = False


class HybridClassificationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    general: GeneralClassificationSpec
    indices: list[IndexRequestSpec] = Field(min_length=1, max_length=32)
    specialists: SpecialistCollectionSpec
    arbitration: HybridArbitrationSpec

    @model_validator(mode="after")
    def hybrid_references_are_resolved(self) -> "HybridClassificationSpec":
        index_ids = [item.index_id for item in self.indices]
        if len(index_ids) != len(set(index_ids)):
            raise ValueError("index request IDs must be unique")
        available = set(index_ids)
        custom = {
            item.index_id for item in self.indices if item.expression is not None
        }
        for specialist in self.specialists.classes:
            missing_parents = (
                set(specialist.eligible_parent_general_classes)
                - set(self.general.class_ids)
            )
            if missing_parents:
                raise ValueError(
                    f"specialist {specialist.class_id} has unavailable parent "
                    f"class(es): {', '.join(sorted(missing_parents))}"
                )
            missing_calibration_classes = (
                set(specialist.calibration.positive_general_classes)
                - set(self.general.class_ids)
            )
            if missing_calibration_classes:
                raise ValueError(
                    f"specialist {specialist.class_id} calibration references "
                    "unavailable general class(es): "
                    + ", ".join(sorted(missing_calibration_classes))
                )
            if specialist.calibration.source == "user_points":
                positive = sum(
                    point.class_id == specialist.class_id
                    for point in specialist.calibration.points
                )
                negative = len(specialist.calibration.points) - positive
                if (
                    positive
                    < specialist.calibration.minimum_positive_support
                ):
                    raise ValueError(
                        f"specialist {specialist.class_id} user points do not "
                        "meet explicit positive support minimum"
                    )
                if (
                    self.specialists.selection_mode
                    in {"recommendation", "automatic"}
                    and negative
                    < specialist.calibration.minimum_negative_support
                ):
                    raise ValueError(
                        f"specialist {specialist.class_id} user points do not "
                        "meet explicit negative support minimum"
                    )
            strategy = specialist.strategy
            if (
                isinstance(strategy, TargetSignatureStrategy)
                and strategy.target_source
                == "positive_calibration_points"
                and specialist.calibration.source != "user_points"
            ):
                raise ValueError(
                    "positive_calibration_points target signatures require "
                    "user_points calibration evidence"
                )
            if (
                isinstance(strategy, TargetSignatureStrategy)
                and strategy.target_source
                == "positive_calibration_points"
                and self.specialists.selection_mode != "user_defined"
            ):
                raise ValueError(
                    "point-estimated target signatures are supported only by "
                    "user_defined mode; learned candidate selection must keep "
                    "the outer holdout isolated"
                )
            if (
                isinstance(strategy, TargetSignatureStrategy)
                and strategy.target_source == "user_provided"
                and specialist.calibration.source != "target_vector"
            ):
                raise ValueError(
                    "user-provided target signatures require target_vector "
                    "calibration evidence"
                )
            if isinstance(strategy, SingleIndexThresholdStrategy):
                referenced = {strategy.condition.index_id}
            elif isinstance(strategy, MultiIndexBooleanStrategy):
                referenced = {
                    condition.index_id for condition in strategy.conditions
                }
            elif isinstance(strategy, MultiIndexWeightedStrategy):
                referenced = {item.index_id for item in strategy.inputs}
            else:
                referenced = {"target_signature_similarity"}
            missing = referenced - available - {"target_signature_similarity"}
            if missing:
                raise ValueError(
                    f"specialist {specialist.class_id} references undeclared "
                    f"index(es): {', '.join(sorted(missing))}"
                )
            if (
                specialist.calibration.source == "target_vector"
                and not isinstance(strategy, TargetSignatureStrategy)
            ):
                raise ValueError(
                    "target_vector calibration requires target-signature strategy"
                )
            if custom & referenced:
                for index_id in custom & referenced:
                    request = next(
                        item for item in self.indices if item.index_id == index_id
                    )
                    parse_index_expression(str(request.expression))
        required_specialist_indices = {
            index_id
            for specialist in self.specialists.classes
            for index_id in _strategy_index_ids(specialist.strategy)
        }
        for request in self.indices:
            if request.index_id in required_specialist_indices and not request.persist:
                raise ValueError(
                    f"specialist input index {request.index_id!r} must be persisted"
                )
        return self


def _strategy_index_ids(strategy: SpecialistStrategy) -> tuple[str, ...]:
    if isinstance(strategy, SingleIndexThresholdStrategy):
        return (strategy.condition.index_id,)
    if isinstance(strategy, MultiIndexBooleanStrategy):
        return tuple(condition.index_id for condition in strategy.conditions)
    if isinstance(strategy, MultiIndexWeightedStrategy):
        return tuple(item.index_id for item in strategy.inputs)
    return ("target_signature_similarity",)


class AgriculturalRecipeV4(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[4]
    recipe_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    analysis_type: Literal["index_guided_hybrid_classification"]
    scientific_claim: Literal[HYBRID_SCIENTIFIC_CLAIM]
    unsupported_claims: list[str] = Field(min_length=1)
    required_assets: list[V3AssetName] = Field(min_length=1)
    maximum_naip_pixel_size_m: float = Field(gt=0, le=100)
    resampling: RecipeResampling
    classification: HybridClassificationSpec
    preview: Literal["index_hybrid_classification_audit"]
    inspection_focus: list[str] = Field(min_length=1)
    defaults: RecipeDefaults
    required_output_artifacts: list[str] = Field(min_length=1)

    @field_validator(
        "required_assets",
        "required_output_artifacts",
        "unsupported_claims",
        "inspection_focus",
    )
    @classmethod
    def v4_lists_are_unique_and_nonempty(
        cls, values: list[str]
    ) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("recipe values must be unique")
        if any(not value.strip() for value in values):
            raise ValueError("recipe text entries must be nonempty")
        return values

    @model_validator(mode="after")
    def hybrid_contract_is_complete(self) -> "AgriculturalRecipeV4":
        if set(self.required_assets) != {"naip_multispectral", "cdl_classes"}:
            raise ValueError(
                "hybrid classification recipes require exactly "
                "naip_multispectral and cdl_classes"
            )
        if set(self.unsupported_claims) != set(HYBRID_UNSUPPORTED_CLAIMS):
            raise ValueError(
                "hybrid recipe must declare the complete unsupported-claims contract"
            )
        return self


AgriculturalRecipe: TypeAlias = Annotated[
    AgriculturalRecipeV2 | AgriculturalRecipeV3 | AgriculturalRecipeV4,
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
    schema["title"] = "FasterRaster agricultural recipe v2-v4"
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


def agricultural_recipe_v4_schema() -> dict:
    schema = AgriculturalRecipeV4.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "FasterRaster agricultural recipe v4"
    return schema
