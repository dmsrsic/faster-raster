from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from PIL import Image
from rasterio.shutil import copy as raster_copy

from faster_raster.ag_classification_contracts import (
    CDL_SURFACE_SUPERCLASSES,
)
from faster_raster.ag_assets import AssetDecision, AssetRecord
from faster_raster.ag_execution import SelectionReviewReady, execute_recipe
from faster_raster.ag_recipes import AgriculturalRecipeV4
from faster_raster.contract_repair import (
    ClassificationRuntimeRequest,
    RecoverableContractFailure,
    build_intervention_record,
)
from faster_raster.hybrid_execution import (
    HybridExecutionError,
    _point_calibration_samples,
    execute_hybrid_classification,
)
from faster_raster.hybrid_publication import (
    render_hybrid_classification_audit,
)
from faster_raster.preview_open import inspect_handoff
from faster_raster.spectral_indices import calculate_index_cog
from scripts.derive_classification_publication import derive_publication


ROOT = Path(__file__).resolve().parents[1]
V4_PATH = (
    ROOT / "recipes/ag/naip_cdl_index_hybrid_classification_audit.json"
)


def _cog(
    path: Path,
    values: np.ndarray,
    *,
    dtype: str,
    nodata: int | float,
    tags: dict[str, str] | None = None,
) -> Path:
    source = path.parent / f".{path.name}.source.tif"
    count, height, width = (
        values.shape if values.ndim == 3 else (1, *values.shape)
    )
    profile = {
        "driver": "GTiff",
        "width": width,
        "height": height,
        "count": count,
        "dtype": dtype,
        "crs": "EPSG:4326",
        "transform": Affine(0.001, 0, -83.1, 0, -0.001, 40.1),
        "nodata": nodata,
        "tiled": True,
        "blockxsize": 16,
        "blockysize": 16,
        "compress": "DEFLATE",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(source, "w", **profile) as sink:
        if values.ndim == 3:
            sink.write(values)
        else:
            sink.write(values, 1)
        if tags:
            sink.update_tags(**tags)
        sink.write_mask(np.full((height, width), 255, dtype=np.uint8))
    raster_copy(
        source,
        path,
        driver="COG",
        blocksize=512,
        compress="DEFLATE",
        overview_resampling="nearest" if dtype == "uint8" else "average",
    )
    source.unlink()
    return path


def _inputs(tmp_path: Path) -> tuple[Path, dict]:
    height = width = 32
    general = np.zeros((height, width), dtype=np.uint8)
    general[:16, :] = 1
    general[16:24, :] = 5
    general[24:, :] = 6

    red = np.full((height, width), 70, dtype=np.uint8)
    green = np.full((height, width), 75, dtype=np.uint8)
    blue = np.full((height, width), 50, dtype=np.uint8)
    nir = np.full((height, width), 90, dtype=np.uint8)
    nir[:24, :] = 210
    red[:24, :] = 45
    green[:24, :] = 80
    red[24:, :] = 30
    green[24:, :] = 90
    nir[24:, :] = 20
    naip = _cog(
        tmp_path / "naip_2023_multispectral.cog.tif",
        np.stack((red, green, blue, nir)),
        dtype="uint8",
        nodata=0,
        tags={
            "FASTERRASTER_BAND_ORDER": (
                "red,green,blue,near_infrared"
            )
        },
    )
    general_path = _cog(
        tmp_path / "naip_2023_surface_classification.cog.tif",
        general,
        dtype="uint8",
        nodata=0,
    )
    confidence = _cog(
        tmp_path / "naip_2023_classification_confidence.cog.tif",
        np.full((height, width), 80, dtype=np.uint8),
        dtype="uint8",
        nodata=0,
    )
    agreement = _cog(
        tmp_path / "naip_2023_cdl_agreement_state.cog.tif",
        np.ones((height, width), dtype=np.uint8),
        dtype="uint8",
        nodata=0,
    )
    cdl_superclasses = _cog(
        tmp_path / "cdl_superclasses.cog.tif",
        general,
        dtype="uint8",
        nodata=0,
    )
    return naip, {
        "paths": {
            "classification": general_path,
            "confidence": confidence,
            "agreement": agreement,
            "cdl_superclasses": cdl_superclasses,
        },
        "metrics": {
            "overall_agreement": 0.8,
            "interpretation": "weak-label agreement, not accuracy",
        },
        "mapping": CDL_SURFACE_SUPERCLASSES.as_dict(),
    }


def _automatic_inputs(tmp_path: Path) -> tuple[Path, dict]:
    height = width = 128
    block = 16
    rows, columns = np.indices((height, width))
    positive = (
        ((rows // block) + (columns // block)) % 2 == 0
    )
    general_values = np.where(positive, 5, 2).astype(np.uint8)
    red = np.where(positive, 35, 120).astype(np.uint8)
    green = np.where(positive, 80, 70).astype(np.uint8)
    blue = np.where(positive, 45, 75).astype(np.uint8)
    nir = np.where(positive, 210, 65).astype(np.uint8)
    # One spatial block is intentionally confounding.
    red[:block, :block] = 120
    nir[:block, :block] = 65
    naip = _cog(
        tmp_path / "naip_2023_multispectral.cog.tif",
        np.stack((red, green, blue, nir)),
        dtype="uint8",
        nodata=0,
        tags={
            "FASTERRASTER_BAND_ORDER": (
                "red,green,blue,near_infrared"
            )
        },
    )
    paths = {
        "classification": _cog(
            tmp_path / "naip_2023_surface_classification.cog.tif",
            general_values,
            dtype="uint8",
            nodata=0,
        ),
        "confidence": _cog(
            tmp_path / "naip_2023_classification_confidence.cog.tif",
            np.full((height, width), 80, dtype=np.uint8),
            dtype="uint8",
            nodata=0,
        ),
        "agreement": _cog(
            tmp_path / "naip_2023_cdl_agreement_state.cog.tif",
            np.ones((height, width), dtype=np.uint8),
            dtype="uint8",
            nodata=0,
        ),
        "cdl_superclasses": _cog(
            tmp_path / "cdl_superclasses.cog.tif",
            general_values,
            dtype="uint8",
            nodata=0,
        ),
    }
    return naip, {
        "paths": paths,
        "metrics": {
            "overall_agreement": 0.8,
            "interpretation": "weak-label agreement, not accuracy",
        },
        "mapping": CDL_SURFACE_SUPERCLASSES.as_dict(),
    }


def _recipe() -> AgriculturalRecipeV4:
    return AgriculturalRecipeV4.model_validate_json(
        V4_PATH.read_text(encoding="utf-8")
    )


def test_windowed_hybrid_execution_writes_complete_deterministic_products(
    tmp_path: Path,
) -> None:
    naip, general = _inputs(tmp_path / "inputs")
    first = execute_hybrid_classification(
        naip,
        general,
        tmp_path / "first",
        _recipe(),
    )
    second = execute_hybrid_classification(
        naip,
        general,
        tmp_path / "second",
        _recipe(),
    )
    for index_id in ("ndvi", "gndvi", "green_nir_water_proxy"):
        first_path = first["paths"]["indices"][index_id]
        second_path = second["paths"]["indices"][index_id]
        assert first_path.is_file()
        assert first["index_statistics"][index_id] == second[
            "index_statistics"
        ][index_id]
        assert first_path.read_bytes() == second_path.read_bytes()
    assert first["registry"]["registry_sha256"] == second["registry"][
        "registry_sha256"
    ]
    assert first["hybrid_receipt"] == second["hybrid_receipt"]
    assert first["selection_receipt"]["status"] == "USER_DEFINED"

    final_path = first["paths"]["final_hybrid_classification"]
    decision_path = first["paths"]["hybrid_decision_state"]
    with rasterio.open(final_path) as final, rasterio.open(
        decision_path
    ) as decision:
        final_values = final.read(1)
        decision_values = decision.read(1)
        assert final.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"
        assert set(np.unique(final_values)) == {20, 21}
        assert set(np.unique(decision_values)) == {2}
    general_path = first["paths"]["general_classification"]
    with rasterio.open(general_path) as source:
        assert set(np.unique(source.read(1))) == {1, 5, 6}

    required = (
        "analysis/indices/index_registry.json",
        "analysis/indices/index_capability_report.json",
        "analysis/indices/index_plan.json",
        "analysis/indices/index_statistics.json",
        "analysis/indices/index_candidate_ranking.json",
        "analysis/indices/specialist_class_rules.json",
        "analysis/indices/specialist_overlap_matrix.json",
        "analysis/indices/hybrid_class_inventory.json",
        "analysis/indices/index_validation_metrics.json",
        "receipts/index_calculation_receipt.json",
        "receipts/index_selection_receipt.json",
        "receipts/specialist_classification_receipt.json",
        "receipts/hybrid_classification_receipt.json",
    )
    root = tmp_path / "first"
    assert all((root / relative).is_file() for relative in required)
    index_receipt = json.loads(
        (root / "receipts/index_calculation_receipt.json").read_text()
    )
    for item in index_receipt["indices"]:
        assert item["output"]["path"].startswith("data/indices/")
        assert (root / item["output"]["path"]).is_file()
    rules = json.loads(
        (root / "analysis/indices/specialist_class_rules.json").read_text()
    )
    for rule in rules["classes"]:
        for key in ("score_output", "candidate_output"):
            assert rule[key]["path"].startswith("data/specialists/")
            assert (root / rule[key]["path"]).is_file()
    hybrid_receipt = json.loads(
        (root / "receipts/hybrid_classification_receipt.json").read_text()
    )
    for output in hybrid_receipt["outputs"].values():
        assert output["path"].startswith("data/")
        assert (root / output["path"]).is_file()


def test_custom_expression_cog_uses_canonical_formula_and_same_grid(
    tmp_path: Path,
) -> None:
    naip, _ = _inputs(tmp_path / "inputs")
    receipt = calculate_index_cog(
        naip,
        tmp_path / "custom.cog.tif",
        index_id="nir_red_ratio",
        expression="nir / (red + 0.01)",
        window_size=16,
    )
    assert receipt["index"]["definition_version"] == "custom-expression-v1"
    assert receipt["index"]["expression"]["required_bands"] == ["red", "nir"]
    assert receipt["output"]["cog_validation"] == "PASS"
    with rasterio.open(tmp_path / "custom.cog.tif") as output:
        assert output.dtypes == ("float32",)
        assert output.tags()["FASTERRASTER_INDEX_ID"] == "nir_red_ratio"


def test_hybrid_publication_is_4k_deterministic_and_display_only(
    tmp_path: Path,
) -> None:
    naip, general = _inputs(tmp_path / "inputs")
    recipe = _recipe()
    result = execute_hybrid_classification(
        naip,
        general,
        tmp_path / "handoff",
        recipe,
    )
    first, first_receipt = render_hybrid_classification_audit(
        tmp_path / "handoff/preview/hybrid/first_4k.png",
        naip_path=naip,
        general_result=general,
        hybrid_result=result,
        recipe=recipe,
        year=2023,
        cdl_year=2023,
        analysis_aoi_epsg_4326=None,
        network_bytes=0,
        reused_bytes=1234,
    )
    second, second_receipt = render_hybrid_classification_audit(
        tmp_path / "handoff/preview/hybrid/second_4k.png",
        naip_path=naip,
        general_result=general,
        hybrid_result=result,
        recipe=recipe,
        year=2023,
        cdl_year=2023,
        analysis_aoi_epsg_4326=None,
        network_bytes=0,
        reused_bytes=1234,
    )
    assert Image.open(first).size == (3840, 2160)
    assert first.read_bytes() == second.read_bytes()
    assert {
        key: value
        for key, value in first_receipt.items()
        if key not in {"preview", "legend"}
    } == {
        key: value
        for key, value in second_receipt.items()
        if key not in {"preview", "legend"}
    }
    assert first_receipt["analytical_rasters_modified"] is False
    assert first_receipt["network_bytes"] == 0
    legend = json.loads(
        (first.parent / "classification_legend.json").read_text()
    )
    assert legend["analytical_rasters_modified"] is False
    assert legend["specialist_score_display"]["score_is_probability"] is False


def test_unsupported_ndmi_fails_before_any_hybrid_output(
    tmp_path: Path,
) -> None:
    naip, general = _inputs(tmp_path / "inputs")
    raw = json.loads(V4_PATH.read_text(encoding="utf-8"))
    raw["classification"]["indices"].append(
        {
            "index_id": "ndmi",
            "persist": True,
            "display": False,
        }
    )
    recipe = AgriculturalRecipeV4.model_validate(raw)
    staging = tmp_path / "staging"
    with pytest.raises(HybridExecutionError, match="ndmi missing swir1"):
        execute_hybrid_classification(naip, general, staging, recipe)
    assert not (staging / "analysis").exists()
    assert not (staging / "data/indices").exists()


def test_target_signature_is_estimated_from_positive_points(
    tmp_path: Path,
) -> None:
    naip, general = _inputs(tmp_path / "inputs")
    raw = json.loads(V4_PATH.read_text(encoding="utf-8"))
    specialists = raw["classification"]["specialists"]
    specialists["classes"] = [specialists["classes"][0]]
    specialists["requested_class_count"] = 1
    specialist = specialists["classes"][0]
    specialist["strategy"] = {
        "type": "target_signature_similarity",
        "target_bands": {"red": 0.0, "green": 0.0, "nir": 0.0},
        "weights": {"red": 1.0, "green": 1.0, "nir": 1.0},
        "threshold": 0.9,
        "target_source": "positive_calibration_points",
    }
    specialist["calibration"] = {
        "source": "user_points",
        "points": [
            {
                "longitude": -83.0995,
                "latitude": 40.0995,
                "class_id": specialist["class_id"],
            },
            {
                "longitude": -83.0905,
                "latitude": 40.0905,
                "class_id": specialist["class_id"],
            },
        ],
        "minimum_positive_support": 2,
        "minimum_negative_support": 1,
        "publish_coordinates": False,
    }
    recipe = AgriculturalRecipeV4.model_validate(raw)
    staging = tmp_path / "target"
    result = execute_hybrid_classification(
        naip,
        general,
        staging,
        recipe,
    )
    assert result["finalized"] is True
    receipt = json.loads(
        (
            staging
            / "receipts/target_signature_calibration_receipt.json"
        ).read_text()
    )
    target = receipt["classes"][0]["target_vector"]
    assert target == pytest.approx(
        {
            "green": 80.0 / 255.0,
            "nir": 210.0 / 255.0,
            "red": 45.0 / 255.0,
        }
    )
    assert receipt["classes"][0]["sample_count"] == 2
    assert "longitude" not in json.dumps(receipt)


@pytest.mark.parametrize(
    ("mode", "selector", "expected_finalized", "expected_status"),
    [
        ("recommendation", None, False, "AWAITING_INDEX_SELECTION"),
        (
            "recommendation",
            lambda class_id, ranking: ranking[0]["candidate_id"],
            True,
            "SELECTED",
        ),
        ("automatic", None, True, "SELECTED"),
    ],
)
def test_recommendation_and_automatic_raster_selection_lifecycle(
    tmp_path: Path,
    mode,
    selector,
    expected_finalized,
    expected_status,
) -> None:
    naip, general = _automatic_inputs(tmp_path / "inputs")
    raw = json.loads(V4_PATH.read_text(encoding="utf-8"))
    general_spec = raw["classification"]["general"]
    general_spec["requested_class_count"] = 2
    general_spec["class_ids"] = [
        "fallow_or_barren",
        "noncrop_vegetation",
    ]
    general_spec["inference_window_size"] = 16
    specialists = raw["classification"]["specialists"]
    specialists["classes"] = [specialists["classes"][0]]
    specialists["requested_class_count"] = 1
    specialists["selection_mode"] = mode
    specialists["automatic_authorized"] = mode == "automatic"
    specialists["search"]["candidate_indices"] = ["ndvi", "gndvi"]
    specialists["search"]["maximum_calibration_samples"] = 2_000
    specialist = specialists["classes"][0]
    specialist["eligible_parent_general_classes"] = [
        "noncrop_vegetation"
    ]
    specialist["calibration"]["source"] = "cdl_weak_labels"
    specialist["calibration"]["positive_general_classes"] = [
        "noncrop_vegetation"
    ]
    recipe = AgriculturalRecipeV4.model_validate(raw)
    result = execute_hybrid_classification(
        naip,
        general,
        tmp_path / mode,
        recipe,
        recommendation_selector=selector,
    )
    assert result["finalized"] is expected_finalized
    assert result["status"] == expected_status
    selection = result["selection_receipt"]
    assert selection["selection_mode"] == mode
    assert selection["automatic_authorized"] is (mode == "automatic")
    assert selection["candidate_count"] >= 2
    selected_class = selection["classes"][0]
    assert selected_class["outer_holdout"][
        "used_for_candidate_selection"
    ] is False
    assert selected_class["outer_holdout"]["metrics"]["macro_f1"] >= 0.5
    if expected_finalized:
        assert result["paths"]["final_hybrid_classification"].is_file()
        assert selected_class["selected"]["threshold"] is not None
    else:
        assert not (
            tmp_path / mode / "data/final_hybrid_classification.cog.tif"
        ).exists()


def test_point_calibration_rejects_distinct_coordinates_in_same_pixel(
    tmp_path: Path,
) -> None:
    index_path = _cog(
        tmp_path / "ndvi.cog.tif",
        np.full((32, 32), 0.5, dtype=np.float32),
        dtype="float32",
        nodata=-9999.0,
    )
    raw = json.loads(V4_PATH.read_text(encoding="utf-8"))
    specialists = raw["classification"]["specialists"]
    specialist = specialists["classes"][0]
    specialists["classes"] = [specialist]
    specialists["requested_class_count"] = 1
    specialists["selection_mode"] = "recommendation"
    specialists["automatic_authorized"] = False
    specialists["search"]["candidate_indices"] = ["ndvi"]
    specialist["calibration"] = {
        "source": "user_points",
        "minimum_positive_support": 1,
        "minimum_negative_support": 1,
        "publish_coordinates": False,
        "points": [
            {
                "longitude": -83.0998,
                "latitude": 40.0998,
                "class_id": specialist["class_id"],
            },
            {
                "longitude": -83.0997,
                "latitude": 40.0997,
                "class_id": "not_target",
            },
        ],
    }
    recipe = AgriculturalRecipeV4.model_validate(raw)
    with pytest.raises(
        HybridExecutionError,
        match="contradictory calibration points map to the same raster pixel",
    ):
        _point_calibration_samples(
            recipe.classification.specialists.classes[0],
            index_paths={"ndvi": index_path},
            recipe=recipe,
            analysis_aoi_epsg_4326=None,
        )


def test_v4_execute_recipe_transaction_finalizes_complete_offline_handoff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    naip, general = _inputs(tmp_path / "inputs")
    cdl = _cog(
        tmp_path / "inputs/cdl_2023_classes.cog.tif",
        np.ones((32, 32), dtype=np.uint8),
        dtype="uint8",
        nodata=0,
    )
    source_paths = {
        "naip_multispectral": naip,
        "cdl_classes": cdl,
    }

    def record(asset_name: str, path: Path) -> AssetRecord:
        return AssetRecord(
            asset_name=asset_name,
            source_family=(
                "USGS_NAIP"
                if asset_name == "naip_multispectral"
                else "USDA_CDL"
            ),
            temporal_key=(
                2021 if asset_name == "naip_multispectral" else 2023
            ),
            bbox_epsg_4326=(-83.1, 40.068, -83.068, 40.1),
            extent_native=(-83.1, 40.068, -83.068, 40.1),
            crs="EPSG:4326",
            pixel_size=(0.001, 0.001),
            pixel_size_m=1.0,
            width=32,
            height=32,
            nodata=(0,),
            semantic_type=(
                "continuous_multiband_imagery"
                if asset_name == "naip_multispectral"
                else "categorical"
            ),
            checksum="a" * 64,
            local_path=str(path),
            originating_handoff=str(tmp_path / "source-handoff"),
            validation_state="PASS",
            validation_errors=(),
            can_crop_locally=True,
            requires_reprojection=False,
            resolution_satisfies_recipe=True,
        )

    records = {
        asset: record(asset, path) for asset, path in source_paths.items()
    }

    def plan(*args, **kwargs):
        return [
            AssetDecision(
                asset_name=asset,
                action="reuse_direct",
                reason="offline synthetic fixture",
                candidate=records[asset],
                spatial_relationship="exact",
                resampling=(
                    "nearest" if asset == "cdl_classes" else "bilinear"
                ),
                tolerance_degrees=1e-6,
            )
            for asset in ("naip_multispectral", "cdl_classes")
        ]

    def resolve(decisions, staging, *args, **kwargs):
        data = staging / "data"
        data.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            naip,
            data / "naip_2021_multispectral.cog.tif",
        )
        shutil.copy2(cdl, data / "cdl_2023_classes.cog.tif")

    def verify(staging, *args, **kwargs):
        return {
            "naip_multispectral": record(
                "naip_multispectral",
                staging / "data/naip_2021_multispectral.cog.tif",
            ),
            "cdl_classes": record(
                "cdl_classes",
                staging / "data/cdl_2023_classes.cog.tif",
            ),
        }

    def classify(naip_path, cdl_path, staging, recipe, **kwargs):
        data = staging / "data"
        analysis = staging / "analysis/classification"
        analysis.mkdir(parents=True, exist_ok=True)
        paths = {
            "classification": data
            / "naip_2023_surface_classification.cog.tif",
            "confidence": data
            / "naip_2023_classification_confidence.cog.tif",
            "agreement": data / "naip_2023_cdl_agreement_state.cog.tif",
            "cdl_superclasses": data / "cdl_superclasses.cog.tif",
        }
        for key, source in (
            ("classification", general["paths"]["classification"]),
            ("confidence", general["paths"]["confidence"]),
            ("agreement", general["paths"]["agreement"]),
            ("cdl_superclasses", general["paths"]["cdl_superclasses"]),
        ):
            if (
                key == "cdl_superclasses"
                and recipe.classification.specialists.selection_mode
                == "recommendation"
            ):
                mixed_labels = np.where(
                    np.indices((32, 32)).sum(axis=0) % 2 == 0,
                    5,
                    1,
                ).astype(np.uint8)
                _cog(
                    paths[key],
                    mixed_labels,
                    dtype="uint8",
                    nodata=0,
                )
            else:
                shutil.copy2(source, paths[key])
        (analysis / "weak_label_metrics.json").write_text(
            json.dumps(general["metrics"]),
            encoding="utf-8",
        )
        (analysis / "training_receipt.json").write_text(
            json.dumps(
                {
                    "train_sample_total": 100,
                    "holdout_sample_total": 20,
                }
            ),
            encoding="utf-8",
        )
        (analysis / "disagreement_summary.json").write_text(
            json.dumps(
                {
                    "high_confidence_disagreement_fraction": 0.0,
                    "post_sieve_class_counts": {"1": 1024},
                }
            ),
            encoding="utf-8",
        )
        return {
            "source_validation": {
                "status": "PASS",
                "band_count": 4,
                "dtype": "uint8",
            },
            "model_receipt": {
                "backend": "synthetic_fixture",
                "mapping_id": CDL_SURFACE_SUPERCLASSES.mapping_id,
            },
            "training_receipt": {
                "train_sample_total": 100,
                "holdout_sample_total": 20,
            },
            "metrics": general["metrics"],
            "agreement": {
                "high_confidence_disagreement_fraction": 0.0
            },
            "paths": paths,
            "mapping": CDL_SURFACE_SUPERCLASSES.as_dict(),
            "mapping_sha256": CDL_SURFACE_SUPERCLASSES.sha256,
        }

    monkeypatch.setattr(
        "faster_raster.ag_execution.discover_cached_assets",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "faster_raster.ag_execution.compile_asset_plan",
        plan,
    )
    monkeypatch.setattr(
        "faster_raster.ag_execution._resolve_reused",
        resolve,
    )
    monkeypatch.setattr(
        "faster_raster.ag_execution._verify_resolved",
        verify,
    )
    monkeypatch.setattr(
        "faster_raster.ag_classification.execute_classification",
        classify,
    )
    monkeypatch.setenv(
        "FASTERRASTER_HANDOFF_ROOT",
        str(tmp_path / "handoffs"),
    )
    recipe = _recipe()
    bbox = (-83.1, 40.068, -83.068, 40.1)
    original_request = ClassificationRuntimeRequest(
        request_bbox_epsg_4326=bbox,
        imagery_start=date(2023, 1, 1),
        imagery_end=date(2023, 12, 31),
        imagery_year=2023,
        cdl_year=2023,
    )
    resolved_request = original_request.with_imagery_year(2021)
    intervention = build_intervention_record(
        original_request=original_request,
        resolved_request=resolved_request,
        failure=RecoverableContractFailure(
            failure_type="imagery_year_unavailable",
            logical_asset="naip_multispectral",
            source="USGS_NAIP",
            code="requested_year_unavailable",
            detail="synthetic fixture offers 2021 imagery instead of 2023",
            original_requested_value=2023,
            compatible_alternatives=(2021,),
            evidence={"available_intersecting_years": [2021]},
        ),
        alternatives_shown=[2021],
        source_evidence={"fixture": "offline_temporal_repair"},
        original_plan_sha256="1" * 64,
        resolved_plan_sha256="2" * 64,
        confirmation_outcome="accepted",
    )
    preview = execute_recipe(
        ROOT,
        recipe=recipe,
        recipe_raw=json.loads(V4_PATH.read_text()),
        name="offline-hybrid",
        bbox=bbox,
        start="2021-01-01",
        end="2021-12-31",
        year=2023,
        imagery_year=2021,
        reuse_mode="auto",
        open_preview=False,
        max_total_bytes=75_000_000,
        service_tile_size=512,
        renderer=lambda *args, **kwargs: pytest.fail(
            "V4 must use hybrid publication"
        ),
        contract_repair=intervention,
    )
    handoff = preview.parents[2]
    manifest = json.loads((handoff / "manifest.json").read_text())
    receipt = json.loads(
        (
            handoff
            / "preview"
            / recipe.recipe_id
            / "recipe_receipt.json"
        ).read_text()
    )
    assert manifest["operation_status"] == "completed"
    assert manifest["network_bytes"] == 0
    assert manifest["actual_imagery"]["year"] == 2021
    assert manifest["order"]["cdl_year"] == 2023
    assert manifest["human_repair_occurred"] is True
    assert (
        manifest["contract_repair"]["intervention_id"]
        == intervention["intervention_id"]
    )
    assert "temporal mismatch" in manifest["classification"][
        "scientific_claim"
    ]
    assert manifest["index_guided_hybrid"]["selection_status"] == (
        "USER_DEFINED"
    )
    assert receipt["final_status"] == "PASS"
    assert (
        receipt["contract_repair"]["intervention_id"]
        == intervention["intervention_id"]
    )
    assert receipt["index_guided_hybrid"]["registry"]["sha256"]
    assert (handoff / "data/final_hybrid_classification.cog.tif").is_file()
    assert (handoff / "checksums.sha256").is_file()
    assert not any(path.name.startswith(".") for path in handoff.rglob("*"))
    inspection = inspect_handoff(handoff)
    hybrid = inspection["index_guided_hybrid"]
    assert hybrid["registry_sha256"]
    assert {
        item["index_id"] for item in hybrid["calculated_indices"]
    } == {"ndvi", "gndvi", "green_nir_water_proxy"}
    assert hybrid["selection_mode"] == "user_defined"
    assert hybrid["selection_status"] == "USER_DEFINED"
    assert len(hybrid["specialist_classes"]) == 2
    assert hybrid["untouched_holdout_metrics"] is None
    assert inspection["contract_repair"]["human_repair_occurred"] is True
    assert (
        inspection["contract_repair"]["intervention_id"]
        == intervention["intervention_id"]
    )

    derived = derive_publication(
        handoff,
        tmp_path / "derived",
        name="offline-hybrid-rerender",
    )
    derived_inspection = inspect_handoff(derived)
    assert derived_inspection["status"] == "completed"
    assert derived_inspection["network_bytes"] == 0
    assert derived_inspection["index_guided_hybrid"]["selection_status"] == (
        "USER_DEFINED"
    )
    derived_receipt = json.loads(
        (
            derived
            / "preview"
            / recipe.recipe_id
            / "recipe_receipt.json"
        ).read_text()
    )
    assert derived_receipt["derived_publication"][
        "analytical_rasters_modified"
    ] is False
    assert (
        derived_receipt["contract_repair"]["intervention_id"]
        == intervention["intervention_id"]
    )

    review_raw = json.loads(V4_PATH.read_text())
    review_general = review_raw["classification"]["general"]
    review_general["inference_window_size"] = 16
    review_general["random_seed"] = 1
    review_general["spatial_holdout_fold"] = 2
    review_specialists = review_raw["classification"]["specialists"]
    review_specialists["classes"] = [review_specialists["classes"][0]]
    review_specialists["requested_class_count"] = 1
    review_specialists["selection_mode"] = "recommendation"
    review_specialists["automatic_authorized"] = False
    review_specialists["search"]["candidate_indices"] = ["ndvi", "gndvi"]
    review_specialists["classes"][0]["calibration"].update(
        {
            "source": "cdl_weak_labels",
            "positive_general_classes": ["noncrop_vegetation"],
        }
    )
    review_recipe = AgriculturalRecipeV4.model_validate(review_raw)
    with pytest.raises(SelectionReviewReady) as raised:
        execute_recipe(
            ROOT,
            recipe=review_recipe,
            recipe_raw=review_raw,
            name="offline-hybrid-review",
            bbox=bbox,
            start="2021-01-01",
            end="2021-12-31",
            year=2023,
            imagery_year=2021,
            reuse_mode="auto",
            open_preview=False,
            max_total_bytes=75_000_000,
            service_tile_size=512,
            renderer=lambda *args, **kwargs: pytest.fail(
                "recommendation review must not use a final renderer"
            ),
            contract_repair=intervention,
        )
    review = raised.value.package_path
    assert review is not None
    review_manifest = json.loads((review / "manifest.json").read_text())
    assert review_manifest["operation_status"] == "AWAITING_INDEX_SELECTION"
    assert review_manifest["finalized"] is False
    assert review_manifest["completed_handoff_created"] is False
    assert review_manifest["final_hybrid_output_declared"] is False
    assert review_manifest["completed_handoff_pointer_updated"] is False
    assert "not evidence of analytical finalization" in (
        review_manifest["checksums_purpose"]
    )
    review_plan = json.loads((review / "asset_plan.json").read_text())
    assert review_plan["published_handoff_id"] is None
    assert review_plan["published_handoff_relative_path"] is None
    assert review_plan["finalized"] is False
    assert review_plan["selection_review_package"] is True
    assert not (review / "data/final_hybrid_classification.cog.tif").exists()
