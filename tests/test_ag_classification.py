from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from PIL import Image
from pydantic import ValidationError
from rasterio.enums import ColorInterp
from rasterio.shutil import copy as raster_copy
from rasterio.transform import from_origin

from faster_raster.ag_classification import (
    ClassificationError,
    calculate_features,
    execute_classification,
    extract_training_samples,
    map_cdl_superclasses,
    prepare_weak_labels,
    run_inference,
    spatial_fold,
    training_core_mask,
    validate_naip_multispectral,
)
from faster_raster.ag_classification_acquisition import (
    RawNaipEvidenceError,
    validate_raw_naip_acquisition_evidence,
)
from faster_raster.ag_classification_contracts import (
    CDL_SURFACE_SUPERCLASSES,
)
from faster_raster.ag_classification_publication import (
    _disagreement_outline,
    _render_numeric_ndvi,
    interpret_naip_date_evidence,
    render_classification_audit,
)
from faster_raster.ag_recipes import (
    AgriculturalRecipeV3,
    ClassificationSpec,
    load_recipe,
)
from faster_raster.development_sources import CDL_CLASS_LABELS


ROOT = Path(__file__).resolve().parent.parent
RECIPE_PATH = ROOT / "recipes/ag/naip_cdl_classification_audit.json"
TRANSFORM = from_origin(500_000, 4_000_000, 1, 1)
CRS = "EPSG:32612"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_cog(
    path: Path,
    values: np.ndarray,
    *,
    transform=TRANSFORM,
    crs: str = CRS,
    mask: np.ndarray | None = None,
) -> Path:
    array = np.asarray(values)
    if array.ndim == 2:
        array = array[np.newaxis, ...]
    working = path.with_name(f".{path.name}.working.tif")
    profile = {
        "driver": "GTiff",
        "width": array.shape[2],
        "height": array.shape[1],
        "count": array.shape[0],
        "dtype": str(array.dtype),
        "crs": crs,
        "transform": transform,
        "tiled": True,
        "blockxsize": 16,
        "blockysize": 16,
        "compress": "DEFLATE",
    }
    with rasterio.open(working, "w", **profile) as sink:
        sink.write(array)
        if array.shape[0] == 4:
            sink.colorinterp = (ColorInterp.undefined,) * 4
            sink.update_tags(
                FASTERRASTER_BAND_ORDER="red,green,blue,near_infrared"
            )
        sink.write_mask(
            np.full(array.shape[1:], 255, dtype=np.uint8)
            if mask is None
            else np.where(mask, 255, 0).astype(np.uint8)
        )
    raster_copy(
        working,
        path,
        driver="COG",
        compress="DEFLATE",
        blocksize=512,
        overview_resampling="nearest",
    )
    working.unlink()
    return path


def _classification_spec(**updates) -> ClassificationSpec:
    raw = json.loads(RECIPE_PATH.read_text(encoding="utf-8"))["classification"]
    raw.update(
        {
            "maximum_samples_per_class": 400,
            "minimum_training_samples_per_class": 20,
            "spatial_holdout_folds": 2,
            "spatial_holdout_fold": 0,
            "inference_window_size": 16,
            "n_estimators": 32,
            "max_depth": 12,
            "min_samples_leaf": 2,
        }
    )
    raw.update(updates)
    return ClassificationSpec.model_validate(raw)


def _synthetic_sources(tmp_path: Path) -> tuple[Path, Path]:
    height = width = 96
    block = 16
    labels = np.zeros((height, width), dtype=np.uint8)
    holdout_blocks: list[tuple[int, int]] = []
    train_blocks: list[tuple[int, int]] = []
    for block_row in range(height // block):
        for block_column in range(width // block):
            target = (
                holdout_blocks
                if spatial_fold(block_row, block_column, 20260724, 2) == 0
                else train_blocks
            )
            target.append((block_row, block_column))
    assert len(holdout_blocks) >= 6
    assert len(train_blocks) >= 6

    assignments: list[tuple[tuple[int, int], int]] = []
    for code in range(1, 7):
        assignments.append((holdout_blocks.pop(), code))
        assignments.append((train_blocks.pop(), code))
    remaining = holdout_blocks + train_blocks
    assignments.extend(
        (position, index % 6 + 1)
        for index, position in enumerate(remaining)
    )
    for (block_row, block_column), code in assignments:
        labels[
            block_row * block : (block_row + 1) * block,
            block_column * block : (block_column + 1) * block,
        ] = code

    cdl_representatives = np.asarray([0, 1, 61, 82, 123, 141, 111], dtype=np.uint8)
    cdl = cdl_representatives[labels]
    signatures = np.asarray(
        [
            [0, 0, 0, 0],
            [60, 82, 42, 188],
            [175, 132, 84, 108],
            [132, 150, 126, 142],
            [108, 105, 116, 96],
            [48, 134, 54, 178],
            [34, 55, 88, 30],
        ],
        dtype=np.int16,
    )
    bands = np.moveaxis(signatures[labels], -1, 0)
    row_variation = (np.arange(height)[:, None] % 5) - 2
    column_variation = (np.arange(width)[None, :] % 3) - 1
    bands = np.clip(
        bands + row_variation[np.newaxis, ...] + column_variation[np.newaxis, ...],
        1,
        254,
    ).astype(np.uint8)
    return (
        _write_cog(tmp_path / "naip_2023_multispectral.cog.tif", bands),
        _write_cog(tmp_path / "cdl_2023_classes.cog.tif", cdl),
    )


def test_v3_recipe_and_mapping_contract_are_complete_and_stable():
    recipe = load_recipe(RECIPE_PATH)
    assert isinstance(recipe, AgriculturalRecipeV3)
    assert recipe.recipe_id == RECIPE_PATH.stem
    assert recipe.required_assets == ["naip_multispectral", "cdl_classes"]

    mapping = CDL_SURFACE_SUPERCLASSES
    mapped = set(mapping.mapped_cdl_codes)
    excluded = set(mapping.excluded_valid_codes)
    assert not mapped & excluded
    assert mapped | excluded == set(CDL_CLASS_LABELS)
    assert not mapped & set(mapping.invalid_codes)
    assert mapping.sha256 == hashlib.sha256(
        json.dumps(
            mapping.as_dict(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert mapping.sha256 == (
        "aff255c8995bd2088fbadeddd05a4d1ba9b4e122bc7378daa9289bcd5d5b929d"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("backend", "xgboost"),
        ("random_seed", -1),
        ("confidence_threshold", 1.01),
        ("maximum_samples_per_class", 0),
        ("features", ["red", "display_ndvi"]),
        ("n_jobs", 2),
    ],
)
def test_invalid_classification_fields_fail_validation(field, value):
    raw = json.loads(RECIPE_PATH.read_text(encoding="utf-8"))
    raw["classification"][field] = value
    with pytest.raises(ValidationError):
        AgriculturalRecipeV3.model_validate(raw)


def test_feature_equations_mask_and_denominator_stability():
    bands = np.asarray(
        [
            [[51, 0], [255, 0]],
            [[102, 0], [51, 0]],
            [[25, 0], [0, 0]],
            [[204, 0], [255, 0]],
        ],
        dtype=np.uint8,
    )
    features = [
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
    source_mask = np.ones((4, 2, 2), dtype=bool)
    source_mask[:, 1, 1] = False
    stack, valid = calculate_features(bands, features, source_mask=source_mask)
    red, green, blue, nir = (value / 255 for value in (51, 102, 25, 204))
    expected = np.asarray(
        [
            red,
            green,
            blue,
            nir,
            (nir - red) / (nir + red + 1e-6),
            (nir - green) / (nir + green + 1e-6),
            (green - red) / (green + red - blue + 1e-6),
            2 * green - red - blue,
            (red + green + blue) / 3,
            (max(red, green, blue) - min(red, green, blue))
            / (max(red, green, blue) + 1e-6),
        ],
        dtype=np.float32,
    )
    np.testing.assert_allclose(stack[:, 0, 0], expected, rtol=0, atol=2e-6)
    assert np.all(np.isfinite(stack))
    assert not valid[1, 1]
    assert np.all(stack[:, 1, 1] == 0)


def test_exact_mapping_and_training_cores_exclude_boundaries():
    values = np.asarray(
        [[1, 61, 82, 123, 141, 111, 87, 0, 81, 255]],
        dtype=np.uint8,
    )
    assert map_cdl_superclasses(values).tolist() == [
        [1, 2, 3, 4, 5, 6, 0, 0, 0, 0]
    ]
    labels = np.ones((7, 7), dtype=np.uint8)
    labels[:, 4:] = 2
    cores = training_core_mask(labels, radius=1)
    assert np.all(cores[1:-1, 1:3] == 1)
    assert np.all(cores[1:-1, 5:-1] == 2)
    assert np.all(cores[:, 3:5] == 0)
    assert np.all(cores[[0, -1], :] == 0)


def test_raw_four_band_validation_passes_and_three_band_fails(tmp_path):
    four = _write_cog(
        tmp_path / "naip_2023_multispectral.cog.tif",
        np.full((4, 16, 16), 50, dtype=np.uint8),
    )
    validation = validate_naip_multispectral(four)
    assert validation["band_order"] == ["red", "green", "blue", "near_infrared"]
    assert validation["sha256"] == _sha256(four)

    three = _write_cog(
        tmp_path / "naip_2023_three_band.cog.tif",
        np.full((3, 16, 16), 50, dtype=np.uint8),
    )
    with pytest.raises(ClassificationError, match="band_count_3_is_not_4"):
        validate_naip_multispectral(three)


def test_raw_acquisition_evidence_rejects_rendering_and_accepts_band_ids():
    manifest = {
        "naip": {"requested_year": 2023, "catalog_match_count": 2},
        "layers": [{"name": "naip_multispectral", "band_ids": [0, 1, 2, 3]}],
        "requests": [
            {
                "label": "naip_multispectral_r000_c000",
                "parameters": {
                    "bandIds": "0,1,2,3",
                    "mosaicRule": '{"where":"Year = 2023"}',
                },
            }
        ],
    }
    assert validate_raw_naip_acquisition_evidence(
        manifest, requested_year=2023
    )["rendering_rule"] is None
    manifest["requests"][0]["parameters"]["renderingRule"] = "NaturalColor"
    with pytest.raises(RawNaipEvidenceError, match="contains_rendering_rule"):
        validate_raw_naip_acquisition_evidence(manifest, requested_year=2023)


def test_label_alignment_sampling_and_spatial_split_are_deterministic(tmp_path):
    naip, cdl = _synthetic_sources(tmp_path)
    labels = prepare_weak_labels(cdl, naip, tmp_path / "data", radius=1)
    spec = _classification_spec()
    first = extract_training_samples(naip, labels["training_core_path"], spec)
    second = extract_training_samples(naip, labels["training_core_path"], spec)
    assert first["coordinate_digest_sha256"] == second["coordinate_digest_sha256"]
    assert first["feature_matrix_sha256"] == second["feature_matrix_sha256"]
    assert first["label_vector_sha256"] == second["label_vector_sha256"]
    assert all(
        values["selected"] <= spec.maximum_samples_per_class
        for values in first["selected_samples_per_class"].values()
    )
    assert set(first["retained_classes"]) == set(range(1, 7))
    train_blocks = set(map(tuple, first["train_block_ids"]))
    holdout_blocks = set(map(tuple, first["holdout_block_ids"]))
    assert train_blocks
    assert holdout_blocks
    assert train_blocks.isdisjoint(holdout_blocks)

    with rasterio.open(labels["superclass_path"]) as warped:
        assert warped.transform == TRANSFORM
        assert warped.width == 96 and warped.height == 96
        assert set(np.unique(warped.read(1))) <= set(range(7))


def test_support_failure_is_explicit(tmp_path):
    naip = _write_cog(
        tmp_path / "naip_2023_multispectral.cog.tif",
        np.full((4, 32, 32), 100, dtype=np.uint8),
    )
    cores = _write_cog(
        tmp_path / "cores.cog.tif",
        np.ones((32, 32), dtype=np.uint8),
    )
    with pytest.raises(
        ClassificationError,
        match="spatial validation cannot be formed",
    ):
        extract_training_samples(
            naip,
            cores,
            _classification_spec(
                maximum_samples_per_class=600,
                minimum_training_samples_per_class=500,
            ),
        )


class _LowConfidenceModel:
    classes_ = np.asarray([1, 2], dtype=np.uint8)

    def predict_proba(self, values):
        return np.tile(np.asarray([[0.55, 0.45]]), (len(values), 1))


def test_confidence_threshold_maps_low_confidence_pixels_to_zero(tmp_path):
    naip = _write_cog(
        tmp_path / "naip_2023_multispectral.cog.tif",
        np.full((4, 16, 16), 100, dtype=np.uint8),
    )
    result = run_inference(
        naip,
        _LowConfidenceModel(),
        _classification_spec(confidence_threshold=0.60),
        tmp_path / "data",
        year=2023,
    )
    with rasterio.open(result["classification_path"]) as classified:
        assert np.all(classified.read(1) == 0)
    with rasterio.open(result["confidence_path"]) as confidence:
        assert np.all(confidence.read(1) == 55)
    assert result["pre_sieve_class_counts"] == result["post_sieve_class_counts"]


@pytest.mark.parametrize(
    ("raw", "expected", "status"),
    [
        (
            [1684627200000],
            "2023-05-21",
            "parsed_unix_epoch_milliseconds",
        ),
        (
            ["1684627200"],
            "2023-05-21",
            "parsed_unix_epoch_seconds",
        ),
        (None, None, "no_evidence"),
        (["not-an-epoch"], None, "unparsed"),
    ],
)
def test_naip_date_evidence_is_interpreted_in_utc(raw, expected, status):
    interpreted = interpret_naip_date_evidence(raw)
    assert interpreted["raw_naip_evidence"] == raw
    assert interpreted["interpreted_naip_date_utc"] == expected
    assert interpreted["naip_evidence_interpretation"] == status


def test_disagreement_rendering_is_outer_outline_only():
    agreement = np.zeros((21, 21), dtype=np.uint8)
    agreement[6:15, 6:15] = 3
    rendered = np.asarray(_disagreement_outline(agreement, 1))
    assert rendered[10, 10, 3] == 0
    assert rendered[5, 10, 3] > 0
    assert rendered[4, 10, 3] > 0
    assert int(rendered[..., 3].max()) < 255

    isolated = np.zeros((21, 21), dtype=np.uint8)
    isolated[10, 10] = 3
    assert not np.asarray(_disagreement_outline(isolated, 9))[..., 3].any()


def test_numeric_ndvi_display_is_zero_centered_and_display_only():
    ndvi = np.asarray([[-0.6, 0.0, 0.7]], dtype=np.float32)
    image, contract = _render_numeric_ndvi(
        ndvi,
        np.ones(ndvi.shape, dtype=bool),
    )
    colors = np.asarray(image)[0]
    assert tuple(colors[0]) != tuple(colors[1])
    assert tuple(colors[1]) != tuple(colors[2])
    assert colors[0, 0] > colors[0, 1]
    assert colors[2, 1] > colors[2, 0]
    assert contract["method"] == "deterministic_zero_centered_percentile_stretch"
    assert contract["numeric_ndvi_modified"] is False


def test_synthetic_classification_and_4k_publication_end_to_end(tmp_path):
    naip, cdl = _synthetic_sources(tmp_path)
    raw = json.loads(RECIPE_PATH.read_text(encoding="utf-8"))
    raw["classification"].update(_classification_spec().model_dump())
    recipe = AgriculturalRecipeV3.model_validate(raw)
    staging = tmp_path / "synthetic_handoff"
    result = execute_classification(naip, cdl, staging, recipe, year=2023)

    with rasterio.open(naip) as source:
        expected = (source.crs, source.transform, source.width, source.height)
    ranges = {
        "classification": (0, 6),
        "confidence": (0, 100),
        "agreement": (0, 3),
    }
    for name, (minimum, maximum) in ranges.items():
        path = result["paths"][name]
        with rasterio.open(path) as output:
            assert (output.crs, output.transform, output.width, output.height) == expected
            values = output.read(1)
            assert int(values.min()) >= minimum
            assert int(values.max()) <= maximum
            assert output.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG"

    preview = (
        staging
        / "preview/naip_cdl_classification_audit"
        / "naip_cdl_classification_audit_4k.png"
    )
    preview, publication = render_classification_audit(
        preview,
        naip_path=naip,
        classification_result=result,
        recipe=recipe,
        year=2023,
        acquisition_evidence={"acquisition_date_evidence": [1684627200000]},
        network_bytes=0,
        reused_bytes=naip.stat().st_size + cdl.stat().st_size,
    )
    assert Image.open(preview).size == (3840, 2160)
    assert len(Image.open(preview).getcolors(maxcolors=10_000_000) or []) > 100
    assert len(publication["panels"]) == 5
    assert publication["legacy_universal_cdl_boundary_overlay_used"] is False
    assert publication["disagreement_rendering_mode"] == (
        "display_only_outer_outline_with_dark_halo"
    )
    assert publication["outline_width_pixels"] == 1
    assert publication["analytical_rasters_modified"] is False
    assert publication["visual_minimum_mapping_unit_pixels"] == 9
    assert publication["raw_naip_evidence"] == [1684627200000]
    assert publication["interpreted_naip_date_utc"] == "2023-05-21"
    legend = json.loads(
        (preview.parent / "classification_legend.json").read_text(encoding="utf-8")
    )
    assert legend["visual_minimum_mapping_unit_pixels"] == 9
    assert legend["analytical_rasters_modified"] is False
    assert legend["predicted_class_display"]["display_generalization_applied"] is False
    assert not any(".staging-" in path for path in publication.values() if isinstance(path, str))
    metrics = json.loads(
        (staging / "analysis/classification/weak_label_metrics.json").read_text(
            encoding="utf-8"
        )
    )
    assert metrics["metric_family"] == "weak-label spatial holdout agreement"
    assert result["metrics"] == metrics
    assert set(result["metrics"]["class_order"]) == set(range(1, 7))
