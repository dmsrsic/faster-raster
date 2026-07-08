from __future__ import annotations

import json
from pathlib import Path

from faster_raster.tiling import plan_tiles


def write_bbox(path: Path, bbox: list[float]) -> None:
    min_x, min_y, max_x, max_y = bbox
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [min_x, min_y],
                                    [max_x, min_y],
                                    [max_x, max_y],
                                    [min_x, max_y],
                                    [min_x, min_y],
                                ]
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_aoi_smaller_than_max_size_has_one_tile(tmp_path):
    aoi = tmp_path / "small.geojson"
    write_bbox(aoi, [0, 0, 60, 60])

    tiles = plan_tiles(aoi, source_crs="EPSG:3857", planning_crs="EPSG:3857", resolution_m=30, max_width=4097, max_height=4097)

    assert len(tiles) == 1
    assert tiles[0]["tile_id"] == "000001"
    assert tiles[0]["source_aoi_bbox"] == [0, 0, 60, 60]
    assert tiles[0]["width_px"] == 2
    assert tiles[0]["height_px"] == 2
    assert tiles[0]["tile_planning_crs"] == "EPSG:3857"


def test_aoi_requiring_width_split(tmp_path):
    aoi = tmp_path / "width.geojson"
    write_bbox(aoi, [0, 0, 150, 60])

    tiles = plan_tiles(aoi, source_crs="EPSG:3857", planning_crs="EPSG:3857", resolution_m=30, max_width=2, max_height=10)

    assert len(tiles) == 3
    assert [(tile["row"], tile["col"]) for tile in tiles] == [(0, 0), (0, 1), (0, 2)]
    assert all(tile["width_px"] <= 2 for tile in tiles)


def test_aoi_requiring_height_split(tmp_path):
    aoi = tmp_path / "height.geojson"
    write_bbox(aoi, [0, 0, 60, 150])

    tiles = plan_tiles(aoi, source_crs="EPSG:3857", planning_crs="EPSG:3857", resolution_m=30, max_width=10, max_height=2)

    assert len(tiles) == 3
    assert [(tile["row"], tile["col"]) for tile in tiles] == [(0, 0), (1, 0), (2, 0)]
    assert all(tile["height_px"] <= 2 for tile in tiles)


def test_aoi_requiring_width_and_height_split(tmp_path):
    aoi = tmp_path / "both.geojson"
    write_bbox(aoi, [0, 0, 150, 150])

    tiles = plan_tiles(aoi, source_crs="EPSG:3857", planning_crs="EPSG:3857", resolution_m=30, max_width=2, max_height=2)

    assert len(tiles) == 9
    assert tiles[0]["tile_id"] == "000001"
    assert tiles[-1]["tile_id"] == "000009"
    assert all(tile["width_px"] <= 2 and tile["height_px"] <= 2 for tile in tiles)
    assert [(tile["row"], tile["col"]) for tile in tiles] == sorted((tile["row"], tile["col"]) for tile in tiles)