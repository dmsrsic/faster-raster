from __future__ import annotations

import hashlib
import itertools
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
from rasterio.transform import rowcol
from rasterio.warp import transform

from faster_raster.ag_recipes import (
    GENERAL_CLASS_CODES,
    CandidateSearchBoundsSpec,
    CalibrationPointSpec,
    HybridArbitrationSpec,
    IndexConditionSpec,
    MultiIndexBooleanStrategy,
    MultiIndexWeightedStrategy,
    SingleIndexThresholdStrategy,
    SpecialistClassSpec,
    SpecialistStrategy,
    TargetSignatureStrategy,
)
from faster_raster.spectral_indices import target_signature_similarity


HYBRID_ENGINE_VERSION = "fasterraster.hybrid-classification/v1"
DECISION_STATE_LABELS = {
    0: "invalid_or_excluded",
    1: "general_class_retained",
    2: "specialist_override",
    3: "unresolved_specialist_overlap",
}


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IndexArray:
    values: np.ndarray
    valid: np.ndarray

    def __post_init__(self) -> None:
        values = np.asarray(self.values)
        valid = np.asarray(self.valid)
        if values.ndim != 2 or valid.shape != values.shape:
            raise ValueError("index values and validity must be aligned 2-D arrays")


@dataclass(frozen=True)
class SpecialistEvaluation:
    class_id: str
    output_code: int
    priority: int
    score: np.ndarray
    score_valid: np.ndarray
    candidate_before_parent: np.ndarray
    candidate: np.ndarray
    parent_codes: tuple[int, ...]
    support_pixels: int
    enabled: bool
    score_semantics: str
    contract: dict[str, Any]


def evaluate_condition(
    index: IndexArray,
    condition: IndexConditionSpec,
) -> tuple[np.ndarray, np.ndarray]:
    values = np.asarray(index.values, dtype=np.float32)
    valid = np.asarray(index.valid, dtype=bool) & np.isfinite(values)
    margin = np.full(values.shape, np.nan, dtype=np.float32)
    if condition.direction == "high":
        assert condition.threshold is not None
        margin[valid] = values[valid] - np.float32(condition.threshold)
    elif condition.direction == "low":
        assert condition.threshold is not None
        margin[valid] = np.float32(condition.threshold) - values[valid]
    else:
        assert condition.minimum is not None and condition.maximum is not None
        lower = values - np.float32(condition.minimum)
        upper = np.float32(condition.maximum) - values
        margin[valid] = np.minimum(lower[valid], upper[valid])
    return valid & (margin >= 0), margin


def _index_array(
    index_values: Mapping[str, IndexArray],
    index_id: str,
) -> IndexArray:
    try:
        return index_values[index_id]
    except KeyError as exc:
        raise ValueError(
            f"specialist strategy requires unavailable index {index_id!r}"
        ) from exc


def _evaluate_strategy(
    strategy: SpecialistStrategy,
    index_values: Mapping[str, IndexArray],
    *,
    source_bands: Mapping[str, np.ndarray] | None,
    source_valid: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, str, dict[str, Any]]:
    if isinstance(strategy, SingleIndexThresholdStrategy):
        source = _index_array(index_values, strategy.condition.index_id)
        candidate, margin = evaluate_condition(source, strategy.condition)
        valid = np.asarray(source.valid, dtype=bool) & np.isfinite(source.values)
        score = np.asarray(source.values, dtype=np.float32).copy()
        score[~valid] = np.nan
        contract = {
            "strategy": strategy.model_dump(mode="json"),
            "score_semantics": (
                f"analytical {strategy.condition.index_id} value; not a probability"
            ),
            "margin_semantics": (
                "signed distance from the declared threshold or range boundary "
                "in native index units"
            ),
            "positive_margin_pixels": int((margin >= 0).sum()),
        }
        return score, valid, candidate, contract["score_semantics"], contract

    if isinstance(strategy, MultiIndexBooleanStrategy):
        results: list[np.ndarray] = []
        validity: list[np.ndarray] = []
        for condition in strategy.conditions:
            result, _ = evaluate_condition(
                _index_array(index_values, condition.index_id),
                condition,
            )
            results.append(result)
            validity.append(
                np.asarray(
                    _index_array(index_values, condition.index_id).valid,
                    dtype=bool,
                )
            )
        valid = np.logical_and.reduce(validity)
        met = np.sum(np.stack(results), axis=0, dtype=np.uint8)
        if strategy.operator == "all":
            required = len(results)
        elif strategy.operator == "any":
            required = 1
        else:
            assert strategy.k is not None
            required = strategy.k
        candidate = valid & (met >= required)
        score = (met.astype(np.float32) / np.float32(len(results))).astype(
            np.float32
        )
        score[~valid] = np.nan
        semantics = (
            "fraction of declared Boolean index conditions satisfied; "
            "not a probability"
        )
        contract = {
            "strategy": strategy.model_dump(mode="json"),
            "required_conditions": required,
            "condition_count": len(results),
            "score_semantics": semantics,
        }
        return score, valid, candidate, semantics, contract

    if isinstance(strategy, MultiIndexWeightedStrategy):
        valid_parts: list[np.ndarray] = []
        normalized: list[np.ndarray] = []
        weights: list[np.float32] = []
        input_evidence: list[dict[str, Any]] = []
        for item in strategy.inputs:
            source = _index_array(index_values, item.index_id)
            valid_parts.append(np.asarray(source.valid, dtype=bool))
            scale = np.float32(
                item.normalization_maximum - item.normalization_minimum
            )
            normalized_value = np.clip(
                (
                    np.asarray(source.values, dtype=np.float32)
                    - np.float32(item.normalization_minimum)
                )
                / scale,
                np.float32(0.0),
                np.float32(1.0),
            )
            normalized.append(normalized_value)
            weights.append(np.float32(item.weight))
            input_evidence.append(
                {
                    "index_id": item.index_id,
                    "normalization": [
                        item.normalization_minimum,
                        item.normalization_maximum,
                    ],
                    "weight": item.weight,
                }
            )
        valid = np.logical_and.reduce(valid_parts)
        score = np.full(valid.shape, np.float32(strategy.intercept))
        for values, weight in zip(normalized, weights, strict=True):
            score += values * weight
        score[~valid] = np.nan
        if strategy.direction == "high":
            candidate = valid & (score >= np.float32(strategy.threshold))
        else:
            candidate = valid & (score <= np.float32(strategy.threshold))
        minimum = strategy.intercept + sum(
            min(0.0, float(weight)) for weight in weights
        )
        maximum = strategy.intercept + sum(
            max(0.0, float(weight)) for weight in weights
        )
        semantics = (
            "deterministic weighted sum of explicitly normalized index inputs; "
            "not a probability"
        )
        contract = {
            "strategy": strategy.model_dump(mode="json"),
            "normalized_inputs": input_evidence,
            "score_range": [minimum, maximum],
            "score_semantics": semantics,
        }
        return score, valid, candidate, semantics, contract

    assert isinstance(strategy, TargetSignatureStrategy)
    if source_bands is None:
        raise ValueError("target-signature strategy requires source band arrays")
    similarity, valid, similarity_contract = target_signature_similarity(
        source_bands,
        strategy.target_bands,
        weights=strategy.weights,
        source_mask=source_valid,
    )
    candidate = valid & (similarity >= np.float32(strategy.threshold))
    semantics = (
        "weighted spectral similarity to the declared target vector on [0,1]; "
        "not a calibrated class probability"
    )
    contract = {
        "strategy": strategy.model_dump(mode="json"),
        "similarity": similarity_contract,
        "score_semantics": semantics,
    }
    return similarity, valid, candidate, semantics, contract


def evaluate_specialist(
    specialist: SpecialistClassSpec,
    general_classes: np.ndarray,
    index_values: Mapping[str, IndexArray],
    *,
    source_bands: Mapping[str, np.ndarray] | None = None,
    source_valid: np.ndarray | None = None,
) -> SpecialistEvaluation:
    general = np.asarray(general_classes)
    if general.ndim != 2:
        raise ValueError("general classification must be a 2-D array")
    score, score_valid, before_parent, semantics, strategy_contract = (
        _evaluate_strategy(
            specialist.strategy,
            index_values,
            source_bands=source_bands,
            source_valid=source_valid,
        )
    )
    if score.shape != general.shape:
        raise ValueError("specialist score and general classification are misaligned")
    parent_codes = tuple(
        GENERAL_CLASS_CODES[class_id]
        for class_id in specialist.eligible_parent_general_classes
    )
    parent_eligible = np.isin(general, np.asarray(parent_codes))
    candidate = before_parent & parent_eligible
    support = int(candidate.sum())
    enabled = support >= specialist.minimum_support_pixels
    if not enabled:
        candidate = np.zeros(candidate.shape, dtype=bool)
    contract = {
        "schema_version": "fasterraster.specialist-class-rule/v1",
        "class_id": specialist.class_id,
        "label": specialist.label,
        "output_code": specialist.output_code,
        "intended_interpretation": specialist.intended_interpretation,
        "unsupported_interpretations": specialist.unsupported_interpretations,
        "eligible_parent_general_classes": list(
            specialist.eligible_parent_general_classes
        ),
        "eligible_parent_codes": list(parent_codes),
        "priority": specialist.priority,
        "minimum_support_pixels": specialist.minimum_support_pixels,
        "support_pixels": support,
        "enabled": enabled,
        "uncertainty_behavior": specialist.uncertainty_behavior,
        "calibration": specialist.calibration.model_dump(
            mode="json",
            exclude={"points"},
        ),
        "raw_calibration_coordinates_published": False,
        "strategy_contract": strategy_contract,
    }
    contract["contract_sha256"] = _canonical_hash(contract)
    return SpecialistEvaluation(
        class_id=specialist.class_id,
        output_code=specialist.output_code,
        priority=specialist.priority,
        score=score,
        score_valid=score_valid,
        candidate_before_parent=before_parent,
        candidate=candidate,
        parent_codes=parent_codes,
        support_pixels=support,
        enabled=enabled,
        score_semantics=semantics,
        contract=contract,
    )


def arbitrate_hybrid(
    general_classes: np.ndarray,
    evaluations: Sequence[SpecialistEvaluation],
    arbitration: HybridArbitrationSpec,
    *,
    valid_mask: np.ndarray | None = None,
    pixel_area_m2: float = 1.0,
) -> dict[str, Any]:
    general = np.asarray(general_classes, dtype=np.uint8)
    if general.ndim != 2:
        raise ValueError("general classification must be a 2-D array")
    valid = general > 0 if valid_mask is None else np.asarray(valid_mask, dtype=bool)
    if valid.shape != general.shape:
        raise ValueError("hybrid valid mask does not match classification")
    ordered = sorted(
        evaluations,
        key=lambda item: (-item.priority, item.output_code, item.class_id),
    )
    for item in ordered:
        if item.candidate.shape != general.shape:
            raise ValueError(f"specialist {item.class_id} is misaligned")
    final = general.copy()
    final[~valid] = 0
    decision = np.zeros(general.shape, dtype=np.uint8)
    decision[valid] = 1
    winner_code = np.zeros(general.shape, dtype=np.uint8)
    winner_priority = np.full(general.shape, -1, dtype=np.int32)
    winner_id = np.full(general.shape, "", dtype=object)
    unresolved = np.zeros(general.shape, dtype=bool)

    for item in ordered:
        candidate = np.asarray(item.candidate, dtype=bool) & valid
        higher = candidate & (item.priority > winner_priority)
        tied = (
            candidate
            & (item.priority == winner_priority)
            & (winner_code != item.output_code)
        )
        final[higher] = np.uint8(item.output_code)
        decision[higher] = 2
        winner_code[higher] = np.uint8(item.output_code)
        winner_priority[higher] = item.priority
        winner_id[higher] = item.class_id
        unresolved[higher] = False
        if np.any(tied):
            if arbitration.equal_priority_tie == "mark_unresolved":
                final[tied] = np.uint8(arbitration.unresolved_code)
                decision[tied] = 3
                winner_code[tied] = np.uint8(arbitration.unresolved_code)
                winner_id[tied] = "unresolved_equal_priority"
                unresolved[tied] = True
            else:
                replace = tied & (item.output_code < winner_code)
                final[replace] = np.uint8(item.output_code)
                decision[replace] = 2
                winner_code[replace] = np.uint8(item.output_code)
                winner_id[replace] = item.class_id
                unresolved[replace] = False

    overlap: list[dict[str, Any]] = []
    for left_index, left in enumerate(ordered):
        for right in ordered[left_index + 1 :]:
            count = int(
                (
                    np.asarray(left.candidate, dtype=bool)
                    & np.asarray(right.candidate, dtype=bool)
                    & valid
                ).sum()
            )
            overlap.append(
                {
                    "left_class_id": left.class_id,
                    "right_class_id": right.class_id,
                    "pixel_count": count,
                }
            )
    codes, counts = np.unique(final[valid], return_counts=True)
    inventory = [
        {
            "class_code": int(code),
            "pixel_count": int(count),
        }
        for code, count in zip(codes, counts, strict=True)
    ]
    winner_counts = {
        item.class_id: int((winner_id == item.class_id).sum())
        for item in ordered
    }
    evidence = {
        "schema_version": "fasterraster.hybrid-arbitration/v1",
        "engine_version": HYBRID_ENGINE_VERSION,
        "policy": arbitration.model_dump(mode="json"),
        "specialist_order": [
            {
                "class_id": item.class_id,
                "output_code": item.output_code,
                "priority": item.priority,
                "candidate_pixels": int(item.candidate.sum()),
                "winner_pixels": winner_counts[item.class_id],
                "enabled": item.enabled,
            }
            for item in ordered
        ],
        "overlap_matrix": overlap,
        "unresolved_pixels": int(unresolved.sum()),
        "valid_pixels": int(valid.sum()),
        "class_inventory": inventory,
        "area_accounting": {
            "status": "DEFERRED_TO_FINAL_CATEGORICAL_RASTER",
            "reason": (
                "physical area requires the finalized raster CRS and mask"
            ),
        },
        "winner_reason": (
            "highest declared priority; equal-priority behavior follows the "
            "explicit arbitration contract; raw unrelated index scores are "
            "never compared"
        ),
        "decision_state_labels": {
            str(code): label for code, label in DECISION_STATE_LABELS.items()
        },
    }
    evidence["arbitration_sha256"] = _canonical_hash(evidence)
    return {
        "final_classes": final,
        "decision_state": decision,
        "winner_code": winner_code,
        "unresolved": unresolved,
        "evidence": evidence,
    }


def _point_in_ring(longitude: float, latitude: float, ring: Sequence[Any]) -> bool:
    inside = False
    previous = ring[-1]
    for current in ring:
        x1, y1 = float(previous[0]), float(previous[1])
        x2, y2 = float(current[0]), float(current[1])
        intersects = (
            (y1 > latitude) != (y2 > latitude)
            and longitude
            < (x2 - x1) * (latitude - y1) / ((y2 - y1) or 1e-300) + x1
        )
        if intersects:
            inside = not inside
        previous = current
    return inside


def _point_in_polygon(
    longitude: float,
    latitude: float,
    coordinates: Sequence[Any],
) -> bool:
    if not coordinates or not _point_in_ring(longitude, latitude, coordinates[0]):
        return False
    return not any(
        _point_in_ring(longitude, latitude, hole)
        for hole in coordinates[1:]
    )


def point_in_aoi(
    longitude: float,
    latitude: float,
    aoi_epsg_4326: Mapping[str, Any],
) -> bool:
    geometry_type = aoi_epsg_4326.get("type")
    coordinates = aoi_epsg_4326.get("coordinates")
    if geometry_type == "Polygon" and isinstance(coordinates, list):
        return _point_in_polygon(longitude, latitude, coordinates)
    if geometry_type == "MultiPolygon" and isinstance(coordinates, list):
        return any(
            _point_in_polygon(longitude, latitude, polygon)
            for polygon in coordinates
        )
    raise ValueError("calibration AOI must be a Polygon or MultiPolygon")


def validate_calibration_points(
    points: Sequence[CalibrationPointSpec],
    *,
    aoi_epsg_4326: Mapping[str, Any],
    raster_crs: str,
    raster_transform: Any,
    width: int,
    height: int,
    valid_mask: np.ndarray,
    minimum_support_by_class: int = 1,
) -> dict[str, Any]:
    if not points:
        raise ValueError("calibration points cannot be empty")
    if minimum_support_by_class < 1:
        raise ValueError("minimum calibration support must be positive")
    valid = np.asarray(valid_mask, dtype=bool)
    if valid.shape != (height, width):
        raise ValueError("calibration validity mask does not match raster grid")
    coordinate_keys = [(point.longitude, point.latitude) for point in points]
    if len(coordinate_keys) != len(set(coordinate_keys)):
        raise ValueError("duplicate calibration coordinates are not allowed")
    extracted: list[tuple[str, int, int]] = []
    digest_rows: list[tuple[str, str, str]] = []
    for point in points:
        if not point_in_aoi(
            point.longitude,
            point.latitude,
            aoi_epsg_4326,
        ):
            raise ValueError(
                f"calibration point for {point.class_id!r} is outside the analysis AOI"
            )
        xs, ys = transform(
            "EPSG:4326",
            raster_crs,
            [point.longitude],
            [point.latitude],
        )
        row, column = rowcol(raster_transform, xs[0], ys[0])
        row, column = int(row), int(column)
        if row < 0 or row >= height or column < 0 or column >= width:
            raise ValueError("calibration point maps outside the raster grid")
        if not valid[row, column]:
            raise ValueError("calibration point maps to source-invalid or AOI-invalid data")
        extracted.append((point.class_id, row, column))
        digest_rows.append(
            (
                format(point.longitude, ".12g"),
                format(point.latitude, ".12g"),
                point.class_id,
            )
        )
    support: dict[str, int] = {}
    for class_id, _, _ in extracted:
        support[class_id] = support.get(class_id, 0) + 1
    insufficient = {
        class_id: count
        for class_id, count in support.items()
        if count < minimum_support_by_class
    }
    if insufficient:
        raise ValueError(
            "insufficient calibration support: "
            + ", ".join(
                f"{class_id}={count}" for class_id, count in sorted(insufficient.items())
            )
        )
    coordinate_digest = _canonical_hash(sorted(digest_rows))
    return {
        "schema_version": "fasterraster.calibration-point-extraction/v1",
        "point_count": len(points),
        "support_by_class": dict(sorted(support.items())),
        "coordinate_digest_sha256": coordinate_digest,
        "point_to_pixel": [
            {"class_id": class_id, "row": row, "column": column}
            for class_id, row, column in extracted
        ],
        "coordinates_epsg": 4326,
        "coordinate_order": "longitude_latitude",
        "raw_coordinates_published": False,
    }


@dataclass(frozen=True)
class CandidateDefinition:
    index_ids: tuple[str, ...]

    @property
    def candidate_id(self) -> str:
        return "+".join(self.index_ids)

    @property
    def complexity(self) -> int:
        return len(self.index_ids)


def generate_bounded_candidates(
    index_ids: Sequence[str],
    bounds: CandidateSearchBoundsSpec,
) -> tuple[CandidateDefinition, ...]:
    ordered = tuple(sorted(index_ids))
    if len(ordered) != len(set(ordered)):
        raise ValueError("candidate index IDs must be unique")
    if len(ordered) > 12:
        raise ValueError("automatic selection accepts at most 12 candidate indices")
    generated: list[CandidateDefinition] = [
        CandidateDefinition((index_id,)) for index_id in ordered
    ]
    pairs = list(itertools.combinations(ordered, 2))[: bounds.maximum_pairs]
    triples = list(itertools.combinations(ordered, 3))[: bounds.maximum_triples]
    generated.extend(CandidateDefinition(pair) for pair in pairs)
    generated.extend(CandidateDefinition(triple) for triple in triples)
    return tuple(generated[: bounds.maximum_candidate_models])


def _binary_metrics(target: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    truth = np.asarray(target, dtype=bool)
    guess = np.asarray(predicted, dtype=bool)
    tp = int((truth & guess).sum())
    tn = int((~truth & ~guess).sum())
    fp = int((~truth & guess).sum())
    fn = int((truth & ~guess).sum())
    positive_recall = tp / (tp + fn) if tp + fn else 0.0
    negative_recall = tn / (tn + fp) if tn + fp else 0.0

    def f1(true_positive: int, false_positive: int, false_negative: int) -> float:
        denominator = 2 * true_positive + false_positive + false_negative
        return 2 * true_positive / denominator if denominator else 0.0

    return {
        "macro_f1": (
            f1(tp, fp, fn) + f1(tn, fn, fp)
        )
        / 2.0,
        "balanced_accuracy": (positive_recall + negative_recall) / 2.0,
        "positive_recall": positive_recall,
        "negative_recall": negative_recall,
        "support_positive": int(truth.sum()),
        "support_negative": int((~truth).sum()),
    }


def _normalized_candidate_score(
    candidate: CandidateDefinition,
    values: Mapping[str, np.ndarray],
    fit_mask: np.ndarray,
) -> tuple[np.ndarray, list[dict[str, float]]]:
    normalized: list[np.ndarray] = []
    evidence: list[dict[str, float]] = []
    for index_id in candidate.index_ids:
        array = np.asarray(values[index_id], dtype=np.float64)
        sample = array[fit_mask]
        if not sample.size or not np.all(np.isfinite(sample)):
            raise ValueError(f"candidate {index_id} has invalid calibration values")
        minimum, maximum = float(sample.min()), float(sample.max())
        if maximum <= minimum:
            transformed = np.zeros(array.shape, dtype=np.float64)
        else:
            transformed = np.clip(
                (array - minimum) / (maximum - minimum),
                0.0,
                1.0,
            )
        normalized.append(transformed)
        evidence.append({"index_id": index_id, "minimum": minimum, "maximum": maximum})
    return np.mean(np.stack(normalized), axis=0), evidence


def _threshold_grid(score: np.ndarray, fit_mask: np.ndarray) -> np.ndarray:
    sample = np.asarray(score, dtype=np.float64)[fit_mask]
    if not sample.size:
        raise ValueError("threshold calibration set is empty")
    quantiles = np.linspace(0.02, 0.98, 49)
    return np.unique(np.quantile(sample, quantiles))


def _fit_threshold(
    score: np.ndarray,
    target: np.ndarray,
    fit_mask: np.ndarray,
    metric_name: str,
) -> dict[str, Any]:
    best: dict[str, Any] | None = None
    for direction in ("high", "low"):
        for threshold in _threshold_grid(score, fit_mask):
            predicted = (
                score >= threshold if direction == "high" else score <= threshold
            )
            metrics = _binary_metrics(target[fit_mask], predicted[fit_mask])
            candidate = {
                "direction": direction,
                "threshold": float(threshold),
                "metrics": metrics,
            }
            key = (
                metrics[metric_name],
                metrics["balanced_accuracy"],
                -abs(float(threshold) - 0.5),
                1 if direction == "high" else 0,
            )
            if best is None or key > best["_key"]:
                best = {**candidate, "_key": key}
    assert best is not None
    best.pop("_key")
    return best


def _ranking_precedes(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    tolerance: float,
) -> bool:
    difference = float(left["selection_metric"]) - float(
        right["selection_metric"]
    )
    if abs(difference) > tolerance:
        return difference > 0
    return (
        int(left["complexity"]),
        str(left["candidate_id"]),
    ) < (
        int(right["complexity"]),
        str(right["candidate_id"]),
    )


def _deterministic_ranking(
    items: Sequence[dict[str, Any]],
    *,
    tolerance: float,
) -> list[dict[str, Any]]:
    remaining = list(items)
    ordered: list[dict[str, Any]] = []
    while remaining:
        best_index = 0
        for index in range(1, len(remaining)):
            if _ranking_precedes(
                remaining[index],
                remaining[best_index],
                tolerance=tolerance,
            ):
                best_index = index
        ordered.append(remaining.pop(best_index))
    return ordered


def nested_spatial_select(
    index_values: Mapping[str, np.ndarray],
    target: np.ndarray,
    spatial_folds: np.ndarray,
    *,
    outer_holdout_fold: int,
    bounds: CandidateSearchBoundsSpec,
    automatic_authorized: bool,
    selection_mode: str = "automatic",
    preferred_candidate_id: str | None = None,
) -> dict[str, Any]:
    if selection_mode not in {"automatic", "recommendation"}:
        raise ValueError("nested selection mode must be automatic or recommendation")
    if selection_mode == "automatic" and not automatic_authorized:
        raise ValueError("automatic selection was not explicitly authorized")
    if selection_mode == "recommendation" and automatic_authorized:
        raise ValueError(
            "recommendation selection cannot claim automatic authorization"
        )
    target_array = np.asarray(target, dtype=bool)
    folds = np.asarray(spatial_folds)
    if target_array.ndim != 1 or folds.shape != target_array.shape:
        raise ValueError("selection target and spatial folds must be aligned vectors")
    if (
        not np.issubdtype(folds.dtype, np.integer)
        or np.any(folds < 0)
        or outer_holdout_fold < 0
    ):
        raise ValueError(
            "selection spatial folds and outer holdout fold must be "
            "nonnegative integers"
        )
    if target_array.size > bounds.maximum_calibration_samples:
        raise ValueError("calibration samples exceed the configured search bound")
    if set(index_values) != set(bounds.candidate_indices):
        raise ValueError("selection values must match configured candidate indices")
    for index_id, values in index_values.items():
        array = np.asarray(values)
        if array.shape != target_array.shape or not np.all(np.isfinite(array)):
            raise ValueError(f"candidate values are invalid for {index_id}")
    selection_mask = folds != outer_holdout_fold
    holdout_mask = folds == outer_holdout_fold
    if not selection_mask.any() or not holdout_mask.any():
        raise ValueError("outer spatial holdout lacks calibration or evaluation support")
    for label, mask in (
        ("selection", selection_mask),
        ("outer holdout", holdout_mask),
    ):
        if np.unique(target_array[mask]).size != 2:
            raise ValueError(f"{label} lacks positive and negative class support")
    inner_ids = np.mod(folds, bounds.inner_spatial_folds)
    available_inner = sorted(set(int(value) for value in inner_ids[selection_mask]))
    if len(available_inner) < 2:
        raise ValueError("insufficient spatial support for inner selection folds")
    candidates = generate_bounded_candidates(
        bounds.candidate_indices,
        bounds,
    )
    ranking: list[dict[str, Any]] = []
    for candidate in candidates:
        fold_metrics: list[float] = []
        fold_evidence: list[dict[str, Any]] = []
        for inner_fold in available_inner:
            validation = selection_mask & (inner_ids == inner_fold)
            calibration = selection_mask & (inner_ids != inner_fold)
            if (
                not validation.any()
                or not calibration.any()
                or np.unique(target_array[validation]).size != 2
                or np.unique(target_array[calibration]).size != 2
            ):
                continue
            score, normalization = _normalized_candidate_score(
                candidate,
                index_values,
                calibration,
            )
            fitted = _fit_threshold(
                score,
                target_array,
                calibration,
                bounds.ranking_metric,
            )
            predicted = (
                score >= fitted["threshold"]
                if fitted["direction"] == "high"
                else score <= fitted["threshold"]
            )
            metrics = _binary_metrics(
                target_array[validation],
                predicted[validation],
            )
            fold_metrics.append(metrics[bounds.ranking_metric])
            fold_evidence.append(
                {
                    "inner_fold": inner_fold,
                    "calibration_samples": int(calibration.sum()),
                    "validation_samples": int(validation.sum()),
                    "threshold": fitted["threshold"],
                    "direction": fitted["direction"],
                    "normalization": normalization,
                    "validation_metrics": metrics,
                }
            )
        if len(fold_metrics) < 2:
            continue
        ranking.append(
            {
                "candidate_id": candidate.candidate_id,
                "index_ids": list(candidate.index_ids),
                "complexity": candidate.complexity,
                "selection_metric": float(np.mean(fold_metrics)),
                "inner_folds": fold_evidence,
            }
        )
    if not ranking:
        raise ValueError("no candidate has sufficient nested spatial support")
    ranking = _deterministic_ranking(
        ranking,
        tolerance=bounds.tie_tolerance,
    )
    eligible = [
        item
        for item in ranking
        if item["selection_metric"] >= bounds.minimum_selection_metric
    ]
    if not eligible:
        return {
            "schema_version": "fasterraster.index-selection-receipt/v1",
            "status": "NO_CANDIDATE_MEETS_GUARD",
            "selection_mode": selection_mode,
            "automatic_authorized": automatic_authorized,
            "candidate_count": len(ranking),
            "candidate_ranking": ranking,
            "minimum_selection_metric": bounds.minimum_selection_metric,
        }
    selected = eligible[0]
    if preferred_candidate_id is not None:
        preferred = next(
            (
                item
                for item in eligible
                if item["candidate_id"] == preferred_candidate_id
            ),
            None,
        )
        if preferred is None:
            raise ValueError(
                "preferred recommendation is unavailable or does not meet "
                "the configured performance guard"
            )
        selected = preferred
    simpler = [
        item for item in eligible if item["complexity"] < selected["complexity"]
    ]
    if simpler and preferred_candidate_id is None:
        best_simpler = _deterministic_ranking(
            simpler,
            tolerance=bounds.tie_tolerance,
        )[0]
        improvement = (
            selected["selection_metric"]
            - best_simpler["selection_metric"]
        )
        if (
            improvement < bounds.minimum_complexity_improvement
            or improvement <= bounds.tie_tolerance
        ):
            selected = best_simpler
    selected_candidate = CandidateDefinition(tuple(selected["index_ids"]))
    final_score, normalization = _normalized_candidate_score(
        selected_candidate,
        index_values,
        selection_mask,
    )
    fitted = _fit_threshold(
        final_score,
        target_array,
        selection_mask,
        bounds.ranking_metric,
    )
    predicted = (
        final_score >= fitted["threshold"]
        if fitted["direction"] == "high"
        else final_score <= fitted["threshold"]
    )
    holdout_metrics = _binary_metrics(
        target_array[holdout_mask],
        predicted[holdout_mask],
    )
    result = {
        "schema_version": "fasterraster.index-selection-receipt/v1",
        "status": "SELECTED",
        "selection_mode": selection_mode,
        "automatic_authorized": automatic_authorized,
        "outer_holdout": {
            "fold": outer_holdout_fold,
            "sample_count": int(holdout_mask.sum()),
            "used_for_candidate_selection": False,
            "metrics": holdout_metrics,
        },
        "inner_selection": {
            "fold_assignment": (
                "existing spatial fold modulo configured inner_spatial_folds"
            ),
            "folds": available_inner,
            "ranking_metric": bounds.ranking_metric,
            "selection_sample_count": int(selection_mask.sum()),
        },
        "search_bounds": bounds.model_dump(mode="json"),
        "candidate_count": len(ranking),
        "formulas_or_combinations_tested": len(ranking),
        "candidate_ranking": ranking,
        "selected": {
            "candidate_id": selected["candidate_id"],
            "index_ids": selected["index_ids"],
            "complexity": selected["complexity"],
            "selection_metric": selected["selection_metric"],
            "direction": fitted["direction"],
            "threshold": fitted["threshold"],
            "normalization": normalization,
            "complexity_guard": (
                "a multi-index candidate must improve on an eligible simpler "
                "candidate by the configured minimum"
            ),
            "selection_source": (
                "interactive_user_acceptance"
                if preferred_candidate_id is not None
                else (
                    "deterministic_automatic_ranking"
                    if selection_mode == "automatic"
                    else "deterministic_recommendation_ranking"
                )
            ),
        },
        "tie_break_rule": (
            "selection metrics within tie_tolerance are equivalent; "
            "complexity ascending, then candidate ID lexical"
        ),
        "tie_tolerance": bounds.tie_tolerance,
        "weak_label_caution": (
            "reported metrics are agreement with calibration evidence, not "
            "independent ground-truth accuracy"
        ),
    }
    result["selection_sha256"] = _canonical_hash(result)
    return result


def recommendation_outcome(
    ranking: Sequence[Mapping[str, Any]],
    *,
    interactive: bool,
    accepted_candidate_id: str | None = None,
) -> dict[str, Any]:
    ordered = sorted(
        (dict(item) for item in ranking),
        key=lambda item: (
            -float(item.get("selection_metric", -math.inf)),
            int(item.get("complexity", 999)),
            str(item.get("candidate_id", "")),
        ),
    )
    if not interactive:
        return {
            "status": "AWAITING_INDEX_SELECTION",
            "finalized": False,
            "candidate_ranking": ordered,
            "prompted": False,
        }
    if accepted_candidate_id is None:
        return {
            "status": "INDEX_SELECTION_CANCELLED",
            "finalized": False,
            "candidate_ranking": ordered,
            "prompted": True,
        }
    selected = next(
        (
            item
            for item in ordered
            if item.get("candidate_id") == accepted_candidate_id
        ),
        None,
    )
    if selected is None:
        raise ValueError("accepted recommendation is not in the candidate ranking")
    return {
        "status": "SELECTED",
        "finalized": True,
        "candidate_ranking": ordered,
        "selected": selected,
        "prompted": True,
        "acceptance_mode": "interactive_user_acceptance",
    }
