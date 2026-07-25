from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


BBox = tuple[float, float, float, float]
WEB_MERCATOR_LATITUDE_LIMIT = 85.05112878
AOI_ESTIMATE_TO_NETWORK_CEILING_RATIO = 16
VALID_AG_ASSETS = frozenset(
    {
        "natural",
        "naip_multispectral",
        "ndvi",
        "cdl_classes",
        "cdl_color",
        "hillshade",
    }
)


class BBoxValidationError(ValueError):
    """Raised before network access when an agricultural AOI is unsafe."""


@dataclass
class SourceCoverageError(RuntimeError):
    source: str
    code: str
    detail: str
    evidence: Mapping[str, Any] | None = None

    def __str__(self) -> str:
        return f"{self.source} coverage check failed [{self.code}]: {self.detail}"


def parse_requested_assets(asset_only: bool, value: str | None) -> set[str]:
    if not asset_only:
        return set(VALID_AG_ASSETS)
    requested = {item.strip() for item in (value or "").split(",") if item.strip()}
    if not requested:
        raise BBoxValidationError("--asset-only requires at least one requested asset")
    unknown = sorted(requested - VALID_AG_ASSETS)
    if unknown:
        raise BBoxValidationError(
            "unknown agricultural asset(s): " + ", ".join(unknown)
        )
    return requested


def asset_safety_profile(
    assets: Iterable[str],
    naip_resolution: float | None,
) -> dict[str, tuple[float, int]]:
    requested = set(assets)
    unknown = sorted(requested - VALID_AG_ASSETS)
    if unknown:
        raise BBoxValidationError(
            "unknown agricultural asset(s): " + ", ".join(unknown)
        )
    resolution = 0.6 if naip_resolution is None else float(naip_resolution)
    if not math.isfinite(resolution) or resolution <= 0:
        raise BBoxValidationError("NAIP resolution must be a positive finite number")
    profile: dict[str, tuple[float, int]] = {}
    for name in sorted(requested & {"natural", "ndvi"}):
        profile[name] = (resolution, 4)
    if "naip_multispectral" in requested:
        profile["naip_multispectral"] = (resolution, 4)
    if "cdl_classes" in requested:
        profile["cdl_classes"] = (30.0, 2)
    if "cdl_color" in requested:
        profile["cdl_color"] = (30.0, 4)
    if "hillshade" in requested:
        profile["hillshade"] = (10.0, 2)
    return profile


def normalize_gdalinfo_paths(
    document: Mapping[str, Any],
    handoff_root: Path,
) -> dict[str, Any]:
    """Make top-level GDAL file references stable within a handoff."""

    root = handoff_root.resolve()

    def stable(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        try:
            return Path(value).resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            return value

    normalized = dict(document)
    normalized["description"] = stable(normalized.get("description"))
    files = normalized.get("files")
    if isinstance(files, list):
        normalized["files"] = [stable(value) for value in files]
    return normalized


def validate_bbox(values: Sequence[float]) -> BBox:
    if len(values) != 4:
        raise BBoxValidationError("bbox must be west,south,east,north")
    try:
        west, south, east, north = (float(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise BBoxValidationError(
            "bbox must contain numeric west,south,east,north coordinates"
        ) from exc
    coordinates = (west, south, east, north)
    if not all(math.isfinite(value) for value in coordinates):
        raise BBoxValidationError("bbox coordinates must be finite numbers")
    if not -180 <= west <= 180 or not -180 <= east <= 180:
        raise BBoxValidationError("bbox longitude must be within [-180, 180]")
    if not -90 <= south <= 90 or not -90 <= north <= 90:
        raise BBoxValidationError("bbox latitude must be within [-90, 90]")
    if west > east:
        raise BBoxValidationError(
            "bbox crosses the antimeridian; antimeridian AOIs are not supported"
        )
    if west == east or south == north:
        raise BBoxValidationError("bbox must have nonzero area")
    if south > north:
        raise BBoxValidationError("bbox must satisfy south < north")
    if max(abs(south), abs(north)) > WEB_MERCATOR_LATITUDE_LIMIT:
        raise BBoxValidationError(
            "bbox exceeds the current EPSG:3857 agricultural execution latitude limit"
        )
    return coordinates


def validate_bbox_text(value: str) -> BBox:
    parts = value.split(",")
    if len(parts) != 4:
        raise BBoxValidationError("bbox must be west,south,east,north")
    try:
        return validate_bbox(tuple(float(part) for part in parts))
    except ValueError as exc:
        if isinstance(exc, BBoxValidationError):
            raise
        raise BBoxValidationError(
            "bbox must contain numeric west,south,east,north coordinates"
        ) from exc


def lon_to_web_mercator(longitude: float) -> float:
    return longitude * 20_037_508.342789244 / 180.0


def lat_to_web_mercator(latitude: float) -> float:
    bounded = max(
        min(latitude, WEB_MERCATOR_LATITUDE_LIMIT),
        -WEB_MERCATOR_LATITUDE_LIMIT,
    )
    return (
        math.log(math.tan((90.0 + bounded) * math.pi / 360.0))
        / (math.pi / 180.0)
        * 20_037_508.342789244
        / 180.0
    )


def web_mercator_to_lon(value: float) -> float:
    return value * 180.0 / 20_037_508.342789244


def web_mercator_to_lat(value: float) -> float:
    degrees = value * 180.0 / 20_037_508.342789244
    return math.degrees(2.0 * math.atan(math.exp(math.radians(degrees))) - math.pi / 2.0)


def projected_bbox(bbox: BBox) -> BBox:
    west, south, east, north = validate_bbox(bbox)
    return (
        lon_to_web_mercator(west),
        lat_to_web_mercator(south),
        lon_to_web_mercator(east),
        lat_to_web_mercator(north),
    )


def estimate_uncompressed_asset_bytes(
    bbox: BBox,
    asset_resolutions: Mapping[str, tuple[float, int]],
) -> int:
    xmin, ymin, xmax, ymax = projected_bbox(bbox)
    width = xmax - xmin
    height = ymax - ymin
    total = 0
    for resolution, bytes_per_pixel in asset_resolutions.values():
        if not math.isfinite(resolution) or resolution <= 0:
            raise BBoxValidationError("asset resolution must be a positive finite number")
        columns = math.ceil(width / resolution)
        rows = math.ceil(height / resolution)
        total += columns * rows * bytes_per_pixel
    return total


def validate_aoi_safety(
    bbox: BBox,
    *,
    maximum_network_bytes: int,
    asset_resolutions: Mapping[str, tuple[float, int]],
) -> dict[str, int]:
    if maximum_network_bytes <= 0:
        raise BBoxValidationError("maximum network bytes must be positive")
    estimated = estimate_uncompressed_asset_bytes(bbox, asset_resolutions)
    safety_limit = maximum_network_bytes * AOI_ESTIMATE_TO_NETWORK_CEILING_RATIO
    if estimated > safety_limit:
        raise BBoxValidationError(
            "bbox exceeds the bounded agricultural execution safety envelope: "
            f"estimated uncompressed assets {estimated:,} bytes exceed "
            f"{AOI_ESTIMATE_TO_NETWORK_CEILING_RATIO}x the configured network ceiling"
        )
    return {
        "estimated_uncompressed_asset_bytes": estimated,
        "configured_network_ceiling_bytes": maximum_network_bytes,
        "estimate_safety_limit_bytes": safety_limit,
    }


def required_source_families(assets: Iterable[str]) -> tuple[str, ...]:
    requested = set(assets)
    sources = []
    if requested & {"natural", "naip_multispectral", "ndvi"}:
        sources.append("USGS_NAIP")
    if requested & {"cdl_classes", "cdl_color"}:
        sources.append("USDA_CDL")
    if "hillshade" in requested:
        sources.append("USGS_3DEP")
    return tuple(sources)


def _extent_bbox(metadata: Mapping[str, Any], source: str) -> BBox:
    extent = metadata.get("fullExtent") or metadata.get("extent")
    if not isinstance(extent, Mapping):
        raise SourceCoverageError(
            source,
            "invalid_response",
            "service metadata did not provide a usable full extent",
        )
    try:
        xmin = float(extent["xmin"])
        ymin = float(extent["ymin"])
        xmax = float(extent["xmax"])
        ymax = float(extent["ymax"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceCoverageError(
            source,
            "invalid_response",
            "service extent coordinates were missing or nonnumeric",
        ) from exc
    if not all(math.isfinite(value) for value in (xmin, ymin, xmax, ymax)) or xmin >= xmax or ymin >= ymax:
        raise SourceCoverageError(
            source,
            "invalid_response",
            "service extent coordinates were invalid",
        )
    spatial_reference = extent.get("spatialReference") or metadata.get("spatialReference") or {}
    wkid = spatial_reference.get("latestWkid") or spatial_reference.get("wkid")
    try:
        wkid_value = int(wkid)
    except (TypeError, ValueError) as exc:
        raise SourceCoverageError(
            source,
            "invalid_response",
            "service extent spatial reference was missing",
        ) from exc
    if wkid_value in {4326, 4269}:
        if xmin < -180 or xmax > 180 or ymin < -90 or ymax > 90:
            raise SourceCoverageError(
                source,
                "invalid_response",
                "service extent exceeds its reported geographic coordinate domain",
            )
        return (xmin, ymin, xmax, ymax)
    if wkid_value in {3857, 102100, 102113}:
        return (
            web_mercator_to_lon(xmin),
            max(web_mercator_to_lat(ymin), -WEB_MERCATOR_LATITUDE_LIMIT),
            web_mercator_to_lon(xmax),
            min(web_mercator_to_lat(ymax), WEB_MERCATOR_LATITUDE_LIMIT),
        )
    raise SourceCoverageError(
        source,
        "invalid_response",
        f"service extent uses unsupported spatial reference {wkid_value}",
    )


def validate_service_extent(
    metadata: Mapping[str, Any],
    bbox: BBox,
    *,
    source: str,
) -> dict[str, Any]:
    if metadata.get("error"):
        raise SourceCoverageError(
            source,
            "service_unavailable",
            f"service metadata reported an error: {metadata['error']}",
        )
    service_bbox = _extent_bbox(metadata, source)
    west, south, east, north = validate_bbox(bbox)
    fully_contains = (
        service_bbox[0] <= west
        and service_bbox[1] <= south
        and service_bbox[2] >= east
        and service_bbox[3] >= north
    )
    if not fully_contains:
        raise SourceCoverageError(
            source,
            "bbox_outside_coverage",
            "requested bbox is not fully contained by the source-reported service extent",
            {"requested_bbox": list(bbox), "service_bbox": list(service_bbox)},
        )
    return {
        "status": "PASS",
        "requested_bbox": list(bbox),
        "service_bbox_epsg_4326": list(service_bbox),
        "fully_contains_requested_bbox": True,
    }


def catalog_features(response: Mapping[str, Any], *, source: str) -> list[dict[str, Any]]:
    if response.get("error"):
        raise SourceCoverageError(
            source,
            "service_unavailable",
            f"catalog response reported an error: {response['error']}",
        )
    features = response.get("features")
    if not isinstance(features, list):
        raise SourceCoverageError(
            source,
            "invalid_response",
            "catalog response did not contain a features list",
        )
    if not all(isinstance(feature, dict) for feature in features):
        raise SourceCoverageError(
            source,
            "invalid_response",
            "catalog response contained a malformed feature",
        )
    return features


def catalog_years(response: Mapping[str, Any], *, source: str) -> list[int]:
    years: set[int] = set()
    for feature in catalog_features(response, source=source):
        attributes = feature.get("attributes")
        if not isinstance(attributes, Mapping):
            continue
        value = attributes.get("Year")
        try:
            year = int(value)
        except (TypeError, ValueError):
            continue
        if 1900 <= year <= 2200:
            years.add(year)
    return sorted(years)


def parse_catalog_acquisition_date(value: Any) -> date | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            return None
        seconds = float(value)
        if abs(seconds) >= 10_000_000_000:
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, timezone.utc).date()
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return parse_catalog_acquisition_date(int(text))
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None


def catalog_acquisition_dates(
    response: Mapping[str, Any],
    *,
    source: str,
    year: int | None = None,
) -> list[date]:
    values: set[date] = set()
    for feature in catalog_features(response, source=source):
        attributes = feature.get("attributes")
        if not isinstance(attributes, Mapping):
            continue
        if year is not None:
            try:
                if int(attributes.get("Year")) != year:
                    continue
            except (TypeError, ValueError):
                continue
        parsed = parse_catalog_acquisition_date(
            attributes.get("acquisition_date")
        )
        if parsed is not None:
            values.add(parsed)
    return sorted(values)


def validate_naip_catalog(
    response: Mapping[str, Any],
    *,
    requested_year: int,
    available_response: Mapping[str, Any] | None = None,
    requested_start: date | str | None = None,
    requested_end: date | str | None = None,
) -> dict[str, Any]:
    source = "USGS_NAIP"
    features = catalog_features(response, source=source)
    available_years = (
        catalog_years(available_response, source=source)
        if available_response is not None
        else []
    )
    start = (
        date.fromisoformat(requested_start)
        if isinstance(requested_start, str)
        else requested_start
    )
    end = (
        date.fromisoformat(requested_end)
        if isinstance(requested_end, str)
        else requested_end
    )
    if bool(start) != bool(end):
        raise ValueError(
            "requested NAIP date validation requires both start and end"
        )
    if start is not None and end is not None:
        if start >= end:
            raise ValueError("requested NAIP start must precede end")
        if start.year != requested_year or end.year != requested_year:
            raise ValueError(
                "requested NAIP date range must match requested imagery year"
            )
    if not features:
        available_dates = (
            catalog_acquisition_dates(
                available_response,
                source=source,
                year=requested_year,
            )
            if available_response is not None
            else []
        )
        if start is not None and requested_year in available_years:
            if available_dates:
                raise SourceCoverageError(
                    source,
                    "date_range_unavailable",
                    f"no NAIP {requested_year} catalog records intersect the "
                    f"requested date range {start.isoformat()} through "
                    f"{end.isoformat()}",
                    {
                        "requested_year": requested_year,
                        "requested_date_range": {
                            "start": start.isoformat(),
                            "end": end.isoformat(),
                        },
                        "available_acquisition_dates": [
                            value.isoformat() for value in available_dates
                        ],
                        "available_intersecting_years": available_years,
                    },
                )
            raise SourceCoverageError(
                source,
                "invalid_response",
                "NAIP reported same-year intersecting records but did not "
                "provide parseable acquisition dates; availability cannot "
                "be classified safely",
                {
                    "requested_year": requested_year,
                    "requested_date_range": {
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                    },
                    "available_intersecting_years": available_years,
                },
            )
        raise SourceCoverageError(
            source,
            "no_intersecting_imagery",
            f"no NAIP {requested_year} catalog records intersect the requested bbox; "
            f"available intersecting years: {available_years}",
            {"requested_year": requested_year, "available_intersecting_years": available_years},
        )
    wrong_years: set[int] = set()
    resolutions: list[float] = []
    selected_dates: list[date] = []
    for feature in features:
        attributes = feature.get("attributes") or {}
        try:
            feature_year = int(attributes.get("Year"))
        except (TypeError, ValueError) as exc:
            raise SourceCoverageError(
                source,
                "invalid_response",
                "an intersecting catalog record did not report a valid Year",
            ) from exc
        if feature_year != requested_year:
            wrong_years.add(feature_year)
        if start is not None and end is not None:
            acquisition_date = parse_catalog_acquisition_date(
                attributes.get("acquisition_date")
            )
            if acquisition_date is None:
                raise SourceCoverageError(
                    source,
                    "invalid_response",
                    "an intersecting NAIP catalog record did not report a "
                    "parseable acquisition_date",
                )
            if not start <= acquisition_date <= end:
                raise SourceCoverageError(
                    source,
                    "invalid_response",
                    "NAIP catalog returned a record outside the requested "
                    "date range",
                    {
                        "requested_date_range": {
                            "start": start.isoformat(),
                            "end": end.isoformat(),
                        },
                        "returned_acquisition_date": acquisition_date.isoformat(),
                    },
                )
            selected_dates.append(acquisition_date)
        try:
            resolution = float(attributes.get("resolution_value"))
        except (TypeError, ValueError):
            continue
        units = str(attributes.get("resolution_units") or "").lower()
        if resolution > 0 and (not units or units.startswith("m") or "meter" in units):
            resolutions.append(resolution)
    if wrong_years:
        raise SourceCoverageError(
            source,
            "wrong_year_response",
            f"catalog returned years {sorted(wrong_years)} for requested year {requested_year}; no substitution is allowed",
        )
    return {
        "status": "PASS",
        "requested_year": requested_year,
        "catalog_match_count": len(features),
        "available_intersecting_years": available_years or [requested_year],
        "requested_date_range": (
            {"start": start.isoformat(), "end": end.isoformat()}
            if start is not None and end is not None
            else None
        ),
        "selected_acquisition_dates": [
            value.isoformat() for value in sorted(set(selected_dates))
        ],
        "source_native_resolution_meters": min(resolutions) if resolutions else None,
        "selected_records": [feature.get("attributes", {}) for feature in features],
    }


def validate_cdl_catalog(
    requested_response: Mapping[str, Any],
    available_response: Mapping[str, Any],
    *,
    requested_year: int,
) -> dict[str, Any]:
    source = "USDA_CDL"
    available_years = catalog_years(available_response, source=source)
    if requested_year not in available_years:
        raise SourceCoverageError(
            source,
            "requested_year_unavailable",
            f"CDL year {requested_year} is unavailable; source-reported years: {available_years}",
            {"requested_year": requested_year, "available_years": available_years},
        )
    features = catalog_features(requested_response, source=source)
    if not features:
        raise SourceCoverageError(
            source,
            "bbox_outside_coverage",
            f"CDL year {requested_year} has no catalog footprint covering the requested bbox",
            {"requested_year": requested_year, "available_years": available_years},
        )
    wrong_year_values: set[int] = set()
    for feature in features:
        attributes = feature.get("attributes")
        if not isinstance(attributes, Mapping):
            raise SourceCoverageError(
                source,
                "invalid_response",
                "a CDL catalog record did not contain attributes",
            )
        try:
            feature_year = int(attributes.get("Year"))
        except (TypeError, ValueError) as exc:
            raise SourceCoverageError(
                source,
                "invalid_response",
                "a CDL catalog record did not report a valid Year",
            ) from exc
        if feature_year != requested_year:
            wrong_year_values.add(feature_year)
    wrong_years = sorted(wrong_year_values)
    if wrong_years:
        raise SourceCoverageError(
            source,
            "wrong_year_response",
            f"CDL catalog returned years {wrong_years} for requested year {requested_year}; no substitution is allowed",
        )
    return {
        "status": "PASS",
        "requested_year": requested_year,
        "available_years": available_years,
        "catalog_match_count": len(features),
        "selected_records": [feature.get("attributes", {}) for feature in features],
        "categorical_resampling": "nearest",
    }


def validate_3dep_catalog(response: Mapping[str, Any]) -> dict[str, Any]:
    source = "USGS_3DEP"
    features = catalog_features(response, source=source)
    if not features:
        raise SourceCoverageError(
            source,
            "bbox_outside_coverage",
            "3DEP catalog has no source record intersecting the requested bbox",
        )
    return {
        "status": "PASS",
        "catalog_match_count": len(features),
        "selected_records": [feature.get("attributes", {}) for feature in features],
        "terrain_resampling": "bilinear",
    }
