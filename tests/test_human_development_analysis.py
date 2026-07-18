from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from faster_raster.human_development import (
    CHANGE_CODE_INFO,
    TargetGrid,
    classify_change,
    summarize_change,
)


BASELINE = np.array(
    [
        [250, 11, 21, 22],
        [11, 11, 21, 23],
        [24, 24, 22, 31],
        [31, 41, 82, 90],
    ],
    dtype=np.uint8,
)
COMPARISON = np.array(
    [
        [11, 21, 21, 23],
        [11, 22, 31, 22],
        [24, 23, 21, 31],
        [22, 41, 23, 90],
    ],
    dtype=np.uint8,
)


def test_all_change_codes_have_stable_meanings() -> None:
    before = np.array([250, 11, 21, 11, 21, 21, 24, 11], dtype=np.uint8)
    after = np.array([11, 11, 21, 21, 11, 22, 21, 31], dtype=np.uint8)
    assert classify_change(before, after).tolist() == list(range(8))
    assert set(CHANGE_CODE_INFO) == set(range(8))


def test_approved_four_by_four_counts_and_areas() -> None:
    codes = classify_change(BASELINE, COMPARISON)
    assert np.bincount(codes.ravel(), minlength=8).tolist() == [1, 4, 2, 4, 1, 1, 3, 0]
    summary = summarize_change(BASELINE, COMPARISON, pixel_area_m2=900.0, elapsed_years=10)
    assert summary["valid_comparison"]["square_metres"] == 13_500.0
    assert summary["invalid_comparison"]["square_metres"] == 900.0
    assert summary["gross_development_gain"]["square_metres"] == 3_600.0
    assert summary["apparent_development_loss"]["square_metres"] == 900.0
    assert summary["net_development_change"]["square_metres"] == 2_700.0
    assert summary["development_intensity_increase"]["square_metres"] == 900.0
    assert summary["development_intensity_decrease"]["square_metres"] == 2_700.0
    assert summary["annualized"] == {
        "gross_gain_square_metres_per_year": 360.0,
        "apparent_loss_square_metres_per_year": 90.0,
        "net_change_square_metres_per_year": 270.0,
    }


def test_target_grid_fingerprint_is_deterministic() -> None:
    grid_a = TargetGrid("EPSG:5070", from_origin(500_000, 2_000_000, 30, 30), 4, 4, 30.0, (500_000, 2_000_000))
    grid_b = TargetGrid("EPSG:5070", from_origin(500_000, 2_000_000, 30, 30), 4, 4, 30.0, (500_000, 2_000_000))
    assert grid_a.fingerprint == grid_b.fingerprint
    assert grid_a.pixel_area_m2 == 900.0


def test_python312_geotiff_window_roundtrip(tmp_path: Path) -> None:
    source = tmp_path / "source.tif"
    copied = tmp_path / "copied.tif"
    profile = {
        "driver": "GTiff",
        "width": 4,
        "height": 4,
        "count": 1,
        "dtype": "uint8",
        "crs": "EPSG:5070",
        "transform": from_origin(500_000, 2_000_000, 30, 30),
        "nodata": 250,
    }
    values = np.arange(16, dtype=np.uint8).reshape(4, 4)
    with rasterio.open(source, "w", **profile) as sink:
        sink.write(values, 1)
    with rasterio.open(source) as dataset:
        assert dataset.read(1, window=((1, 3), (1, 4))).tolist() == [[5, 6, 7], [9, 10, 11]]
        copied_profile = dataset.profile
        copied_values = dataset.read(1)
    with rasterio.open(copied, "w", **copied_profile) as sink:
        sink.write(copied_values, 1)
    with rasterio.open(copied) as dataset:
        assert dataset.transform == profile["transform"]
        assert dataset.crs.to_string() == "EPSG:5070"
        assert dataset.dtypes == ("uint8",)
        assert dataset.nodata == 250
        assert np.array_equal(dataset.read(1), values)
