from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Sequence

from faster_raster.adapter_contract import stable_json


ALTERNATIVES_SCHEMA_VERSION = "fasterraster.temporal-alternatives/v1"
RESOLUTION_SCHEMA_VERSION = "fasterraster.temporal-resolution/v1"


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
