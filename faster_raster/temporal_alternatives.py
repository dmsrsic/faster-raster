from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Sequence

from faster_raster.adapter_contract import stable_json


ALTERNATIVES_SCHEMA_VERSION = "fasterraster.temporal-alternatives/v1"
RESOLUTION_SCHEMA_VERSION = "fasterraster.temporal-resolution/v1"
CLASSIFICATION_ALTERNATIVES_SCHEMA_VERSION = (
    "fasterraster.classification-temporal-alternatives/v1"
)
CLASSIFICATION_RESOLUTION_SCHEMA_VERSION = (
    "fasterraster.classification-temporal-resolution/v1"
)


def _hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(stable_json(dict(value)).encode("utf-8")).hexdigest()


def _as_date(value: Any) -> date:
    text = str(value)
    if len(text) == 4 and text.isdigit():
        return date(int(text), 1, 1)
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(f"invalid temporal value {value!r}; use YYYY or YYYY-MM-DD") from exc


def _optional_fraction(value: Any, name: str) -> float | None:
    if value is None or value == "unknown":
        return None
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return result


def _optional_positive(value: Any, name: str) -> float | None:
    if value is None or value == "unknown":
        return None
    result = float(value)
    if result < 0:
        raise ValueError(f"{name} must not be negative")
    return result


def _unknown_last(value: float | int | None) -> tuple[int, float]:
    return (1, 0.0) if value is None else (0, float(value))


def _normalized_candidate(
    raw: Mapping[str, Any],
    *,
    requested_time: str,
    requested: Mapping[str, Any],
) -> dict[str, Any]:
    if "candidate_time" not in raw:
        raise ValueError("temporal candidate is missing candidate_time")
    candidate_time = str(raw["candidate_time"])
    requested_date = _as_date(requested_time)
    candidate_date = _as_date(candidate_time)
    coverage = _optional_fraction(raw.get("coverage_fraction"), "coverage_fraction")
    cloud = _optional_fraction(raw.get("cloud_fraction"), "cloud_fraction")
    nodata = _optional_fraction(raw.get("nodata_fraction"), "nodata_fraction")
    resolution = _optional_positive(raw.get("spatial_resolution_m"), "spatial_resolution_m")
    estimate = _optional_positive(raw.get("estimated_transfer_bytes"), "estimated_transfer_bytes")
    same_provider = bool(
        raw.get(
            "same_provider",
            requested.get("provider") is not None
            and raw.get("provider") == requested.get("provider"),
        )
    )
    same_product = bool(
        raw.get(
            "same_product",
            requested.get("product") is not None
            and raw.get("product") == requested.get("product"),
        )
    )
    same_processing_family = bool(
        raw.get(
            "same_processing_family",
            requested.get("processing_family") is not None
            and raw.get("processing_family") == requested.get("processing_family"),
        )
    )
    return {
        "candidate_time": candidate_time,
        "distance_days": abs((candidate_date - requested_date).days),
        "season_distance_days": min(
            abs(candidate_date.timetuple().tm_yday - requested_date.timetuple().tm_yday),
            366
            - abs(candidate_date.timetuple().tm_yday - requested_date.timetuple().tm_yday),
        ),
        "coverage_fraction": coverage if coverage is not None else "unknown",
        "same_provider": same_provider,
        "same_product": same_product,
        "same_processing_family": same_processing_family,
        "cloud_fraction": cloud if cloud is not None else "unknown",
        "nodata_fraction": nodata if nodata is not None else "unknown",
        "spatial_resolution_m": resolution if resolution is not None else "unknown",
        "asset_compatible": (
            bool(raw["asset_compatible"])
            if raw.get("asset_compatible") is not None
            else "unknown"
        ),
        "accessible": (
            bool(raw["accessible"]) if raw.get("accessible") is not None else "unknown"
        ),
        "estimated_transfer_bytes": estimate if estimate is not None else "unknown",
        "verification_status": str(raw.get("verification_status") or "unknown"),
        "provider": raw.get("provider"),
        "product": raw.get("product"),
        "processing_family": raw.get("processing_family"),
        "candidate_id": str(raw.get("candidate_id") or candidate_time),
        "metadata": dict(raw.get("metadata") or {}),
    }


def _sort_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    coverage = candidate["coverage_fraction"]
    coverage_value = None if coverage == "unknown" else float(coverage)
    cloud = candidate["cloud_fraction"]
    nodata = candidate["nodata_fraction"]
    resolution = candidate["spatial_resolution_m"]
    estimate = candidate["estimated_transfer_bytes"]
    return (
        0 if candidate["same_provider"] else 1,
        0 if candidate["same_product"] else 1,
        0 if candidate["same_processing_family"] else 1,
        0 if coverage_value == 1.0 else 1,
        1 if coverage_value is None else 0,
        -(coverage_value or 0.0),
        int(candidate["distance_days"]),
        int(candidate["season_distance_days"]),
        _unknown_last(None if cloud == "unknown" else float(cloud)),
        _unknown_last(None if nodata == "unknown" else float(nodata)),
        0 if candidate["asset_compatible"] is True else 1,
        _unknown_last(None if resolution == "unknown" else float(resolution)),
        0 if candidate["accessible"] is True else 1,
        _unknown_last(None if estimate == "unknown" else float(estimate)),
        0 if candidate["verification_status"] in {"verified", "live_verified"} else 1,
        _as_date(candidate["candidate_time"]),
        str(candidate["candidate_id"]),
    )

def build_temporal_alternatives(
    requested_time: str | int,
    candidates: Iterable[Mapping[str, Any] | str | int],
    *,
    source_id: str,
    provider: str | None = None,
    product: str | None = None,
    processing_family: str | None = None,
    tolerance_days: int | None = None,
    search_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    requested_text = str(requested_time)
    _as_date(requested_text)
    if tolerance_days is not None and tolerance_days < 0:
        raise ValueError("tolerance_days must not be negative")
    requested = {
        "provider": provider,
        "product": product,
        "processing_family": processing_family,
    }
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for value in candidates:
        raw = {"candidate_time": str(value)} if not isinstance(value, Mapping) else dict(value)
        candidate = _normalized_candidate(
            raw,
            requested_time=requested_text,
            requested=requested,
        )
        identity = (candidate["candidate_time"], candidate["candidate_id"])
        if candidate["candidate_time"] == requested_text or identity in seen:
            continue
        seen.add(identity)
        if tolerance_days is not None and candidate["distance_days"] > tolerance_days:
            continue
        normalized.append(candidate)
    normalized.sort(key=_sort_key)
    minimum_distance = min(
        (int(item["distance_days"]) for item in normalized),
        default=None,
    )
    for rank, candidate in enumerate(normalized, start=1):
        reasons: list[str] = []
        if candidate["same_provider"]:
            reasons.append("same_provider")
        if candidate["same_product"]:
            reasons.append("same_product")
        if candidate["same_processing_family"]:
            reasons.append("same_processing_family")
        if candidate["coverage_fraction"] == 1.0:
            reasons.append("complete_coverage")
        elif candidate["coverage_fraction"] != "unknown":
            reasons.append("partial_coverage")
        else:
            reasons.append("coverage_unknown")
        if candidate["distance_days"] == minimum_distance:
            reasons.append("closest_time")
        if candidate["asset_compatible"] is True:
            reasons.append("asset_compatible")
        if candidate["verification_status"] in {"verified", "live_verified"}:
            reasons.append("source_verified")
        candidate["rank"] = rank
        candidate["reason_codes"] = reasons
    search_contract = {
        "schema_version": ALTERNATIVES_SCHEMA_VERSION,
        "source_id": source_id,
        "requested_time": requested_text,
        "requested_provider": provider,
        "requested_product": product,
        "requested_processing_family": processing_family,
        "tolerance_days": tolerance_days,
        "search_metadata": dict(search_metadata or {}),
        "candidate_metadata": [
            {key: value for key, value in item.items() if key not in {"rank", "reason_codes"}}
            for item in normalized
        ],
    }
    result = {
        "schema_version": ALTERNATIVES_SCHEMA_VERSION,
        "source_id": source_id,
        "requested_time": requested_text,
        "status": (
            "AWAITING_TEMPORAL_SELECTION"
            if normalized
            else "NO_TEMPORAL_ALTERNATIVES"
        ),
        "search_contract_sha256": _hash(search_contract),
        "selection_required": bool(normalized),
        "original_request_unchanged": True,
        "candidate_count": len(normalized),
        "candidates": normalized,
        "ranking_policy": [
            "same_provider_product_processing_family",
            "complete_or_highest_known_coverage",
            "absolute_temporal_distance",
            "season_distance",
            "known_lower_cloud_and_nodata",
            "asset_compatibility_and_resolution",
            "accessibility_and_transfer_estimate",
            "verification_status",
            "earlier_time_then_candidate_id",
        ],
    }
    result["temporal_alternatives_sha256"] = _hash(result)
    return result


def select_temporal_candidate(
    alternatives: Mapping[str, Any],
    candidate: str,
) -> dict[str, Any]:
    if alternatives.get("schema_version") != ALTERNATIVES_SCHEMA_VERSION:
        raise ValueError("unsupported temporal alternatives schema")
    if alternatives.get("status") != "AWAITING_TEMPORAL_SELECTION":
        raise ValueError("temporal alternatives are not awaiting selection")
    matches = [
        item
        for item in alternatives.get("candidates") or []
        if str(item.get("candidate_id")) == candidate
        or str(item.get("candidate_time")) == candidate
    ]
    if len(matches) != 1:
        raise ValueError(f"candidate {candidate!r} is not a unique ranked alternative")
    stable = {
        "schema_version": RESOLUTION_SCHEMA_VERSION,
        "status": "RESOLVED",
        "source_id": alternatives.get("source_id"),
        "requested_time": alternatives.get("requested_time"),
        "selected_time": matches[0]["candidate_time"],
        "selected_candidate": matches[0],
        "search_contract_sha256": alternatives.get("search_contract_sha256"),
        "temporal_alternatives_sha256": alternatives.get(
            "temporal_alternatives_sha256"
        ),
        "selection_method": "explicit_user_selection",
        "original_request_unchanged": True,
    }
    stable["resolved_contract_sha256"] = _hash(stable)
    return stable


def alternatives_from_years(
    requested_year: int,
    years: Sequence[int],
    *,
    source_id: str,
    provider: str | None = None,
    product: str | None = None,
    coverage_by_year: Mapping[int, float | None] | None = None,
    tolerance_days: int | None = None,
) -> dict[str, Any]:
    coverage = coverage_by_year or {}
    return build_temporal_alternatives(
        requested_year,
        [
            {
                "candidate_time": str(year),
                "provider": provider,
                "product": product,
                "coverage_fraction": coverage.get(year),
            }
            for year in years
        ],
        source_id=source_id,
        provider=provider,
        product=product,
        tolerance_days=tolerance_days,
    )


def build_classification_temporal_alternatives(
    requested_imagery_year: int,
    requested_cdl_year: int,
    available_imagery_years: Sequence[int],
    *,
    available_cdl_years: Sequence[int] | None = None,
    coverage_by_imagery_year: Mapping[int, float | None] | None = None,
    source_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build explicit imagery-only and coherent-pair repair choices."""

    requested_imagery = int(requested_imagery_year)
    requested_cdl = int(requested_cdl_year)
    imagery_years = sorted(
        {
            int(year)
            for year in available_imagery_years
            if int(year) != requested_imagery
        }
    )
    cdl_years = (
        {int(year) for year in available_cdl_years}
        if available_cdl_years is not None
        else set()
    )
    exact_available = (
        requested_imagery in {
            int(year) for year in available_imagery_years
        }
        and (
            available_cdl_years is None
            or requested_cdl in cdl_years
        )
    )
    coverage = coverage_by_imagery_year or {}
    candidates: list[dict[str, Any]] = []
    for year in imagery_years:
        if year in cdl_years:
            candidates.append(
                {
                    "candidate_id": f"coherent:{year}:{year}",
                    "repair_mode": (
                        "coherent_imagery_and_weak_labels"
                    ),
                    "imagery_year": year,
                    "cdl_year": year,
                    "distance_years": abs(
                        year - requested_imagery
                    ),
                    "coverage_fraction": (
                        coverage.get(year)
                        if coverage.get(year) is not None
                        else "unknown"
                    ),
                    "coherent_pair": True,
                }
            )
        candidates.append(
            {
                "candidate_id": (
                    f"imagery_only:{year}:{requested_cdl}"
                ),
                "repair_mode": "imagery_only",
                "imagery_year": year,
                "cdl_year": requested_cdl,
                "distance_years": abs(year - requested_imagery),
                "coverage_fraction": (
                    coverage.get(year)
                    if coverage.get(year) is not None
                    else "unknown"
                ),
                "coherent_pair": year == requested_cdl,
            }
        )
    candidates.sort(
        key=lambda item: (
            0 if item["coherent_pair"] else 1,
            0
            if item["coverage_fraction"] == 1.0
            else 1,
            1
            if item["coverage_fraction"] == "unknown"
            else 0,
            -(
                0.0
                if item["coverage_fraction"] == "unknown"
                else float(item["coverage_fraction"])
            ),
            int(item["distance_years"]),
            int(item["imagery_year"]),
            str(item["candidate_id"]),
        )
    )
    for rank, candidate in enumerate(candidates, start=1):
        candidate["rank"] = rank
        candidate["reason_codes"] = [
            (
                "coherent_imagery_and_cdl_year"
                if candidate["coherent_pair"]
                else "imagery_only_preserves_requested_cdl"
            ),
            "closest_available_imagery_year"
            if candidate["distance_years"]
            == min(
                (
                    item["distance_years"]
                    for item in candidates
                ),
                default=candidate["distance_years"],
            )
            else "available_imagery_year",
        ]
    coherent_candidates = [
        item for item in candidates if item["coherent_pair"]
    ]
    if exact_available:
        status = "EXACT_TIME_AVAILABLE"
    elif candidates:
        status = "AWAITING_TEMPORAL_SELECTION"
    else:
        status = "NO_COHERENT_ALTERNATIVE"
    search_contract = {
        "schema_version": (
            CLASSIFICATION_ALTERNATIVES_SCHEMA_VERSION
        ),
        "requested_pair": {
            "imagery_year": requested_imagery,
            "cdl_year": requested_cdl,
        },
        "available_imagery_years": imagery_years,
        "available_cdl_years": sorted(cdl_years),
        "source_evidence": dict(source_evidence or {}),
    }
    result = {
        "schema_version": (
            CLASSIFICATION_ALTERNATIVES_SCHEMA_VERSION
        ),
        "status": status,
        "coherent_pair_status": (
            "EXACT_TIME_AVAILABLE"
            if exact_available
            else (
                "AWAITING_TEMPORAL_SELECTION"
                if coherent_candidates
                else "NO_COHERENT_ALTERNATIVE"
            )
        ),
        "requested_pair": search_contract["requested_pair"],
        "original_request_unchanged": True,
        "selection_required": status
        == "AWAITING_TEMPORAL_SELECTION",
        "raster_acquisition_authorized": False,
        "network_bytes": 0,
        "search_contract_sha256": _hash(search_contract),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "ranking_policy": [
            "coherent_imagery_and_cdl_pair",
            "complete_or_highest_known_coverage",
            "absolute_year_distance",
            "earlier_year_then_candidate_id",
        ],
    }
    result["temporal_alternatives_sha256"] = _hash(result)
    return result


def select_classification_temporal_candidate(
    alternatives: Mapping[str, Any],
    candidate_id: str,
    *,
    selection_method: str = "explicit_user_selection",
) -> dict[str, Any]:
    if alternatives.get("schema_version") != (
        CLASSIFICATION_ALTERNATIVES_SCHEMA_VERSION
    ):
        raise ValueError(
            "unsupported classification temporal alternatives schema"
        )
    if alternatives.get("status") != (
        "AWAITING_TEMPORAL_SELECTION"
    ):
        raise ValueError(
            "classification alternatives are not awaiting selection"
        )
    matches = [
        dict(item)
        for item in alternatives.get("candidates") or []
        if str(item.get("candidate_id")) == candidate_id
    ]
    if len(matches) != 1:
        raise ValueError(
            f"candidate {candidate_id!r} is not a unique alternative"
        )
    selected = matches[0]
    stable = {
        "schema_version": (
            CLASSIFICATION_RESOLUTION_SCHEMA_VERSION
        ),
        "status": "TEMPORAL_SELECTION_RESOLVED",
        "requested_pair": dict(
            alternatives.get("requested_pair") or {}
        ),
        "resolved_pair": {
            "imagery_year": int(selected["imagery_year"]),
            "cdl_year": int(selected["cdl_year"]),
        },
        "selected_candidate": selected,
        "selection_method": selection_method,
        "original_request_unchanged": True,
        "raster_acquisition_during_selection": False,
        "search_contract_sha256": alternatives.get(
            "search_contract_sha256"
        ),
        "temporal_alternatives_sha256": alternatives.get(
            "temporal_alternatives_sha256"
        ),
    }
    stable["resolved_contract_sha256"] = _hash(stable)
    return stable


def explicit_classification_temporal_resolution(
    requested_imagery_year: int,
    requested_cdl_year: int,
    resolved_imagery_year: int,
    resolved_cdl_year: int,
) -> dict[str, Any]:
    candidate_id = (
        f"coherent:{resolved_imagery_year}:{resolved_cdl_year}"
        if resolved_imagery_year == resolved_cdl_year
        else (
            f"imagery_only:{resolved_imagery_year}:"
            f"{resolved_cdl_year}"
        )
    )
    alternatives = build_classification_temporal_alternatives(
        requested_imagery_year,
        requested_cdl_year,
        [resolved_imagery_year],
        available_cdl_years=(
            [resolved_cdl_year]
            if resolved_imagery_year == resolved_cdl_year
            else []
        ),
        source_evidence={
            "selection_source": "explicit_cli_year_arguments"
        },
    )
    if alternatives["status"] != "AWAITING_TEMPORAL_SELECTION":
        raise ValueError(
            "explicit temporal resolution does not change the request"
        )
    return select_classification_temporal_candidate(
        alternatives,
        candidate_id,
        selection_method="explicit_cli_year_arguments",
    )
