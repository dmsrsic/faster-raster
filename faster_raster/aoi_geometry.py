from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping, Sequence

from faster_raster.ag_geography import (
    BBox,
    BBoxValidationError,
    WEB_MERCATOR_LATITUDE_LIMIT,
    validate_bbox,
)


DistanceUnit = Literal["meters", "kilometers", "miles"]
BufferShape = Literal["square", "circle"]

DISTANCE_TO_METERS: Mapping[str, float] = {
    "meters": 1.0,
    "kilometers": 1000.0,
    "miles": 1609.344,
}
CIRCLE_SEGMENT_COUNT = 128
SQUARE_EDGE_SEGMENT_COUNT = 32
MAXIMUM_BUFFER_METERS = 500_000.0


class AreaConstructionError(ValueError):
    """Raised when a requested interactive AOI cannot be represented safely."""


def _transform_geometry(
    source_crs: Any,
    destination_crs: Any,
    geometry: Mapping[str, Any],
    *,
    precision: int,
) -> dict[str, Any]:
    from rasterio.warp import transform_geom

    return transform_geom(
        source_crs,
        destination_crs,
        dict(geometry),
        precision=precision,
    )


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


def _polygon_area(coordinates: Sequence[Sequence[float]]) -> float:
    return abs(
        sum(
            float(first[0]) * float(second[1])
            - float(second[0]) * float(first[1])
            for first, second in zip(
                coordinates,
                coordinates[1:],
            )
        )
    ) / 2.0


def _polygon_from_bbox(bbox: BBox) -> dict[str, Any]:
    west, south, east, north = bbox
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [west, south],
                [east, south],
                [east, north],
                [west, north],
                [west, south],
            ]
        ],
    }


def _densify_ring(
    vertices: Sequence[Sequence[float]],
    segments_per_edge: int,
) -> list[list[float]]:
    result: list[list[float]] = []
    for start, end in zip(vertices, vertices[1:]):
        for index in range(segments_per_edge):
            fraction = index / segments_per_edge
            result.append(
                [
                    float(start[0])
                    + (float(end[0]) - float(start[0])) * fraction,
                    float(start[1])
                    + (float(end[1]) - float(start[1])) * fraction,
                ]
            )
    result.append([float(vertices[-1][0]), float(vertices[-1][1])])
    return result


def _validate_center(longitude: Any, latitude: Any) -> tuple[float, float]:
    try:
        lon = float(longitude)
        lat = float(latitude)
    except (TypeError, ValueError) as exc:
        raise AreaConstructionError(
            "center coordinates must be numeric longitude, then latitude"
        ) from exc
    if not math.isfinite(lon) or not math.isfinite(lat):
        raise AreaConstructionError("center coordinates must be finite")
    if not -180.0 <= lon <= 180.0:
        raise AreaConstructionError("center longitude must be within [-180, 180]")
    if not -90.0 <= lat <= 90.0:
        raise AreaConstructionError("center latitude must be within [-90, 90]")
    if abs(lat) > WEB_MERCATOR_LATITUDE_LIMIT:
        raise AreaConstructionError(
            "center latitude exceeds the current EPSG:3857 execution limit"
        )
    return lon, lat


def _normalize_distance(value: Any, unit: str) -> tuple[str, float, float]:
    entered = str(value).strip()
    try:
        numeric = float(entered)
    except (TypeError, ValueError) as exc:
        raise AreaConstructionError("buffer distance must be numeric") from exc
    if not math.isfinite(numeric) or numeric <= 0:
        raise AreaConstructionError(
            "buffer distance must be a finite positive number"
        )
    if unit not in DISTANCE_TO_METERS:
        raise AreaConstructionError(
            "distance unit must be meters, kilometers, or miles"
        )
    meters = numeric * DISTANCE_TO_METERS[unit]
    if meters > MAXIMUM_BUFFER_METERS:
        raise AreaConstructionError(
            f"buffer distance exceeds the {MAXIMUM_BUFFER_METERS:g} meter "
            "interactive construction limit"
        )
    return entered, numeric, meters


@dataclass(frozen=True)
class ConstructedArea:
    schema_version: str
    coordinate_order: str
    center_longitude: float
    center_latitude: float
    entered_buffer_text: str
    entered_buffer_value: float
    entered_distance_unit: DistanceUnit
    normalized_distance_meters: float
    shape: BufferShape
    buffer_semantics: str
    geometry_construction_method: str
    geometry_construction_crs: str
    circle_segment_count: int | None
    square_edge_segment_count: int | None
    analysis_aoi_epsg_4326: dict[str, Any]
    request_bbox_epsg_4326: BBox
    acquisition_geometry_differs_from_analysis_aoi: bool
    analysis_aoi_area_square_meters: float
    request_envelope_area_square_meters: float
    envelope_only_area_square_meters: float
    geometry_sha256: str

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["request_bbox_epsg_4326"] = list(self.request_bbox_epsg_4326)
        return result


def build_point_buffer_area(
    longitude: Any,
    latitude: Any,
    distance: Any,
    unit: DistanceUnit | str,
    shape: BufferShape | str,
) -> ConstructedArea:
    lon, lat = _validate_center(longitude, latitude)
    entered, numeric, meters = _normalize_distance(distance, str(unit))
    if shape not in {"square", "circle"}:
        raise AreaConstructionError("shape must be square or circle")
    local_crs = (
        f"+proj=aeqd +lat_0={lat:.12g} +lon_0={lon:.12g} "
        "+datum=WGS84 +units=m +no_defs"
    )
    if shape == "square":
        local_ring = _densify_ring(
            [
                [-meters, -meters],
                [meters, -meters],
                [meters, meters],
                [-meters, meters],
                [-meters, -meters],
            ],
            SQUARE_EDGE_SEGMENT_COUNT,
        )
        segment_count: int | None = None
        square_edge_segment_count: int | None = (
            SQUARE_EDGE_SEGMENT_COUNT
        )
        semantics = (
            "entered distance is the half-width and half-height; "
            "full side length is twice the entered distance"
        )
    else:
        local_ring = [
            [
                meters
                * math.cos(
                    2.0 * math.pi * index / CIRCLE_SEGMENT_COUNT
                ),
                meters
                * math.sin(
                    2.0 * math.pi * index / CIRCLE_SEGMENT_COUNT
                ),
            ]
            for index in range(CIRCLE_SEGMENT_COUNT)
        ]
        local_ring.append(local_ring[0])
        segment_count = CIRCLE_SEGMENT_COUNT
        square_edge_segment_count = None
        semantics = "entered distance is the radius from center to boundary"

    local_geometry = {"type": "Polygon", "coordinates": [local_ring]}
    try:
        geographic = _transform_geometry(
            local_crs,
            "EPSG:4326",
            local_geometry,
            precision=12,
        )
    except Exception as exc:
        raise AreaConstructionError(
            f"unable to transform buffered geometry safely: {exc}"
        ) from exc
    if geographic.get("type") != "Polygon":
        raise AreaConstructionError(
            "buffer crosses the antimeridian; split AOIs are not supported"
        )
    ring = geographic.get("coordinates", [[]])[0]
    if len(ring) < 4:
        raise AreaConstructionError("generated analysis AOI is empty")
    coordinates = [
        (float(pair[0]), float(pair[1]))
        for pair in ring
        if isinstance(pair, (list, tuple)) and len(pair) >= 2
    ]
    if len(coordinates) != len(ring) or not all(
        math.isfinite(value) for pair in coordinates for value in pair
    ):
        raise AreaConstructionError(
            "generated analysis AOI contains invalid coordinates"
        )
    if any(
        abs(first[0] - second[0]) > 180.0
        for first, second in zip(coordinates, coordinates[1:])
    ):
        raise AreaConstructionError(
            "buffer crosses the antimeridian; antimeridian AOIs are not supported"
        )
    bbox_values = (
        min(pair[0] for pair in coordinates),
        min(pair[1] for pair in coordinates),
        max(pair[0] for pair in coordinates),
        max(pair[1] for pair in coordinates),
    )
    try:
        bbox = validate_bbox(bbox_values)
    except BBoxValidationError as exc:
        raise AreaConstructionError(str(exc)) from exc

    envelope_geometry = {
        "type": "Polygon",
        "coordinates": [
            _densify_ring(
                _polygon_from_bbox(bbox)["coordinates"][0],
                SQUARE_EDGE_SEGMENT_COUNT,
            )
        ],
    }
    try:
        local_envelope = _transform_geometry(
            "EPSG:4326",
            local_crs,
            envelope_geometry,
            precision=9,
        )
    except Exception as exc:
        raise AreaConstructionError(
            f"unable to measure the request envelope safely: {exc}"
        ) from exc
    envelope_ring = local_envelope.get("coordinates", [[]])[0]
    analysis_area = _polygon_area(local_ring)
    envelope_area = _polygon_area(envelope_ring)
    normalized_geometry = {
        "type": "Polygon",
        "coordinates": [
            [[float(pair[0]), float(pair[1])] for pair in coordinates]
        ],
    }
    return ConstructedArea(
        schema_version="fasterraster.constructed-area/v1",
        coordinate_order="longitude,latitude",
        center_longitude=lon,
        center_latitude=lat,
        entered_buffer_text=entered,
        entered_buffer_value=numeric,
        entered_distance_unit=str(unit),  # type: ignore[arg-type]
        normalized_distance_meters=meters,
        shape=str(shape),  # type: ignore[arg-type]
        buffer_semantics=semantics,
        geometry_construction_method=(
            "local_azimuthal_equidistant_projected_metric_buffer"
        ),
        geometry_construction_crs=local_crs,
        circle_segment_count=segment_count,
        square_edge_segment_count=square_edge_segment_count,
        analysis_aoi_epsg_4326=normalized_geometry,
        request_bbox_epsg_4326=bbox,
        acquisition_geometry_differs_from_analysis_aoi=True,
        analysis_aoi_area_square_meters=analysis_area,
        request_envelope_area_square_meters=envelope_area,
        envelope_only_area_square_meters=max(0.0, envelope_area - analysis_area),
        geometry_sha256=_canonical_sha256(normalized_geometry),
    )


def explicit_bbox_area(bbox: Sequence[float]) -> dict[str, Any]:
    validated = validate_bbox(bbox)
    geometry = _polygon_from_bbox(validated)
    return {
        "schema_version": "fasterraster.constructed-area/v1",
        "coordinate_order": "west,south,east,north",
        "shape": "bbox",
        "geometry_construction_method": "explicit_epsg4326_bbox",
        "geometry_construction_crs": "EPSG:4326",
        "circle_segment_count": None,
        "square_edge_segment_count": None,
        "analysis_aoi_epsg_4326": geometry,
        "request_bbox_epsg_4326": list(validated),
        "acquisition_geometry_differs_from_analysis_aoi": False,
        "geometry_sha256": _canonical_sha256(geometry),
    }


def raster_aoi_mask(
    dataset: Any,
    analysis_geometry_epsg_4326: Mapping[str, Any] | None,
    *,
    window: Any | None = None,
    out_shape: tuple[int, int] | None = None,
) -> Any:
    """Return a boolean valid-inside-AOI mask aligned to a dataset/window."""

    if window is None:
        source_height = int(dataset.height)
        source_width = int(dataset.width)
        transform = dataset.transform
    else:
        source_height = int(window.height)
        source_width = int(window.width)
        transform = dataset.window_transform(window)
    height, width = out_shape or (source_height, source_width)
    if analysis_geometry_epsg_4326 is None:
        import numpy as np

        return np.ones((height, width), dtype=bool)
    from rasterio import features
    from rasterio.transform import Affine

    try:
        geometry = _transform_geometry(
            "EPSG:4326",
            dataset.crs,
            dict(analysis_geometry_epsg_4326),
            precision=12,
        )
    except Exception as exc:
        raise AreaConstructionError(
            f"unable to align analysis AOI to raster grid: {exc}"
        ) from exc
    if (height, width) != (source_height, source_width):
        transform = transform * Affine.scale(
            source_width / width,
            source_height / height,
        )
    return features.geometry_mask(
        [geometry],
        out_shape=(height, width),
        transform=transform,
        invert=True,
        all_touched=False,
    )
