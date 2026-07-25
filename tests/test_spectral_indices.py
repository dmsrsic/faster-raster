from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from rasterio.shutil import copy as raster_copy

from faster_raster.ag_classification import calculate_features
from faster_raster.contract_repair import build_point_buffer_area
from faster_raster.spectral_indices import (
    BUILTIN_INDEX_REGISTRY,
    DEFAULT_EPSILON,
    INDEX_NODATA,
    IndexCapabilityError,
    SpectralIndexRegistry,
    calculate_index_cog,
    evaluate_builtin_index,
    evaluate_index_expression,
    naip_source_capabilities,
    parse_index_expression,
    source_capabilities_from_raster,
    target_signature_similarity,
    validate_index_compatibility,
)


EXPECTED_IDS = (
    "blue",
    "brightness",
    "excess_green",
    "gndvi",
    "green",
    "green_nir_water_proxy",
    "nbr",
    "ndmi",
    "ndvi",
    "nir",
    "normalized_difference",
    "red",
    "saturation",
    "target_signature_similarity",
    "vari",
)
V3_FEATURES = (
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


def _legacy_features(
    bands: np.ndarray,
    mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    scaled = bands.astype(np.float32) / np.float32(255.0)
    valid = np.all(np.isfinite(scaled), axis=0)
    if mask is not None:
        valid &= np.all(mask, axis=0) if mask.ndim == 3 else mask
    red, green, blue, nir = scaled
    maximum = np.maximum(np.maximum(red, green), blue)
    minimum = np.minimum(np.minimum(red, green), blue)
    epsilon = DEFAULT_EPSILON
    values = (
        red,
        green,
        blue,
        nir,
        np.clip((nir - red) / (nir + red + epsilon), -1.0, 1.0),
        np.clip((nir - green) / (nir + green + epsilon), -1.0, 1.0),
        np.clip(
            (green - red) / (green + red - blue + epsilon),
            -1.0,
            1.0,
        ),
        np.clip(2.0 * green - red - blue, -2.0, 2.0),
        np.clip((red + green + blue) / 3.0, 0.0, 1.0),
        np.clip(
            (maximum - minimum) / (maximum + epsilon),
            0.0,
            1.0,
        ),
    )
    stack = np.stack(values).astype(np.float32, copy=False)
    valid &= np.all(np.isfinite(stack), axis=0)
    stack[:, ~valid] = 0.0
    return stack, valid


def _write_naip(
    tmp_path: Path,
    *,
    order: str = "red,green,blue,near_infrared",
    data_level: str | None = None,
    declared_scale: float | None = None,
    values: np.ndarray | None = None,
) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source_path = tmp_path / "source.tif"
    cog_path = tmp_path / "naip_2023_multispectral.cog.tif"
    rows, columns = np.indices((32, 32))
    bands = (
        np.stack(
            (
                40 + rows,
                60 + columns,
                25 + ((rows + columns) % 20),
                90 + rows + columns,
            )
        ).astype(np.uint8)
        if values is None
        else values
    )
    profile = {
        "driver": "GTiff",
        "width": 32,
        "height": 32,
        "count": 4,
        "dtype": "uint8",
        "crs": "EPSG:4326",
        "transform": Affine(0.001, 0, -83.1, 0, -0.001, 40.1),
        "tiled": True,
        "blockxsize": 16,
        "blockysize": 16,
        "compress": "DEFLATE",
    }
    with rasterio.open(source_path, "w", **profile) as sink:
        sink.write(bands)
        tags = {"FASTERRASTER_BAND_ORDER": order}
        if data_level is not None:
            tags["FASTERRASTER_DATA_LEVEL"] = data_level
        sink.update_tags(**tags)
        if declared_scale is not None:
            sink.scales = (declared_scale,) * 4
        sink.write_mask(np.full((32, 32), 255, dtype=np.uint8))
    raster_copy(
        source_path,
        cog_path,
        driver="COG",
        blocksize=512,
        compress="DEFLATE",
    )
    source_path.unlink()
    return cog_path


def test_builtin_registry_is_ordered_canonical_and_stable() -> None:
    assert BUILTIN_INDEX_REGISTRY.ids == EXPECTED_IDS
    document = BUILTIN_INDEX_REGISTRY.as_dict()
    assert document == BUILTIN_INDEX_REGISTRY.as_dict()
    assert len(document["registry_sha256"]) == 64
    assert [item["index_id"] for item in document["indices"]] == list(
        EXPECTED_IDS
    )
    ndmi = BUILTIN_INDEX_REGISTRY.get("ndmi")
    nbr = BUILTIN_INDEX_REGISTRY.get("nbr")
    wet = BUILTIN_INDEX_REGISTRY.get("green_nir_water_proxy")
    assert ndmi.required_bands == ("nir", "swir1")
    assert nbr.required_bands == ("nir", "swir2")
    assert "NDMI" in wet.unsupported_interpretations
    assert ndmi.content_hash == hashlib.sha256(
        ndmi.as_dict()["canonical_serialization"].encode()
    ).hexdigest()


def test_registry_rejects_duplicate_definitions() -> None:
    definition = BUILTIN_INDEX_REGISTRY.get("ndvi")
    with pytest.raises(ValueError, match="duplicate spectral index"):
        SpectralIndexRegistry((definition, definition))


def test_v3_feature_values_are_numerically_identical_to_beta3() -> None:
    bands = np.array(
        [
            [[0, 40, 255], [80, 100, 120]],
            [[0, 50, 200], [90, 110, 130]],
            [[0, 30, 100], [70, 90, 110]],
            [[0, 90, 240], [160, 140, 120]],
        ],
        dtype=np.uint8,
    )
    mask = np.ones_like(bands, dtype=bool)
    mask[:, 1, 2] = False
    expected, expected_valid = _legacy_features(bands, mask)
    actual, actual_valid = calculate_features(
        bands,
        V3_FEATURES,
        source_mask=mask,
    )
    np.testing.assert_array_equal(actual_valid, expected_valid)
    np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize(
    ("index_id", "formula"),
    (
        ("ndvi", lambda red, green, blue, nir: (nir - red) / (nir + red + 1e-6)),
        ("gndvi", lambda red, green, blue, nir: (nir - green) / (nir + green + 1e-6)),
        (
            "green_nir_water_proxy",
            lambda red, green, blue, nir: (green - nir) / (green + nir + 1e-6),
        ),
    ),
)
def test_builtin_formula_values(index_id, formula) -> None:
    arrays = {
        "red": np.array([[0.2, 0.5]], dtype=np.float32),
        "green": np.array([[0.3, 0.4]], dtype=np.float32),
        "blue": np.array([[0.1, 0.2]], dtype=np.float32),
        "nir": np.array([[0.8, 0.2]], dtype=np.float32),
    }
    actual, valid = evaluate_builtin_index(index_id, arrays)
    expected = np.clip(formula(**arrays), -1.0, 1.0).astype(np.float32)
    np.testing.assert_allclose(actual, expected, rtol=0, atol=2e-7)
    assert valid.all()


def test_naip_capability_rejects_ndmi_and_nbr_structurally() -> None:
    capabilities = naip_source_capabilities()
    assert validate_index_compatibility("ndvi", capabilities)["status"] == (
        "COMPATIBLE"
    )
    for index_id, missing in (("ndmi", ["swir1"]), ("nbr", ["swir2"])):
        with pytest.raises(IndexCapabilityError) as caught:
            validate_index_compatibility(index_id, capabilities)
        assert caught.value.as_dict() == {
            "schema_version": "fasterraster.index-capability-error/v1",
            "status": "INCOMPATIBLE",
            "requested_index": index_id,
            "required_bands": ["nir", missing[0]],
            "available_bands": ["red", "green", "blue", "nir"],
            "missing_bands": missing,
            "source_asset": "naip_multispectral",
            "evidence_state": "complete_semantic_band_evidence",
            "another_configured_source_can_satisfy": False,
        }


def test_source_capability_preserves_scaling_and_rejects_wrong_naip_order(
    tmp_path: Path,
) -> None:
    path = _write_naip(tmp_path)
    capability = source_capabilities_from_raster(
        path,
        source_asset="naip_multispectral",
        source_id="synthetic_naip",
        acquisition_id="fixture-1",
    )
    assert capability.available_bands == ("red", "green", "blue", "nir")
    assert capability.bands[0].scale == pytest.approx(1 / 255)
    assert capability.bands[0].data_level == "raw_digital_number"
    assert capability.source_sha256

    wrong = _write_naip(tmp_path / "wrong", order="blue,green,red,nir")
    with pytest.raises(ValueError, match="NAIP semantic band order"):
        source_capabilities_from_raster(
            wrong,
            source_asset="naip_multispectral",
            source_id="synthetic_naip",
        )

    missing = _write_naip(tmp_path / "missing", order="")
    with pytest.raises(ValueError, match="metadata is missing"):
        source_capabilities_from_raster(
            missing,
            source_asset="naip_multispectral",
            source_id="synthetic_naip",
        )

    rendered = _write_naip(
        tmp_path / "rendered",
        data_level="rendered_rgb",
    )
    with pytest.raises(ValueError, match="radiometric data level"):
        source_capabilities_from_raster(
            rendered,
            source_asset="naip_multispectral",
            source_id="synthetic_naip",
        )

    contradictory = _write_naip(
        tmp_path / "contradictory",
        declared_scale=0.01,
    )
    with pytest.raises(ValueError, match="contradictory scale or offset"):
        source_capabilities_from_raster(
            contradictory,
            source_asset="naip_multispectral",
            source_id="synthetic_naip",
        )


def test_custom_expression_canonicalization_masking_and_safe_division() -> None:
    parsed = parse_index_expression(
        " clip( normalized_difference(nir, red) + abs(green-red), -1, 1 ) "
    )
    same = parse_index_expression(
        "clip(normalized_difference(nir,red)+abs(green-red),-1.0,1.0)"
    )
    assert parsed.canonical_expression == same.canonical_expression
    assert parsed.formula_hash == same.formula_hash
    assert parsed.required_bands == ("red", "green", "nir")
    arrays = {
        "red": np.array([[0.2, 0.0]], dtype=np.float32),
        "green": np.array([[0.3, 0.0]], dtype=np.float32),
        "nir": np.array([[0.8, 0.0]], dtype=np.float32),
    }
    values, valid = evaluate_index_expression(parsed, arrays)
    assert valid.tolist() == [[True, True]]
    assert -1 <= values[0, 0] <= 1

    divided = parse_index_expression("nir / red")
    values, valid = evaluate_index_expression(divided, arrays)
    assert valid.tolist() == [[True, False]]
    assert values[0, 1] == 0


@pytest.mark.parametrize(
    "expression",
    (
        "__import__('os').system('echo unsafe')",
        "red.__class__",
        "(lambda: red)()",
        "[red for red in nir]",
        "red[0]",
        "open('/tmp/unsafe')",
        "np.maximum(red, nir)",
        "red ** 2",
        "import os",
        "red; __import__('os')",
        "f'{red}'",
        "(item for item in red)",
        "def unsafe():\n    return red",
        "globals()",
        "getattr(red, '__class__')",
        "clip(red, low=0, high=1)",
        "abs(maximum(red, __import__('os')))",
        "ｒｅｄ + nir",
        "red + 0x10",
        "red + 1_000",
        "$(touch /tmp/unsafe)",
    ),
)
def test_custom_expression_rejects_executable_or_unbounded_syntax(
    expression: str,
) -> None:
    with pytest.raises(ValueError):
        parse_index_expression(expression)


def test_custom_expression_complexity_limits() -> None:
    with pytest.raises(ValueError, match="maximum length"):
        parse_index_expression("red" + " " * 600)
    with pytest.raises(ValueError, match="maximum AST nodes"):
        parse_index_expression("+".join(["red"] * 60))
    with pytest.raises(ValueError, match="maximum depth"):
        parse_index_expression("-" * 20 + "red")
    with pytest.raises(ValueError, match="finite"):
        parse_index_expression("red + 1e999")
    with pytest.raises(ValueError, match="finite"):
        parse_index_expression("red + " + "9" * 400)
    with pytest.raises(ValueError):
        parse_index_expression("(" * 200 + "red" + ")" * 200)


def test_target_signature_similarity_is_deterministic_and_masked() -> None:
    bands = {
        "red": np.array([[0.4, 0.9]], dtype=np.float32),
        "green": np.array([[0.41, 0.1]], dtype=np.float32),
        "blue": np.array([[0.39, 0.2]], dtype=np.float32),
        "nir": np.array([[0.3, 0.8]], dtype=np.float32),
    }
    score, valid, contract = target_signature_similarity(
        bands,
        {"red": 0.4, "green": 0.4, "blue": 0.4, "nir": 0.3},
        weights={"red": 1, "green": 1.2, "blue": 1, "nir": 0.6},
    )
    assert valid.all()
    assert score[0, 0] > score[0, 1]
    assert contract == target_signature_similarity(
        bands,
        {"red": 0.4, "green": 0.4, "blue": 0.4, "nir": 0.3},
        weights={"red": 1, "green": 1.2, "blue": 1, "nir": 0.6},
    )[2]


def test_windowed_index_cog_is_deterministic_and_aoi_masked(
    tmp_path: Path,
) -> None:
    source = _write_naip(tmp_path)
    aoi = {
        "type": "Polygon",
        "coordinates": [
            [
                [-83.095, 40.095],
                [-83.075, 40.095],
                [-83.075, 40.075],
                [-83.095, 40.075],
                [-83.095, 40.095],
            ]
        ],
    }
    first = calculate_index_cog(
        source,
        tmp_path / "ndvi-a.cog.tif",
        index_id="ndvi",
        analysis_aoi_epsg_4326=aoi,
        window_size=16,
        maximum_quantile_samples=1024,
    )
    second = calculate_index_cog(
        source,
        tmp_path / "ndvi-b.cog.tif",
        index_id="ndvi",
        analysis_aoi_epsg_4326=aoi,
        window_size=16,
        maximum_quantile_samples=1024,
    )
    assert first["output"]["sha256"] == second["output"]["sha256"]
    assert first["statistics"] == second["statistics"]
    assert first["statistics"]["valid_pixel_count"] == 400
    assert first["statistics"]["nodata_pixel_count"] == 624
    assert first["display"]["analytical_values_modified"] is False
    with rasterio.open(tmp_path / "ndvi-a.cog.tif") as output:
        values = output.read(1)
        assert output.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"
        assert np.count_nonzero(values == INDEX_NODATA) == 624


def test_circular_aoi_excludes_envelope_extremes_from_index_statistics(
    tmp_path: Path,
) -> None:
    bands = np.full((4, 32, 32), 50, dtype=np.uint8)
    bands[1] = 75
    bands[2] = 25
    bands[3] = 150
    for rows, columns in (
        (slice(0, 6), slice(0, 6)),
        (slice(0, 6), slice(26, 32)),
        (slice(26, 32), slice(0, 6)),
        (slice(26, 32), slice(26, 32)),
    ):
        bands[0, rows, columns] = 250
        bands[3, rows, columns] = 1
    source = _write_naip(tmp_path, values=bands)
    area = build_point_buffer_area(
        -83.084,
        40.084,
        1000,
        "meters",
        "circle",
    )
    result = calculate_index_cog(
        source,
        tmp_path / "circular-ndvi.cog.tif",
        index_id="ndvi",
        analysis_aoi_epsg_4326=area.analysis_aoi_epsg_4326,
        window_size=16,
        maximum_quantile_samples=1024,
    )
    scaled_nir = 150.0 / 255.0
    scaled_red = 50.0 / 255.0
    expected = (scaled_nir - scaled_red) / (
        scaled_nir + scaled_red + float(DEFAULT_EPSILON)
    )
    assert 0 < result["statistics"]["valid_pixel_count"] < 32 * 32
    assert result["statistics"]["minimum"] == pytest.approx(expected)
    assert result["statistics"]["maximum"] == pytest.approx(expected)
    assert result["statistics"]["mean"] == pytest.approx(expected)
    with rasterio.open(tmp_path / "circular-ndvi.cog.tif") as output:
        assert output.read(1)[0, 0] == INDEX_NODATA
        assert output.dataset_mask()[0, 0] == 0


def test_index_cog_rejects_stale_source_capability_evidence(
    tmp_path: Path,
) -> None:
    source = _write_naip(tmp_path)
    capability = source_capabilities_from_raster(
        source,
        source_asset="naip_multispectral",
        source_id="synthetic_naip",
    )
    stale = replace(capability, source_sha256="0" * 64)
    with pytest.raises(ValueError, match="stale"):
        calculate_index_cog(
            source,
            tmp_path / "stale.cog.tif",
            index_id="ndvi",
            capabilities=stale,
        )
    assert not (tmp_path / "stale.cog.tif").exists()
