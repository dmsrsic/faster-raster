from __future__ import annotations

import csv
import json
from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds
from rasterio.warp import transform, transform_geom

import faster_raster.fr_cli as fr_cli
from faster_raster import ag_classification, ag_execution
from faster_raster import ag_classification_publication as ag_publication
from scripts import derive_classification_publication as derive_publication
from faster_raster.ag_assets import AssetRecord
from faster_raster.ag_classification import (
    audit_cdl_agreement,
    extract_training_samples,
    run_inference,
)
from faster_raster.ag_classification_contracts import (
    classification_scientific_claim,
)
from faster_raster.ag_execution import RecoverableRecipeExecutionError
from faster_raster.ag_geography import (
    SourceCoverageError,
    lat_to_web_mercator,
    lon_to_web_mercator,
    validate_naip_catalog,
)
from faster_raster.ag_recipes import load_named_recipe
from faster_raster.aoi_geometry import (
    CIRCLE_SEGMENT_COUNT,
    AreaConstructionError,
    build_point_buffer_area,
    raster_aoi_mask,
)
from faster_raster.contract_repair import (
    ClassificationRuntimeRequest,
    PromptSession,
    RepairCancelled,
    RepairAttemptsExceeded,
    amended_workfile,
    build_intervention_record,
    intervention_reference,
    prompt_imagery_dates,
    prompt_imagery_year,
    prompt_location,
    recoverable_failure_from_document,
    terminal_interaction_enabled,
)
from faster_raster.preview_open import inspect_handoff
from faster_raster.workfiles import Workfile, WorkfileSpec


ROOT = Path(__file__).resolve().parent.parent


class ScriptedInput:
    def __init__(self, values: list[str]) -> None:
        self.values = iter(values)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return next(self.values)


def session(values: list[str]) -> PromptSession:
    return PromptSession(reader=ScriptedInput(values), writer=lambda _: None)


def workfile(tmp_path: Path) -> Workfile:
    raw = {
        "schema_version": "fasterraster.work/v1",
        "name": "repair-test",
        "recipe": "naip-cdl-classification-audit",
        "area": {"bbox": [-83.1, 39.9, -83.0, 40.0]},
        "time": {
            "start": "2023-04-01",
            "end": "2023-10-31",
            "crop_year": 2023,
        },
        "sources": {"policy": "auto"},
        "data": {"reuse": "auto", "allow_network": True},
        "processing": {"resolution_m": 1.2},
        "limits": {"maximum_download_mb": 75},
        "outputs": {"preview": True, "open_when_complete": False},
    }
    return Workfile(
        path=tmp_path / "study.fr.md",
        spec=WorkfileSpec.model_validate(raw),
        prose="",
        front_matter=raw,
    )


def failure_document(
    code: str,
    *,
    evidence: dict | None = None,
) -> dict:
    return {
        "schema_version": "fasterraster.ag-source-failure/v1",
        "source": "USGS_NAIP",
        "code": code,
        "detail": f"test {code}",
        "requested_bbox": [-83.1, 39.9, -83.0, 40.0],
        "requested_imagery_year": 2023,
        "requested_start": "2023-04-01",
        "requested_end": "2023-10-31",
        "evidence": evidence or {},
        "network_bytes": 100,
    }


def recoverable(code: str, evidence: dict | None = None):
    result = recoverable_failure_from_document(
        failure_document(code, evidence=evidence)
    )
    assert result is not None
    return result


def repair_plan(tmp_path: Path) -> dict:
    values = {
        "state_root": {"value": str(tmp_path / "state")},
        "maximum_download_mb": {"value": 75},
        "service_tile_size": {"value": 512},
        "resolution_m": {"value": 1.2},
        "reuse_mode": {"value": "auto"},
        "open_when_complete": {"value": False},
    }
    return {
        "schema_version": "fasterraster.study-plan/v1",
        "study_name": "repair-test",
        "workflow": "naip_cdl_classification_audit",
        "blocking": False,
        "rows": [],
        "asset_plan": {
            "network_required_assets": [
                "naip_multispectral",
                "cdl_classes",
            ]
        },
        "maximum_download_bytes": 75_000_000,
        "classification": {
            "estimated_uncompressed_transfer_bytes": 10_000_000
        },
        "source_resolution": {"decisions": []},
        "resolved_config": {"values": values},
    }


@pytest.mark.parametrize(
    ("distance", "unit", "expected"),
    [
        ("1000", "meters", 1000.0),
        ("1", "kilometers", 1000.0),
        ("1", "miles", 1609.344),
    ],
)
def test_square_units_and_side_semantics(distance, unit, expected):
    area = build_point_buffer_area(
        -83.0123,
        39.9987,
        distance,
        unit,
        "square",
    )
    assert area.normalized_distance_meters == expected
    assert area.analysis_aoi_area_square_meters == pytest.approx(
        4.0 * expected * expected
    )
    assert area.circle_segment_count is None
    assert "twice" in area.buffer_semantics


def test_circle_is_deterministic_and_bbox_encloses_geometry():
    first = build_point_buffer_area(
        -83.0123, 39.9987, "2.0", "kilometers", "circle"
    )
    second = build_point_buffer_area(
        -83.0123, 39.9987, "2.0", "kilometers", "circle"
    )
    assert first == second
    assert first.circle_segment_count == CIRCLE_SEGMENT_COUNT
    assert len(first.analysis_aoi_epsg_4326["coordinates"][0]) == (
        CIRCLE_SEGMENT_COUNT + 1
    )
    west, south, east, north = first.request_bbox_epsg_4326
    for longitude, latitude in first.analysis_aoi_epsg_4326["coordinates"][0]:
        assert west <= longitude <= east
        assert south <= latitude <= north
    assert first.envelope_only_area_square_meters > 0


@pytest.mark.parametrize("shape", ["square", "circle"])
def test_point_buffer_metric_geometry_matches_entered_distance(shape):
    area = build_point_buffer_area(
        -83.01234567890123,
        39.99876543210987,
        "2",
        "kilometers",
        shape,
    )
    local = transform_geom(
        "EPSG:4326",
        area.geometry_construction_crs,
        area.analysis_aoi_epsg_4326,
        precision=-1,
    )
    ring = np.asarray(local["coordinates"][0], dtype=np.float64)
    center_x, center_y = transform(
        "EPSG:4326",
        area.geometry_construction_crs,
        [area.center_longitude],
        [area.center_latitude],
    )
    offsets = ring[:-1] - np.array([center_x[0], center_y[0]])
    if shape == "circle":
        radii = np.sqrt(np.square(offsets).sum(axis=1))
        assert radii == pytest.approx(
            np.full(CIRCLE_SEGMENT_COUNT, 2000.0),
            abs=0.001,
        )
        assert area.circle_segment_count == CIRCLE_SEGMENT_COUNT
        assert area.square_edge_segment_count is None
    else:
        assert offsets[:, 0].min() == pytest.approx(-2000.0, abs=0.001)
        assert offsets[:, 0].max() == pytest.approx(2000.0, abs=0.001)
        assert offsets[:, 1].min() == pytest.approx(-2000.0, abs=0.001)
        assert offsets[:, 1].max() == pytest.approx(2000.0, abs=0.001)
        assert area.square_edge_segment_count == 32
        assert area.circle_segment_count is None


def test_point_buffer_exact_distance_cap_is_accepted():
    area = build_point_buffer_area(
        -100.0,
        35.0,
        500,
        "kilometers",
        "circle",
    )
    assert area.normalized_distance_meters == 500_000.0


@pytest.mark.parametrize(
    ("longitude", "latitude", "distance", "unit", "shape"),
    [
        ("bad", 40, 1, "meters", "square"),
        (181, 40, 1, "meters", "square"),
        (-83, "bad", 1, "meters", "square"),
        (-83, 91, 1, "meters", "square"),
        (-83, 40, 0, "meters", "square"),
        (-83, 40, -1, "meters", "square"),
        (-83, 40, "bad", "meters", "square"),
        (-83, 40, 1, "feet", "square"),
        (-83, 40, 1, "meters", "triangle"),
        (-83, 40, 600, "kilometers", "circle"),
        (-83, 86, 1, "kilometers", "circle"),
        (179.999, 10, 10, "kilometers", "circle"),
    ],
)
def test_unsafe_point_buffer_inputs_fail(
    longitude, latitude, distance, unit, shape
):
    with pytest.raises(AreaConstructionError):
        build_point_buffer_area(
            longitude, latitude, distance, unit, shape
        )


def test_high_latitude_buffer_within_execution_limit_is_valid():
    area = build_point_buffer_area(
        10.0, 80.0, 1000, "meters", "circle"
    )
    assert area.request_bbox_epsg_4326[1] > 79.0
    assert area.request_bbox_epsg_4326[3] < 81.0


def test_naip_date_range_failure_is_structured():
    available = {
        "features": [
            {
                "attributes": {
                    "Year": 2023,
                    "acquisition_date": "2023-03-01",
                }
            }
        ]
    }
    with pytest.raises(SourceCoverageError) as failure:
        validate_naip_catalog(
            {"features": []},
            requested_year=2023,
            available_response=available,
            requested_start="2023-04-01",
            requested_end="2023-10-31",
        )
    assert failure.value.code == "date_range_unavailable"
    assert failure.value.evidence["available_acquisition_dates"] == [
        "2023-03-01"
    ]


def test_naip_date_range_accepts_epoch_milliseconds():
    result = validate_naip_catalog(
        {
            "features": [
                {
                    "attributes": {
                        "Year": 2023,
                        "acquisition_date": 1685577600000,
                        "resolution_value": 0.6,
                        "resolution_units": "meters",
                    }
                }
            ]
        },
        requested_year=2023,
        requested_start="2023-04-01",
        requested_end="2023-10-31",
    )
    assert result["selected_acquisition_dates"] == ["2023-06-01"]


def test_incomplete_same_year_catalog_evidence_fails_closed():
    available = {
        "features": [{"attributes": {"Year": 2023}}]
    }
    with pytest.raises(SourceCoverageError) as failure:
        validate_naip_catalog(
            {"features": []},
            requested_year=2023,
            available_response=available,
            requested_start="2023-04-01",
            requested_end="2023-10-31",
        )
    assert failure.value.code == "invalid_response"
    assert (
        recoverable_failure_from_document(
            {
                "source": failure.value.source,
                "code": failure.value.code,
                "detail": failure.value.detail,
                "evidence": dict(failure.value.evidence or {}),
            }
        )
        is None
    )


def test_failure_mapping_distinguishes_year_date_and_location():
    year = recoverable(
        "no_intersecting_imagery",
        {
            "requested_year": 2023,
            "available_intersecting_years": [2021, 2022],
        },
    )
    dates = recoverable(
        "date_range_unavailable",
        {
            "requested_date_range": {
                "start": "2023-04-01",
                "end": "2023-10-31",
            },
            "available_acquisition_dates": ["2023-03-01"],
        },
    )
    location = recoverable("bbox_outside_coverage")
    assert year.failure_type == "imagery_year_unavailable"
    assert year.compatible_alternatives == (2021, 2022)
    assert dates.failure_type == "imagery_date_range_unavailable"
    assert location.failure_type == "location_unavailable"


@pytest.mark.parametrize(
    ("code", "evidence"),
    [
        ("service_unavailable", {}),
        ("invalid_response", {}),
        (
            "wrong_year_response",
            {"requested_year": 2023, "available_intersecting_years": [2021]},
        ),
        (
            "requested_year_unavailable",
            {"requested_year": 2023, "available_intersecting_years": []},
        ),
        (
            "no_intersecting_imagery",
            {
                "requested_year": 2023,
                "available_intersecting_years": [2023],
            },
        ),
        ("date_range_unavailable", {"available_acquisition_dates": []}),
    ],
)
def test_unsupported_or_incomplete_source_failures_are_not_promptable(
    code,
    evidence,
):
    assert (
        recoverable_failure_from_document(
            failure_document(code, evidence=evidence)
        )
        is None
    )


def test_temporal_mismatch_scientific_claim_is_explicit():
    claim = classification_scientific_claim(2021, 2023)
    assert "2021 imagery" in claim
    assert "2023 USDA CDL" in claim
    assert "temporally mismatched" in claim
    assert "originally requested imagery year" in claim


def test_prompt_year_uses_listed_alternative(tmp_path):
    request = ClassificationRuntimeRequest.from_workfile(workfile(tmp_path))
    result = prompt_imagery_year(
        recoverable(
            "no_intersecting_imagery",
            {"available_intersecting_years": [2021, 2022]},
        ),
        request,
        session(["1"]),
    )
    assert result.imagery_year == 2022
    assert result.cdl_year == 2023
    assert result.imagery_start == date(2022, 4, 1)


def test_prompt_year_manual_invalid_then_valid(tmp_path):
    request = ClassificationRuntimeRequest.from_workfile(workfile(tmp_path))
    result = prompt_imagery_year(
        recoverable(
            "no_intersecting_imagery",
            {"available_intersecting_years": [2021, 2022]},
        ),
        request,
        session(["3", "bad", "3", "2020"]),
    )
    assert result.imagery_year == 2020


def test_prompt_dates_reprompts_malformed_and_inverted(tmp_path):
    request = ClassificationRuntimeRequest.from_workfile(workfile(tmp_path))
    result = prompt_imagery_dates(
        recoverable(
            "date_range_unavailable",
            {"available_acquisition_dates": ["2023-03-01"]},
        ),
        request,
        session(
            [
                "bad",
                "2023-05-01",
                "bad",
                "2023-04-01",
                "2023-09-01",
            ]
        ),
    )
    assert result.imagery_start == date(2023, 5, 1)
    assert result.imagery_end == date(2023, 9, 1)


def test_prompt_direct_bbox_invalid_then_valid(tmp_path):
    request = ClassificationRuntimeRequest.from_workfile(workfile(tmp_path))
    result = prompt_location(
        recoverable("bbox_outside_coverage"),
        request,
        session(
            [
                "1",
                "bad",
                "1",
                "-83.02,39.98,-82.98,40.02",
                "y",
            ]
        ),
    )
    assert result.request_bbox_epsg_4326 == (
        -83.02,
        39.98,
        -82.98,
        40.02,
    )
    assert result.spatial_construction["shape"] == "bbox"


def test_prompt_point_buffer_preserves_entered_values(tmp_path):
    request = ClassificationRuntimeRequest.from_workfile(workfile(tmp_path))
    result = prompt_location(
        recoverable("bbox_outside_coverage"),
        request,
        session(["2", "-83.0123", "39.9987", "2.0", "2", "2", "y"]),
    )
    construction = result.spatial_construction
    assert construction["coordinate_order"] == "longitude,latitude"
    assert construction["entered_buffer_text"] == "2.0"
    assert construction["entered_distance_unit"] == "kilometers"
    assert construction["normalized_distance_meters"] == 2000.0
    assert construction["shape"] == "circle"


def test_prompt_can_cancel(tmp_path):
    request = ClassificationRuntimeRequest.from_workfile(workfile(tmp_path))
    with pytest.raises(RepairCancelled):
        prompt_location(
            recoverable("bbox_outside_coverage"),
            request,
            session(["q"]),
        )


def test_prompt_eof_is_clean_cancellation(tmp_path):
    request = ClassificationRuntimeRequest.from_workfile(workfile(tmp_path))

    def eof(_prompt):
        raise EOFError

    with pytest.raises(RepairCancelled, match="input ended"):
        prompt_location(
            recoverable("bbox_outside_coverage"),
            request,
            PromptSession(reader=eof, writer=lambda _: None),
        )


def test_invalid_prompt_loop_is_bounded(tmp_path):
    request = ClassificationRuntimeRequest.from_workfile(workfile(tmp_path))
    bounded = PromptSession(
        reader=ScriptedInput(["not-a-choice", "still-not-a-choice"]),
        writer=lambda _: None,
        maximum_invalid_attempts=2,
    )
    with pytest.raises(RepairAttemptsExceeded, match="attempt limit"):
        prompt_imagery_year(
            recoverable(
                "requested_year_unavailable",
                {
                    "requested_year": 2023,
                    "available_intersecting_years": [2021],
                },
            ),
            request,
            bounded,
        )


def test_amended_workfile_does_not_mutate_original(tmp_path):
    original = workfile(tmp_path)
    original_front = deepcopy(original.front_matter)
    request = ClassificationRuntimeRequest.from_workfile(
        original
    ).with_explicit_bbox((-83.02, 39.98, -82.98, 40.02))
    amended = amended_workfile(original, request)
    assert original.front_matter == original_front
    assert tuple(original.spec.area.bbox) == (-83.1, 39.9, -83.0, 40.0)
    assert tuple(amended.spec.area.bbox) == request.request_bbox_epsg_4326


def test_intervention_identifier_is_deterministic(tmp_path):
    original = ClassificationRuntimeRequest.from_workfile(workfile(tmp_path))
    resolved = original.with_imagery_year(2021)
    kwargs = {
        "original_request": original,
        "resolved_request": resolved,
        "failure": recoverable(
            "no_intersecting_imagery",
            {"available_intersecting_years": [2021]},
        ),
        "alternatives_shown": [2021],
        "source_evidence": {"status": "PASS"},
        "original_plan_sha256": "a" * 64,
        "resolved_plan_sha256": "b" * 64,
        "confirmation_outcome": "accepted",
    }
    first = build_intervention_record(**kwargs)
    second = build_intervention_record(**kwargs)
    assert first["intervention_id"] == second["intervention_id"]
    assert first["temporal_mismatch"]["explicitly_accepted"] is True
    assert first["workfile_write_back"]["performed"] is False


class FakeTTY:
    def __init__(self, value: bool):
        self.value = value

    def isatty(self):
        return self.value


def test_terminal_gating_is_fail_closed_unless_explicit():
    assert (
        terminal_interaction_enabled(
            None, stdin=FakeTTY(False), stdout=FakeTTY(False)
        )
        is False
    )
    assert (
        terminal_interaction_enabled(
            False, stdin=FakeTTY(True), stdout=FakeTTY(True)
        )
        is False
    )
    assert (
        terminal_interaction_enabled(
            True, stdin=FakeTTY(False), stdout=FakeTTY(False)
        )
        is True
    )
    assert (
        terminal_interaction_enabled(
            True,
            stdin=FakeTTY(True),
            stdout=FakeTTY(True),
            json_output=True,
        )
        is False
    )
    assert (
        terminal_interaction_enabled(
            None,
            stdin=FakeTTY(True),
            stdout=FakeTTY(True),
            json_output=True,
        )
        is False
    )


class FixedModel:
    classes_ = np.array([1, 2], dtype=np.uint8)

    def predict_proba(self, matrix):
        return np.tile(np.array([[0.9, 0.1]]), (len(matrix), 1))


def test_circular_mask_excludes_envelope_pixels_and_statistics(tmp_path):
    area = build_point_buffer_area(
        -83.0, 40.0, 2000, "meters", "circle"
    )
    center_x = lon_to_web_mercator(-83.0)
    center_y = lat_to_web_mercator(40.0)
    transform = from_bounds(
        center_x - 2500,
        center_y - 2500,
        center_x + 2500,
        center_y + 2500,
        100,
        100,
    )
    naip_path = tmp_path / "naip.tif"
    with rasterio.open(
        naip_path,
        "w",
        driver="GTiff",
        width=100,
        height=100,
        count=4,
        dtype="uint8",
        crs="EPSG:3857",
        transform=transform,
        nodata=0,
    ) as sink:
        for band, value in enumerate((100, 120, 80, 160), start=1):
            sink.write(np.full((100, 100), value, dtype=np.uint8), band)
    with rasterio.open(naip_path) as source:
        mask = raster_aoi_mask(
            source,
            area.analysis_aoi_epsg_4326,
        )
    assert mask[50, 50]
    assert not mask[0, 0]

    recipe = load_named_recipe(ROOT, "naip_cdl_classification_audit")
    inference = run_inference(
        naip_path,
        FixedModel(),
        recipe.classification,
        tmp_path / "data",
        year=2023,
        analysis_aoi_epsg_4326=area.analysis_aoi_epsg_4326,
    )
    assert 0 < inference["valid_source_pixels"] < 10_000
    assert (
        inference["aoi_excluded_pixels"]
        + inference["valid_source_pixels"]
        == 10_000
    )
    assert (
        inference["post_sieve_class_counts"]["1"]
        == inference["valid_source_pixels"]
    )
    with rasterio.open(inference["classification_path"]) as source:
        values = source.read(1)
    assert values[50, 50] == 1
    assert values[0, 0] == 0

    superclass = tmp_path / "superclass.tif"
    with rasterio.open(
        superclass,
        "w",
        driver="GTiff",
        width=100,
        height=100,
        count=1,
        dtype="uint8",
        crs="EPSG:3857",
        transform=transform,
        nodata=0,
    ) as sink:
        weak_labels = np.where(mask, 1, 6).astype(np.uint8)
        sink.write(weak_labels, 1)
    analysis = tmp_path / "analysis"
    analysis.mkdir()
    agreement = audit_cdl_agreement(
        inference,
        superclass,
        tmp_path / "data",
        analysis,
        analysis_aoi_epsg_4326=area.analysis_aoi_epsg_4326,
    )
    assert (
        agreement["valid_comparison_pixels"]
        == inference["valid_source_pixels"]
    )
    with rasterio.open(agreement["agreement_path"]) as source:
        states = source.read(1)
    assert states[50, 50] == 1
    assert states[0, 0] == 0
    assert (
        agreement["top_cdl_to_prediction_disagreement_pairs"]
        == []
    )
    rows = list(
        csv.DictReader(
            (analysis / "class_area_inventory.csv").open(encoding="utf-8")
        )
    )
    class_one = next(row for row in rows if row["predicted_class_code"] == "1")
    assert int(class_one["pixel_count"]) == inference["valid_source_pixels"]

    training_cores = tmp_path / "training_cores.tif"
    inside_labels = np.where(
        np.indices(mask.shape)[1] < 50,
        1,
        2,
    )
    adversarial_cores = np.where(mask, inside_labels, 6).astype(np.uint8)
    with rasterio.open(
        training_cores,
        "w",
        driver="GTiff",
        width=100,
        height=100,
        count=1,
        dtype="uint8",
        crs="EPSG:3857",
        transform=transform,
        nodata=0,
    ) as sink:
        sink.write(adversarial_cores, 1)
    sample_spec = recipe.classification.model_copy(
        update={
            "minimum_training_samples_per_class": 1,
            "maximum_samples_per_class": 20_000,
            "spatial_holdout_folds": 2,
            "spatial_holdout_fold": 0,
            "inference_window_size": 16,
        }
    )
    samples = extract_training_samples(
        naip_path,
        training_cores,
        sample_spec,
        analysis_aoi_epsg_4326=area.analysis_aoi_epsg_4326,
    )
    assert samples["eligible_pixels_per_class"]["6"] == 0
    assert set(samples["retained_classes"]) == {1, 2}
    assert (
        samples["selected_samples_per_class"]["1"]["selected"]
        == samples["selected_samples_per_class"]["1"]["train"]
        + samples["selected_samples_per_class"]["1"]["holdout"]
    )


def test_recoverable_execution_error_exposes_typed_failure():
    error = RecoverableRecipeExecutionError(
        failure_document(
            "no_intersecting_imagery",
            evidence={"available_intersecting_years": [2021]},
        )
    )
    assert error.recoverable_failure.failure_type == "imagery_year_unavailable"
    assert error.failure_document["source"] == "USGS_NAIP"


def test_provider_outage_is_not_converted_to_interactive_repair(tmp_path):
    document = failure_document("service_unavailable")
    (tmp_path / "source_coverage_failure.json").write_text(
        json.dumps(document),
        encoding="utf-8",
    )
    result = SimpleNamespace(stderr="provider outage", stdout="")
    with pytest.raises(
        ag_execution.RecipeExecutionError,
        match="provider outage",
    ):
        ag_execution._raise_acquisition_failure(tmp_path, result)


def test_cli_repair_recompiles_reprompts_and_executes_only_after_confirmation(
    tmp_path,
    monkeypatch,
):
    original_workfile = workfile(tmp_path)
    original_front_matter = deepcopy(original_workfile.front_matter)
    original_plan = repair_plan(tmp_path)
    scripted = ScriptedInput(
        [
            "1",  # listed imagery year 2021
            "y",  # accept temporal mismatch
            "y",  # confirm first candidate before source access
            "2",  # repair still-uncovered location with point/buffer
            "-83.05",
            "39.95",
            "1",
            "2",  # kilometers
            "2",  # circle
            "y",  # use generated location
            "y",  # accept temporal mismatch
            "y",  # confirm second candidate before source access
        ]
    )
    prompt_session = PromptSession(
        reader=scripted,
        writer=lambda _: None,
    )
    monkeypatch.setattr(fr_cli, "PromptSession", lambda: prompt_session)
    monkeypatch.setattr(
        fr_cli,
        "validate_aoi_safety",
        lambda *args, **kwargs: {"status": "PASS", "estimated_bytes": 1234},
    )

    compiled: list[dict] = []

    def fake_compile(
        root,
        repaired_workfile,
        paths,
        *,
        runtime_request,
        **kwargs,
    ):
        plan = deepcopy(original_plan)
        plan["runtime_request"] = deepcopy(runtime_request)
        plan["asset_plan"]["runtime_request"] = deepcopy(runtime_request)
        compiled.append(
            {
                "workfile_bbox": tuple(repaired_workfile.spec.area.bbox),
                "runtime_request": deepcopy(runtime_request),
            }
        )
        return plan

    monkeypatch.setattr(fr_cli, "compile_study_plan", fake_compile)
    execution_calls: list[dict] = []

    def fake_execute(
        root,
        repaired_workfile,
        plan,
        request,
        **kwargs,
    ):
        execution_calls.append(
            {
                "request": request,
                "plan": deepcopy(plan),
                "contract_repair": deepcopy(kwargs["contract_repair"]),
                "confirmation_prompts": len(
                    [
                        prompt
                        for prompt in scripted.prompts
                        if prompt.startswith(
                            "Continue with source validation and raster "
                            "acquisition/reuse?"
                        )
                    ]
                ),
            }
        )
        assert execution_calls[-1]["confirmation_prompts"] == len(
            execution_calls
        )
        if len(execution_calls) == 1:
            raise RecoverableRecipeExecutionError(
                failure_document(
                    "no_intersecting_imagery",
                    evidence={"requested_bbox": list(request.request_bbox_epsg_4326)},
                )
            )
        return tmp_path / "preview.png"

    monkeypatch.setattr(
        fr_cli,
        "_execute_classification_request",
        fake_execute,
    )
    recipe = load_named_recipe(ROOT, "naip_cdl_classification_audit")
    preview, resolved_plan = fr_cli._repair_classification_cook(
        SimpleNamespace(),
        root=ROOT,
        workfile=original_workfile,
        paths=None,
        original_plan=original_plan,
        recipe=recipe,
        recipe_raw={},
        initial_error=RecoverableRecipeExecutionError(
            failure_document(
                "requested_year_unavailable",
                evidence={
                    "requested_year": 2023,
                    "available_intersecting_years": [2021],
                },
            )
        ),
    )

    assert preview == tmp_path / "preview.png"
    assert len(compiled) == 2
    assert len(execution_calls) == 2
    assert compiled[0]["runtime_request"]["imagery_year"] == 2021
    assert compiled[1]["runtime_request"]["spatial_construction"]["shape"] == "circle"
    assert (
        resolved_plan["runtime_request"]["request_bbox_epsg_4326"]
        == compiled[1]["runtime_request"]["request_bbox_epsg_4326"]
    )
    resolved_request = execution_calls[-1]["request"]
    assert resolved_request.imagery_year == 2021
    assert resolved_request.cdl_year == 2023
    assert resolved_request.acquisition_geometry_differs is True
    intervention = execution_calls[-1]["contract_repair"]
    assert intervention["temporal_mismatch"]["explicitly_accepted"] is True
    assert (
        intervention["source_evidence_used"]["candidate_catalog_validation"][
            "status"
        ]
        == "deferred_until_after_explicit_confirmation"
    )
    assert intervention["spatial_construction"]["shape"] == "circle"
    assert original_workfile.front_matter == original_front_matter
    assert tuple(original_workfile.spec.area.bbox) == (
        -83.1,
        39.9,
        -83.0,
        40.0,
    )


def test_temporal_mismatch_rejection_stops_before_source_activity(
    tmp_path,
    monkeypatch,
):
    original_workfile = workfile(tmp_path)
    original_plan = repair_plan(tmp_path)
    scripted = ScriptedInput(["1", "n"])
    monkeypatch.setattr(
        fr_cli,
        "PromptSession",
        lambda: PromptSession(reader=scripted, writer=lambda _: None),
    )
    monkeypatch.setattr(
        fr_cli,
        "validate_aoi_safety",
        lambda *args, **kwargs: {"status": "PASS"},
    )
    compilations: list[dict] = []

    def fake_compile(*args, runtime_request, **kwargs):
        compilations.append(deepcopy(runtime_request))
        plan = deepcopy(original_plan)
        plan["runtime_request"] = deepcopy(runtime_request)
        return plan

    monkeypatch.setattr(fr_cli, "compile_study_plan", fake_compile)
    monkeypatch.setattr(
        fr_cli,
        "_execute_classification_request",
        lambda *args, **kwargs: pytest.fail(
            "source and raster activity must not begin"
        ),
    )
    with pytest.raises(RepairCancelled, match="temporal mismatch"):
        fr_cli._repair_classification_cook(
            SimpleNamespace(),
            root=ROOT,
            workfile=original_workfile,
            paths=None,
            original_plan=original_plan,
            recipe=load_named_recipe(
                ROOT, "naip_cdl_classification_audit"
            ),
            recipe_raw={},
            initial_error=RecoverableRecipeExecutionError(
                failure_document(
                    "no_intersecting_imagery",
                    evidence={
                        "requested_year": 2023,
                        "available_intersecting_years": [2021],
                    },
                )
            ),
        )
    assert len(compilations) == 1
    assert compilations[0]["imagery_year"] == 2021
    assert not any(
        prompt.startswith(
            "Continue with source validation and raster acquisition/reuse?"
        )
        for prompt in scripted.prompts
    )


def test_cli_declined_final_confirmation_has_no_source_or_raster_activity(
    tmp_path,
    monkeypatch,
):
    original_workfile = workfile(tmp_path)
    original_plan = repair_plan(tmp_path)
    scripted = ScriptedInput(
        [
            "1",
            "-82.1,39.1,-82.0,39.2",
            "y",
            "n",
        ]
    )
    monkeypatch.setattr(
        fr_cli,
        "PromptSession",
        lambda: PromptSession(reader=scripted, writer=lambda _: None),
    )
    monkeypatch.setattr(
        fr_cli,
        "validate_aoi_safety",
        lambda *args, **kwargs: {"status": "PASS"},
    )
    monkeypatch.setattr(
        fr_cli,
        "compile_study_plan",
        lambda *args, runtime_request, **kwargs: {
            **deepcopy(original_plan),
            "runtime_request": deepcopy(runtime_request),
        },
    )
    activity: list[str] = []
    monkeypatch.setattr(
        fr_cli,
        "_execute_classification_request",
        lambda *args, **kwargs: activity.append("network_or_execution"),
    )

    with pytest.raises(RepairCancelled, match="confirmation declined"):
        fr_cli._repair_classification_cook(
            SimpleNamespace(),
            root=ROOT,
            workfile=original_workfile,
            paths=None,
            original_plan=original_plan,
            recipe=load_named_recipe(
                ROOT, "naip_cdl_classification_audit"
            ),
            recipe_raw={},
            initial_error=RecoverableRecipeExecutionError(
                failure_document("bbox_outside_coverage")
            ),
        )
    assert activity == []


def test_interactive_json_is_rejected_before_planning_or_network(monkeypatch):
    monkeypatch.setattr(
        fr_cli,
        "_load_and_plan",
        lambda args: pytest.fail("planning must not start"),
    )
    with pytest.raises(fr_cli.CommandError, match="cannot be combined"):
        fr_cli.command_cook(
            SimpleNamespace(json=True, interactive=True)
        )


def test_cli_temporal_year_arguments_must_be_paired(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(fr_cli, "repository_root", lambda: ROOT)
    monkeypatch.setattr(
        fr_cli,
        "load_workfile",
        lambda *args, **kwargs: workfile(tmp_path),
    )
    with pytest.raises(
        fr_cli.CommandError,
        match="must be provided together",
    ):
        fr_cli._load_and_plan(
            SimpleNamespace(
                workfile=tmp_path / "study.fr.md",
                resolve_imagery_year=2019,
                resolve_cdl_year=None,
            )
        )


def test_cli_coherent_temporal_resolution_reaches_plan_without_acquisition(
    tmp_path,
    monkeypatch,
):
    original = workfile(tmp_path)
    observed: dict = {}
    monkeypatch.setattr(fr_cli, "repository_root", lambda: ROOT)
    monkeypatch.setattr(
        fr_cli,
        "load_workfile",
        lambda *args, **kwargs: original,
    )
    monkeypatch.setattr(fr_cli, "_paths", lambda *args: None)
    monkeypatch.setattr(
        fr_cli,
        "resolved_config_document",
        lambda *args: ({}, None),
    )

    def compile_plan(
        root,
        repaired,
        paths,
        *,
        runtime_request,
        **kwargs,
    ):
        observed["runtime_request"] = runtime_request
        observed["workfile"] = repaired
        return {"blocking": False, "rows": []}

    monkeypatch.setattr(fr_cli, "compile_study_plan", compile_plan)
    _, repaired, _, plan = fr_cli._load_and_plan(
        SimpleNamespace(
            workfile=original.path,
            resolve_imagery_year=2019,
            resolve_cdl_year=2019,
            out=None,
            refresh_sources=False,
            offline=True,
        )
    )
    resolution = plan["classification_temporal_resolution"]
    assert resolution["status"] == "TEMPORAL_SELECTION_RESOLVED"
    assert resolution["requested_pair"] == {
        "imagery_year": 2023,
        "cdl_year": 2023,
    }
    assert resolution["resolved_pair"] == {
        "imagery_year": 2019,
        "cdl_year": 2019,
    }
    assert resolution["raster_acquisition_during_selection"] is False
    assert observed["runtime_request"]["imagery_year"] == 2019
    assert observed["runtime_request"]["cdl_year"] == 2019
    assert repaired.spec.time.crop_year == 2019


def test_v4_workfile_threshold_override_source_reaches_execution(
    tmp_path,
    monkeypatch,
):
    captured: dict = {}
    recipe = load_named_recipe(
        ROOT,
        "naip_cdl_index_hybrid_classification_audit",
    )
    request = ClassificationRuntimeRequest(
        request_bbox_epsg_4326=(-83.1, 39.9, -83.0, 40.0),
        imagery_start=date(2023, 4, 1),
        imagery_end=date(2023, 10, 31),
        imagery_year=2023,
        cdl_year=2023,
    )
    plan = {
        "resolved_config": {
            "values": {
                "reuse_mode": {"value": "auto"},
                "open_when_complete": {"value": False},
                "maximum_download_mb": {"value": 75},
                "service_tile_size": {"value": 512},
                "resolution_m": {"value": 1.2},
            }
        }
    }

    def fake_execute(*args, **kwargs):
        captured.update(kwargs)
        return tmp_path / "preview.png"

    monkeypatch.setattr(fr_cli, "execute_recipe", fake_execute)
    monkeypatch.setattr(fr_cli, "_recipe_renderer", lambda: object())
    fr_cli._execute_classification_request(
        ROOT,
        SimpleNamespace(
            spec=SimpleNamespace(
                name="override",
                classification=recipe.classification,
            )
        ),
        plan,
        request,
        recipe=recipe,
        recipe_raw={},
    )
    assert (
        captured["confidence_threshold_source"]
        == "configured_override"
    )


def test_main_emits_pure_structured_blocked_json(monkeypatch, capsys):
    error = RecoverableRecipeExecutionError(
        failure_document(
            "no_intersecting_imagery",
            evidence={
                "requested_year": 2023,
                "available_intersecting_years": [2021],
            },
        )
    )

    def handler(_args):
        raise error

    args = SimpleNamespace(handler=handler, json=True, debug=False)
    monkeypatch.setattr(
        fr_cli,
        "build_parser",
        lambda: SimpleNamespace(parse_args=lambda _argv: args),
    )
    assert fr_cli.main([]) == 2
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "BLOCKED"
    assert (
        payload["recoverable_failure"]["failure_type"]
        == "imagery_year_unavailable"
    )
    assert captured.err == ""


def test_main_emits_pure_json_for_plan_blocking(monkeypatch, capsys):
    def handler(_args):
        raise fr_cli.BlockedCommandError("offline plan is blocked")

    args = SimpleNamespace(handler=handler, json=True, debug=False)
    monkeypatch.setattr(
        fr_cli,
        "build_parser",
        lambda: SimpleNamespace(parse_args=lambda _argv: args),
    )
    assert fr_cli.main([]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "status": "BLOCKED",
        "error_type": "BlockedCommandError",
        "message": "offline plan is blocked",
    }
    assert captured.err == ""


def test_repaired_values_and_intervention_reach_handoff_and_receipts(
    tmp_path,
    monkeypatch,
):
    recipe = load_named_recipe(ROOT, "naip_cdl_classification_audit")
    source_root = tmp_path / "cached"
    source_root.mkdir()
    source_records = []
    for name, year in (
        ("naip_multispectral", 2021),
        ("cdl_classes", 2023),
    ):
        path = source_root / name / "data" / f"{name}.tif"
        path.parent.mkdir(parents=True)
        path.write_bytes(name.encode("ascii"))
        source_records.append(
            AssetRecord(
                asset_name=name,
                source_family=(
                    "USGS_NAIP"
                    if name == "naip_multispectral"
                    else "USDA_CDL"
                ),
                temporal_key=year,
                bbox_epsg_4326=(-83.2, 39.8, -82.9, 40.1),
                extent_native=(0.0, 0.0, 10.0, 10.0),
                crs="EPSG:3857",
                pixel_size=(1.0, 1.0),
                pixel_size_m=1.0,
                width=10,
                height=10,
                nodata=(0,),
                semantic_type=(
                    "continuous_multiband_imagery"
                    if name == "naip_multispectral"
                    else "categorical"
                ),
                checksum=None,
                local_path=str(path),
                originating_handoff=str(path.parents[2]),
                validation_state="valid",
                validation_errors=(),
                can_crop_locally=True,
                requires_reprojection=False,
            )
        )
    monkeypatch.setattr(
        ag_execution,
        "discover_cached_assets",
        lambda *args, **kwargs: source_records,
    )
    monkeypatch.setattr(
        ag_execution,
        "_run_selective_acquisition",
        lambda *args, **kwargs: pytest.fail(
            "compatible cached fixtures must not access a source"
        ),
    )

    def verified(staging, recipe_value, bbox, year, imagery_year=None):
        resolved = ag_execution._find_resolved_paths(
            staging,
            year,
            recipe_value.required_assets,
            imagery_year=imagery_year,
        )
        return {
            name: replace(
                next(
                    record
                    for record in source_records
                    if record.asset_name == name
                ),
                local_path=str(path),
                originating_handoff=str(staging),
            )
            for name, path in resolved.items()
        }

    monkeypatch.setattr(ag_execution, "_verify_resolved", verified)
    monkeypatch.setattr(
        ag_classification,
        "classification_dependency_status",
        lambda: {"available": True},
    )

    repair_summary_seen: list[dict] = []

    def fake_classification(
        naip_path,
        cdl_path,
        staging,
        recipe_value,
        *,
        year,
        cdl_year,
        analysis_aoi_epsg_4326,
        contract_repair,
    ):
        repair_summary = intervention_reference(contract_repair)
        repair_summary_seen.append(repair_summary)
        analysis = staging / "analysis" / "classification"
        data = staging / "data"
        analysis.mkdir(parents=True)
        for filename in (
            "holdout_confusion_matrix.csv",
            "holdout_confusion_matrix.json",
            "weak_label_metrics.json",
            "training_receipt.json",
            "model_receipt.json",
            "feature_contract.json",
            "class_agreement_matrix.csv",
            "class_agreement_matrix.json",
            "disagreement_summary.json",
            "class_area_inventory.csv",
            "area_accounting.json",
        ):
            path = analysis / filename
            if filename in {"training_receipt.json", "model_receipt.json"}:
                path.write_text(
                    json.dumps({"repair_provenance": repair_summary})
                )
            else:
                path.write_text("{}\n")
        for filename in (
            "cdl_superclasses.cog.tif",
            "cdl_training_cores.cog.tif",
            f"naip_{year}_surface_classification.cog.tif",
            f"naip_{year}_classification_confidence.cog.tif",
            f"naip_{year}_cdl_agreement_state.cog.tif",
        ):
            (data / filename).write_bytes(b"synthetic")
        return {
            "mapping": {
                "mapping_id": "test-mapping",
                "contract_version": "1",
            },
            "mapping_sha256": "a" * 64,
            "source_validation": {"status": "PASS"},
            "confidence_provenance": {
                "confidence_metric": "maximum_class_probability",
                "confidence_threshold": 0.6,
                "unknown_class_code": 0,
                "threshold_source": "recipe_default",
            },
            "model_receipt": {"repair_provenance": repair_summary},
            "training_receipt": {"repair_provenance": repair_summary},
            "metrics": {"status": "PASS"},
            "agreement": {"status": "PASS"},
        }

    monkeypatch.setattr(
        ag_classification,
        "execute_classification",
        fake_classification,
    )

    def fake_publication(
        destination,
        *,
        contract_repair,
        analysis_aoi_epsg_4326,
        year,
        cdl_year,
        **kwargs,
    ):
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"preview")
        (destination.parent / "classification_legend.json").write_text(
            "{}\n"
        )
        return destination, {
            "imagery_year": year,
            "cdl_year": cdl_year,
            "analysis_aoi_epsg_4326": analysis_aoi_epsg_4326,
            "repair_provenance": intervention_reference(contract_repair),
        }

    monkeypatch.setattr(
        ag_publication,
        "render_classification_audit",
        fake_publication,
    )
    original = ClassificationRuntimeRequest.from_workfile(workfile(tmp_path))
    area = build_point_buffer_area(
        -83.05,
        39.95,
        "1",
        "kilometers",
        "circle",
    )
    resolved = original.with_imagery_year(2021).with_constructed_area(area)
    source_records[:] = [
        replace(
            record,
            bbox_epsg_4326=resolved.request_bbox_epsg_4326,
        )
        for record in source_records
    ]
    intervention = build_intervention_record(
        original_request=original,
        resolved_request=resolved,
        failure=recoverable(
            "requested_year_unavailable",
            {
                "requested_year": 2023,
                "available_intersecting_years": [2021],
            },
        ),
        alternatives_shown=[2021],
        source_evidence={"test": "synthetic"},
        original_plan_sha256="1" * 64,
        resolved_plan_sha256="2" * 64,
        confirmation_outcome="accepted",
    )
    preview = ag_execution.execute_recipe(
        tmp_path,
        recipe=recipe,
        recipe_raw=json.loads(
            (
                ROOT
                / "recipes/ag/naip_cdl_classification_audit.json"
            ).read_text()
        ),
        name="repaired_provenance",
        bbox=resolved.request_bbox_epsg_4326,
        start=resolved.imagery_start.isoformat(),
        end=resolved.imagery_end.isoformat(),
        year=resolved.cdl_year,
        imagery_year=resolved.imagery_year,
        reuse_mode="only",
        open_preview=False,
        max_total_bytes=75_000_000,
        service_tile_size=512,
        renderer=lambda *args: pytest.fail("V3 uses classification renderer"),
        naip_resolution_m=1.2,
        analysis_aoi_epsg_4326=resolved.analysis_aoi_epsg_4326,
        contract_repair=intervention,
    )
    handoff = preview.parents[2]
    manifest = json.loads((handoff / "manifest.json").read_text())
    receipt = json.loads(
        next(handoff.glob("preview/*/recipe_receipt.json")).read_text()
    )
    assert manifest["actual_imagery"]["year"] == 2021
    assert manifest["order"]["cdl_year"] == 2023
    assert "temporally mismatched" in manifest["classification"][
        "scientific_claim"
    ]
    assert manifest["human_repair_occurred"] is True
    assert (
        manifest["resolved_location"]["analysis_aoi_epsg_4326"]
        == resolved.analysis_aoi_epsg_4326
    )
    assert manifest["resolved_location"][
        "acquisition_uses_request_envelope"
    ] is True
    assert receipt["contract_repair"]["original_request"] == original.as_dict()
    assert "temporally mismatched" in receipt["scientific_claim"]
    assert (
        receipt["classification"]["publication"]["repair_provenance"][
            "intervention_id"
        ]
        == intervention["intervention_id"]
    )
    repair_reference = repair_summary_seen[0]
    assert repair_reference["resolved_request"]["imagery_year"] == 2021
    assert repair_reference["resolved_request"]["cdl_year"] == 2023
    assert repair_reference["resolved_request"][
        "analysis_aoi_geometry_sha256"
    ] == (
        intervention["resolved_request"]["spatial_construction"][
            "geometry_sha256"
        ]
    )
    assert "analysis_aoi_epsg_4326" not in repair_reference
    intervention_lines = (
        handoff / "interventions.jsonl"
    ).read_text().splitlines()
    assert len(intervention_lines) == 1
    assert (
        json.loads(intervention_lines[0])["intervention_id"]
        == intervention["intervention_id"]
    )
    for filename in ("training_receipt.json", "model_receipt.json"):
        derived_receipt = json.loads(
            (
                handoff / "analysis" / "classification" / filename
            ).read_text()
        )
        assert derived_receipt["repair_provenance"][
            "human_repair_occurred"
        ] is True

    report = inspect_handoff(handoff)
    assert report["contract_repair"]["human_repair_occurred"] is True
    assert (
        report["contract_repair"]["intervention_id"]
        == intervention["intervention_id"]
    )

    rerender_calls: list[dict] = []

    def fake_rerender(destination, **kwargs):
        rerender_calls.append(kwargs)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"rerendered preview")
        (destination.parent / "classification_legend.json").write_text(
            "{}\n"
        )
        return destination, {
            "imagery_year": kwargs["year"],
            "cdl_year": kwargs["cdl_year"],
            "analysis_aoi_epsg_4326": kwargs[
                "analysis_aoi_epsg_4326"
            ],
            "repair_provenance": intervention_reference(
                kwargs["contract_repair"]
            ),
        }

    monkeypatch.setattr(
        derive_publication,
        "render_classification_audit",
        fake_rerender,
    )
    derived = derive_publication.derive_publication(
        handoff,
        tmp_path / "derived",
        name="repaired_publication",
    )
    assert derived.is_dir()
    assert len(rerender_calls) == 1
    rerender = rerender_calls[0]
    assert rerender["year"] == 2021
    assert rerender["cdl_year"] == 2023
    assert (
        rerender["analysis_aoi_epsg_4326"]
        == resolved.analysis_aoi_epsg_4326
    )
    assert (
        rerender["contract_repair"]["intervention_id"]
        == intervention["intervention_id"]
    )
