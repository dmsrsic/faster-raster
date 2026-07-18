from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import os
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import rasterio
from affine import Affine
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin
from rasterio.io import MemoryFile
from rasterio.vrt import WarpedVRT
from rasterio.warp import Resampling
from rasterio.windows import Window

from faster_raster.ag_execution import (
    _assert_no_staging_provenance,
    _open_final_preview,
    _regenerate_checksums,
    handoff_transaction,
)
from faster_raster.ag_geography import SourceCoverageError, validate_naip_catalog
from faster_raster.cdl_acquisition import NAIP_ENDPOINT, NAIP_SOURCE_ID
from faster_raster.development_sources import DevelopmentMapping
from faster_raster.human_development import CHANGE_CODE_INFO, development_rank


MODES = ("regional-change", "developed-state", "hotspot", "combined")
REUSE_MODES = ("auto", "only", "never")
CHANGE_COLORS = {
    0: (35, 39, 45), 1: (211, 216, 205), 2: (151, 76, 100),
    3: (224, 54, 44), 4: (53, 112, 205), 5: (245, 147, 38),
    6: (76, 173, 213), 7: (198, 194, 202),
}
STATE_COLORS = {
    -1: (35, 39, 45), 0: (213, 208, 190), 1: (225, 205, 190),
    2: (235, 155, 135), 3: (215, 85, 75), 4: (155, 30, 45),
}


class PublicationError(ValueError):
    pass


class NetworkCeilingError(PublicationError):
    pass


class TileTimeoutError(PublicationError):
    pass


@dataclass(frozen=True)
class PublicationOptions:
    mode: str = "combined"
    imagery_year: int = 2021
    regional_resolution_m: float = 4.2
    hotspot_resolution_m: float = 1.0
    hotspot_size_m: float = 1024.0
    maximum_download_mb: float = 75.0
    workers: int = 2
    reuse: str = "auto"
    allow_network: bool = False
    open_when_complete: bool = False

    def validate(self) -> None:
        if self.mode not in MODES:
            raise PublicationError(f"unsupported publication mode: {self.mode}")
        if self.reuse not in REUSE_MODES:
            raise PublicationError(f"unsupported publication reuse mode: {self.reuse}")
        if not 1 <= self.workers <= 4:
            raise PublicationError("publication workers must be between 1 and 4")
        for label, value in (
            ("regional resolution", self.regional_resolution_m),
            ("hotspot resolution", self.hotspot_resolution_m),
            ("hotspot size", self.hotspot_size_m),
            ("maximum download", self.maximum_download_mb),
        ):
            if not math.isfinite(value) or value <= 0:
                raise PublicationError(f"{label} must be a positive finite value")
        if self.reuse == "only" and self.allow_network:
            raise PublicationError("reuse only is incompatible with --allow-network")


@dataclass(frozen=True)
class TileSpec:
    tile_id: str
    row_off: int
    col_off: int
    height: int
    width: int
    bounds: tuple[float, float, float, float]
    depth: int = 0


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublicationError(f"invalid or missing JSON evidence: {path.name}") from exc
    if not isinstance(value, dict):
        raise PublicationError(f"JSON evidence must be an object: {path.name}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checksum_map(root: Path) -> dict[str, str]:
    path = root / "checksums.sha256"
    if not path.is_file():
        raise PublicationError("finalized output is missing checksums.sha256")
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "  " not in line:
            raise PublicationError("malformed checksum evidence")
        digest, relative = line.split("  ", 1)
        relative = relative.strip()
        if len(digest) != 64 or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise PublicationError("unsafe checksum evidence")
        result[relative] = digest
    if not result:
        raise PublicationError("checksum evidence is empty")
    return result


def _verify_checksums(root: Path, checksums: Mapping[str, str]) -> None:
    for relative, digest in checksums.items():
        path = root / relative
        if not path.is_file() or _sha256(path) != digest:
            raise PublicationError(f"invalid checksum: {relative}")


def _evidence_path(root: Path, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise PublicationError("unsafe handoff evidence path")
    path = root / relative
    if not path.is_file():
        raise PublicationError(f"missing handoff evidence: {value}")
    return path


def validate_handoff(handoff: Path) -> dict[str, Any]:
    root = handoff.expanduser().resolve()
    if not root.is_dir():
        raise PublicationError(f"handoff does not exist: {root}")
    if any(part.startswith(".failed-") or ".staging-" in part for part in root.parts):
        raise PublicationError("publication requires a finalized handoff")
    manifest = _read_json(root / "manifest.json")
    if manifest.get("operation_status") != "completed":
        raise PublicationError("handoff is incomplete")
    if manifest.get("verification_status") != "PASS":
        raise PublicationError("handoff verification did not pass")
    if manifest.get("workflow") != "human_development_change":
        raise PublicationError("unsupported workflow ID")
    receipt = _read_json(_evidence_path(root, str(manifest.get("workflow_receipt") or "")))
    if receipt.get("final_status") != "PASS":
        raise PublicationError("workflow receipt is incomplete")
    checksums = _checksum_map(root)
    _verify_checksums(root, checksums)
    mapping_name = str(receipt.get("source_mapping_contract") or "")
    if not mapping_name:
        raise PublicationError("missing source mapping evidence")
    mapping = _read_json(_evidence_path(root, mapping_name))
    mapping_hash = str(receipt.get("mapping_contract_sha256") or "")
    developed_evidence = (
        mapping.get("developed_states") or mapping.get("developed_ranks")
    )
    if mapping.get("sha256") != mapping_hash or not developed_evidence:
        raise PublicationError("invalid source mapping evidence")
    endpoint = receipt.get("endpoint_comparison")
    if not isinstance(endpoint, dict) or not endpoint.get("statistics"):
        raise PublicationError("missing endpoint comparison")
    statistics_path = _evidence_path(root, str(endpoint["statistics"]))
    endpoint_dir = statistics_path.parent
    change_path = endpoint_dir / "change_codes.tif"
    change_relative = change_path.relative_to(root).as_posix()
    if not change_path.is_file() or change_relative not in checksums:
        raise PublicationError("missing checksummed endpoint change raster")
    years = receipt.get("epochs")
    if not isinstance(years, list) or len(years) < 2:
        raise PublicationError("malformed epoch evidence")
    endpoint_year = int(years[-1])
    land_path = root / "data" / "epochs" / str(endpoint_year) / "land_cover.tif"
    if land_path.relative_to(root).as_posix() not in checksums:
        raise PublicationError("missing checksummed endpoint land-cover raster")
    grid = receipt.get("target_grid")
    if not isinstance(grid, dict):
        raise PublicationError("malformed target grid")
    try:
        width = int(grid["width"])
        height = int(grid["height"])
        transform = Affine(*[float(value) for value in grid["transform"]])
    except (KeyError, TypeError, ValueError) as exc:
        raise PublicationError("malformed target grid") from exc
    if width <= 0 or height <= 0 or grid.get("crs") != "EPSG:5070":
        raise PublicationError("publication requires a positive EPSG:5070 grid")
    with rasterio.open(change_path) as source:
        if (
            source.width != width
            or source.height != height
            or source.crs is None
            or source.crs.to_string() != "EPSG:5070"
            or any(abs(a - b) > 1e-7 for a, b in zip(source.transform, transform))
        ):
            raise PublicationError("endpoint raster does not match the target grid")
    bbox = receipt.get("requested_bbox_epsg_4326")
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise PublicationError("missing handoff AOI evidence")
    checksum_identity = hashlib.sha256(
        json.dumps(checksums, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    methodology_name = str(
        receipt.get("methodology_receipt") or manifest.get("methodology_receipt") or ""
    )
    return {
        "root": root,
        "handoff_id": root.name,
        "receipt": receipt,
        "checksums": checksums,
        "checksum_identity": checksum_identity,
        "mapping": mapping,
        "mapping_hash": mapping_hash,
        "change_path": change_path,
        "land_path": land_path,
        "endpoint_statistics": _read_json(statistics_path),
        "source_transition": _read_json(endpoint_dir / "source_transition_matrix.json"),
        "methodology": _read_json(_evidence_path(root, methodology_name)),
        "grid": grid,
        "transform": transform,
        "bbox": tuple(float(value) for value in bbox),
        "endpoint_year": endpoint_year,
    }


class SharedBudget:
    def __init__(self, ceiling: int) -> None:
        self.ceiling = ceiling
        self.total = 0
        self.lock = threading.Lock()

    def add(self, size: int) -> None:
        with self.lock:
            if self.total + size > self.ceiling:
                raise NetworkCeilingError(
                    f"publication byte ceiling exceeded: {self.ceiling:,}"
                )
            self.total += size


class PublicationClient:
    def __init__(
        self,
        byte_ceiling: int,
        allow_network: bool,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleeper: Callable[[float], None] = time.sleep,
        attempts: int = 3,
    ) -> None:
        self.allow_network = allow_network
        self.opener = opener
        self.sleeper = sleeper
        self.attempts = attempts
        self.budget = SharedBudget(byte_ceiling)
        self.lock = threading.Lock()
        self.metadata_requests = 0
        self.raster_requests = 0
        self.requests: list[dict[str, Any]] = []
        self.tile_keys: set[str] = set()

    def request(
        self,
        endpoint: str,
        params: Mapping[str, Any],
        *,
        label: str,
        raster: bool,
        tile_key: str | None = None,
    ) -> bytes:
        if not self.allow_network:
            raise PublicationError("network use was not explicitly allowed")
        if tile_key:
            with self.lock:
                if tile_key in self.tile_keys:
                    raise PublicationError(f"duplicate tile request: {tile_key}")
                self.tile_keys.add(tile_key)
        encoded = {
            key: json.dumps(value, separators=(",", ":"))
            if isinstance(value, (dict, list))
            else str(value)
            for key, value in params.items()
        }
        retryable = {408, 429, 500, 502, 503, 504}
        for attempt in range(1, self.attempts + 1):
            request = urllib.request.Request(
                endpoint,
                data=urllib.parse.urlencode(encoded).encode(),
                method="POST",
                headers={
                    "User-Agent": "FasterRaster-Hybrid-Publication/1.0",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            try:
                chunks = []
                with self.opener(request, timeout=180) as response:
                    content_type = str(response.headers.get("Content-Type", ""))
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        self.budget.add(len(chunk))
                        chunks.append(chunk)
                payload = b"".join(chunks)
                if payload.lstrip().startswith(b"{"):
                    value = json.loads(payload)
                    error = value.get("error") if isinstance(value, dict) else None
                    code = error.get("code") if isinstance(error, dict) else None
                    if code in retryable and attempt < self.attempts:
                        self.sleeper(min(4, 2 ** (attempt - 1)))
                        continue
                    if error:
                        raise PublicationError(f"{label} returned an ArcGIS error")
                with self.lock:
                    if raster:
                        self.raster_requests += 1
                    else:
                        self.metadata_requests += 1
                    self.requests.append({
                        "label": label,
                        "kind": "raster" if raster else "metadata",
                        "attempts": attempt,
                        "bytes": len(payload),
                        "content_type": content_type,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    })
                return payload
            except urllib.error.HTTPError as exc:
                if exc.code not in retryable or attempt == self.attempts:
                    if exc.code in {408, 504}:
                        raise TileTimeoutError(f"{label} timed out") from exc
                    raise PublicationError(f"{label} failed with HTTP {exc.code}") from exc
                self.sleeper(min(4, 2 ** (attempt - 1)))
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if attempt == self.attempts:
                    raise TileTimeoutError(f"{label} timed out") from exc
                self.sleeper(min(4, 2 ** (attempt - 1)))
        raise TileTimeoutError(f"{label} exhausted retries")

    def json(self, endpoint: str, params: Mapping[str, Any], label: str) -> dict[str, Any]:
        payload = self.request(
            endpoint, {**params, "f": "json"}, label=label, raster=False
        )
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise PublicationError(f"{label} returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise PublicationError(f"{label} returned malformed JSON")
        return value

def _bounds(grid: Mapping[str, Any]) -> tuple[float, float, float, float]:
    transform = Affine(*grid["transform"])
    left, top = transform * (0, 0)
    right, bottom = transform * (int(grid["width"]), int(grid["height"]))
    return min(left, right), min(bottom, top), max(left, right), max(bottom, top)


def plan_tiles(
    bounds: Sequence[float], resolution: float, tile_size: int = 900
) -> tuple[list[TileSpec], Affine, int, int]:
    left, bottom, right, top = [float(value) for value in bounds]
    width = int(math.ceil((right - left) / resolution))
    height = int(math.ceil((top - bottom) / resolution))
    transform = Affine(resolution, 0, left, 0, -resolution, top)
    tiles = []
    for row in range(0, height, tile_size):
        for col in range(0, width, tile_size):
            h = min(tile_size, height - row)
            w = min(tile_size, width - col)
            tile_left = left + col * resolution
            tile_top = top - row * resolution
            tiles.append(TileSpec(
                f"r{row:04d}_c{col:04d}_h{h:04d}_w{w:04d}",
                row, col, h, w,
                (
                    tile_left,
                    tile_top - h * resolution,
                    tile_left + w * resolution,
                    tile_top,
                ),
            ))
    return tiles, transform, width, height


def _write_rgb(path: Path, values: np.ndarray, transform: Affine) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "width": values.shape[2],
        "height": values.shape[1],
        "count": 3,
        "dtype": "uint8",
        "crs": "EPSG:5070",
        "transform": transform,
        "compress": "deflate",
    }
    if values.shape[1] >= 16 and values.shape[2] >= 16:
        profile.update(tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(path, "w", **profile) as sink:
        sink.write(values[:3].astype(np.uint8, copy=False))


def _download_tile(
    client: PublicationClient,
    tile: TileSpec,
    year: int,
    resolution: float,
    destination: Path,
) -> dict[str, Any]:
    params = {
        "bbox": ",".join(str(value) for value in tile.bounds),
        "bboxSR": "5070",
        "imageSR": "5070",
        "size": f"{tile.width},{tile.height}",
        "format": "tiff",
        "f": "image",
        "interpolation": "RSP_BilinearInterpolation",
        "renderingRule": {"rasterFunction": "NaturalColor"},
        "mosaicRule": {
            "mosaicMethod": "esriMosaicAttribute",
            "where": f"Year = {year}",
            "sortField": "Year",
            "sortValue": str(year),
            "ascending": True,
            "mosaicOperation": "MT_FIRST",
        },
        "transparent": "false",
    }
    payload = client.request(
        NAIP_ENDPOINT + "/exportImage",
        params,
        label=f"naip_{year}_{tile.tile_id}",
        raster=True,
        tile_key=tile.tile_id,
    )
    if payload.lstrip().startswith((b"{", b"<")):
        raise PublicationError(f"NAIP tile {tile.tile_id} returned an error document")
    try:
        with MemoryFile(payload) as memory:
            with memory.open() as source:
                if (
                    source.count < 3
                    or source.width != tile.width
                    or source.height != tile.height
                ):
                    raise PublicationError(f"NAIP tile {tile.tile_id} is malformed")
                values = source.read((1, 2, 3), out_dtype="uint8")
    except rasterio.errors.RasterioError as exc:
        raise PublicationError(f"NAIP tile {tile.tile_id} is not a TIFF") from exc
    transform = Affine(resolution, 0, tile.bounds[0], 0, -resolution, tile.bounds[3])
    _write_rgb(destination, values, transform)
    request = next(item for item in reversed(client.requests) if item["label"].endswith(tile.tile_id))
    return {
        "tile_id": tile.tile_id,
        "bounds_epsg5070": list(tile.bounds),
        "row_offset": tile.row_off,
        "column_offset": tile.col_off,
        "width": tile.width,
        "height": tile.height,
        "resolution_m": resolution,
        "status": "DOWNLOADED",
        "attempts": request["attempts"],
        "network_bytes": request["bytes"],
        "sha256": _sha256(destination),
        "output": destination.name,
        "children": [],
    }


def _subtiles(tile: TileSpec, resolution: float) -> list[TileSpec]:
    if tile.width < 2 or tile.height < 2:
        return []
    widths = (tile.width // 2, tile.width - tile.width // 2)
    heights = (tile.height // 2, tile.height - tile.height // 2)
    result = []
    row = tile.row_off
    for r_index, height in enumerate(heights):
        col = tile.col_off
        for c_index, width in enumerate(widths):
            left = tile.bounds[0] + (col - tile.col_off) * resolution
            top = tile.bounds[3] - (row - tile.row_off) * resolution
            result.append(TileSpec(
                f"{tile.tile_id}.s{r_index}{c_index}",
                row, col, height, width,
                (left, top - height * resolution, left + width * resolution, top),
                tile.depth + 1,
            ))
            col += width
        row += height
    return result


def _adaptive_tile(
    client: PublicationClient,
    tile: TileSpec,
    year: int,
    resolution: float,
    directory: Path,
    maximum_depth: int = 2,
) -> dict[str, Any]:
    destination = directory / f"{tile.tile_id}.tif"
    try:
        return _download_tile(client, tile, year, resolution, destination)
    except TileTimeoutError:
        children = _subtiles(tile, resolution) if tile.depth < maximum_depth else []
        if not children:
            raise
        receipts = [
            _adaptive_tile(client, child, year, resolution, directory, maximum_depth)
            for child in children
        ]
        values = np.zeros((3, tile.height, tile.width), dtype=np.uint8)
        for child, receipt in zip(children, receipts):
            with rasterio.open(directory / receipt["output"]) as source:
                child_values = source.read((1, 2, 3), out_dtype="uint8")
            row = child.row_off - tile.row_off
            col = child.col_off - tile.col_off
            values[:, row : row + child.height, col : col + child.width] = child_values
        transform = Affine(
            resolution, 0, tile.bounds[0], 0, -resolution, tile.bounds[3]
        )
        _write_rgb(destination, values, transform)
        return {
            "tile_id": tile.tile_id,
            "bounds_epsg5070": list(tile.bounds),
            "row_offset": tile.row_off,
            "column_offset": tile.col_off,
            "width": tile.width,
            "height": tile.height,
            "resolution_m": resolution,
            "status": "DOWNLOADED_AFTER_SUBDIVISION",
            "attempts": sum(item["attempts"] for item in receipts),
            "network_bytes": sum(item["network_bytes"] for item in receipts),
            "sha256": _sha256(destination),
            "output": destination.name,
            "children": [item["tile_id"] for item in receipts],
        }


def acquire_tiles(
    client: PublicationClient,
    tiles: Sequence[TileSpec],
    year: int,
    resolution: float,
    directory: Path,
    workers: int,
) -> list[dict[str, Any]]:
    if not 1 <= workers <= 4:
        raise PublicationError("publication workers must be between 1 and 4")
    if len({tile.tile_id for tile in tiles}) != len(tiles):
        raise PublicationError("duplicate tile IDs in plan")
    directory.mkdir(parents=True, exist_ok=True)
    if workers == 1:
        receipts = [
            _adaptive_tile(client, tile, year, resolution, directory)
            for tile in tiles
        ]
    else:
        receipts = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    _adaptive_tile, client, tile, year, resolution, directory
                ): tile
                for tile in tiles
            }
            try:
                for future in concurrent.futures.as_completed(futures):
                    receipts.append(future.result())
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
    return sorted(receipts, key=lambda item: item["tile_id"])


def assemble(
    destination: Path,
    receipts: Sequence[Mapping[str, Any]],
    tile_directory: Path,
    transform: Affine,
    width: int,
    height: int,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        destination,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=3,
        dtype="uint8",
        crs="EPSG:5070",
        transform=transform,
        compress="deflate",
        tiled=True,
        blockxsize=512,
        blockysize=512,
    ) as sink:
        for receipt in sorted(receipts, key=lambda item: str(item["tile_id"])):
            with rasterio.open(tile_directory / str(receipt["output"])) as source:
                values = source.read((1, 2, 3), out_dtype="uint8")
            sink.write(
                values,
                window=Window(
                    int(receipt["column_offset"]),
                    int(receipt["row_offset"]),
                    int(receipt["width"]),
                    int(receipt["height"]),
                ),
            )


def query_catalog(
    client: PublicationClient, bbox: Sequence[float], year: int
) -> dict[str, Any]:
    geometry = {
        "xmin": bbox[0], "ymin": bbox[1], "xmax": bbox[2], "ymax": bbox[3],
        "spatialReference": {"wkid": 4326},
    }
    response = client.json(
        NAIP_ENDPOINT + "/query",
        {
            "where": f"Year = {year}",
            "geometry": geometry,
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "OBJECTID,Name,Year,resolution_value,resolution_units",
            "returnGeometry": "false",
            "resultRecordCount": "100",
        },
        f"naip_{year}_exact_aoi_catalog",
    )
    available = None
    if not response.get("features"):
        available = client.json(
            NAIP_ENDPOINT + "/query",
            {
                "where": "1=1",
                "geometry": geometry,
                "geometryType": "esriGeometryEnvelope",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "Year",
                "returnGeometry": "false",
                "resultRecordCount": "100",
            },
            "naip_available_aoi_years",
        )
    try:
        validation = validate_naip_catalog(
            response, requested_year=year, available_response=available
        )
    except SourceCoverageError as exc:
        raise PublicationError(str(exc)) from exc
    records = validation["selected_records"]
    return {
        "schema_version": "fasterraster.publication-context-catalog/v1",
        "status": "PASS",
        "source_id": NAIP_SOURCE_ID,
        "requested_year": year,
        "requested_bbox_epsg4326": list(bbox),
        "silent_year_substitution_allowed": False,
        "record_ids": sorted(int(item["OBJECTID"]) for item in records),
        "records": [
            {
                "object_id": int(item["OBJECTID"]),
                "name": str(item.get("Name") or ""),
                "year": int(item["Year"]),
                "resolution_value": item.get("resolution_value"),
                "resolution_units": item.get("resolution_units"),
            }
            for item in records
        ],
        "source_native_resolution_m": validation.get(
            "source_native_resolution_meters"
        ),
    }


def select_hotspot(change_path: Path, size_m: float) -> dict[str, Any]:
    with rasterio.open(change_path) as source:
        values = source.read((1,))[0]
        resolution = abs(float(source.transform.a))
        cell = min(
            source.width, source.height, max(1, int(round(size_m / resolution)))
        )
        candidates = []
        for row in range(0, source.height - cell + 1, cell):
            for col in range(0, source.width - cell + 1, cell):
                window = values[row : row + cell, col : col + cell]
                count = int(np.count_nonzero(np.isin(window, (3, 4, 5, 6))))
                candidates.append((-count, row, col))
        if not candidates:
            raise PublicationError("endpoint raster cannot support a hotspot")
        negative_count, row, col = min(candidates)
        x, y = source.transform * (col, row)
    return {
        "strategy": (
            "highest count of endpoint development-related change codes 3-6 "
            "within deterministic cells"
        ),
        "coarse_change_pixels": -negative_count,
        "coarse_row": row,
        "coarse_column": col,
        "bounds_epsg5070": [float(x), float(y - size_m), float(x + size_m), float(y)],
        "size_m": size_m,
    }


def _mapping(value: Mapping[str, Any]) -> DevelopmentMapping:
    if "developed_states" in value:
        developed = {
            int(key): int(item["intensity_rank"])
            for key, item in value["developed_states"].items()
        }
        labels = {
            int(key): str(label) for key, label in value["valid_classes"].items()
        }
        valid = tuple(sorted(labels))
        invalid = tuple(int(item) for item in value["invalid_codes"])
        nodata = invalid[0]
    else:
        developed = {
            int(key): int(rank) for key, rank in value["developed_ranks"].items()
        }
        labels = {
            int(key): str(label) for key, label in value["class_labels"].items()
        }
        valid = tuple(int(item) for item in value["valid_codes"])
        invalid = tuple(int(item) for item in value["invalid_codes"])
        nodata = int(value["nodata_code"])
    return DevelopmentMapping(
        mapping_id=str(value["mapping_id"]),
        source_id=str(value["source_id"]),
        source_semantic_type=str(value["source_semantic_type"]),
        invalid_codes=invalid,
        developed_ranks=developed,
        class_labels=labels,
        scientific_claim=str(value["scientific_claim"]),
    )


def _warp(path: Path, transform: Affine, width: int, height: int) -> np.ndarray:
    with rasterio.open(path) as source:
        with WarpedVRT(
            source,
            crs="EPSG:5070",
            transform=transform,
            width=width,
            height=height,
            resampling=Resampling.nearest,
        ) as warped:
            return warped.read((1,), out_dtype="uint8")[0]


def _palette(values: np.ndarray, colors: Mapping[int, tuple[int, int, int]]) -> np.ndarray:
    result = np.zeros((*values.shape, 3), dtype=np.uint8)
    result[:] = (35, 39, 45)
    for code, color in colors.items():
        result[values == code] = color
    return result


def render_hybrid(
    imagery_path: Path,
    classification_path: Path,
    destination: Path,
    kind: str,
    mapping_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    with rasterio.open(imagery_path) as imagery:
        bands = imagery.read((1, 2, 3), out_dtype="uint8")
        transform = imagery.transform
        width, height = imagery.width, imagery.height
    values = _warp(classification_path, transform, width, height)
    rgb = np.moveaxis(bands, 0, 2)
    if kind == "change":
        base = _palette(values, CHANGE_COLORS)
        replace = np.isin(values, (3, 4, 5, 6))
        classes = [3, 4, 5, 6]
    elif kind == "developed-state":
        contract = _mapping(mapping_receipt)
        ranks = development_rank(values, contract)
        base = _palette(ranks, STATE_COLORS)
        declared = (
            mapping_receipt.get("developed_states")
            or mapping_receipt.get("developed_ranks")
        )
        classes = sorted(int(code) for code in declared)
        replace = np.isin(values, classes)
    else:
        raise PublicationError(f"unknown hybrid kind: {kind}")
    mask = replace & np.any(rgb != 0, axis=2)
    base[mask] = rgb[mask]
    destination.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(base, mode="RGB").save(destination, "PNG", optimize=True)
    return {
        "classification_base": kind,
        "imagery_replacement_classes": classes,
        "imagery_replaced_pixels": int(np.count_nonzero(mask)),
        "output": destination.name,
        "sha256": _sha256(destination),
    }

def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    names = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            pass
    return ImageFont.load_default()


def _fit(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    result = Image.new("RGB", size, (247, 246, 241))
    fitted = image.copy()
    fitted.thumbnail(size, Image.Resampling.LANCZOS)
    result.paste(
        fitted,
        ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2),
    )
    return result


def render_preview(
    destination: Path,
    regional: Path | None,
    hotspot: Path | None,
    evidence: Mapping[str, Any],
    mode: str,
    imagery_year: int,
    network_bytes: int,
    reused_bytes: int,
    locator: Mapping[str, Any] | None,
) -> Path:
    canvas = Image.new("RGB", (3840, 2160), (230, 229, 222))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, 3840, 150), fill=(31, 44, 55))
    draw.text(
        (64, 24),
        "Human-development hybrid publication",
        fill=(250, 248, 241),
        font=_font(46, True),
    )
    draw.text(
        (66, 88),
        f"{evidence['handoff_id']} | {mode} | {imagery_year} NAIP context",
        fill=(204, 215, 219),
        font=_font(22),
    )
    boxes = (
        (55, 185, 2440, 1005),
        (2520, 185, 3785, 1005),
        (55, 1105, 2440, 2040),
        (2520, 1105, 3785, 2040),
    )
    for box in boxes:
        draw.rounded_rectangle(
            box, radius=20, fill=(247, 246, 241), outline=(204, 205, 198), width=3
        )
    if regional:
        canvas.paste(_fit(Image.open(regional).convert("RGB"), (2325, 730)), (85, 245))
        draw.text(
            (85, 202),
            "Regional classification-first hybrid",
            fill=(31, 44, 55),
            font=_font(30, True),
        )
    else:
        draw.text(
            (85, 230),
            "Regional hybrid not requested",
            fill=(31, 44, 55),
            font=_font(30, True),
        )
    if hotspot:
        canvas.paste(_fit(Image.open(hotspot).convert("RGB"), (1165, 730)), (2570, 245))
        draw.text(
            (2570, 202),
            "Deterministic high-change hotspot",
            fill=(31, 44, 55),
            font=_font(30, True),
        )
    else:
        draw.text(
            (2570, 230),
            "Hotspot not requested",
            fill=(31, 44, 55),
            font=_font(30, True),
        )
    legend_y = 1035
    draw.text(
        (290, legend_y),
        "Endpoint change:",
        fill=(31, 44, 55),
        font=_font(18, True),
    )
    x = 485
    for code in range(8):
        draw.rectangle((x, legend_y, x + 20, legend_y + 20), fill=CHANGE_COLORS[code])
        draw.text(
            (x + 26, legend_y),
            f"{code} {CHANGE_CODE_INFO[code][0].replace('_', ' ')}",
            fill=(43, 51, 56),
            font=_font(13),
        )
        x += 410
    stats = evidence["endpoint_statistics"]
    lines = [
        f"Endpoint: {stats['before_year']} to {stats['after_year']}",
        f"Valid comparison: {stats['valid_comparison']['hectares']:.2f} ha",
        f"Gross gain: {stats['gross_development_gain']['hectares']:.2f} ha",
        f"Apparent loss: {stats['apparent_development_loss']['hectares']:.2f} ha",
        f"Net mapped change: {stats['net_development_change']['hectares']:+.2f} ha",
        f"Network bytes: {network_bytes:,}",
        f"Verified reused bytes: {reused_bytes:,}",
        f"Mapping: {evidence['mapping']['mapping_id']}",
        "Transition reconciliation: "
        + ("PASS" if stats["transition_reconciliation"]["reconciles"] else "FAIL"),
    ]
    draw.text(
        (85, 1130),
        "Original statistics and source evidence",
        fill=(31, 44, 55),
        font=_font(30, True),
    )
    y = 1180
    for line in lines:
        draw.text((90, y), line, fill=(48, 58, 63), font=_font(22))
        y += 41
    transitions = sorted(
        evidence["source_transition"].get("rows", []),
        key=lambda row: (
            -int(row["pixel_count"]),
            int(row["baseline_source_class"]),
            int(row["comparison_source_class"]),
        ),
    )[:4]
    y += 10
    draw.text((90, y), "Top source transitions", fill=(31, 44, 55), font=_font(24, True))
    y += 42
    for row in transitions:
        text = (
            f"{row['baseline_source_class']} {row['baseline_class_label']} -> "
            f"{row['comparison_source_class']} {row['comparison_class_label']}: "
            f"{row['pixel_count']:,} px"
        )
        draw.text((95, y), text, fill=(48, 58, 63), font=_font(17))
        y += 31
    draw.text(
        (2560, 1130),
        "Methodology and limitations",
        fill=(31, 44, 55),
        font=_font(30, True),
    )
    limitation = str(
        evidence["methodology"].get("source_qualification")
        or "Mapped land-cover evidence only."
    )
    words = limitation.split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and draw.textlength(candidate, font=_font(18)) > 1120:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    y = 1185
    for line in lines:
        draw.text((2560, y), line, fill=(96, 57, 52), font=_font(18))
        y += 29
    y += 25
    for line in (
        "NAIP is exact-year natural-color visual context only.",
        "Imagery replacement does not alter analytical counts or rasters.",
        "No population, economic, construction-date, occupancy, or causal claim.",
        f"Hotspot locator: {'available' if locator else 'not requested'}.",
    ):
        draw.text((2560, y), line, fill=(48, 58, 63), font=_font(17))
        y += 36
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("workflow", "human_development_hybrid_publication")
    metadata.add_text("mode", mode)
    metadata.add_text("imagery_year", str(imagery_year))
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, "PNG", optimize=True, pnginfo=metadata)
    return destination


def _compatibility(
    evidence: Mapping[str, Any], options: PublicationOptions
) -> dict[str, Any]:
    return {
        "source_handoff_id": evidence["handoff_id"],
        "source_handoff_checksums_sha256": evidence["checksum_identity"],
        "context_source_id": NAIP_SOURCE_ID,
        "imagery_year": options.imagery_year,
        "publication_mode": options.mode,
        "regional_resolution_m": options.regional_resolution_m,
        "hotspot_resolution_m": options.hotspot_resolution_m,
        "hotspot_size_m": options.hotspot_size_m,
        "crs": "EPSG:5070",
        "mapping_contract_sha256": evidence["mapping_hash"],
    }


def _reuse_candidate(
    publication_root: Path, compatibility: Mapping[str, Any]
) -> Path | None:
    if not publication_root.is_dir():
        return None
    for candidate in sorted(publication_root.iterdir(), reverse=True):
        if not candidate.is_dir() or candidate.name.startswith("."):
            continue
        try:
            manifest = _read_json(candidate / "publication_manifest.json")
            if manifest.get("operation_status") != "completed":
                continue
            if manifest.get("verification_status") != "PASS":
                continue
            if manifest.get("compatibility") != compatibility:
                continue
            _verify_checksums(candidate, _checksum_map(candidate))
            return candidate
        except PublicationError:
            continue
    return None


def _copy_reuse(
    source: Path, staging: Path, relative_paths: Sequence[str]
) -> tuple[int, list[dict[str, Any]]]:
    reused = 0
    receipts = []
    for relative in relative_paths:
        source_path = source / relative
        if not source_path.is_file():
            raise PublicationError(f"reusable imagery is missing: {relative}")
        destination = staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination)
        reused += destination.stat().st_size
        receipts.append({
            "status": "REUSED",
            "output": relative,
            "source_publication_id": source.name,
            "source_checksum_sha256": _sha256(source_path),
            "output_checksum_sha256": _sha256(destination),
            "network_bytes": 0,
            "reused_bytes": destination.stat().st_size,
        })
    return reused, receipts


def publish_human_development_hybrid(
    repository_root: Path,
    handoff: Path,
    options: PublicationOptions,
) -> Path:
    options.validate()
    evidence = validate_handoff(handoff)
    publication_root = Path(
        os.environ.get(
            "FASTERRASTER_PUBLICATION_ROOT",
            str(repository_root / "outputs" / "publications"),
        )
    )
    compatibility = _compatibility(evidence, options)
    reusable = (
        None
        if options.reuse == "never"
        else _reuse_candidate(publication_root, compatibility)
    )
    if options.reuse == "only" and reusable is None:
        raise PublicationError(
            "strict reuse found no compatible verified finalized publication"
        )
    if reusable is None and not options.allow_network:
        raise PublicationError(
            "no reusable imagery; pass --allow-network or select compatible reuse"
        )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = publication_root / (
        f"human-development-hybrid-{evidence['handoff_id']}-{stamp}"
    )
    if final.exists():
        final = final.with_name(
            final.name
            + "-"
            + hashlib.sha256(datetime.now(timezone.utc).isoformat().encode()).hexdigest()[:6]
        )
    regional_required = options.mode in {
        "regional-change", "developed-state", "combined"
    }
    hotspot_required = options.mode in {"hotspot", "combined"}
    regional_relative = f"imagery/naip_{options.imagery_year}_regional.tif"
    hotspot_relative = f"imagery/naip_{options.imagery_year}_hotspot.tif"
    required = []
    if regional_required:
        required.append(regional_relative)
    if hotspot_required:
        required.append(hotspot_relative)
    preview_relative = Path("preview/publication_4k.png")
    with handoff_transaction(final) as staging:
        _write_json(staging / "resolved_publication_config.json", {
            "schema_version": "fasterraster.resolved-publication-config/v1",
            "source_handoff_id": evidence["handoff_id"],
            "mode": options.mode,
            "imagery_year": options.imagery_year,
            "regional_resolution_m": options.regional_resolution_m,
            "hotspot_resolution_m": options.hotspot_resolution_m,
            "hotspot_size_m": options.hotspot_size_m,
            "maximum_download_bytes": int(options.maximum_download_mb * 1_000_000),
            "workers": options.workers,
            "reuse": options.reuse,
            "allow_network": options.allow_network,
        })
        _write_json(staging / "source_handoff_receipt.json", {
            "schema_version": "fasterraster.publication-source-handoff/v1",
            "source_handoff_id": evidence["handoff_id"],
            "workflow": "human_development_change",
            "source_handoff_checksums_sha256": evidence["checksum_identity"],
            "mapping_contract_sha256": evidence["mapping_hash"],
            "endpoint_year": evidence["endpoint_year"],
        })
        _write_json(staging / "source_mapping_receipt.json", evidence["mapping"])
        locator = (
            select_hotspot(evidence["change_path"], options.hotspot_size_m)
            if hotspot_required
            else None
        )
        client = PublicationClient(
            int(options.maximum_download_mb * 1_000_000),
            options.allow_network and reusable is None,
        )
        total_reused = 0
        reuse_receipts: list[dict[str, Any]] = []
        if reusable:
            total_reused, reuse_receipts = _copy_reuse(
                reusable, staging, required
            )
            catalog = _read_json(reusable / "context_catalog_evidence.json")
            catalog = {
                **catalog,
                "status": "REUSED",
                "source_publication_id": reusable.name,
            }
            for index, receipt in enumerate(reuse_receipts):
                _write_json(
                    staging / "tile_receipts" / f"reuse-{index:03d}.json",
                    receipt,
                )
        else:
            catalog = query_catalog(
                client, evidence["bbox"], options.imagery_year
            )
            record_ids = catalog["record_ids"]
            if regional_required:
                tiles, transform, width, height = plan_tiles(
                    _bounds(evidence["grid"]), options.regional_resolution_m
                )
                tile_dir = staging / "imagery" / "tiles" / "regional"
                receipts = acquire_tiles(
                    client,
                    tiles,
                    options.imagery_year,
                    options.regional_resolution_m,
                    tile_dir,
                    options.workers,
                )
                assemble(
                    staging / regional_relative,
                    receipts,
                    tile_dir,
                    transform,
                    width,
                    height,
                )
                for receipt in receipts:
                    output_name = str(receipt["output"])
                    receipt["record_ids"] = record_ids
                    receipt["output"] = (
                        tile_dir / output_name
                    ).relative_to(staging).as_posix()
                    _write_json(
                        staging / "tile_receipts" / f"{receipt['tile_id']}.json",
                        receipt,
                    )
            if hotspot_required and locator:
                tile_size = max(
                    1024,
                    int(math.ceil(
                        options.hotspot_size_m / options.hotspot_resolution_m
                    )),
                )
                tiles, transform, width, height = plan_tiles(
                    locator["bounds_epsg5070"],
                    options.hotspot_resolution_m,
                    tile_size,
                )
                tile_dir = staging / "imagery" / "tiles" / "hotspot"
                receipts = acquire_tiles(
                    client,
                    tiles,
                    options.imagery_year,
                    options.hotspot_resolution_m,
                    tile_dir,
                    1,
                )
                assemble(
                    staging / hotspot_relative,
                    receipts,
                    tile_dir,
                    transform,
                    width,
                    height,
                )
                for receipt in receipts:
                    output_name = str(receipt["output"])
                    receipt["record_ids"] = record_ids
                    receipt["output"] = (
                        tile_dir / output_name
                    ).relative_to(staging).as_posix()
                    _write_json(
                        staging
                        / "tile_receipts"
                        / f"hotspot-{receipt['tile_id']}.json",
                        receipt,
                    )
        total_network = client.budget.total
        _write_json(staging / "context_catalog_evidence.json", catalog)
        _write_json(staging / "imagery_reuse_evidence.json", {
            "schema_version": "fasterraster.publication-imagery-reuse/v1",
            "reuse_mode": options.reuse,
            "source_publication_id": reusable.name if reusable else None,
            "compatibility": compatibility,
            "assets": reuse_receipts,
            "metadata_requests": client.metadata_requests,
            "raster_requests": client.raster_requests,
            "network_bytes": total_network,
            "reused_bytes": total_reused,
        })
        hybrids = []
        regional_hybrid = None
        hotspot_hybrid = None
        if regional_required:
            regional_hybrid = staging / "hybrids" / "regional_hybrid.png"
            kind = (
                "developed-state"
                if options.mode == "developed-state"
                else "change"
            )
            classification = (
                evidence["land_path"]
                if kind == "developed-state"
                else evidence["change_path"]
            )
            hybrids.append(render_hybrid(
                staging / regional_relative,
                classification,
                regional_hybrid,
                kind,
                evidence["mapping"],
            ))
        if hotspot_required:
            hotspot_hybrid = staging / "hybrids" / "hotspot_hybrid.png"
            hybrids.append(render_hybrid(
                staging / hotspot_relative,
                evidence["change_path"],
                hotspot_hybrid,
                "change",
                evidence["mapping"],
            ))
        if locator:
            bounds = locator["bounds_epsg5070"]
            _write_json(staging / "locator_geometry.geojson", {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [bounds[0], bounds[1]], [bounds[2], bounds[1]],
                        [bounds[2], bounds[3]], [bounds[0], bounds[3]],
                        [bounds[0], bounds[1]],
                    ]],
                },
                "properties": locator,
            })
        preview = render_preview(
            staging / preview_relative,
            regional_hybrid,
            hotspot_hybrid,
            evidence,
            options.mode,
            options.imagery_year,
            total_network,
            total_reused,
            locator,
        )
        limitation = str(
            evidence["methodology"].get("source_qualification")
            or "Mapped land-cover evidence only."
        )
        (staging / "methodology_and_limitations.md").write_text(
            "# Methodology and limitations\n\n"
            + limitation
            + "\n\nNAIP is exact-year natural-color visual context. "
            "It does not alter source rasters, masks, transitions, or counts.\n",
            encoding="utf-8",
        )
        manifest = {
            "schema_version": (
                "fasterraster.human-development-hybrid-publication/v1"
            ),
            "operation_status": "completed",
            "verification_status": "PASS",
            "publication_id": final.name,
            "workflow": "human_development_hybrid_publication",
            "source_handoff_id": evidence["handoff_id"],
            "mode": options.mode,
            "compatibility": compatibility,
            "context_catalog_evidence": "context_catalog_evidence.json",
            "source_handoff_receipt": "source_handoff_receipt.json",
            "source_mapping_receipt": "source_mapping_receipt.json",
            "imagery_reuse_evidence": "imagery_reuse_evidence.json",
            "locator_geometry": (
                "locator_geometry.geojson" if locator else None
            ),
            "hybrids": hybrids,
            "preview": preview.relative_to(staging).as_posix(),
            "preview_sha256": _sha256(preview),
            "preview_dimensions": [3840, 2160],
            "network": {
                "metadata_requests": client.metadata_requests,
                "raster_requests": client.raster_requests,
                "network_bytes": total_network,
                "reused_bytes": total_reused,
            },
            "methodology_and_limitations": "methodology_and_limitations.md",
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        _write_json(staging / "publication_manifest.json", manifest)
        _regenerate_checksums(staging)
        _assert_no_staging_provenance(staging)
    preview = final / preview_relative
    if options.open_when_complete:
        _open_final_preview(preview)
    return preview
