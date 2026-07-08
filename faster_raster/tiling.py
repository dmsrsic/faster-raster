from __future__ import annotations

import json
import math
from pathlib import Path

from faster_raster.crs import transform_bbox

DEFAULT_BBOX = [-83.2, 39.8, -82.9, 40.1]


def bbox_from_geojson(path: Path) -> list[float]:
    if not path.exists():
        return DEFAULT_BBOX
    with path.open("r", encoding="utf-8") as handle:
        geojson = json.load(handle)
    coords: list[tuple[float, float]] = []

    def walk(value):
        if isinstance(value, list) and len(value) >= 2 and all(isinstance(x, (int, float)) for x in value[:2]):
            coords.append((float(value[0]), float(value[1])))
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)

    walk(geojson.get("features", geojson))
    if not coords:
        return DEFAULT_BBOX
    xs = [x for x, _ in coords]
    ys = [y for _, y in coords]
    return [round(min(xs), 8), round(min(ys), 8), round(max(xs), 8), round(max(ys), 8)]


def _projected_span_m(bbox: list[float], source_crs: str, planning_crs: str) -> tuple[float, float]:
    planning_bbox = transform_bbox(bbox, source_crs, planning_crs)
    min_x, min_y, max_x, max_y = planning_bbox
    return abs(max_x - min_x), abs(max_y - min_y)


def _ceil_positive(value: float) -> int:
    return max(1, int(math.ceil(value)))


def plan_tiles(
    aoi_path: Path,
    *,
    source_crs: str = "EPSG:4326",
    planning_crs: str = "EPSG:3857",
    resolution_m: float = 30,
    max_width: int = 4097,
    max_height: int = 4097,
) -> list[dict]:
    bbox = bbox_from_geojson(aoi_path)
    min_x, min_y, max_x, max_y = bbox
    if min_x >= max_x or min_y >= max_y:
        raise ValueError(f"Invalid AOI bbox: {bbox}")
    if resolution_m <= 0:
        raise ValueError("resolution_m must be positive")
    if max_width <= 0 or max_height <= 0:
        raise ValueError("max_width and max_height must be positive")

    width_m, height_m = _projected_span_m(bbox, source_crs, planning_crs)
    total_width_px = _ceil_positive(width_m / resolution_m)
    total_height_px = _ceil_positive(height_m / resolution_m)
    cols = _ceil_positive(total_width_px / max_width)
    rows = _ceil_positive(total_height_px / max_height)

    lon_step = (max_x - min_x) / cols
    lat_step = (max_y - min_y) / rows
    tiles: list[dict] = []
    tile_number = 1
    for row in range(rows):
        for col in range(cols):
            tile_min_x = min_x + col * lon_step
            tile_max_x = max_x if col == cols - 1 else min_x + (col + 1) * lon_step
            tile_min_y = min_y + row * lat_step
            tile_max_y = max_y if row == rows - 1 else min_y + (row + 1) * lat_step
            tile_bbox = [
                round(tile_min_x, 8),
                round(tile_min_y, 8),
                round(tile_max_x, 8),
                round(tile_max_y, 8),
            ]
            tile_width_m, tile_height_m = _projected_span_m(tile_bbox, source_crs, planning_crs)
            width_px = min(max_width, _ceil_positive(tile_width_m / resolution_m))
            height_px = min(max_height, _ceil_positive(tile_height_m / resolution_m))
            tiles.append(
                {
                    "tile_id": f"{tile_number:06d}",
                    "row": row,
                    "col": col,
                    "source_aoi_bbox": tile_bbox,
                    "source_aoi_crs": source_crs,
                    "tile_planning_crs": planning_crs,
                    "width_px": width_px,
                    "height_px": height_px,
                }
            )
            tile_number += 1
    return tiles
