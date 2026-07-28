from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds, from_origin

from faster_raster.area_accounting import (
    AreaAccountingError,
    account_categorical_raster_area,
)


GREELEY_BBOX = (-104.80, 40.34, -104.58, 40.51)
GREELEY_GEODESIC_AREA_KM2 = 352.43212


def _write(
    path: Path,
    values: np.ndarray,
    *,
    crs: str,
    transform,
    nodata: int | None = None,
) -> Path:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=values.shape[1],
        height=values.shape[0],
        count=1,
        dtype=str(values.dtype),
        crs=crs,
        transform=transform,
        nodata=nodata,
    ) as sink:
        sink.write(values, 1)
    return path


def test_greeley_web_mercator_area_is_physical_not_nominal(
    tmp_path: Path,
):
    geographic = tmp_path / "greeley-geographic.tif"
    values = np.ones((170, 220), dtype=np.uint8)
    _write(
        geographic,
        values,
        crs="EPSG:4326",
        transform=from_bounds(*GREELEY_BBOX, 220, 170),
    )
    result = account_categorical_raster_area(
        geographic,
        class_codes=[1],
    )
    measured_km2 = (
        result["valid_footprint_area_square_meters"] / 1_000_000.0
    )
    # The fixture is only 220×170, so boundary rasterization dominates the
    # error. Half a percent safely covers that discretization while sharply
    # rejecting the 72% Web Mercator inflation (about 608.851 km²).
    assert measured_km2 == pytest.approx(
        GREELEY_GEODESIC_AREA_KM2,
        rel=0.005,
    )
    assert measured_km2 != pytest.approx(608.851, rel=0.1)
    assert result["area_reference_crs"] == "EPSG:6933"


def test_equal_area_projected_raster_uses_native_grid(
    tmp_path: Path,
):
    path = _write(
        tmp_path / "equal-area.tif",
        np.array([[1, 1], [2, 2]], dtype=np.uint8),
        crs="EPSG:6933",
        transform=from_origin(0, 60, 30, 30),
    )
    result = account_categorical_raster_area(
        path,
        class_codes=[1, 2],
    )
    assert result["area_method"] == "native_declared_equal_area_grid"
    assert result["class_area_square_meters"] == {
        "1": 1800.0,
        "2": 1800.0,
    }


def test_web_mercator_raster_is_reprojected(
    tmp_path: Path,
):
    path = _write(
        tmp_path / "mercator.tif",
        np.ones((16, 16), dtype=np.uint8),
        crs="EPSG:3857",
        transform=from_origin(-11_660_000, 4_950_000, 1.2, 1.2),
    )
    result = account_categorical_raster_area(
        path,
        class_codes=[1],
    )
    assert result["area_method"].startswith(
        "nearest_neighbor_reprojection"
    )
    assert result["area_reference_crs"] == "EPSG:6933"


def test_validity_mask_preserves_unknown_class_and_excludes_nodata(
    tmp_path: Path,
):
    classes = _write(
        tmp_path / "classes.tif",
        np.array([[0, 1], [2, 0]], dtype=np.uint8),
        crs="EPSG:6933",
        transform=from_origin(0, 20, 10, 10),
        nodata=0,
    )
    validity = _write(
        tmp_path / "validity.tif",
        np.array([[1, 1], [1, 0]], dtype=np.uint8),
        crs="EPSG:6933",
        transform=from_origin(0, 20, 10, 10),
        nodata=0,
    )
    result = account_categorical_raster_area(
        classes,
        valid_mask_path=validity,
        class_codes=[0, 1, 2],
    )
    assert result["native_class_pixel_counts"] == {
        "0": 1,
        "1": 1,
        "2": 1,
    }
    assert result["valid_footprint_area_square_meters"] == 300.0
    assert result["summed_class_area_square_meters"] == 300.0
    assert result["area_reconciliation_status"] == "PASS"


def test_repeated_area_accounting_is_deterministic(
    tmp_path: Path,
):
    path = _write(
        tmp_path / "deterministic.tif",
        np.arange(36, dtype=np.uint8).reshape(6, 6) % 3,
        crs="EPSG:4326",
        transform=from_origin(-105, 41, 0.01, 0.01),
    )
    first = account_categorical_raster_area(
        path,
        class_codes=[0, 1, 2],
    )
    second = account_categorical_raster_area(
        path,
        class_codes=[0, 1, 2],
    )
    assert first == second
    assert first["area_accounting_sha256"]


def test_unsupported_valid_class_code_fails_closed(
    tmp_path: Path,
):
    path = _write(
        tmp_path / "unsupported.tif",
        np.array([[1, 9]], dtype=np.uint8),
        crs="EPSG:6933",
        transform=from_origin(0, 10, 10, 10),
    )
    with pytest.raises(
        AreaAccountingError,
        match="unsupported class codes",
    ):
        account_categorical_raster_area(
            path,
            class_codes=[1, 2],
        )
