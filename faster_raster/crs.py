from __future__ import annotations

import math


class UnsupportedCRSTransform(ValueError):
    """Raised when v0 cannot transform between the requested CRS pair."""


WEB_MERCATOR_MAX_LAT = 85.05112878
EARTH_RADIUS_M = 6378137.0


def normalize_crs(crs: str) -> str:
    return crs.upper()


def epsg_number(crs: str) -> str:
    return normalize_crs(crs).replace("EPSG:", "")


def lonlat_to_web_mercator(lon: float, lat: float) -> tuple[float, float]:
    clamped_lat = min(max(lat, -WEB_MERCATOR_MAX_LAT), WEB_MERCATOR_MAX_LAT)
    x = EARTH_RADIUS_M * math.radians(lon)
    y = EARTH_RADIUS_M * math.log(math.tan(math.pi / 4.0 + math.radians(clamped_lat) / 2.0))
    return x, y


def web_mercator_to_lonlat(x: float, y: float) -> tuple[float, float]:
    lon = math.degrees(x / EARTH_RADIUS_M)
    lat = math.degrees(2.0 * math.atan(math.exp(y / EARTH_RADIUS_M)) - math.pi / 2.0)
    return lon, lat


def transform_bbox(bbox: list[float], from_crs: str, to_crs: str) -> list[float]:
    source = normalize_crs(from_crs)
    target = normalize_crs(to_crs)
    if source == target:
        return [round(value, 8) for value in bbox]

    min_x, min_y, max_x, max_y = bbox
    corners = [(min_x, min_y), (min_x, max_y), (max_x, min_y), (max_x, max_y)]
    if source == "EPSG:4326" and target == "EPSG:3857":
        transformed = [lonlat_to_web_mercator(x, y) for x, y in corners]
    elif source == "EPSG:3857" and target == "EPSG:4326":
        transformed = [web_mercator_to_lonlat(x, y) for x, y in corners]
    else:
        raise UnsupportedCRSTransform(
            f"UnsupportedCRSTransform: {source} -> {target}; install pyproj-backed transform support in a later milestone."
        )

    xs = [x for x, _ in transformed]
    ys = [y for _, y in transformed]
    return [round(min(xs), 8), round(min(ys), 8), round(max(xs), 8), round(max(ys), 8)]
