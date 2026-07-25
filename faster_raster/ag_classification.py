from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import platform
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import numpy as np
import rasterio
from affine import Affine
from rasterio.enums import Resampling
from rasterio.shutil import copy as raster_copy
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window

from faster_raster.ag_classification_contracts import (
    CDL_SURFACE_SUPERCLASSES,
    ClassificationMapping,
)
from faster_raster.ag_recipes import AgriculturalRecipeV3, ClassificationSpec
from faster_raster.aoi_geometry import raster_aoi_mask
from faster_raster.contract_repair import intervention_reference


FEATURE_EPSILON = np.float32(1e-6)
SUPPORTED_FEATURES = (
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
)
AGREEMENT_STATE_LABELS = {
    0: "invalid_or_excluded",
    1: "prediction_agrees_with_cdl",
    2: "low_confidence_or_unknown",
    3: "high_confidence_disagreement",
}


class ClassificationError(RuntimeError):
    """Raised when the classification scientific or output contract fails."""


@dataclass(frozen=True)
class RasterGrid:
    crs: str
    transform: Affine
    width: int
    height: int

    @classmethod
    def from_dataset(cls, dataset: rasterio.io.DatasetReader) -> "RasterGrid":
        if dataset.crs is None:
            raise ClassificationError("raster grid must declare a CRS")
        return cls(
            crs=dataset.crs.to_string(),
            transform=dataset.transform,
            width=dataset.width,
            height=dataset.height,
        )

    @property
    def pixel_area_m2(self) -> float:
        return abs(
            self.transform.a * self.transform.e
            - self.transform.b * self.transform.d
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "crs": self.crs,
            "transform": list(self.transform)[:6],
            "width": self.width,
            "height": self.height,
            "pixel_size": [
                abs(float(self.transform.a)),
                abs(float(self.transform.e)),
            ],
            "pixel_area_m2": self.pixel_area_m2,
        }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_array(value: np.ndarray) -> str:
    array = np.ascontiguousarray(value)
    digest = hashlib.sha256()
    digest.update(str(array.dtype).encode("ascii"))
    digest.update(json.dumps(list(array.shape)).encode("ascii"))
    digest.update(array.tobytes(order="C"))
    return digest.hexdigest()


def classification_dependency_status() -> dict[str, Any]:
    available = importlib.util.find_spec("sklearn") is not None
    return {
        "extra": "classification",
        "available": available,
        "install_command": "pip install -e '.[classification]'",
        "required_before_network_transfer": True,
    }


def _load_sklearn() -> tuple[Any, Any, str]:
    if not classification_dependency_status()["available"]:
        raise ClassificationError(
            "This recipe requires the FasterRaster classification extra: "
            "pip install -e '.[classification]'"
        )
    try:
        import sklearn
        from sklearn import metrics
        from sklearn.ensemble import RandomForestClassifier
    except (ImportError, ModuleNotFoundError) as exc:
        raise ClassificationError(
            "This recipe requires the FasterRaster classification extra: "
            "pip install -e '.[classification]'"
        ) from exc
    return RandomForestClassifier, metrics, sklearn.__version__


def iter_windows(width: int, height: int, size: int) -> Iterator[Window]:
    for row_off in range(0, height, size):
        for col_off in range(0, width, size):
            yield Window(
                col_off=col_off,
                row_off=row_off,
                width=min(size, width - col_off),
                height=min(size, height - row_off),
            )


def _source_valid_mask(
    bands: np.ndarray,
    source_mask: np.ndarray | None,
) -> np.ndarray:
    valid = np.all(np.isfinite(bands), axis=0)
    if source_mask is not None:
        mask = np.asarray(source_mask)
        if mask.ndim == 3:
            mask = np.all(mask, axis=0)
        if mask.shape != bands.shape[1:]:
            raise ValueError("source mask does not match four-band raster shape")
        valid &= mask.astype(bool)
    return valid


def calculate_features(
    bands: np.ndarray,
    features: Sequence[str],
    *,
    source_mask: np.ndarray | None = None,
    epsilon: float = float(FEATURE_EPSILON),
) -> tuple[np.ndarray, np.ndarray]:
    numeric = np.asarray(bands)
    if numeric.ndim != 3 or numeric.shape[0] != 4:
        raise ValueError("NAIP feature input must have shape (4, rows, columns)")
    unsupported = sorted(set(features) - set(SUPPORTED_FEATURES))
    if unsupported:
        raise ValueError(
            "unsupported classification feature(s): " + ", ".join(unsupported)
        )
    if len(features) != len(set(features)):
        raise ValueError("classification features must be unique")
    if numeric.dtype == np.uint8:
        scaled = numeric.astype(np.float32) / np.float32(255.0)
    else:
        scaled = numeric.astype(np.float32, copy=False)
    valid = _source_valid_mask(scaled, source_mask)
    red, green, blue, nir = scaled
    maximum = np.maximum(np.maximum(red, green), blue)
    minimum = np.minimum(np.minimum(red, green), blue)
    values: dict[str, np.ndarray] = {
        "red": red,
        "green": green,
        "blue": blue,
        "nir": nir,
        "ndvi": np.clip((nir - red) / (nir + red + epsilon), -1.0, 1.0),
        "gndvi": np.clip(
            (nir - green) / (nir + green + epsilon),
            -1.0,
            1.0,
        ),
        "vari": np.clip(
            (green - red) / (green + red - blue + epsilon),
            -1.0,
            1.0,
        ),
        "excess_green": np.clip(2.0 * green - red - blue, -2.0, 2.0),
        "brightness": np.clip((red + green + blue) / 3.0, 0.0, 1.0),
        "saturation": np.clip(
            (maximum - minimum) / (maximum + epsilon),
            0.0,
            1.0,
        ),
    }
    stack = np.stack([values[name] for name in features]).astype(
        np.float32,
        copy=False,
    )
    valid &= np.all(np.isfinite(stack), axis=0)
    stack[:, ~valid] = 0.0
    return stack, valid


def map_cdl_superclasses(
    values: np.ndarray,
    mapping: ClassificationMapping = CDL_SURFACE_SUPERCLASSES,
) -> np.ndarray:
    source = np.asarray(values)
    result = np.zeros(source.shape, dtype=np.uint8)
    in_range = (source >= 0) & (source <= 255)
    lut = np.zeros(256, dtype=np.uint8)
    for output_class in mapping.output_classes:
        if output_class.code:
            lut[list(output_class.cdl_codes)] = output_class.code
    result[in_range] = lut[source[in_range].astype(np.uint8)]
    return result


def training_core_mask(
    superclasses: np.ndarray,
    radius: int = 1,
) -> np.ndarray:
    if radius < 1:
        raise ValueError("training core radius must be at least one cell")
    labels = np.asarray(superclasses, dtype=np.uint8)
    if labels.ndim != 2:
        raise ValueError("training core input must be a two-dimensional raster")
    core = labels > 0
    rows, columns = labels.shape
    for row_shift in range(-radius, radius + 1):
        for column_shift in range(-radius, radius + 1):
            if row_shift == 0 and column_shift == 0:
                continue
            shifted = np.zeros_like(labels)
            src_row_start = max(0, -row_shift)
            src_row_end = min(rows, rows - row_shift)
            src_col_start = max(0, -column_shift)
            src_col_end = min(columns, columns - column_shift)
            dst_row_start = src_row_start + row_shift
            dst_row_end = src_row_end + row_shift
            dst_col_start = src_col_start + column_shift
            dst_col_end = src_col_end + column_shift
            shifted[
                dst_row_start:dst_row_end,
                dst_col_start:dst_col_end,
            ] = labels[
                src_row_start:src_row_end,
                src_col_start:src_col_end,
            ]
            core &= shifted == labels
    return np.where(core, labels, 0).astype(np.uint8)


def validate_naip_multispectral(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ClassificationError(f"raw four-band NAIP asset is missing: {path}")
    try:
        with rasterio.open(path) as source:
            errors: list[str] = []
            if source.count != 4:
                errors.append(f"band_count_{source.count}_is_not_4")
            if tuple(source.dtypes) != ("uint8", "uint8", "uint8", "uint8"):
                errors.append(f"dtypes_are_{list(source.dtypes)}_not_uint8")
            color_interpretation = tuple(
                value.name for value in source.colorinterp
            )
            allowed_color_interpretations = {
                ("red", "green", "blue", "undefined"),
                ("undefined", "undefined", "undefined", "undefined"),
                ("gray", "undefined", "undefined", "undefined"),
            }
            if color_interpretation not in allowed_color_interpretations:
                errors.append(
                    f"band_color_interpretation_is_{color_interpretation}"
                )
            band_order = source.tags().get("FASTERRASTER_BAND_ORDER")
            if band_order != "red,green,blue,near_infrared":
                errors.append("band_order_metadata_missing_or_invalid")
            if source.crs is None:
                errors.append("crs_missing")
            if source.width <= 0 or source.height <= 0:
                errors.append("dimensions_invalid")
            if (
                not np.isfinite(source.transform.a)
                or not np.isfinite(source.transform.e)
                or source.transform.a == 0
                or source.transform.e == 0
            ):
                errors.append("transform_invalid")
            mask_flags = tuple(
                tuple(str(flag) for flag in flags)
                for flags in source.mask_flag_enums
            )
            if len(set(mask_flags)) > 1:
                errors.append("band_mask_contracts_differ")
            if source.count == 4:
                for _, window in source.block_windows(1):
                    masks = source.read_masks((1, 2, 3, 4), window=window)
                    if not np.all(masks == masks[0]):
                        errors.append("band_masks_differ")
                        break
            layout = source.tags(ns="IMAGE_STRUCTURE").get("LAYOUT")
            if layout != "COG":
                errors.append("layout_is_not_cog")
            if errors:
                raise ClassificationError(
                    "raw four-band NAIP validation failed: " + ", ".join(errors)
                )
            return {
                "status": "PASS",
                "path": path.name,
                "band_count": source.count,
                "band_order": ["red", "green", "blue", "near_infrared"],
                "dtype": "uint8",
                "crs": source.crs.to_string(),
                "transform": list(source.transform)[:6],
                "width": source.width,
                "height": source.height,
                "pixel_size": [
                    abs(float(source.transform.a)),
                    abs(float(source.transform.e)),
                ],
                "shared_mask": True,
                "cog_layout": True,
                "sha256": _sha256_file(path),
            }
    except ClassificationError:
        raise
    except (OSError, rasterio.errors.RasterioError) as exc:
        raise ClassificationError(
            f"unable to read raw four-band NAIP asset: {exc}"
        ) from exc


def _working_path(destination: Path) -> Path:
    return destination.parent / f".{destination.name}.working.tif"


def _raster_profile(
    grid: RasterGrid,
    dtype: str,
    nodata: int | float,
) -> dict[str, Any]:
    return {
        "driver": "GTiff",
        "width": grid.width,
        "height": grid.height,
        "count": 1,
        "dtype": dtype,
        "crs": grid.crs,
        "transform": grid.transform,
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
        compress="DEFLATE",
        blocksize=512,
        bigtiff="IF_SAFER",
        overview_resampling="nearest" if categorical else "average",
    )
    working.unlink(missing_ok=True)
    return destination


def _validate_grid_product(
    path: Path,
    grid: RasterGrid,
    *,
    dtype: str,
    minimum: int,
    maximum: int,
) -> dict[str, Any]:
    with rasterio.open(path) as source:
        if (
            source.count != 1
            or source.dtypes[0] != dtype
            or source.crs is None
            or source.crs.to_string() != grid.crs
            or source.transform != grid.transform
            or source.width != grid.width
            or source.height != grid.height
        ):
            raise ClassificationError(f"output grid validation failed: {path}")
        observed_min = maximum
        observed_max = minimum
        for _, window in source.block_windows(1):
            values = source.read(1, window=window)
            if values.size:
                observed_min = min(observed_min, int(values.min()))
                observed_max = max(observed_max, int(values.max()))
        if observed_min < minimum or observed_max > maximum:
            raise ClassificationError(
                f"output value range failed for {path}: "
                f"{observed_min}..{observed_max}"
            )
        if source.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") != "COG":
            raise ClassificationError(f"output is not a COG: {path}")
        return {
            "path": path.name,
            "dtype": dtype,
            "minimum": observed_min,
            "maximum": observed_max,
            "sha256": _sha256_file(path),
        }


def prepare_weak_labels(
    cdl_path: Path,
    naip_path: Path,
    output_directory: Path,
    *,
    radius: int,
) -> dict[str, Any]:
    output_directory.mkdir(parents=True, exist_ok=True)
    with rasterio.open(naip_path) as naip:
        grid = RasterGrid.from_dataset(naip)
    try:
        with rasterio.open(cdl_path) as cdl:
            if cdl.count != 1 or cdl.crs is None:
                raise ClassificationError(
                    "CDL weak-label source must be a single-band georeferenced raster"
                )
            source_values = cdl.read(1)
            source_mask = cdl.read_masks(1) > 0
            mapped = map_cdl_superclasses(source_values)
            mapped[~source_mask] = 0
            cores = training_core_mask(mapped, radius)
            native_profile = cdl.profile.copy()
            native_profile.update(
                driver="GTiff",
                count=1,
                dtype="uint8",
                nodata=0,
                tiled=True,
                compress="DEFLATE",
                bigtiff="IF_SAFER",
            )
            native_mapped = output_directory / ".cdl_superclasses_native.tif"
            native_cores = output_directory / ".cdl_training_cores_native.tif"
            for path, values in (
                (native_mapped, mapped),
                (native_cores, cores),
            ):
                with rasterio.open(path, "w", **native_profile) as sink:
                    sink.write(values, 1)
    except ClassificationError:
        raise
    except (OSError, rasterio.errors.RasterioError) as exc:
        raise ClassificationError(f"unable to prepare CDL weak labels: {exc}") from exc

    superclass_path = output_directory / "cdl_superclasses.cog.tif"
    core_path = output_directory / "cdl_training_cores.cog.tif"
    for native, destination in (
        (native_mapped, superclass_path),
        (native_cores, core_path),
    ):
        working = _working_path(destination)
        with rasterio.open(native) as source:
            with WarpedVRT(
                source,
                crs=grid.crs,
                transform=grid.transform,
                width=grid.width,
                height=grid.height,
                resampling=Resampling.nearest,
                nodata=0,
            ) as aligned:
                with rasterio.open(
                    working,
                    "w",
                    **_raster_profile(grid, "uint8", 0),
                ) as sink:
                    for window in iter_windows(grid.width, grid.height, 512):
                        sink.write(aligned.read(1, window=window), 1, window=window)
        _finalize_cog(working, destination, categorical=True)
    native_mapped.unlink(missing_ok=True)
    native_cores.unlink(missing_ok=True)
    return {
        "mapping_id": CDL_SURFACE_SUPERCLASSES.mapping_id,
        "mapping_sha256": CDL_SURFACE_SUPERCLASSES.sha256,
        "training_core_radius_cdl_cells": radius,
        "complete_superclasses": _validate_grid_product(
            superclass_path,
            grid,
            dtype="uint8",
            minimum=0,
            maximum=6,
        ),
        "training_cores": _validate_grid_product(
            core_path,
            grid,
            dtype="uint8",
            minimum=0,
            maximum=6,
        ),
        "superclass_path": superclass_path,
        "training_core_path": core_path,
    }


def spatial_fold(
    block_row: int,
    block_column: int,
    random_seed: int,
    folds: int,
) -> int:
    payload = f"{block_row}:{block_column}:{random_seed}".encode("ascii")
    value = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return value % folds


def _priorities(
    rows: np.ndarray,
    columns: np.ndarray,
    seed: int,
    class_code: int,
) -> np.ndarray:
    value = (
        rows.astype(np.uint64) * np.uint64(0x9E3779B185EBCA87)
        + columns.astype(np.uint64) * np.uint64(0xC2B2AE3D27D4EB4F)
        + np.uint64(seed)
        + np.uint64(class_code * 0x165667B1)
    )
    value ^= value >> np.uint64(30)
    value *= np.uint64(0xBF58476D1CE4E5B9)
    value ^= value >> np.uint64(27)
    value *= np.uint64(0x94D049BB133111EB)
    value ^= value >> np.uint64(31)
    return value


def _bounded_merge(
    current: dict[str, np.ndarray] | None,
    candidate: dict[str, np.ndarray],
    cap: int,
) -> dict[str, np.ndarray]:
    if current is None:
        merged = candidate
    else:
        merged = {
            key: np.concatenate((current[key], candidate[key]), axis=0)
            for key in current
        }
    count = len(merged["priority"])
    if count <= cap:
        return merged
    selected = np.argpartition(merged["priority"], cap - 1)[:cap]
    selected = selected[np.argsort(merged["priority"][selected], kind="stable")]
    return {key: value[selected] for key, value in merged.items()}


def extract_training_samples(
    naip_path: Path,
    training_core_path: Path,
    spec: ClassificationSpec,
    *,
    analysis_aoi_epsg_4326: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    retained: dict[int, dict[str, np.ndarray]] = {}
    eligible = Counter()
    invalid_source_pixels = 0
    aoi_excluded_source_pixels = 0
    with rasterio.open(naip_path) as naip, rasterio.open(
        training_core_path
    ) as cores:
        grid = RasterGrid.from_dataset(naip)
        if (
            cores.crs != naip.crs
            or cores.transform != naip.transform
            or cores.width != naip.width
            or cores.height != naip.height
        ):
            raise ClassificationError(
                "training-core raster is not aligned to raw NAIP"
            )
        for window in iter_windows(
            grid.width,
            grid.height,
            spec.inference_window_size,
        ):
            bands = naip.read((1, 2, 3, 4), window=window)
            masks = naip.read_masks((1, 2, 3, 4), window=window) > 0
            stack, source_valid = calculate_features(
                bands,
                spec.features,
                source_mask=masks,
            )
            invalid_source_pixels += int((~source_valid).sum())
            if analysis_aoi_epsg_4326 is not None:
                aoi_valid = raster_aoi_mask(
                    naip,
                    analysis_aoi_epsg_4326,
                    window=window,
                )
                aoi_excluded_source_pixels += int(
                    (source_valid & ~aoi_valid).sum()
                )
                source_valid &= aoi_valid
            labels = cores.read(1, window=window)
            for class_code in range(1, 7):
                local_rows, local_columns = np.nonzero(
                    (labels == class_code) & source_valid
                )
                count = len(local_rows)
                if not count:
                    continue
                eligible[class_code] += count
                global_rows = local_rows.astype(np.int64) + int(window.row_off)
                global_columns = (
                    local_columns.astype(np.int64) + int(window.col_off)
                )
                feature_values = stack[:, local_rows, local_columns].T
                folds = np.fromiter(
                    (
                        spatial_fold(
                            int(row) // spec.inference_window_size,
                            int(column) // spec.inference_window_size,
                            spec.random_seed,
                            spec.spatial_holdout_folds,
                        )
                        for row, column in zip(
                            global_rows,
                            global_columns,
                            strict=True,
                        )
                    ),
                    dtype=np.uint8,
                    count=count,
                )
                candidate = {
                    "features": feature_values.astype(np.float32, copy=False),
                    "rows": global_rows,
                    "columns": global_columns,
                    "folds": folds,
                    "priority": _priorities(
                        global_rows,
                        global_columns,
                        spec.random_seed,
                        class_code,
                    ),
                }
                retained[class_code] = _bounded_merge(
                    retained.get(class_code),
                    candidate,
                    spec.maximum_samples_per_class,
                )

    minimum_holdout = max(
        1,
        spec.minimum_training_samples_per_class
        // max(1, spec.spatial_holdout_folds - 1),
    )
    retained_classes: list[int] = []
    excluded_classes: dict[str, str] = {}
    train_parts: list[np.ndarray] = []
    train_labels: list[np.ndarray] = []
    holdout_parts: list[np.ndarray] = []
    holdout_labels: list[np.ndarray] = []
    selected_counts: dict[str, dict[str, int]] = {}
    coordinate_rows: list[tuple[int, int, int, int]] = []
    train_block_ids: set[tuple[int, int]] = set()
    holdout_block_ids: set[tuple[int, int]] = set()
    for class_code in range(1, 7):
        sample = retained.get(class_code)
        if sample is None:
            excluded_classes[str(class_code)] = "no_eligible_training_core_pixels"
            selected_counts[str(class_code)] = {
                "eligible": int(eligible[class_code]),
                "selected": 0,
                "train": 0,
                "holdout": 0,
            }
            continue
        holdout = sample["folds"] == spec.spatial_holdout_fold
        train_count = int((~holdout).sum())
        holdout_count = int(holdout.sum())
        selected_counts[str(class_code)] = {
            "eligible": int(eligible[class_code]),
            "selected": int(len(sample["folds"])),
            "train": train_count,
            "holdout": holdout_count,
        }
        if train_count < spec.minimum_training_samples_per_class:
            excluded_classes[str(class_code)] = (
                f"train_support_{train_count}_below_"
                f"{spec.minimum_training_samples_per_class}"
            )
            continue
        if holdout_count < minimum_holdout:
            excluded_classes[str(class_code)] = (
                f"holdout_support_{holdout_count}_below_{minimum_holdout}"
            )
            continue
        retained_classes.append(class_code)
        train_parts.append(sample["features"][~holdout])
        train_labels.append(
            np.full(train_count, class_code, dtype=np.uint8)
        )
        holdout_parts.append(sample["features"][holdout])
        holdout_labels.append(
            np.full(holdout_count, class_code, dtype=np.uint8)
        )
        coordinate_rows.extend(
            (
                class_code,
                int(row),
                int(column),
                int(fold),
            )
            for row, column, fold in zip(
                sample["rows"],
                sample["columns"],
                sample["folds"],
                strict=True,
            )
        )
        for row, column, fold in zip(
            sample["rows"],
            sample["columns"],
            sample["folds"],
            strict=True,
        ):
            block_id = (
                int(row) // spec.inference_window_size,
                int(column) // spec.inference_window_size,
            )
            if int(fold) == spec.spatial_holdout_fold:
                holdout_block_ids.add(block_id)
            else:
                train_block_ids.add(block_id)
    overlapping_blocks = train_block_ids & holdout_block_ids
    if overlapping_blocks:
        raise ClassificationError(
            "spatial validation block leakage detected: "
            f"{sorted(overlapping_blocks)}"
        )
    if len(retained_classes) < 2:
        raise ClassificationError(
            "spatial validation cannot be formed: fewer than two weak-label "
            f"classes have sufficient train and holdout support; {excluded_classes}"
        )
    x_train = np.concatenate(train_parts, axis=0)
    y_train = np.concatenate(train_labels)
    x_holdout = np.concatenate(holdout_parts, axis=0)
    y_holdout = np.concatenate(holdout_labels)
    coordinate_digest = hashlib.sha256(
        "\n".join(
            f"{class_code},{row},{column},{fold}"
            for class_code, row, column, fold in sorted(coordinate_rows)
        ).encode("ascii")
    ).hexdigest()
    return {
        "x_train": x_train,
        "y_train": y_train,
        "x_holdout": x_holdout,
        "y_holdout": y_holdout,
        "retained_classes": retained_classes,
        "excluded_classes": excluded_classes,
        "eligible_pixels_per_class": {
            str(code): int(eligible[code]) for code in range(1, 7)
        },
        "selected_samples_per_class": selected_counts,
        "invalid_source_pixels_seen": invalid_source_pixels,
        "aoi_excluded_source_pixels_seen": aoi_excluded_source_pixels,
        "train_block_ids": sorted(train_block_ids),
        "holdout_block_ids": sorted(holdout_block_ids),
        "coordinate_digest_sha256": coordinate_digest,
        "feature_matrix_sha256": _sha256_array(x_train),
        "label_vector_sha256": _sha256_array(y_train),
        "holdout_feature_matrix_sha256": _sha256_array(x_holdout),
        "holdout_label_vector_sha256": _sha256_array(y_holdout),
        "minimum_holdout_samples_per_class": minimum_holdout,
    }


def _metric_value(value: Any) -> float:
    return float(value) if np.isfinite(value) else 0.0


def fit_and_evaluate(
    samples: dict[str, Any],
    spec: ClassificationSpec,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    classifier_type, metrics, sklearn_version = _load_sklearn()
    hyperparameters = {
        "n_estimators": spec.n_estimators,
        "max_depth": spec.max_depth,
        "min_samples_leaf": spec.min_samples_leaf,
        "max_features": spec.max_features,
        "class_weight": spec.class_weight,
        "random_state": spec.random_seed,
        "n_jobs": spec.n_jobs,
    }
    model = classifier_type(**hyperparameters)
    model.fit(samples["x_train"], samples["y_train"])
    predicted = model.predict(samples["x_holdout"])
    labels = list(samples["retained_classes"])
    matrix = metrics.confusion_matrix(
        samples["y_holdout"],
        predicted,
        labels=labels,
    )
    precision, recall, f1, support = metrics.precision_recall_fscore_support(
        samples["y_holdout"],
        predicted,
        labels=labels,
        zero_division=0,
    )
    result = {
        "metric_family": "weak-label spatial holdout agreement",
        "holdout_fold": spec.spatial_holdout_fold,
        "spatial_holdout_folds": spec.spatial_holdout_folds,
        "class_order": labels,
        "zero_division_policy": 0,
        "overall_agreement": _metric_value(
            metrics.accuracy_score(samples["y_holdout"], predicted)
        ),
        "balanced_agreement": _metric_value(
            metrics.balanced_accuracy_score(
                samples["y_holdout"],
                predicted,
            )
        ),
        "macro_precision": _metric_value(
            metrics.precision_score(
                samples["y_holdout"],
                predicted,
                labels=labels,
                average="macro",
                zero_division=0,
            )
        ),
        "macro_recall": _metric_value(
            metrics.recall_score(
                samples["y_holdout"],
                predicted,
                labels=labels,
                average="macro",
                zero_division=0,
            )
        ),
        "macro_f1": _metric_value(
            metrics.f1_score(
                samples["y_holdout"],
                predicted,
                labels=labels,
                average="macro",
                zero_division=0,
            )
        ),
        "cohen_kappa": _metric_value(
            metrics.cohen_kappa_score(
                samples["y_holdout"],
                predicted,
                labels=labels,
            )
        ),
        "per_class": {
            str(code): {
                "precision": _metric_value(precision[index]),
                "recall": _metric_value(recall[index]),
                "f1": _metric_value(f1[index]),
                "support": int(support[index]),
            }
            for index, code in enumerate(labels)
        },
        "confusion_matrix": matrix.astype(int).tolist(),
    }
    model_receipt = {
        "backend": spec.backend,
        "sklearn_version": sklearn_version,
        "numpy_version": np.__version__,
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "hyperparameters": hyperparameters,
        "feature_order": list(spec.features),
        "class_order": [int(value) for value in model.classes_],
        "seed": spec.random_seed,
        "training_feature_matrix_sha256": samples[
            "feature_matrix_sha256"
        ],
        "training_label_vector_sha256": samples["label_vector_sha256"],
        "mapping_id": CDL_SURFACE_SUPERCLASSES.mapping_id,
        "mapping_sha256": CDL_SURFACE_SUPERCLASSES.sha256,
        "serialization": "none; unsafe pickle artifacts are not written",
    }
    return model, result, model_receipt


def _write_confusion_matrix(
    directory: Path,
    metrics: dict[str, Any],
) -> None:
    labels = metrics["class_order"]
    rows = metrics["confusion_matrix"]
    path = directory / "holdout_confusion_matrix.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["weak_label_class", *labels])
        for label, row in zip(labels, rows, strict=True):
            writer.writerow([label, *row])
    _write_json(
        directory / "holdout_confusion_matrix.json",
        {
            "metric_family": metrics["metric_family"],
            "class_order": labels,
            "matrix": rows,
        },
    )


def run_inference(
    naip_path: Path,
    model: Any,
    spec: ClassificationSpec,
    data_directory: Path,
    *,
    year: int,
    analysis_aoi_epsg_4326: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    data_directory.mkdir(parents=True, exist_ok=True)
    classification_path = (
        data_directory / f"naip_{year}_surface_classification.cog.tif"
    )
    confidence_path = (
        data_directory / f"naip_{year}_classification_confidence.cog.tif"
    )
    class_working = _working_path(classification_path)
    confidence_working = _working_path(confidence_path)
    raw_counts = Counter()
    final_counts = Counter()
    valid_source_pixels = 0
    source_valid_pixels_before_aoi = 0
    aoi_excluded_pixels = 0
    with rasterio.open(naip_path) as naip:
        grid = RasterGrid.from_dataset(naip)
        with rasterio.open(
            class_working,
            "w",
            **_raster_profile(grid, "uint8", 0),
        ) as class_sink, rasterio.open(
            confidence_working,
            "w",
            **_raster_profile(grid, "uint8", 0),
        ) as confidence_sink:
            for window in iter_windows(
                grid.width,
                grid.height,
                spec.inference_window_size,
            ):
                bands = naip.read((1, 2, 3, 4), window=window)
                masks = naip.read_masks((1, 2, 3, 4), window=window) > 0
                stack, valid = calculate_features(
                    bands,
                    spec.features,
                    source_mask=masks,
                )
                source_valid_pixels_before_aoi += int(valid.sum())
                if analysis_aoi_epsg_4326 is not None:
                    aoi_valid = raster_aoi_mask(
                        naip,
                        analysis_aoi_epsg_4326,
                        window=window,
                    )
                    aoi_excluded_pixels += int((valid & ~aoi_valid).sum())
                    valid &= aoi_valid
                shape = valid.shape
                classes = np.zeros(shape, dtype=np.uint8)
                confidence = np.zeros(shape, dtype=np.uint8)
                valid_count = int(valid.sum())
                valid_source_pixels += valid_count
                if valid_count:
                    matrix = stack[:, valid].T
                    probabilities = model.predict_proba(matrix)
                    best = np.argmax(probabilities, axis=1)
                    predicted = np.asarray(model.classes_, dtype=np.uint8)[best]
                    maximum = probabilities[
                        np.arange(len(probabilities)),
                        best,
                    ]
                    raw_counts.update(
                        {
                            int(code): int(count)
                            for code, count in zip(
                                *np.unique(predicted, return_counts=True),
                                strict=True,
                            )
                        }
                    )
                    accepted = maximum >= spec.confidence_threshold
                    thresholded = np.where(accepted, predicted, 0).astype(
                        np.uint8
                    )
                    confidence_values = np.clip(
                        np.rint(maximum * 100.0),
                        1,
                        100,
                    ).astype(np.uint8)
                    classes[valid] = thresholded
                    confidence[valid] = confidence_values
                    final_counts.update(
                        {
                            int(code): int(count)
                            for code, count in zip(
                                *np.unique(
                                    thresholded,
                                    return_counts=True,
                                ),
                                strict=True,
                            )
                        }
                    )
                class_sink.write(classes, 1, window=window)
                confidence_sink.write(confidence, 1, window=window)
    _finalize_cog(class_working, classification_path, categorical=True)
    _finalize_cog(confidence_working, confidence_path, categorical=False)
    return {
        "grid": grid,
        "classification_path": classification_path,
        "confidence_path": confidence_path,
        "valid_source_pixels": valid_source_pixels,
        "source_valid_pixels_before_aoi": source_valid_pixels_before_aoi,
        "aoi_excluded_pixels": aoi_excluded_pixels,
        "raw_model_class_counts": {
            str(code): int(raw_counts[code]) for code in range(0, 7)
        },
        "pre_sieve_class_counts": {
            str(code): int(final_counts[code]) for code in range(0, 7)
        },
        "post_sieve_class_counts": {
            str(code): int(final_counts[code]) for code in range(0, 7)
        },
        "sieve": {
            "configured_minimum_pixels": spec.sieve_minimum_pixels,
            "applied": False,
            "reason": (
                "optional model postprocessing is disabled to preserve exact "
                "blockwise inference and invalid-data gaps"
            ),
        },
        "classification_validation": _validate_grid_product(
            classification_path,
            grid,
            dtype="uint8",
            minimum=0,
            maximum=6,
        ),
        "confidence_validation": _validate_grid_product(
            confidence_path,
            grid,
            dtype="uint8",
            minimum=0,
            maximum=100,
        ),
    }


def audit_cdl_agreement(
    inference: dict[str, Any],
    superclass_path: Path,
    data_directory: Path,
    analysis_directory: Path,
    *,
    analysis_aoi_epsg_4326: Mapping[str, Any] | None = None,
    analysis_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    grid: RasterGrid = inference["grid"]
    agreement_path = (
        data_directory
        / inference["classification_path"].name.replace(
            "surface_classification",
            "cdl_agreement_state",
        )
    )
    working = _working_path(agreement_path)
    state_counts = Counter()
    matrix = np.zeros((6, 7), dtype=np.int64)
    disagreement_pairs = Counter()
    with rasterio.open(
        inference["classification_path"]
    ) as predicted_source, rasterio.open(
        superclass_path
    ) as cdl_source, rasterio.open(
        working,
        "w",
        **_raster_profile(grid, "uint8", 0),
    ) as sink:
        for window in iter_windows(grid.width, grid.height, 512):
            predicted = predicted_source.read(1, window=window)
            weak = cdl_source.read(1, window=window)
            state = np.zeros(predicted.shape, dtype=np.uint8)
            aoi_valid = raster_aoi_mask(
                predicted_source,
                analysis_aoi_epsg_4326,
                window=window,
            )
            comparable = (weak > 0) & aoi_valid
            unknown = comparable & (predicted == 0)
            agrees = comparable & (predicted == weak)
            disagrees = comparable & (predicted > 0) & (predicted != weak)
            state[agrees] = 1
            state[unknown] = 2
            state[disagrees] = 3
            sink.write(state, 1, window=window)
            state_counts.update(
                {
                    int(code): int(count)
                    for code, count in zip(
                        *np.unique(state, return_counts=True),
                        strict=True,
                    )
                }
            )
            for weak_code in range(1, 7):
                subset = comparable & (weak == weak_code)
                if not np.any(subset):
                    continue
                bincount = np.bincount(
                    predicted[subset],
                    minlength=7,
                )[:7]
                matrix[weak_code - 1] += bincount
                for predicted_code in range(1, 7):
                    if predicted_code != weak_code and bincount[predicted_code]:
                        disagreement_pairs[(weak_code, predicted_code)] += int(
                            bincount[predicted_code]
                        )
    _finalize_cog(working, agreement_path, categorical=True)
    valid_comparison = sum(state_counts[code] for code in (1, 2, 3))
    denominator = max(1, valid_comparison)
    summary = {
        "valid_comparison_pixels": valid_comparison,
        "agreement_fraction": state_counts[1] / denominator,
        "low_confidence_fraction": state_counts[2] / denominator,
        "high_confidence_disagreement_fraction": state_counts[3] / denominator,
        "state_counts": {
            str(code): int(state_counts[code]) for code in range(0, 4)
        },
        "top_cdl_to_prediction_disagreement_pairs": [
            {
                "cdl_class": weak,
                "predicted_class": predicted,
                "pixel_count": count,
            }
            for (weak, predicted), count in sorted(
                disagreement_pairs.items(),
                key=lambda item: (-item[1], item[0]),
            )[:20]
        ],
        "interpretation": (
            "Disagreement is an audit state; it is not automatically a CDL "
            "error or a model error."
        ),
        "pre_sieve_class_counts": inference["pre_sieve_class_counts"],
        "post_sieve_class_counts": inference["post_sieve_class_counts"],
        "analysis_aoi_mask_applied": (
            analysis_aoi_epsg_4326 is not None
        ),
        "aoi_excluded_source_pixels": int(
            inference.get("aoi_excluded_pixels", 0)
        ),
        "analysis_context": (
            dict(analysis_context)
            if analysis_context is not None
            else None
        ),
    }
    matrix_document = {
        "row_order_cdl_superclass": list(range(1, 7)),
        "column_order_prediction": list(range(0, 7)),
        "matrix": matrix.tolist(),
    }
    _write_json(
        analysis_directory / "class_agreement_matrix.json",
        matrix_document,
    )
    with (
        analysis_directory / "class_agreement_matrix.csv"
    ).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["cdl_superclass", *range(0, 7)])
        for code, row in zip(range(1, 7), matrix.tolist(), strict=True):
            writer.writerow([code, *row])
    _write_json(
        analysis_directory / "disagreement_summary.json",
        summary,
    )
    pixel_hectares = grid.pixel_area_m2 / 10_000.0
    with (
        analysis_directory / "class_area_inventory.csv"
    ).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "predicted_class_code",
                "predicted_class_name",
                "pixel_count",
                "hectares",
            ]
        )
        labels = CDL_SURFACE_SUPERCLASSES.class_labels
        for code in range(0, 7):
            count = int(inference["post_sieve_class_counts"][str(code)])
            writer.writerow(
                [code, labels[code], count, round(count * pixel_hectares, 6)]
            )
    return {
        **summary,
        "agreement_path": agreement_path,
        "agreement_validation": _validate_grid_product(
            agreement_path,
            grid,
            dtype="uint8",
            minimum=0,
            maximum=3,
        ),
    }


def execute_classification(
    naip_path: Path,
    cdl_path: Path,
    staging: Path,
    recipe: AgriculturalRecipeV3,
    *,
    year: int,
    cdl_year: int | None = None,
    analysis_aoi_epsg_4326: Mapping[str, Any] | None = None,
    contract_repair: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_cdl_year = cdl_year if cdl_year is not None else year
    _, _, _ = _load_sklearn()
    source_validation = validate_naip_multispectral(naip_path)
    analysis = staging / "analysis" / "classification"
    data = staging / "data"
    analysis.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    labels = prepare_weak_labels(
        cdl_path,
        naip_path,
        data,
        radius=recipe.classification.training_core_radius_cdl_cells,
    )
    samples = extract_training_samples(
        naip_path,
        labels["training_core_path"],
        recipe.classification,
        analysis_aoi_epsg_4326=analysis_aoi_epsg_4326,
    )
    model, metrics, model_receipt = fit_and_evaluate(
        samples,
        recipe.classification,
    )
    repair_provenance = intervention_reference(contract_repair)
    analysis_context = {
        "imagery_year": year,
        "cdl_year": resolved_cdl_year,
        "temporal_mismatch": year != resolved_cdl_year,
        "analysis_aoi_mask_applied": (
            analysis_aoi_epsg_4326 is not None
        ),
        "repair_provenance": repair_provenance,
        "interpretation": (
            "Metrics measure spatial holdout agreement with weak CDL "
            "labels, not independent ground-truth accuracy."
        ),
    }
    model_receipt.update(analysis_context)
    metrics = {**metrics, "analysis_context": analysis_context}
    _write_confusion_matrix(analysis, metrics)
    _write_json(analysis / "weak_label_metrics.json", metrics)
    training_receipt = {
        "sampling_algorithm": (
            "bounded deterministic minimum-priority reservoir by superclass"
        ),
        "seed": recipe.classification.random_seed,
        "window_size": recipe.classification.inference_window_size,
        "maximum_samples_per_class": (
            recipe.classification.maximum_samples_per_class
        ),
        "minimum_training_samples_per_class": (
            recipe.classification.minimum_training_samples_per_class
        ),
        "spatial_holdout_folds": recipe.classification.spatial_holdout_folds,
        "spatial_holdout_fold": recipe.classification.spatial_holdout_fold,
        "eligible_pixels_per_class": samples["eligible_pixels_per_class"],
        "selected_samples_per_class": samples[
            "selected_samples_per_class"
        ],
        "retained_classes": samples["retained_classes"],
        "excluded_classes": samples["excluded_classes"],
        "spatial_blocks": {
            "assignment": (
                "sha256(block_row:block_column:random_seed) modulo folds"
            ),
            "train_block_count": len(samples["train_block_ids"]),
            "holdout_block_count": len(samples["holdout_block_ids"]),
            "overlap_count": 0,
            "train_block_digest_sha256": hashlib.sha256(
                json.dumps(
                    samples["train_block_ids"], separators=(",", ":")
                ).encode("ascii")
            ).hexdigest(),
            "holdout_block_digest_sha256": hashlib.sha256(
                json.dumps(
                    samples["holdout_block_ids"], separators=(",", ":")
                ).encode("ascii")
            ).hexdigest(),
        },
        "source_masks": {
            "policy": "all four NAIP band masks must be valid",
            "invalid_pixels_seen": samples["invalid_source_pixels_seen"],
            "analysis_aoi_mask_applied": analysis_aoi_epsg_4326 is not None,
            "aoi_excluded_source_pixels": samples[
                "aoi_excluded_source_pixels_seen"
            ],
        },
        "imagery_year": year,
        "cdl_year": resolved_cdl_year,
        "temporal_mismatch": year != resolved_cdl_year,
        "repair_provenance": repair_provenance,
        "sample_coordinate_digest_sha256": samples[
            "coordinate_digest_sha256"
        ],
        "feature_matrix_sha256": samples["feature_matrix_sha256"],
        "label_vector_sha256": samples["label_vector_sha256"],
        "holdout_feature_matrix_sha256": samples[
            "holdout_feature_matrix_sha256"
        ],
        "holdout_label_vector_sha256": samples[
            "holdout_label_vector_sha256"
        ],
        "train_sample_total": int(len(samples["y_train"])),
        "holdout_sample_total": int(len(samples["y_holdout"])),
        "raw_coordinates_published": False,
    }
    _write_json(analysis / "training_receipt.json", training_receipt)
    _write_json(analysis / "model_receipt.json", model_receipt)
    feature_contract = {
        "schema_version": "fasterraster.naip-feature-contract/v1",
        "source_band_order": ["red", "green", "blue", "near_infrared"],
        "source_scaling": "uint8 divided by 255 to float32 [0,1]",
        "feature_order": list(recipe.classification.features),
        "epsilon": float(FEATURE_EPSILON),
        "equations": {
            "ndvi": "(NIR - R) / (NIR + R + epsilon)",
            "gndvi": "(NIR - G) / (NIR + G + epsilon)",
            "vari": "(G - R) / (G + R - B + epsilon)",
            "excess_green": "2*G - R - B",
            "brightness": "(R + G + B) / 3",
            "saturation": (
                "(max(R,G,B) - min(R,G,B)) / "
                "(max(R,G,B) + epsilon)"
            ),
        },
        "clamps": {
            "ndvi": [-1.0, 1.0],
            "gndvi": [-1.0, 1.0],
            "vari": [-1.0, 1.0],
            "excess_green": [-2.0, 2.0],
            "brightness": [0.0, 1.0],
            "saturation": [0.0, 1.0],
        },
        "invalid_policy": (
            "any invalid NAIP band or non-finite derived feature invalidates "
            "the source pixel"
        ),
        "display_products": {
            "natural_color": "derived locally as R,G,B",
            "color_infrared": "derived locally as NIR,R,G",
            "numeric_ndvi": "derived from unstretched numeric predictors",
        },
    }
    _write_json(analysis / "feature_contract.json", feature_contract)
    inference = run_inference(
        naip_path,
        model,
        recipe.classification,
        data,
        year=year,
        analysis_aoi_epsg_4326=analysis_aoi_epsg_4326,
    )
    agreement = audit_cdl_agreement(
        inference,
        labels["superclass_path"],
        data,
        analysis,
        analysis_aoi_epsg_4326=analysis_aoi_epsg_4326,
        analysis_context=analysis_context,
    )
    return {
        "source_validation": source_validation,
        "analysis_aoi": {
            "geometry_epsg_4326": analysis_aoi_epsg_4326,
            "mask_applied": analysis_aoi_epsg_4326 is not None,
            "imagery_year": year,
            "cdl_year": resolved_cdl_year,
            "temporal_mismatch": year != resolved_cdl_year,
        },
        "repair_provenance": repair_provenance,
        "label_receipt": {
            key: value
            for key, value in labels.items()
            if key not in {"superclass_path", "training_core_path"}
        },
        "training_receipt": training_receipt,
        "model_receipt": model_receipt,
        "feature_contract": feature_contract,
        "metrics": metrics,
        "inference": {
            key: value
            for key, value in inference.items()
            if key
            not in {
                "grid",
                "classification_path",
                "confidence_path",
            }
        },
        "agreement": {
            key: value
            for key, value in agreement.items()
            if key != "agreement_path"
        },
        "paths": {
            "classification": inference["classification_path"],
            "confidence": inference["confidence_path"],
            "agreement": agreement["agreement_path"],
            "cdl_superclasses": labels["superclass_path"],
            "cdl_training_cores": labels["training_core_path"],
        },
        "mapping": CDL_SURFACE_SUPERCLASSES.as_dict(),
        "mapping_sha256": CDL_SURFACE_SUPERCLASSES.sha256,
        "python_executable": Path(sys.executable).name,
    }
