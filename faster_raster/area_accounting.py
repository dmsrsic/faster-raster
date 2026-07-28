from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import rasterio
from affine import Affine
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.warp import calculate_default_transform
from rasterio.windows import Window


AREA_ACCOUNTING_SCHEMA_VERSION = (
    "fasterraster.categorical-area-accounting/v1"
)
DEFAULT_EQUAL_AREA_CRS = "EPSG:6933"
DEFAULT_RECONCILIATION_TOLERANCE_FRACTION = 0.001
DECLARED_EQUAL_AREA_CRS = frozenset({"EPSG:5070", "EPSG:6933"})


class AreaAccountingError(RuntimeError):
    """Raised when a categorical raster cannot be measured safely."""


def _canonical_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _iter_windows(
    width: int,
    height: int,
    size: int,
) -> Iterable[Window]:
    for row_off in range(0, height, size):
        for col_off in range(0, width, size):
            yield Window(
                col_off=col_off,
                row_off=row_off,
                width=min(size, width - col_off),
                height=min(size, height - row_off),
            )


def _aligned(
    left: rasterio.io.DatasetReader,
    right: rasterio.io.DatasetReader,
) -> bool:
    return (
        left.crs == right.crs
        and left.transform.almost_equals(right.transform)
        and left.width == right.width
        and left.height == right.height
    )


def _free_integer_sentinel(
    dtype: str,
    class_codes: set[int] | None,
) -> int:
    parsed = np.dtype(dtype)
    if not np.issubdtype(parsed, np.integer):
        raise AreaAccountingError(
            "categorical area accounting requires an integer raster"
        )
    limits = np.iinfo(parsed)
    occupied = class_codes or set()
    for candidate in (int(limits.max), int(limits.min)):
        if candidate not in occupied:
            return candidate
    raise AreaAccountingError(
        "categorical raster has no free integer nodata sentinel"
    )


def _count_native(
    source: rasterio.io.DatasetReader,
    validity: rasterio.io.DatasetReader | None,
    *,
    class_codes: set[int] | None,
    window_size: int,
) -> tuple[Counter[int], int]:
    counts: Counter[int] = Counter()
    valid_total = 0
    for window in _iter_windows(source.width, source.height, window_size):
        values = source.read(1, window=window)
        valid = (
            validity.read(1, window=window) > 0
            if validity is not None
            else source.read_masks(1, window=window) > 0
        )
        selected = values[valid]
        if selected.size:
            unique, unique_counts = np.unique(
                selected,
                return_counts=True,
            )
            counts.update(
                {
                    int(code): int(count)
                    for code, count in zip(
                        unique,
                        unique_counts,
                        strict=True,
                    )
                }
            )
        valid_total += int(valid.sum())
    unsupported = (
        sorted(set(counts) - class_codes)
        if class_codes is not None
        else []
    )
    if unsupported:
        raise AreaAccountingError(
            "valid raster cells contain unsupported class codes: "
            + ", ".join(str(code) for code in unsupported)
        )
    return counts, valid_total


def _grid_document(
    *,
    crs: str,
    transform: Affine,
    width: int,
    height: int,
) -> dict[str, Any]:
    return {
        "crs": crs,
        "transform": [float(value) for value in list(transform)[:6]],
        "width": int(width),
        "height": int(height),
        "pixel_area_square_meters": abs(
            float(
                transform.a * transform.e
                - transform.b * transform.d
            )
        ),
    }


def account_categorical_raster_area(
    raster_path: Path,
    *,
    valid_mask_path: Path | None = None,
    class_codes: Iterable[int] | None = None,
    equal_area_crs: str = DEFAULT_EQUAL_AREA_CRS,
    reconciliation_tolerance_fraction: float = (
        DEFAULT_RECONCILIATION_TOLERANCE_FRACTION
    ),
    window_size: int = 2048,
) -> dict[str, Any]:
    """Measure categorical areas on a deterministic equal-area grid.

    Native class counts are counted on the original raster. Physical areas
    are counted independently after nearest-neighbor reprojection to an
    equal-area grid, unless the source already declares a supported
    equal-area CRS. A separate validity raster may preserve valid class zero
    while excluding nodata and pixels outside the analytical mask.
    """

    if reconciliation_tolerance_fraction < 0:
        raise ValueError(
            "area reconciliation tolerance must not be negative"
        )
    if window_size < 1:
        raise ValueError("area accounting window size must be positive")
    expected_codes = (
        {int(code) for code in class_codes}
        if class_codes is not None
        else None
    )
    with rasterio.open(raster_path) as source:
        if source.crs is None:
            raise AreaAccountingError(
                "categorical raster must declare a CRS"
            )
        validity_context = (
            rasterio.open(valid_mask_path)
            if valid_mask_path is not None
            else None
        )
        try:
            if validity_context is not None and not _aligned(
                source,
                validity_context,
            ):
                raise AreaAccountingError(
                    "validity raster is not aligned with categorical raster"
                )
            native_counts, native_valid_count = _count_native(
                source,
                validity_context,
                class_codes=expected_codes,
                window_size=window_size,
            )
            source_crs = source.crs.to_string()
            source_grid = _grid_document(
                crs=source_crs,
                transform=source.transform,
                width=source.width,
                height=source.height,
            )
            if source_crs.upper() in DECLARED_EQUAL_AREA_CRS:
                area_method = "native_declared_equal_area_grid"
                reference_crs = source_crs
                area_grid = source_grid
                area_counts = Counter(native_counts)
            else:
                area_method = (
                    "nearest_neighbor_reprojection_to_equal_area_grid"
                )
                reference_crs = equal_area_crs
                transform, width, height = calculate_default_transform(
                    source.crs,
                    equal_area_crs,
                    source.width,
                    source.height,
                    *source.bounds,
                )
                area_grid = _grid_document(
                    crs=equal_area_crs,
                    transform=transform,
                    width=width,
                    height=height,
                )
                sentinel = _free_integer_sentinel(
                    source.dtypes[0],
                    expected_codes or set(native_counts),
                )
                vrt_options = {
                    "crs": equal_area_crs,
                    "transform": transform,
                    "width": width,
                    "height": height,
                    "resampling": Resampling.nearest,
                }
                area_counts = Counter()
                with WarpedVRT(
                    source,
                    **vrt_options,
                    src_nodata=sentinel,
                    nodata=sentinel,
                ) as area_source:
                    if validity_context is not None:
                        validity_vrt_context = WarpedVRT(
                            validity_context,
                            **vrt_options,
                            nodata=0,
                        )
                    else:
                        validity_vrt_context = WarpedVRT(
                            source,
                            **vrt_options,
                        )
                    with validity_vrt_context as area_validity:
                        for window in _iter_windows(
                            width,
                            height,
                            window_size,
                        ):
                            values = area_source.read(
                                1,
                                window=window,
                            )
                            valid = (
                                area_validity.read(
                                    1,
                                    window=window,
                                )
                                > 0
                                if validity_context is not None
                                else area_validity.read_masks(
                                    1,
                                    window=window,
                                )
                                > 0
                            )
                            selected = values[valid]
                            if selected.size:
                                unique, unique_counts = np.unique(
                                    selected,
                                    return_counts=True,
                                )
                                area_counts.update(
                                    {
                                        int(code): int(count)
                                        for code, count in zip(
                                            unique,
                                            unique_counts,
                                            strict=True,
                                        )
                                    }
                                )
                unsupported = (
                    sorted(set(area_counts) - expected_codes)
                    if expected_codes is not None
                    else []
                )
                if unsupported:
                    raise AreaAccountingError(
                        "equal-area grid contains unsupported class codes: "
                        + ", ".join(str(code) for code in unsupported)
                    )
        finally:
            if validity_context is not None:
                validity_context.close()

    codes = sorted(
        expected_codes
        if expected_codes is not None
        else set(native_counts) | set(area_counts)
    )
    pixel_area_m2 = float(
        area_grid["pixel_area_square_meters"]
    )
    class_area_square_meters = {
        str(code): float(area_counts[code]) * pixel_area_m2
        for code in codes
    }
    class_area_hectares = {
        code: value / 10_000.0
        for code, value in class_area_square_meters.items()
    }
    valid_area_pixels = int(sum(area_counts.values()))
    valid_area_m2 = valid_area_pixels * pixel_area_m2
    summed_area_m2 = float(sum(class_area_square_meters.values()))
    difference_m2 = abs(summed_area_m2 - valid_area_m2)
    difference_fraction = (
        difference_m2 / valid_area_m2 if valid_area_m2 else 0.0
    )
    status = (
        "PASS"
        if difference_fraction
        <= reconciliation_tolerance_fraction
        else "FAIL"
    )
    result = {
        "schema_version": AREA_ACCOUNTING_SCHEMA_VERSION,
        "area_method": area_method,
        "area_units": "hectares",
        "area_reference_crs": reference_crs,
        "categorical_resampling": "nearest",
        "native_class_counts_preserved": True,
        "source_grid": source_grid,
        "area_grid": area_grid,
        "native_valid_pixel_count": native_valid_count,
        "native_class_pixel_counts": {
            str(code): int(native_counts[code]) for code in codes
        },
        "equal_area_class_pixel_counts": {
            str(code): int(area_counts[code]) for code in codes
        },
        "class_area_square_meters": class_area_square_meters,
        "class_area_hectares": class_area_hectares,
        "area_reconciliation_tolerance_fraction": (
            reconciliation_tolerance_fraction
        ),
        "valid_footprint_area_square_meters": valid_area_m2,
        "valid_footprint_area_hectares": valid_area_m2 / 10_000.0,
        "summed_class_area_square_meters": summed_area_m2,
        "summed_class_area_hectares": summed_area_m2 / 10_000.0,
        "area_reconciliation_difference_fraction": difference_fraction,
        "area_reconciliation_status": status,
    }
    result["area_accounting_sha256"] = _canonical_hash(result)
    if status != "PASS":
        raise AreaAccountingError(
            "class-area totals do not reconcile with valid footprint"
        )
    return result
