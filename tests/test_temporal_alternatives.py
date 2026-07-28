from __future__ import annotations

from copy import deepcopy

import pytest

from faster_raster.temporal_alternatives import (
    alternatives_from_years,
    build_classification_temporal_alternatives,
    build_temporal_alternatives,
    explicit_classification_temporal_resolution,
    select_classification_temporal_candidate,
    select_temporal_candidate,
)


def test_equal_distance_prefers_earlier_candidate_deterministically():
    first = alternatives_from_years(
        2022,
        [2023, 2021],
        source_id="naip",
        provider="USGS",
        product="NAIP",
    )
    second = alternatives_from_years(
        2022,
        [2021, 2023],
        source_id="naip",
        provider="USGS",
        product="NAIP",
    )
    assert [item["candidate_time"] for item in first["candidates"]] == ["2021", "2023"]
    assert first == second
    assert first["status"] == "AWAITING_TEMPORAL_SELECTION"


def test_known_complete_coverage_precedes_partial_and_unknown():
    result = build_temporal_alternatives(
        "2022",
        [
            {"candidate_time": "2021", "coverage_fraction": "unknown"},
            {"candidate_time": "2020", "coverage_fraction": 1.0},
            {"candidate_time": "2019", "coverage_fraction": 0.7},
        ],
        source_id="fixture",
    )
    assert [item["candidate_time"] for item in result["candidates"]] == [
        "2020",
        "2019",
        "2021",
    ]
    assert result["candidates"][2]["coverage_fraction"] == "unknown"


def test_tolerance_filters_candidates_without_fabricating_values():
    result = alternatives_from_years(
        2022,
        [2010, 2021],
        source_id="naip",
        tolerance_days=400,
    )
    assert [item["candidate_time"] for item in result["candidates"]] == ["2021"]
    assert result["candidates"][0]["cloud_fraction"] == "unknown"


def test_explicit_selection_creates_new_hash_without_mutation():
    alternatives = alternatives_from_years(2022, [2021], source_id="naip")
    original = deepcopy(alternatives)
    resolution = select_temporal_candidate(alternatives, "2021")
    assert alternatives == original
    assert resolution["selected_time"] == "2021"
    assert resolution["selection_method"] == "explicit_user_selection"
    assert resolution["resolved_contract_sha256"] != alternatives["search_contract_sha256"]


def test_no_exact_or_alternative_candidate_is_explicit():
    result = alternatives_from_years(2022, [2022], source_id="naip")
    assert result["status"] == "NO_TEMPORAL_ALTERNATIVES"
    assert result["candidates"] == []


def test_selection_rejects_unlisted_candidate():
    alternatives = alternatives_from_years(2022, [2021], source_id="naip")
    with pytest.raises(ValueError, match="not a unique ranked alternative"):
        select_temporal_candidate(alternatives, "2020")


def test_greeley_classification_ranks_coherent_2019_pair_first():
    alternatives = build_classification_temporal_alternatives(
        2023,
        2023,
        [2019],
        available_cdl_years=[2019],
        source_evidence={
            "bbox_epsg_4326": [
                -104.80,
                40.34,
                -104.58,
                40.51,
            ]
        },
    )
    assert alternatives["status"] == "AWAITING_TEMPORAL_SELECTION"
    assert alternatives["coherent_pair_status"] == (
        "AWAITING_TEMPORAL_SELECTION"
    )
    assert alternatives["candidates"][0]["candidate_id"] == (
        "coherent:2019:2019"
    )
    assert alternatives["candidates"][0]["coherent_pair"] is True
    assert alternatives["network_bytes"] == 0
    assert alternatives["raster_acquisition_authorized"] is False

    original = deepcopy(alternatives)
    resolution = select_classification_temporal_candidate(
        alternatives,
        "coherent:2019:2019",
    )
    assert alternatives == original
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
    assert resolution["resolved_contract_sha256"]


def test_classification_temporal_states_are_explicit():
    exact = build_classification_temporal_alternatives(
        2023,
        2023,
        [2023],
        available_cdl_years=[2023],
    )
    assert exact["status"] == "EXACT_TIME_AVAILABLE"

    unavailable = build_classification_temporal_alternatives(
        2023,
        2023,
        [],
        available_cdl_years=[],
    )
    assert unavailable["status"] == "NO_COHERENT_ALTERNATIVE"
    assert unavailable["coherent_pair_status"] == (
        "NO_COHERENT_ALTERNATIVE"
    )


def test_explicit_cli_pair_creates_immutable_resolution():
    result = explicit_classification_temporal_resolution(
        2023,
        2023,
        2019,
        2019,
    )
    assert result["selection_method"] == (
        "explicit_cli_year_arguments"
    )
    assert result["resolved_pair"]["imagery_year"] == 2019
    assert result["resolved_pair"]["cdl_year"] == 2019
