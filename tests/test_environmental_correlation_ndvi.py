import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from faster_raster.environmental_correlation import (
    EnvironmentalCorrelationError,
    _derive_ndvi_5070,
)


def _write_test_naip(path, red, nir, *, nodata=0):
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=red.shape[1],
        height=red.shape[0],
        count=4,
        dtype="uint8",
        crs="EPSG:3857",
        transform=from_origin(-9_800_000, 4_900_000, 30, 30),
        nodata=nodata,
    ) as dataset:
        dataset.write(red, 1)
        dataset.write(np.full(red.shape, 20, dtype=np.uint8), 2)
        dataset.write(np.full(red.shape, 30, dtype=np.uint8), 3)
        dataset.write(nir, 4)
        dataset.write_mask(np.full(red.shape, 255, dtype=np.uint8))
        dataset.update_tags(
            FASTERRASTER_BAND_ORDER="red,green,blue,near_infrared"
        )


def test_ndvi_uses_dataset_coverage_mask_when_zero_is_declared_nodata(tmp_path):
    source = tmp_path / "naip.tif"
    destination = tmp_path / "ndvi.cog.tif"
    red = np.zeros((16, 16), dtype=np.uint8)
    red[:, 8:] = 40
    nir = np.tile(np.arange(16, dtype=np.uint8), (16, 1)) * 8
    _write_test_naip(source, red, nir)

    receipt = _derive_ndvi_5070(source, destination, resolution_m=30)

    assert receipt["valid_pixel_count"] > 0
    assert receipt["native_coverage_pixel_count"] == 16 * 16
    with rasterio.open(destination) as dataset:
        values = dataset.read(indexes=(1,), masked=False)[0]
        valid = values != dataset.nodata
        assert np.count_nonzero(valid) > 0
        assert np.nanmin(values[valid]) >= -1.0
        assert np.nanmax(values[valid]) <= 1.0


def test_ndvi_rejects_constant_red_or_nir_band(tmp_path):
    source = tmp_path / "constant-naip.tif"
    destination = tmp_path / "ndvi.cog.tif"
    red = np.full((16, 16), 20, dtype=np.uint8)
    nir = np.full((16, 16), 30, dtype=np.uint8)
    _write_test_naip(source, red, nir)

    with pytest.raises(
        EnvironmentalCorrelationError,
        match="naip_red_or_nir_band_is_constant",
    ):
        _derive_ndvi_5070(source, destination, resolution_m=30)
