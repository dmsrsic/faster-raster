from __future__ import annotations

import hashlib
import json
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np
import rasterio
from rasterio.shutil import copy as raster_copy
from rasterio.windows import Window
from rasterio.warp import transform

from faster_raster.ag_classification import _priorities, spatial_fold
from faster_raster.ag_recipes import (
    AgriculturalRecipeV4,
    IndexConditionSpec,
    MultiIndexBooleanStrategy,
    MultiIndexWeightedStrategy,
    SingleIndexThresholdStrategy,
    SpecialistClassSpec,
    TargetSignatureStrategy,
    WeightedIndexInputSpec,
)
from faster_raster.aoi_geometry import raster_aoi_mask
from faster_raster.hybrid_classification import (
    HYBRID_ENGINE_VERSION,
    IndexArray,
    SpecialistEvaluation,
    arbitrate_hybrid,
    evaluate_specialist,
    nested_spatial_select,
    point_in_aoi,
)
from faster_raster.spectral_indices import (
    BUILTIN_INDEX_REGISTRY,
    INDEX_NODATA,
    IndexCapabilityError,
    calculate_index_cog,
    source_capabilities_from_raster,
    validate_index_compatibility,
)


SPECIALIST_SCORE_NODATA = np.float32(-9999.0)


class HybridExecutionError(RuntimeError):
    """Raised when the hybrid analytical contract cannot be completed."""


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


def _iter_windows(width: int, height: int, size: int):
    for row_off in range(0, height, size):
        for col_off in range(0, width, size):
            yield Window(
                col_off=col_off,
                row_off=row_off,
                width=min(size, width - col_off),
                height=min(size, height - row_off),
            )


def _profile(
    source: rasterio.io.DatasetReader,
    *,
    dtype: str,
    nodata: int | float,
) -> dict[str, Any]:
    return {
        "driver": "GTiff",
        "width": source.width,
        "height": source.height,
        "count": 1,
        "dtype": dtype,
        "crs": source.crs,
        "transform": source.transform,
        "nodata": nodata,
        "tiled": True,
        "blockxsize": 512,
        "blockysize": 512,
        "compress": "DEFLATE",
        "bigtiff": "IF_SAFER",
    }


def _finalize_cog(
    working: Path,
    destination: Path,
    *,
    categorical: bool,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    raster_copy(
        working,
        destination,
        driver="COG",
        blocksize=512,
        compress="DEFLATE",
        bigtiff="IF_SAFER",
        overview_resampling="nearest" if categorical else "average",
    )
    working.unlink(missing_ok=True)
    return destination


def _validate_cog(
    path: Path,
    reference: rasterio.io.DatasetReader,
    *,
    dtype: str,
    receipt_path: str,
) -> dict[str, Any]:
    with rasterio.open(path) as source:
        if (
            source.count != 1
            or source.dtypes[0] != dtype
            or source.crs != reference.crs
            or source.transform != reference.transform
            or source.width != reference.width
            or source.height != reference.height
            or source.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") != "COG"
        ):
            raise HybridExecutionError(f"hybrid COG validation failed: {path}")
    return {
        "path": receipt_path,
        "dtype": dtype,
        "sha256": _sha256(path),
        "cog_validation": "PASS",
    }


def _strategy_index_ids(specialist: SpecialistClassSpec) -> tuple[str, ...]:
    strategy = specialist.strategy
    if isinstance(strategy, SingleIndexThresholdStrategy):
        return (strategy.condition.index_id,)
    if isinstance(strategy, MultiIndexBooleanStrategy):
        return tuple(condition.index_id for condition in strategy.conditions)
    if isinstance(strategy, MultiIndexWeightedStrategy):
        return tuple(item.index_id for item in strategy.inputs)
    return ()


def _preflight_document(
    naip_path: Path,
    recipe: AgriculturalRecipeV4,
) -> tuple[Any, dict[str, Any]]:
    capabilities = source_capabilities_from_raster(
        naip_path,
        source_asset="naip_multispectral",
        source_id="usgs_naip_imageserver",
    )
    reports: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for request in recipe.classification.indices:
        try:
            if request.expression is None:
                report = validate_index_compatibility(
                    request.index_id,
                    capabilities,
                )
            else:
                from faster_raster.spectral_indices import parse_index_expression

                parsed = parse_index_expression(request.expression)
                report = validate_index_compatibility(
                    request.index_id,
                    capabilities,
                    required_bands=parsed.required_bands,
                )
            reports.append(report)
        except IndexCapabilityError as exc:
            failures.append(exc.as_dict())
    requested_ids = {
        request.index_id for request in recipe.classification.indices
    }
    if recipe.classification.specialists.selection_mode in {
        "recommendation",
        "automatic",
    }:
        for index_id in (
            recipe.classification.specialists.search.candidate_indices
        ):
            if index_id in requested_ids:
                continue
            try:
                reports.append(
                    validate_index_compatibility(index_id, capabilities)
                )
            except IndexCapabilityError as exc:
                failures.append(exc.as_dict())
    for specialist in recipe.classification.specialists.classes:
        if isinstance(specialist.strategy, TargetSignatureStrategy):
            required = tuple(specialist.strategy.target_bands)
            try:
                reports.append(
                    validate_index_compatibility(
                        "target_signature_similarity",
                        capabilities,
                        required_bands=required,
                    )
                )
            except IndexCapabilityError as exc:
                failures.append(exc.as_dict())
    document = {
        "schema_version": "fasterraster.index-capability-report/v1",
        "source": capabilities.as_dict(),
        "compatibility": reports,
        "failures": failures,
        "status": "PASS" if not failures else "INCOMPATIBLE",
    }
    if failures:
        raise HybridExecutionError(
            "spectral-index preflight failed: "
            + "; ".join(
                f"{item['requested_index']} missing "
                f"{','.join(item['missing_bands'])}"
                for item in failures
            )
        )
    return capabilities, document


def _load_index_window(
    sources: Mapping[str, rasterio.io.DatasetReader],
    index_ids: tuple[str, ...],
    window: Window,
) -> dict[str, IndexArray]:
    result: dict[str, IndexArray] = {}
    for index_id in index_ids:
        source = sources[index_id]
        values = source.read(1, window=window).astype(np.float32)
        valid = (
            source.read_masks(1, window=window) > 0
        ) & np.isfinite(values) & (values != INDEX_NODATA)
        result[index_id] = IndexArray(values=values, valid=valid)
    return result


def _load_target_bands(
    source: rasterio.io.DatasetReader,
    capabilities: Any,
    strategy: TargetSignatureStrategy,
    window: Window,
    analysis_aoi_epsg_4326: Mapping[str, Any] | None,
) -> tuple[dict[str, np.ndarray], np.ndarray]:
    names = tuple(strategy.target_bands)
    positions = [capabilities.band_positions[name] for name in names]
    raw = source.read(positions, window=window)
    masks = source.read_masks(positions, window=window) > 0
    values: dict[str, np.ndarray] = {}
    for array, name, position in zip(raw, names, positions, strict=True):
        evidence = capabilities.bands[position - 1]
        values[name] = (
            array.astype(np.float32) * np.float32(evidence.scale)
            + np.float32(evidence.offset)
        )
    valid = np.all(masks, axis=0)
    if analysis_aoi_epsg_4326 is not None:
        valid &= raster_aoi_mask(
            source,
            analysis_aoi_epsg_4326,
            window=window,
        )
    return values, valid


def _write_specialist_products(
    specialist: SpecialistClassSpec,
    *,
    general_path: Path,
    naip_path: Path,
    index_paths: Mapping[str, Path],
    capabilities: Any,
    destination_directory: Path,
    analysis_aoi_epsg_4326: Mapping[str, Any] | None,
    window_size: int,
) -> dict[str, Any]:
    score_path = (
        destination_directory / f"{specialist.class_id}_score.cog.tif"
    )
    candidate_path = (
        destination_directory / f"{specialist.class_id}_candidate.cog.tif"
    )
    score_working = score_path.parent / f".{score_path.name}.working.tif"
    candidate_working = (
        candidate_path.parent / f".{candidate_path.name}.working.tif"
    )
    destination_directory.mkdir(parents=True, exist_ok=True)
    referenced = _strategy_index_ids(specialist)
    before_parent_count = 0
    candidate_count = 0
    valid_score_count = 0
    representative_contract: dict[str, Any] | None = None
    with ExitStack() as stack:
        general_source = stack.enter_context(rasterio.open(general_path))
        naip_source = stack.enter_context(rasterio.open(naip_path))
        index_sources = {
            index_id: stack.enter_context(rasterio.open(index_paths[index_id]))
            for index_id in referenced
        }
        for source in [naip_source, *index_sources.values()]:
            if (
                source.crs != general_source.crs
                or source.transform != general_source.transform
                or source.width != general_source.width
                or source.height != general_source.height
            ):
                raise HybridExecutionError(
                    f"specialist source grid is misaligned: {specialist.class_id}"
                )
        score_sink = stack.enter_context(
            rasterio.open(
                score_working,
                "w",
                **_profile(
                    general_source,
                    dtype="float32",
                    nodata=float(SPECIALIST_SCORE_NODATA),
                ),
            )
        )
        candidate_sink = stack.enter_context(
            rasterio.open(
                candidate_working,
                "w",
                **_profile(general_source, dtype="uint8", nodata=0),
            )
        )
        score_sink.update_tags(
            FASTERRASTER_SPECIALIST_CLASS_ID=specialist.class_id,
            FASTERRASTER_SCORE_SEMANTICS="nonprobability",
        )
        candidate_sink.update_tags(
            FASTERRASTER_SPECIALIST_CLASS_ID=specialist.class_id,
            FASTERRASTER_CANDIDATE_VALUES="0=false,1=true",
        )
        window_spec = specialist.model_copy(
            update={"minimum_support_pixels": 1}
        )
        for window in _iter_windows(
            general_source.width,
            general_source.height,
            window_size,
        ):
            general = general_source.read(1, window=window)
            indices = _load_index_window(index_sources, referenced, window)
            if isinstance(specialist.strategy, TargetSignatureStrategy):
                source_bands, source_valid = _load_target_bands(
                    naip_source,
                    capabilities,
                    specialist.strategy,
                    window,
                    analysis_aoi_epsg_4326,
                )
            else:
                source_bands, source_valid = None, None
            evaluation = evaluate_specialist(
                window_spec,
                general,
                indices,
                source_bands=source_bands,
                source_valid=source_valid,
            )
            representative_contract = evaluation.contract
            before_parent_count += int(
                evaluation.candidate_before_parent.sum()
            )
            candidate_count += int(evaluation.candidate.sum())
            valid_score_count += int(evaluation.score_valid.sum())
            score = np.full(
                evaluation.score.shape,
                SPECIALIST_SCORE_NODATA,
                dtype=np.float32,
            )
            score[evaluation.score_valid] = evaluation.score[
                evaluation.score_valid
            ]
            score_sink.write(score, 1, window=window)
            candidate_sink.write(
                evaluation.candidate.astype(np.uint8),
                1,
                window=window,
            )
    _finalize_cog(score_working, score_path, categorical=False)
    _finalize_cog(candidate_working, candidate_path, categorical=True)
    enabled = candidate_count >= specialist.minimum_support_pixels
    if not enabled:
        with rasterio.open(candidate_path) as source:
            zero_working = (
                candidate_path.parent
                / f".{candidate_path.name}.disabled.working.tif"
            )
            with rasterio.open(
                zero_working,
                "w",
                **_profile(source, dtype="uint8", nodata=0),
            ) as sink:
                for window in _iter_windows(
                    source.width,
                    source.height,
                    window_size,
                ):
                    sink.write(
                        np.zeros(
                            (int(window.height), int(window.width)),
                            dtype=np.uint8,
                        ),
                        1,
                        window=window,
                    )
        replacement = candidate_path.parent / (
            f".{candidate_path.name}.disabled.cog.tif"
        )
        _finalize_cog(zero_working, replacement, categorical=True)
        replacement.replace(candidate_path)
    with rasterio.open(general_path) as reference:
        score_validation = _validate_cog(
            score_path,
            reference,
            dtype="float32",
            receipt_path=f"data/specialists/{score_path.name}",
        )
        candidate_validation = _validate_cog(
            candidate_path,
            reference,
            dtype="uint8",
            receipt_path=f"data/specialists/{candidate_path.name}",
        )
    rule = {
        **(representative_contract or {}),
        "minimum_support_pixels": specialist.minimum_support_pixels,
        "candidate_before_parent_pixels": before_parent_count,
        "candidate_pixels": candidate_count if enabled else 0,
        "raw_candidate_pixels": candidate_count,
        "valid_score_pixels": valid_score_count,
        "enabled": enabled,
        "score_output": score_validation,
        "candidate_output": candidate_validation,
    }
    return {
        "class_id": specialist.class_id,
        "output_code": specialist.output_code,
        "priority": specialist.priority,
        "enabled": enabled,
        "candidate_pixels": candidate_count if enabled else 0,
        "score_path": score_path,
        "candidate_path": candidate_path,
        "rule": rule,
    }


def _arbitrate_rasters(
    *,
    general_path: Path,
    specialist_products: list[dict[str, Any]],
    recipe: AgriculturalRecipeV4,
    data_directory: Path,
    window_size: int,
) -> dict[str, Any]:
    final_path = data_directory / "final_hybrid_classification.cog.tif"
    decision_path = data_directory / "hybrid_decision_state.cog.tif"
    final_working = final_path.parent / f".{final_path.name}.working.tif"
    decision_working = decision_path.parent / f".{decision_path.name}.working.tif"
    overlap_counts: dict[tuple[str, str], int] = {}
    winner_counts = {
        item.class_id: 0
        for item in recipe.classification.specialists.classes
    }
    inventory_counts: dict[int, int] = {}
    unresolved = 0
    valid_pixels = 0
    with ExitStack() as stack:
        general_source = stack.enter_context(rasterio.open(general_path))
        candidate_sources = {
            product["class_id"]: stack.enter_context(
                rasterio.open(product["candidate_path"])
            )
            for product in specialist_products
        }
        final_sink = stack.enter_context(
            rasterio.open(
                final_working,
                "w",
                **_profile(general_source, dtype="uint8", nodata=0),
            )
        )
        decision_sink = stack.enter_context(
            rasterio.open(
                decision_working,
                "w",
                **_profile(general_source, dtype="uint8", nodata=0),
            )
        )
        final_sink.update_tags(
            FASTERRASTER_CLASSIFICATION_TYPE="hybrid_general_plus_specialists"
        )
        decision_sink.update_tags(
            FASTERRASTER_DECISION_STATES=(
                "0=invalid,1=general,2=specialist,3=unresolved"
            )
        )
        product_by_id = {
            product["class_id"]: product for product in specialist_products
        }
        for window in _iter_windows(
            general_source.width,
            general_source.height,
            window_size,
        ):
            general = general_source.read(1, window=window)
            evaluations: list[SpecialistEvaluation] = []
            for specialist in recipe.classification.specialists.classes:
                product = product_by_id[specialist.class_id]
                candidate = (
                    candidate_sources[specialist.class_id].read(
                        1,
                        window=window,
                    )
                    > 0
                )
                evaluations.append(
                    SpecialistEvaluation(
                        class_id=specialist.class_id,
                        output_code=specialist.output_code,
                        priority=specialist.priority,
                        score=np.zeros(candidate.shape, dtype=np.float32),
                        score_valid=np.ones(candidate.shape, dtype=bool),
                        candidate_before_parent=candidate,
                        candidate=candidate,
                        parent_codes=(),
                        support_pixels=product["candidate_pixels"],
                        enabled=product["enabled"],
                        score_semantics="recorded in specialist score COG",
                        contract=product["rule"],
                    )
                )
            result = arbitrate_hybrid(
                general,
                evaluations,
                recipe.classification.arbitration,
                pixel_area_m2=abs(
                    general_source.transform.a * general_source.transform.e
                    - general_source.transform.b * general_source.transform.d
                ),
            )
            final_sink.write(result["final_classes"], 1, window=window)
            decision_sink.write(result["decision_state"], 1, window=window)
            evidence = result["evidence"]
            valid_pixels += int(evidence["valid_pixels"])
            unresolved += int(evidence["unresolved_pixels"])
            for item in evidence["specialist_order"]:
                winner_counts[item["class_id"]] += int(item["winner_pixels"])
            for item in evidence["overlap_matrix"]:
                key = (item["left_class_id"], item["right_class_id"])
                overlap_counts[key] = overlap_counts.get(key, 0) + int(
                    item["pixel_count"]
                )
            values, counts = np.unique(
                result["final_classes"][result["final_classes"] > 0],
                return_counts=True,
            )
            for value, count in zip(values, counts, strict=True):
                inventory_counts[int(value)] = (
                    inventory_counts.get(int(value), 0) + int(count)
                )
    _finalize_cog(final_working, final_path, categorical=True)
    _finalize_cog(decision_working, decision_path, categorical=True)
    with rasterio.open(general_path) as reference:
        pixel_area_m2 = abs(
            reference.transform.a * reference.transform.e
            - reference.transform.b * reference.transform.d
        )
        final_validation = _validate_cog(
            final_path,
            reference,
            dtype="uint8",
            receipt_path=f"data/{final_path.name}",
        )
        decision_validation = _validate_cog(
            decision_path,
            reference,
            dtype="uint8",
            receipt_path=f"data/{decision_path.name}",
        )
    overlap = [
        {
            "left_class_id": key[0],
            "right_class_id": key[1],
            "pixel_count": count,
            "area_square_meters": count * pixel_area_m2,
        }
        for key, count in sorted(overlap_counts.items())
    ]
    inventory = [
        {
            "class_code": code,
            "pixel_count": count,
            "area_square_meters": count * pixel_area_m2,
            "hectares": count * pixel_area_m2 / 10_000.0,
        }
        for code, count in sorted(inventory_counts.items())
    ]
    evidence = {
        "schema_version": "fasterraster.hybrid-classification-receipt/v1",
        "engine_version": HYBRID_ENGINE_VERSION,
        "arbitration": recipe.classification.arbitration.model_dump(
            mode="json"
        ),
        "winner_pixels_by_specialist": winner_counts,
        "overlap_matrix": overlap,
        "unresolved_pixels": unresolved,
        "valid_pixels": valid_pixels,
        "class_inventory": inventory,
        "general_classification_preserved": True,
        "raw_unrelated_scores_compared": False,
        "winner_reason": (
            "highest declared priority with explicit equal-priority tie policy"
        ),
        "outputs": {
            "final_hybrid_classification": final_validation,
            "hybrid_decision_state": decision_validation,
        },
    }
    return {
        "final_path": final_path,
        "decision_path": decision_path,
        "evidence": evidence,
    }


def _merge_bounded_samples(
    retained: dict[str, np.ndarray] | None,
    *,
    rows: np.ndarray,
    columns: np.ndarray,
    values: Mapping[str, np.ndarray],
    target: bool,
    limit: int,
    seed: int,
) -> dict[str, np.ndarray]:
    priorities = _priorities(
        rows.astype(np.int64, copy=False),
        columns.astype(np.int64, copy=False),
        seed,
        1 if target else 0,
    )
    incoming = {
        "rows": rows.astype(np.int64, copy=False),
        "columns": columns.astype(np.int64, copy=False),
        "priorities": priorities,
        **{
            f"index:{index_id}": np.asarray(array, dtype=np.float32)
            for index_id, array in values.items()
        },
    }
    if retained is None:
        merged = incoming
    else:
        merged = {
            key: np.concatenate((retained[key], incoming[key]))
            for key in incoming
        }
    count = len(merged["rows"])
    if count > limit:
        selected = np.argpartition(
            merged["priorities"],
            limit - 1,
        )[:limit]
        selected = selected[
            np.lexsort(
                (
                    merged["columns"][selected],
                    merged["rows"][selected],
                    merged["priorities"][selected],
                )
            )
        ]
        merged = {key: array[selected] for key, array in merged.items()}
    return merged


def _finish_calibration_samples(
    positive: dict[str, np.ndarray] | None,
    negative: dict[str, np.ndarray] | None,
    *,
    specialist: SpecialistClassSpec,
    index_ids: tuple[str, ...],
    recipe: AgriculturalRecipeV4,
    evidence: dict[str, Any],
) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray, dict[str, Any]]:
    if positive is None or negative is None:
        raise HybridExecutionError(
            f"specialist {specialist.class_id} calibration lacks positive "
            "or negative support"
        )
    positive_count = len(positive["rows"])
    negative_count = len(negative["rows"])
    calibration = specialist.calibration
    if (
        positive_count < calibration.minimum_positive_support
        or negative_count < calibration.minimum_negative_support
    ):
        raise HybridExecutionError(
            f"specialist {specialist.class_id} calibration support "
            f"positive={positive_count}, negative={negative_count} is below "
            "the configured minimum"
        )
    rows = np.concatenate((positive["rows"], negative["rows"]))
    columns = np.concatenate(
        (positive["columns"], negative["columns"])
    )
    target = np.concatenate(
        (
            np.ones(positive_count, dtype=bool),
            np.zeros(negative_count, dtype=bool),
        )
    )
    index_values = {
        index_id: np.concatenate(
            (
                positive[f"index:{index_id}"],
                negative[f"index:{index_id}"],
            )
        )
        for index_id in index_ids
    }
    general = recipe.classification.general
    folds = np.fromiter(
        (
            spatial_fold(
                int(row) // general.inference_window_size,
                int(column) // general.inference_window_size,
                general.random_seed,
                general.spatial_holdout_folds,
            )
            for row, column in zip(rows, columns, strict=True)
        ),
        dtype=np.int16,
        count=len(rows),
    )
    sample_digest = _canonical_hash(
        [
            [
                int(row),
                int(column),
                bool(label),
                int(fold),
            ]
            for row, column, label, fold in sorted(
                zip(rows, columns, target, folds, strict=True),
                key=lambda item: (
                    int(item[0]),
                    int(item[1]),
                    bool(item[2]),
                ),
            )
        ]
    )
    evidence.update(
        {
            "sample_count": len(rows),
            "positive_support": positive_count,
            "negative_support": negative_count,
            "sample_pixel_digest_sha256": sample_digest,
            "spatial_fold_method": (
                "sha256(block_row:block_column:random_seed) modulo folds"
            ),
            "outer_holdout_fold": general.spatial_holdout_fold,
            "raw_coordinates_published_in_summary": False,
        }
    )
    return index_values, target, folds, evidence


def _cdl_calibration_samples(
    specialist: SpecialistClassSpec,
    *,
    index_paths: Mapping[str, Path],
    cdl_superclasses_path: Path,
    recipe: AgriculturalRecipeV4,
) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray, dict[str, Any]]:
    index_ids = tuple(
        recipe.classification.specialists.search.candidate_indices
    )
    calibration = specialist.calibration
    positive_codes = {
        recipe.classification.general.class_codes[
            recipe.classification.general.class_ids.index(class_id)
        ]
        for class_id in calibration.positive_general_classes
    }
    general_codes = set(recipe.classification.general.class_codes)
    negative_codes = general_codes - positive_codes
    if not positive_codes or not negative_codes:
        raise HybridExecutionError(
            f"specialist {specialist.class_id} CDL calibration requires "
            "nonempty explicit positive and negative general classes"
        )
    per_label_limit = max(
        1,
        recipe.classification.specialists.search.maximum_calibration_samples
        // 2,
    )
    positive: dict[str, np.ndarray] | None = None
    negative: dict[str, np.ndarray] | None = None
    with ExitStack() as stack:
        labels = stack.enter_context(rasterio.open(cdl_superclasses_path))
        sources = {
            index_id: stack.enter_context(rasterio.open(index_paths[index_id]))
            for index_id in index_ids
        }
        for source in sources.values():
            if (
                source.crs != labels.crs
                or source.transform != labels.transform
                or source.width != labels.width
                or source.height != labels.height
            ):
                raise HybridExecutionError(
                    "automatic calibration grid does not align with "
                    "persisted indices"
                )
        for window in _iter_windows(
            labels.width,
            labels.height,
            recipe.classification.general.inference_window_size,
        ):
            label_values = labels.read(1, window=window)
            index_values = {
                index_id: source.read(1, window=window).astype(np.float32)
                for index_id, source in sources.items()
            }
            valid = labels.read_masks(1, window=window) > 0
            for index_id, source in sources.items():
                values = index_values[index_id]
                valid &= (
                    (source.read_masks(1, window=window) > 0)
                    & np.isfinite(values)
                    & (values != INDEX_NODATA)
                )
            for is_positive, codes in (
                (True, positive_codes),
                (False, negative_codes),
            ):
                local_rows, local_columns = np.nonzero(
                    valid & np.isin(label_values, tuple(sorted(codes)))
                )
                if not len(local_rows):
                    continue
                rows = local_rows.astype(np.int64) + int(window.row_off)
                columns = (
                    local_columns.astype(np.int64) + int(window.col_off)
                )
                selected_values = {
                    index_id: values[local_rows, local_columns]
                    for index_id, values in index_values.items()
                }
                merged = _merge_bounded_samples(
                    positive if is_positive else negative,
                    rows=rows,
                    columns=columns,
                    values=selected_values,
                    target=is_positive,
                    limit=per_label_limit,
                    seed=recipe.classification.general.random_seed,
                )
                if is_positive:
                    positive = merged
                else:
                    negative = merged
    return _finish_calibration_samples(
        positive,
        negative,
        specialist=specialist,
        index_ids=index_ids,
        recipe=recipe,
        evidence={
            "source": "cdl_weak_labels",
            "positive_general_classes": list(
                calibration.positive_general_classes
            ),
            "positive_codes": sorted(positive_codes),
            "negative_codes": sorted(negative_codes),
            "weak_label_caution": (
                "selection metrics measure agreement with CDL-derived weak "
                "labels, not independent ground-truth accuracy"
            ),
        },
    )


def _point_calibration_samples(
    specialist: SpecialistClassSpec,
    *,
    index_paths: Mapping[str, Path],
    recipe: AgriculturalRecipeV4,
    analysis_aoi_epsg_4326: Mapping[str, Any] | None,
) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray, dict[str, Any]]:
    index_ids = tuple(
        recipe.classification.specialists.search.candidate_indices
    )
    positive_rows: list[int] = []
    positive_columns: list[int] = []
    negative_rows: list[int] = []
    negative_columns: list[int] = []
    positive_values = {index_id: [] for index_id in index_ids}
    negative_values = {index_id: [] for index_id in index_ids}
    private_rows: list[dict[str, Any]] = []
    digest_rows: list[tuple[str, str, str]] = []
    seen_coordinates: set[tuple[float, float]] = set()
    seen_pixels: dict[tuple[int, int], str] = {}
    with ExitStack() as stack:
        sources = {
            index_id: stack.enter_context(rasterio.open(index_paths[index_id]))
            for index_id in index_ids
        }
        reference = next(iter(sources.values()))
        for source in sources.values():
            if (
                source.crs != reference.crs
                or source.transform != reference.transform
                or source.width != reference.width
                or source.height != reference.height
            ):
                raise HybridExecutionError(
                    "point calibration index grids are not aligned"
                )
        for point in specialist.calibration.points:
            coordinate = (point.longitude, point.latitude)
            if coordinate in seen_coordinates:
                raise HybridExecutionError(
                    "duplicate or contradictory calibration coordinates are "
                    "not allowed"
                )
            seen_coordinates.add(coordinate)
            if (
                analysis_aoi_epsg_4326 is not None
                and not point_in_aoi(
                    point.longitude,
                    point.latitude,
                    analysis_aoi_epsg_4326,
                )
            ):
                raise HybridExecutionError(
                    f"calibration point for {point.class_id} is outside "
                    "the analysis AOI"
                )
            xs, ys = transform(
                "EPSG:4326",
                str(reference.crs),
                [point.longitude],
                [point.latitude],
            )
            row, column = reference.index(xs[0], ys[0])
            if (
                row < 0
                or row >= reference.height
                or column < 0
                or column >= reference.width
            ):
                raise HybridExecutionError(
                    "calibration point maps outside the raster grid"
                )
            pixel = (int(row), int(column))
            previous_class = seen_pixels.get(pixel)
            if previous_class is not None:
                qualifier = (
                    "contradictory"
                    if previous_class != point.class_id
                    else "duplicate"
                )
                raise HybridExecutionError(
                    f"{qualifier} calibration points map to the same raster "
                    "pixel"
                )
            seen_pixels[pixel] = point.class_id
            extracted: dict[str, float] = {}
            for index_id, source in sources.items():
                window = Window(column, row, 1, 1)
                value = float(source.read(1, window=window)[0, 0])
                valid = int(source.read_masks(1, window=window)[0, 0]) > 0
                if (
                    not valid
                    or not np.isfinite(value)
                    or value == float(INDEX_NODATA)
                ):
                    raise HybridExecutionError(
                        "calibration point maps to source-invalid or "
                        "AOI-invalid data"
                    )
                extracted[index_id] = value
            is_positive = point.class_id == specialist.class_id
            rows = positive_rows if is_positive else negative_rows
            columns = (
                positive_columns if is_positive else negative_columns
            )
            values = positive_values if is_positive else negative_values
            rows.append(int(row))
            columns.append(int(column))
            for index_id, value in extracted.items():
                values[index_id].append(value)
            private_rows.append(
                {
                    "longitude": point.longitude,
                    "latitude": point.latitude,
                    "class_id": point.class_id,
                    "row": int(row),
                    "column": int(column),
                }
            )
            digest_rows.append(
                (
                    format(point.longitude, ".12g"),
                    format(point.latitude, ".12g"),
                    point.class_id,
                )
            )

    def point_group(
        rows: list[int],
        columns: list[int],
        values: Mapping[str, list[float]],
    ) -> dict[str, np.ndarray] | None:
        if not rows:
            return None
        return {
            "rows": np.asarray(rows, dtype=np.int64),
            "columns": np.asarray(columns, dtype=np.int64),
            "priorities": np.zeros(len(rows), dtype=np.uint64),
            **{
                f"index:{index_id}": np.asarray(array, dtype=np.float32)
                for index_id, array in values.items()
            },
        }

    result = _finish_calibration_samples(
        point_group(positive_rows, positive_columns, positive_values),
        point_group(negative_rows, negative_columns, negative_values),
        specialist=specialist,
        index_ids=index_ids,
        recipe=recipe,
        evidence={
            "source": "user_points",
            "coordinate_order": "longitude_latitude",
            "coordinates_epsg": 4326,
            "coordinate_digest_sha256": _canonical_hash(
                sorted(digest_rows)
            ),
            "raw_coordinates_published_in_summary": False,
        },
    )
    result[3]["local_private_point_extraction"] = private_rows
    return result


def _calibration_samples(
    specialist: SpecialistClassSpec,
    *,
    index_paths: Mapping[str, Path],
    general_result: Mapping[str, Any],
    recipe: AgriculturalRecipeV4,
    analysis_aoi_epsg_4326: Mapping[str, Any] | None,
) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray, dict[str, Any]]:
    if specialist.calibration.source == "cdl_weak_labels":
        cdl_path = general_result.get("paths", {}).get("cdl_superclasses")
        if cdl_path is None:
            raise HybridExecutionError(
                "CDL calibration requires the preserved superclass raster"
            )
        return _cdl_calibration_samples(
            specialist,
            index_paths=index_paths,
            cdl_superclasses_path=Path(cdl_path),
            recipe=recipe,
        )
    if specialist.calibration.source == "user_points":
        return _point_calibration_samples(
            specialist,
            index_paths=index_paths,
            recipe=recipe,
            analysis_aoi_epsg_4326=analysis_aoi_epsg_4326,
        )
    raise HybridExecutionError(
        "learned selection requires CDL weak labels or user points"
    )


def _estimate_target_signature(
    specialist: SpecialistClassSpec,
    *,
    naip_path: Path,
    capabilities: Any,
    analysis_aoi_epsg_4326: Mapping[str, Any] | None,
) -> tuple[SpecialistClassSpec, dict[str, Any]]:
    strategy = specialist.strategy
    if (
        not isinstance(strategy, TargetSignatureStrategy)
        or strategy.target_source != "positive_calibration_points"
    ):
        return specialist, {}
    positives = [
        point
        for point in specialist.calibration.points
        if point.class_id == specialist.class_id
    ]
    band_names = tuple(strategy.target_bands)
    samples = {band_name: [] for band_name in band_names}
    point_pixels: list[tuple[int, int]] = []
    seen_pixels: set[tuple[int, int]] = set()
    with rasterio.open(naip_path) as source:
        positions = [
            capabilities.band_positions[band_name]
            for band_name in band_names
        ]
        for point in positives:
            if (
                analysis_aoi_epsg_4326 is not None
                and not point_in_aoi(
                    point.longitude,
                    point.latitude,
                    analysis_aoi_epsg_4326,
                )
            ):
                raise HybridExecutionError(
                    "target-signature calibration point is outside the "
                    "analysis AOI"
                )
            xs, ys = transform(
                "EPSG:4326",
                str(source.crs),
                [point.longitude],
                [point.latitude],
            )
            row, column = source.index(xs[0], ys[0])
            if (
                row < 0
                or row >= source.height
                or column < 0
                or column >= source.width
            ):
                raise HybridExecutionError(
                    "target-signature point maps outside the source grid"
                )
            pixel = (int(row), int(column))
            if pixel in seen_pixels:
                raise HybridExecutionError(
                    "duplicate target-signature calibration points map to "
                    "the same raster pixel"
                )
            seen_pixels.add(pixel)
            window = Window(column, row, 1, 1)
            raw = source.read(positions, window=window)[:, 0, 0]
            masks = (
                source.read_masks(positions, window=window)[:, 0, 0]
                > 0
            )
            if not np.all(masks):
                raise HybridExecutionError(
                    "target-signature point maps to source-invalid data"
                )
            for band_name, position, value in zip(
                band_names,
                positions,
                raw,
                strict=True,
            ):
                evidence = capabilities.bands[position - 1]
                scaled = (
                    float(value) * float(evidence.scale)
                    + float(evidence.offset)
                )
                if not np.isfinite(scaled):
                    raise HybridExecutionError(
                        "target-signature sample is nonfinite"
                    )
                samples[band_name].append(scaled)
            point_pixels.append(pixel)
    target = {
        band_name: float(np.mean(values, dtype=np.float64))
        for band_name, values in samples.items()
    }
    updated_strategy = strategy.model_copy(
        update={"target_bands": target}
    )
    updated = specialist.model_copy(
        update={"strategy": updated_strategy}
    )
    evidence = {
        "class_id": specialist.class_id,
        "target_source": "positive_calibration_points",
        "target_vector": target,
        "semantic_band_order": list(band_names),
        "sample_count": len(positives),
        "sample_pixel_digest_sha256": _canonical_hash(
            sorted(point_pixels)
        ),
        "source_scaling": {
            band_name: {
                "scale": capabilities.bands[
                    capabilities.band_positions[band_name] - 1
                ].scale,
                "offset": capabilities.bands[
                    capabilities.band_positions[band_name] - 1
                ].offset,
                "data_level": capabilities.bands[
                    capabilities.band_positions[band_name] - 1
                ].data_level,
            }
            for band_name in band_names
        },
        "raw_coordinates_published": False,
        "formula": (
            "per-band arithmetic mean of source-scaled positive calibration "
            "samples"
        ),
    }
    evidence["target_estimation_sha256"] = _canonical_hash(evidence)
    return updated, evidence


def _strategy_from_selection(
    selection: Mapping[str, Any],
) -> SingleIndexThresholdStrategy | MultiIndexWeightedStrategy:
    selected = selection["selected"]
    index_ids = list(selected["index_ids"])
    normalization = {
        item["index_id"]: item for item in selected["normalization"]
    }
    threshold = float(selected["threshold"])
    direction = str(selected["direction"])
    if len(index_ids) == 1:
        index_id = index_ids[0]
        evidence = normalization[index_id]
        minimum = float(evidence["minimum"])
        maximum = float(evidence["maximum"])
        raw_threshold = minimum + threshold * (maximum - minimum)
        return SingleIndexThresholdStrategy(
            type="single_index_threshold",
            condition=IndexConditionSpec(
                index_id=index_id,
                direction=direction,
                threshold=raw_threshold,
            ),
        )
    return MultiIndexWeightedStrategy(
        type="multi_index_weighted_score",
        inputs=[
            WeightedIndexInputSpec(
                index_id=index_id,
                normalization_minimum=float(
                    normalization[index_id]["minimum"]
                ),
                normalization_maximum=float(
                    normalization[index_id]["maximum"]
                ),
                weight=1.0 / len(index_ids),
            )
            for index_id in index_ids
        ],
        intercept=0.0,
        direction=direction,
        threshold=threshold,
        weights_source="learned_spatial_calibration",
    )


def execute_hybrid_classification(
    naip_path: Path,
    general_result: Mapping[str, Any],
    staging: Path,
    recipe: AgriculturalRecipeV4,
    *,
    analysis_aoi_epsg_4326: Mapping[str, Any] | None = None,
    recommendation_selector: (
        Callable[[str, list[dict[str, Any]]], str | None] | None
    ) = None,
) -> dict[str, Any]:
    mode = recipe.classification.specialists.selection_mode
    capabilities, capability_report = _preflight_document(naip_path, recipe)
    analysis = staging / "analysis" / "indices"
    receipts = staging / "receipts"
    data = staging / "data"
    index_directory = data / "indices"
    specialist_directory = data / "specialists"
    for directory in (
        analysis,
        receipts,
        index_directory,
        specialist_directory,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    registry_document = BUILTIN_INDEX_REGISTRY.as_dict()
    _write_json(analysis / "index_registry.json", registry_document)
    _write_json(
        analysis / "index_capability_report.json",
        capability_report,
    )
    search_indices = (
        set(recipe.classification.specialists.search.candidate_indices)
        if mode in {"recommendation", "automatic"}
        else set()
    )
    specialist_inputs = (
        search_indices
        if search_indices
        else {
            index_id
            for specialist in recipe.classification.specialists.classes
            for index_id in _strategy_index_ids(specialist)
        }
    )
    request_by_id = {
        request.index_id: request
        for request in recipe.classification.indices
    }
    plan_ids = list(request_by_id)
    plan_ids.extend(
        sorted(search_indices - set(request_by_id))
    )
    calculation_ids = [
        index_id
        for index_id in plan_ids
        if (
            index_id in search_indices
            or index_id in specialist_inputs
            or request_by_id[index_id].persist
            or request_by_id[index_id].display
        )
    ]
    index_plan = {
        "schema_version": "fasterraster.index-plan/v1",
        "registry_version": registry_document["schema_version"],
        "registry_sha256": registry_document["registry_sha256"],
        "source_asset": "naip_multispectral",
        "requested_indices": [
            {
                **(
                    request_by_id[index_id].model_dump(mode="json")
                    if index_id in request_by_id
                    else {
                        "index_id": index_id,
                        "expression": None,
                        "persist": True,
                        "display": False,
                    }
                ),
                "required_by_specialist": index_id in specialist_inputs,
                "required_by_candidate_search": index_id in search_indices,
                "output": (
                    f"data/indices/{index_id}.cog.tif"
                    if index_id in calculation_ids
                    else None
                ),
            }
            for index_id in plan_ids
        ],
        "specialist_classes": [
            {
                "class_id": specialist.class_id,
                "strategy_type": specialist.strategy.type,
                "index_inputs": list(_strategy_index_ids(specialist)),
                "eligible_parent_general_classes": list(
                    specialist.eligible_parent_general_classes
                ),
                "priority": specialist.priority,
            }
            for specialist in recipe.classification.specialists.classes
        ],
        "selection_mode": mode,
        "candidate_search_bounds": (
            recipe.classification.specialists.search.model_dump(mode="json")
        ),
        "expected_output_rasters": [
            "data/final_hybrid_classification.cog.tif",
            "data/hybrid_decision_state.cog.tif",
            *[
                f"data/indices/{index_id}.cog.tif"
                for index_id in calculation_ids
            ],
            *[
                f"data/specialists/{specialist.class_id}_score.cog.tif"
                for specialist in recipe.classification.specialists.classes
            ],
            *[
                f"data/specialists/{specialist.class_id}_candidate.cog.tif"
                for specialist in recipe.classification.specialists.classes
            ],
        ],
        "memory_policy": (
            "windowed source/index reads and writes; no AOI-wide feature cube"
        ),
    }
    _write_json(analysis / "index_plan.json", index_plan)

    index_receipts: list[dict[str, Any]] = []
    index_paths: dict[str, Path] = {}
    for index_id in calculation_ids:
        request = request_by_id.get(index_id)
        destination = index_directory / f"{index_id}.cog.tif"
        receipt = calculate_index_cog(
            naip_path,
            destination,
            index_id=index_id,
            capabilities=capabilities,
            analysis_aoi_epsg_4326=analysis_aoi_epsg_4326,
            window_size=recipe.classification.general.inference_window_size,
            expression=request.expression if request is not None else None,
        )
        receipt["output"]["path"] = (
            destination.relative_to(staging).as_posix()
        )
        index_paths[index_id] = destination
        index_receipts.append(receipt)
    index_statistics = {
        receipt["index"]["index_id"]: receipt["statistics"]
        for receipt in index_receipts
    }
    _write_json(analysis / "index_statistics.json", index_statistics)
    index_calculation_receipt = {
        "schema_version": "fasterraster.index-calculation-batch/v1",
        "registry_sha256": registry_document["registry_sha256"],
        "index_count": len(index_receipts),
        "indices": index_receipts,
        "analysis_aoi_mask_applied": analysis_aoi_epsg_4326 is not None,
    }
    _write_json(
        receipts / "index_calculation_receipt.json",
        index_calculation_receipt,
    )
    effective_specialists: list[SpecialistClassSpec] = []
    target_estimation_receipts: list[dict[str, Any]] = []
    for specialist in recipe.classification.specialists.classes:
        effective, target_evidence = _estimate_target_signature(
            specialist,
            naip_path=naip_path,
            capabilities=capabilities,
            analysis_aoi_epsg_4326=analysis_aoi_epsg_4326,
        )
        effective_specialists.append(effective)
        if target_evidence:
            target_estimation_receipts.append(target_evidence)
    if target_estimation_receipts:
        _write_json(
            receipts / "target_signature_calibration_receipt.json",
            {
                "schema_version": (
                    "fasterraster.target-signature-calibration/v1"
                ),
                "classes": target_estimation_receipts,
            },
        )
    calibration_receipts: list[dict[str, Any]] = []
    public_calibration: list[dict[str, Any]] = []
    class_selections: list[dict[str, Any]] = []
    finalized = True
    if mode == "user_defined":
        selection_receipt = {
            "schema_version": "fasterraster.index-selection-receipt/v1",
            "status": "USER_DEFINED",
            "selection_mode": mode,
            "automatic_authorized": False,
            "candidate_count": 0,
            "selected_contract": [
                specialist.strategy.model_dump(mode="json")
                for specialist in effective_specialists
            ],
            "user_contract_executed_without_hidden_search": True,
        }
        candidate_ranking = {
            "status": "NOT_APPLICABLE_USER_DEFINED",
            "candidate_count": 0,
            "classes": [],
        }
        selection_validation: dict[str, Any] = {
            "selection_mode": mode,
            "calibration_source": "fixed user rule",
            "inner_selection_metrics": None,
            "untouched_holdout_metrics": None,
        }
    else:
        selected_specialists: list[SpecialistClassSpec] = []
        for specialist in recipe.classification.specialists.classes:
            values, target, folds, calibration_evidence = (
                _calibration_samples(
                    specialist,
                    index_paths=index_paths,
                    general_result=general_result,
                    recipe=recipe,
                    analysis_aoi_epsg_4326=analysis_aoi_epsg_4326,
                )
            )
            private_evidence = dict(calibration_evidence)
            public_evidence = dict(calibration_evidence)
            public_evidence.pop("local_private_point_extraction", None)
            calibration_receipts.append(
                {
                    "class_id": specialist.class_id,
                    **private_evidence,
                }
            )
            public_calibration.append(
                {
                    "class_id": specialist.class_id,
                    **public_evidence,
                }
            )
            selection = nested_spatial_select(
                values,
                target,
                folds,
                outer_holdout_fold=(
                    recipe.classification.general.spatial_holdout_fold
                ),
                bounds=recipe.classification.specialists.search,
                automatic_authorized=(
                    mode == "automatic"
                    and recipe.classification.specialists.automatic_authorized
                ),
                selection_mode=mode,
            )
            if selection["status"] != "SELECTED":
                if mode == "automatic":
                    raise HybridExecutionError(
                        f"specialist {specialist.class_id}: no automatic "
                        "candidate meets the configured performance guard"
                    )
                finalized = False
                class_selections.append(
                    {
                        "class_id": specialist.class_id,
                        **selection,
                    }
                )
                continue
            if mode == "recommendation":
                if recommendation_selector is None:
                    finalized = False
                else:
                    chosen = recommendation_selector(
                        specialist.class_id,
                        list(selection["candidate_ranking"]),
                    )
                    if chosen is None:
                        finalized = False
                        selection = {
                            **selection,
                            "status": "INDEX_SELECTION_CANCELLED",
                            "selected": None,
                        }
                    else:
                        selection = nested_spatial_select(
                            values,
                            target,
                            folds,
                            outer_holdout_fold=(
                                recipe.classification.general.spatial_holdout_fold
                            ),
                            bounds=(
                                recipe.classification.specialists.search
                            ),
                            automatic_authorized=False,
                            selection_mode="recommendation",
                            preferred_candidate_id=chosen,
                        )
            class_selections.append(
                {
                    "class_id": specialist.class_id,
                    **selection,
                }
            )
            if selection["status"] == "SELECTED" and finalized:
                selected_specialists.append(
                    specialist.model_copy(
                        update={
                            "strategy": _strategy_from_selection(selection)
                        }
                    )
                )
        if finalized and len(selected_specialists) != len(
            recipe.classification.specialists.classes
        ):
            finalized = False
        if finalized:
            effective_specialists = selected_specialists
        status = (
            "SELECTED"
            if finalized
            else (
                "INDEX_SELECTION_CANCELLED"
                if any(
                    item["status"] == "INDEX_SELECTION_CANCELLED"
                    for item in class_selections
                )
                else "AWAITING_INDEX_SELECTION"
            )
        )
        selection_receipt = {
            "schema_version": "fasterraster.index-selection-receipt/v1",
            "status": status,
            "selection_mode": mode,
            "automatic_authorized": (
                recipe.classification.specialists.automatic_authorized
            ),
            "explicit_automatic_authorization_recorded": (
                mode == "automatic"
                and recipe.classification.specialists.automatic_authorized
            ),
            "candidate_count": sum(
                int(item.get("candidate_count", 0))
                for item in class_selections
            ),
            "classes": class_selections,
            "calibration": public_calibration,
            "finalized": finalized,
        }
        candidate_ranking = {
            "schema_version": "fasterraster.index-candidate-ranking/v1",
            "status": status,
            "selection_mode": mode,
            "classes": [
                {
                    "class_id": item["class_id"],
                    "candidate_count": item.get("candidate_count", 0),
                    "candidate_ranking": item.get(
                        "candidate_ranking", []
                    ),
                }
                for item in class_selections
            ],
        }
        selection_validation = {
            "selection_mode": mode,
            "calibration_source": sorted(
                {
                    specialist.calibration.source
                    for specialist in (
                        recipe.classification.specialists.classes
                    )
                }
            ),
            "inner_selection_metrics": [
                {
                    "class_id": item["class_id"],
                    "inner_selection": item.get("inner_selection"),
                    "selected": item.get("selected"),
                }
                for item in class_selections
            ],
            "untouched_holdout_metrics": [
                {
                    "class_id": item["class_id"],
                    "outer_holdout": item.get("outer_holdout"),
                }
                for item in class_selections
            ],
        }
    _write_json(
        receipts / "index_selection_receipt.json",
        selection_receipt,
    )
    _write_json(
        analysis / "index_candidate_ranking.json",
        candidate_ranking,
    )
    if calibration_receipts:
        _write_json(
            receipts / "index_calibration_local.json",
            {
                "schema_version": (
                    "fasterraster.index-calibration-local/v1"
                ),
                "public_summaries_omit_raw_coordinates": True,
                "classes": calibration_receipts,
            },
        )
    if not finalized:
        validation_metrics = {
            "schema_version": (
                "fasterraster.index-validation-metrics/v1"
            ),
            **selection_validation,
            "finalized": False,
            "interpretation": (
                "Candidate metrics are spatial agreement with declared "
                "calibration evidence. The hybrid classification is not "
                "finalized until a user accepts a candidate."
            ),
            "general_weak_label_metrics": general_result.get("metrics"),
        }
        _write_json(
            analysis / "index_validation_metrics.json",
            validation_metrics,
        )
        return {
            "finalized": False,
            "status": selection_receipt["status"],
            "registry": registry_document,
            "capability_report": capability_report,
            "index_plan": index_plan,
            "index_statistics": index_statistics,
            "selection_receipt": selection_receipt,
            "paths": {
                "general_classification": Path(
                    general_result["paths"]["classification"]
                ),
                "general_confidence": Path(
                    general_result["paths"]["confidence"]
                ),
                "general_agreement": Path(
                    general_result["paths"]["agreement"]
                ),
                "indices": index_paths,
            },
        }

    effective_collection = (
        recipe.classification.specialists.model_copy(
            update={"classes": effective_specialists}
        )
    )
    effective_classification = recipe.classification.model_copy(
        update={"specialists": effective_collection}
    )
    effective_recipe = recipe.model_copy(
        update={"classification": effective_classification}
    )

    general_path = Path(general_result["paths"]["classification"])
    specialist_products = [
        _write_specialist_products(
            specialist,
            general_path=general_path,
            naip_path=naip_path,
            index_paths=index_paths,
            capabilities=capabilities,
            destination_directory=specialist_directory,
            analysis_aoi_epsg_4326=analysis_aoi_epsg_4326,
            window_size=recipe.classification.general.inference_window_size,
        )
        for specialist in effective_specialists
    ]
    rules_document = {
        "schema_version": "fasterraster.specialist-class-rules/v1",
        "requested_class_count": (
            effective_collection.requested_class_count
        ),
        "actual_class_count": len(specialist_products),
        "classes": [product["rule"] for product in specialist_products],
    }
    _write_json(analysis / "specialist_class_rules.json", rules_document)
    specialist_receipt = {
        "schema_version": "fasterraster.specialist-classification-receipt/v1",
        "class_count": len(specialist_products),
        "classes": [
            {
                key: (
                    value.relative_to(staging).as_posix()
                    if isinstance(value, Path)
                    else value
                )
                for key, value in product.items()
                if key != "rule"
            }
            for product in specialist_products
        ],
        "general_model_confidence_combined_with_specialist_scores": False,
        "specialist_scores_are_probabilities": False,
    }
    _write_json(
        receipts / "specialist_classification_receipt.json",
        specialist_receipt,
    )

    hybrid = _arbitrate_rasters(
        general_path=general_path,
        specialist_products=specialist_products,
        recipe=effective_recipe,
        data_directory=data,
        window_size=recipe.classification.general.inference_window_size,
    )
    _write_json(
        analysis / "specialist_overlap_matrix.json",
        {
            "schema_version": "fasterraster.specialist-overlap-matrix/v1",
            "overlaps": hybrid["evidence"]["overlap_matrix"],
        },
    )
    _write_json(
        analysis / "hybrid_class_inventory.json",
        {
            "schema_version": "fasterraster.hybrid-class-inventory/v1",
            "classes": hybrid["evidence"]["class_inventory"],
        },
    )
    validation_metrics = {
        "schema_version": "fasterraster.index-validation-metrics/v1",
        **selection_validation,
        "interpretation": (
            (
                "The shipped fixed-rule specialists are inspectable "
                "scene-relative rules. They are not independently validated "
                "supervised results."
            )
            if mode == "user_defined"
            else (
                "Selection and holdout metrics measure spatial agreement "
                "with declared calibration evidence, not independent "
                "ground-truth accuracy or physical causation."
            )
        ),
        "general_weak_label_metrics": general_result.get("metrics"),
    }
    _write_json(
        analysis / "index_validation_metrics.json",
        validation_metrics,
    )
    _write_json(
        receipts / "hybrid_classification_receipt.json",
        hybrid["evidence"],
    )
    return {
        "finalized": True,
        "status": selection_receipt["status"],
        "registry": registry_document,
        "capability_report": capability_report,
        "index_plan": index_plan,
        "index_statistics": index_statistics,
        "selection_receipt": selection_receipt,
        "specialist_rules": rules_document,
        "specialist_receipt": specialist_receipt,
        "hybrid_receipt": hybrid["evidence"],
        "paths": {
            "general_classification": general_path,
            "general_confidence": Path(general_result["paths"]["confidence"]),
            "general_agreement": Path(general_result["paths"]["agreement"]),
            "indices": index_paths,
            "specialist_scores": {
                item["class_id"]: item["score_path"]
                for item in specialist_products
            },
            "specialist_candidates": {
                item["class_id"]: item["candidate_path"]
                for item in specialist_products
            },
            "final_hybrid_classification": hybrid["final_path"],
            "hybrid_decision_state": hybrid["decision_path"],
        },
    }
