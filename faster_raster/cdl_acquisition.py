from __future__ import annotations

import hashlib
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import rasterio
from rasterio.io import MemoryFile
from rasterio.warp import transform_bounds
from rasterio.windows import Window

from faster_raster.ag_geography import SourceCoverageError, validate_cdl_catalog
from faster_raster.development_sources import DevelopmentMapping, USDA_CDL_MAPPING
from faster_raster.human_development import HumanDevelopmentError, TargetGrid, valid_land_cover


CDL_ENDPOINT = "https://pdi.scinet.usda.gov/image/rest/services/CDL_WM/ImageServer"
NAIP_ENDPOINT = "https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPImagery/ImageServer"
CDL_SOURCE_ID = "usda_nass_cdl_imageserver"
NAIP_SOURCE_ID = "usgs_naip_imageserver"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class CacheAsset:
    path: Path
    source_handoff_id: str
    source_checksum: str
    year: int
    action: str
    source_id: str
    semantic_type: str
    evidence: Mapping[str, Any]

    @property
    def bytes(self) -> int:
        return self.path.stat().st_size

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "source_handoff_id": self.source_handoff_id,
            "source_checksum": self.source_checksum,
            "year": self.year,
            "action": self.action,
            "source_id": self.source_id,
            "semantic_type": self.semantic_type,
            "bytes": self.bytes,
            "evidence": dict(self.evidence),
        }


class ArcGISClient:
    def __init__(self, *, byte_ceiling: int, allow_network: bool) -> None:
        self.byte_ceiling = byte_ceiling
        self.allow_network = allow_network
        self.total_bytes = 0
        self.requests: list[dict[str, Any]] = []

    @staticmethod
    def _encoded(params: Mapping[str, Any]) -> dict[str, str]:
        return {
            key: json.dumps(value, separators=(",", ":")) if isinstance(value, (dict, list)) else str(value)
            for key, value in params.items()
        }

    def request(self, endpoint: str, params: Mapping[str, Any], *, label: str, raster: bool = False) -> tuple[bytes, str]:
        if not self.allow_network:
            raise HumanDevelopmentError(f"network is disabled; cannot request {label}")
        encoded = self._encoded(params)
        body = urllib.parse.urlencode(encoded).encode("utf-8")
        headers = {
            "User-Agent": "FasterRaster-Human-Development-CDL/1.0",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "image/tiff,application/json,application/octet-stream" if raster else "application/json",
        }
        retryable = {408, 425, 429, 500, 502, 503, 504}
        last_error: Exception | None = None
        for attempt in range(1, 5):
            started = time.monotonic()
            try:
                request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
                with urllib.request.urlopen(request, timeout=180) as response:
                    content_type = response.headers.get("Content-Type", "")
                    chunks: list[bytes] = []
                    received = 0
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        received += len(chunk)
                        if self.total_bytes + received > self.byte_ceiling:
                            raise HumanDevelopmentError(
                                f"configured byte ceiling exceeded: {self.byte_ceiling:,} bytes"
                            )
                        chunks.append(chunk)
                payload = b"".join(chunks)
                self.total_bytes += len(payload)
                self.requests.append({
                    "label": label,
                    "endpoint": endpoint,
                    "method": "POST",
                    "attempt": attempt,
                    "request_kind": "raster_export" if raster else "metadata_catalog",
                    "redacted_parameters": {
                        key: value
                        for key, value in encoded.items()
                        if key in {"f", "where", "outFields", "returnGeometry", "resultRecordCount", "bboxSR", "imageSR", "size", "format", "interpolation"}
                    },
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "content_type": content_type,
                    "duration_seconds": round(time.monotonic() - started, 3),
                })
                return payload, content_type
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in retryable or attempt == 4:
                    raise HumanDevelopmentError(f"{label} failed with HTTP {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last_error = exc
                if attempt == 4:
                    raise HumanDevelopmentError(f"{label} transport failed: {exc}") from exc
            time.sleep(min(8, 2**attempt))
        raise HumanDevelopmentError(f"{label} failed after retries") from last_error

    def json(self, endpoint: str, params: Mapping[str, Any], *, label: str) -> dict[str, Any]:
        payload, _ = self.request(endpoint, {**params, "f": "json"}, label=label)
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HumanDevelopmentError(f"{label} did not return JSON") from exc
        if not isinstance(value, dict) or "error" in value:
            raise HumanDevelopmentError(f"{label} returned an ArcGIS error: {value.get('error') if isinstance(value, dict) else value}")
        return value


def _geometry(bbox: Sequence[float]) -> dict[str, Any]:
    return {
        "xmin": float(bbox[0]), "ymin": float(bbox[1]),
        "xmax": float(bbox[2]), "ymax": float(bbox[3]),
        "spatialReference": {"wkid": 4326},
    }


def discover_cdl_coverage(
    client: ArcGISClient,
    bbox: Sequence[float],
    years: Sequence[int],
) -> dict[str, Any]:
    service = client.json(CDL_ENDPOINT, {"f": "json"}, label="cdl_service_metadata")
    available = client.json(
        CDL_ENDPOINT + "/query",
        {"where": "1=1", "outFields": "Year", "returnDistinctValues": "true", "returnGeometry": "false", "resultRecordCount": "500"},
        label="cdl_available_years_query",
    )
    attribute_table = client.json(
        CDL_ENDPOINT + "/rasterAttributeTable",
        {"f": "json"},
        label="cdl_raster_attribute_table",
    )
    rat_classes = {
        int(item["attributes"]["Value"]): item["attributes"].get("Class_Names")
        for item in attribute_table.get("features", [])
        if isinstance(item, dict) and isinstance(item.get("attributes"), dict)
    }
    if service.get("bandCount") != 1 or service.get("pixelType") != "U8" or service.get("noDataValue") != 0:
        raise HumanDevelopmentError("CDL service metadata no longer matches the one-band U8 nodata-0 contract")
    for code, rank in USDA_CDL_MAPPING.developed_ranks.items():
        if not rat_classes.get(code):
            raise HumanDevelopmentError(f"CDL raster attribute table is missing developed proxy class {code}")
    results = []
    for year in years:
        response = client.json(
            CDL_ENDPOINT + "/query",
            {
                "where": f"Year = {year}", "geometry": _geometry(bbox),
                "geometryType": "esriGeometryEnvelope", "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "OBJECTID,Name,Year,MinPS,MaxPS,LowPS,HighPS",
                "returnGeometry": "false", "resultRecordCount": "10",
            },
            label=f"cdl_{year}_bbox_catalog_query",
        )
        try:
            validation = validate_cdl_catalog(response, available, requested_year=year)
        except SourceCoverageError:
            raise
        records = [feature.get("attributes", {}) for feature in response.get("features", [])]
        results.append({
            "requested_year": year,
            "exact_coverage_status": "PASS",
            "catalog_record_ids": [int(record["OBJECTID"]) for record in records],
            "catalog_records": records,
            "validation": validation,
            "exact_response": response,
        })
    return {
        "schema_version": "fasterraster.cdl-source-discovery/v1",
        "source_id": CDL_SOURCE_ID,
        "endpoint": CDL_ENDPOINT,
        "service_contract": {
            "capabilities": service.get("capabilities"), "band_count": service.get("bandCount"),
            "pixel_type": service.get("pixelType"), "nodata": service.get("noDataValue"),
            "mean_pixel_size": service.get("meanPixelSize"), "source_crs": "EPSG:3857",
            "default_resampling": service.get("defaultResamplingMethod"),
        },
        "mapping_id": USDA_CDL_MAPPING.mapping_id,
        "mapping_contract_sha256": USDA_CDL_MAPPING.sha256,
        "attribute_table_declared_values": sorted(code for code, label in rat_classes.items() if label),
        "attribute_table_developed_classes": {str(code): rat_classes[code] for code in USDA_CDL_MAPPING.developed_ranks},
        "epochs": results,
        "metadata_network_bytes": client.total_bytes,
        "requests": client.requests,
        "discovered_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def _tile_windows(grid: TargetGrid, ceiling: int) -> list[Window]:
    windows = []
    for row in range(0, grid.height, ceiling):
        for col in range(0, grid.width, ceiling):
            windows.append(Window(col, row, min(ceiling, grid.width - col), min(ceiling, grid.height - row)))
    return windows


def _window_bounds(grid: TargetGrid, window: Window) -> tuple[float, float, float, float]:
    left = grid.transform.c + window.col_off * grid.transform.a
    top = grid.transform.f + window.row_off * grid.transform.e
    right = left + window.width * grid.transform.a
    bottom = top + window.height * grid.transform.e
    return float(left), float(bottom), float(right), float(top)


def inspect_raw_cdl(path: Path, mapping: DevelopmentMapping = USDA_CDL_MAPPING) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size == 0:
        raise HumanDevelopmentError(f"CDL raster is missing or empty: {path}")
    try:
        with rasterio.open(path) as source:
            if source.count != 1 or source.dtypes[0] != "uint8":
                raise HumanDevelopmentError("CDL raw export must be one-band uint8; rendered multiband imagery is rejected")
            values = source.read((1,))[0]
            observed = sorted(int(value) for value in np.unique(values))
            valid = valid_land_cover(values, mapping)
            if not np.any(valid):
                raise HumanDevelopmentError("CDL raw export contains no declared valid class pixels")
            undeclared = sorted(code for code in observed if code not in mapping.valid_codes and code not in mapping.invalid_codes)
            return {
                "driver": source.driver, "width": source.width, "height": source.height,
                "band_count": source.count, "dtype": source.dtypes[0],
                "crs": source.crs.to_string() if source.crs else None,
                "transform": list(source.transform)[:6], "bounds": list(source.bounds),
                "nodata": source.nodata, "observed_class_values": observed,
                "undeclared_values_classified_invalid": undeclared,
                "valid_pixel_count": int(valid.sum()),
            }
    except rasterio.errors.RasterioError as exc:
        raise HumanDevelopmentError(f"CDL payload is not a readable GeoTIFF: {exc}") from exc


def acquire_cdl_epoch(
    *,
    bbox: Sequence[float],
    year: int,
    grid: TargetGrid,
    request_tile_ceiling: int,
    byte_ceiling: int,
    destination: Path,
    allow_network: bool,
    catalog_record_ids: Sequence[int],
) -> dict[str, Any]:
    client = ArcGISClient(byte_ceiling=byte_ceiling, allow_network=allow_network)
    destination.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff", "width": grid.width, "height": grid.height, "count": 1,
        "dtype": "uint8", "crs": grid.crs, "transform": grid.transform,
        "nodata": USDA_CDL_MAPPING.nodata_code, "compress": "deflate",
    }
    windows = _tile_windows(grid, request_tile_ceiling)
    with rasterio.open(destination, "w", **profile) as sink:
        for index, window in enumerate(windows):
            year_start = int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
            year_end = int(datetime(year + 1, 1, 1, tzinfo=timezone.utc).timestamp() * 1000) - 1
            mosaic_rule = {
                "mosaicMethod": "esriMosaicAttribute", "where": f"Year = {year}",
                "sortField": "Year", "sortValue": str(year), "ascending": True,
                "mosaicOperation": "MT_FIRST",
            }
            payload, content_type = client.request(
                CDL_ENDPOINT + "/exportImage",
                {
                    "bbox": ",".join(str(value) for value in _window_bounds(grid, window)),
                    "bboxSR": "5070", "imageSR": "5070",
                    "size": f"{int(window.width)},{int(window.height)}", "format": "tiff",
                    "f": "image", "interpolation": "RSP_NearestNeighbor",
                    "mosaicRule": mosaic_rule, "time": f"{year_start},{year_end}",
                    "transparent": "false",
                },
                label=f"cdl_{year}_raw_r{int(window.row_off):05d}_c{int(window.col_off):05d}",
                raster=True,
            )
            if payload.lstrip().startswith((b"{", b"<")) or "json" in content_type.lower() or "html" in content_type.lower():
                raise HumanDevelopmentError("CDL export returned an error document instead of raw TIFF pixels")
            try:
                with MemoryFile(payload) as memory:
                    with memory.open() as tile:
                        if tile.count != 1 or tile.dtypes[0] != "uint8":
                            raise HumanDevelopmentError("CDL export was rendered or multiband instead of one-band uint8")
                        if tile.width != int(window.width) or tile.height != int(window.height):
                            raise HumanDevelopmentError("CDL service export dimensions differ from the requested exact grid")
                        values = tile.read((1,))[0]
            except rasterio.errors.RasterioError as exc:
                raise HumanDevelopmentError(f"CDL export payload is not a GeoTIFF: {exc}") from exc
            sink.write(values, 1, window=window)
    inspection = inspect_raw_cdl(destination)
    expected = grid.as_dict()
    if inspection["crs"] != grid.crs or inspection["width"] != grid.width or inspection["height"] != grid.height:
        raise HumanDevelopmentError("final CDL epoch grid does not match the deterministic target grid")
    if any(abs(float(a) - float(b)) > 1e-7 for a, b in zip(inspection["transform"], expected["transform"])):
        raise HumanDevelopmentError("final CDL affine transform does not match the deterministic target grid")
    return {
        "schema_version": "fasterraster.cdl-acquisition-receipt/v1",
        "source_id": CDL_SOURCE_ID, "year": year, "service_endpoint": CDL_ENDPOINT,
        "catalog_record_ids": [int(value) for value in catalog_record_ids],
        "export_request_count": len(windows), "requests": client.requests,
        "response_media_types": sorted({item["content_type"] for item in client.requests}),
        "response_bytes": client.total_bytes, "total_network_bytes": client.total_bytes,
        "raster": inspection, "target_grid": grid.as_dict(),
        "resampling": "nearest", "reprojection_status": "service_export_exact_epsg5070",
        "source_checksum_sha256": sha256_file(destination),
        "output_checksum_sha256": sha256_file(destination),
        "output": str(destination),
    }


def _checksums(handoff: Path) -> dict[str, str]:
    path = handoff / "checksums.sha256"
    if not path.is_file():
        return {}
    result = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "  " in line:
            digest, relative = line.split("  ", 1)
            result[relative.strip()] = digest.strip()
    return result


def _checksum_valid(handoff: Path, asset: Path) -> tuple[bool, str | None]:
    try:
        relative = asset.relative_to(handoff).as_posix()
    except ValueError:
        return False, None
    expected = _checksums(handoff).get(relative)
    return bool(expected and expected == sha256_file(asset)), expected


def _covers_bbox(path: Path, bbox: Sequence[float]) -> bool:
    with rasterio.open(path) as source:
        if source.crs is None:
            return False
        bounds = transform_bounds(source.crs, "EPSG:4326", *source.bounds, densify_pts=21)
    return bounds[0] <= bbox[0] + 1e-5 and bounds[1] <= bbox[1] + 1e-5 and bounds[2] >= bbox[2] - 1e-5 and bounds[3] >= bbox[3] - 1e-5


def find_cached_cdl_asset(handoff_root: Path, bbox: Sequence[float], year: int, grid: TargetGrid) -> CacheAsset | None:
    if not handoff_root.is_dir():
        return None
    for handoff in sorted((path for path in handoff_root.iterdir() if path.is_dir() and not path.name.startswith(".")), reverse=True):
        receipt_path = handoff / "workflow_receipt.json"
        if receipt_path.is_file():
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                receipt = {}
            asset = handoff / "data" / "epochs" / str(year) / "land_cover.tif"
            valid_checksum, checksum = _checksum_valid(handoff, asset) if asset.is_file() else (False, None)
            if (
                receipt.get("final_status") == "PASS"
                and receipt.get("source_id") == CDL_SOURCE_ID
                and receipt.get("mapping_id") == USDA_CDL_MAPPING.mapping_id
                and year in receipt.get("epochs", [])
                and valid_checksum
            ):
                with rasterio.open(asset) as source:
                    exact = source.crs and source.crs.to_string() == grid.crs and source.width == grid.width and source.height == grid.height and source.transform == grid.transform
                if exact:
                    return CacheAsset(asset, handoff.name, str(checksum), year, "reuse_exact", CDL_SOURCE_ID, "categorical_raw_classes", {"finalized": True, "checksums": True})
        manifest_path = handoff / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        order = manifest.get("order", {})
        if manifest.get("operation_status") != "completed" or int(order.get("cdl_year", -1)) != year:
            continue
        if list(order.get("bbox_epsg_4326", [])) != [float(value) for value in bbox]:
            continue
        for layer in manifest.get("layers", []):
            if layer.get("name") != "cdl_classes" or layer.get("semantic_type") != "categorical":
                continue
            asset = handoff / str(layer.get("output"))
            valid_checksum, checksum = _checksum_valid(handoff, asset) if asset.is_file() else (False, None)
            if valid_checksum and _covers_bbox(asset, bbox):
                inspection = inspect_raw_cdl(asset)
                return CacheAsset(asset, handoff.name, str(checksum), year, "reuse_crop", CDL_SOURCE_ID, "categorical_raw_classes", {"finalized": True, "checksums": True, "inspection": inspection})
    return None


def find_cached_naip_context(handoff_root: Path, bbox: Sequence[float], year: int) -> CacheAsset | None:
    if not handoff_root.is_dir():
        return None
    for handoff in sorted((path for path in handoff_root.iterdir() if path.is_dir() and not path.name.startswith(".")), reverse=True):
        manifest_path = handoff / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        order = manifest.get("order", {})
        if manifest.get("operation_status") != "completed" or int(order.get("cdl_year", -1)) != year:
            continue
        if list(order.get("bbox_epsg_4326", [])) != [float(value) for value in bbox]:
            continue
        for layer in manifest.get("layers", []):
            if layer.get("name") != "natural" or layer.get("semantic_type") != "continuous":
                continue
            asset = handoff / str(layer.get("output"))
            valid_checksum, checksum = _checksum_valid(handoff, asset) if asset.is_file() else (False, None)
            if valid_checksum and _covers_bbox(asset, bbox):
                return CacheAsset(asset, handoff.name, str(checksum), year, "reuse_crop", NAIP_SOURCE_ID, "natural_color_context", {"finalized": True, "checksums": True})
    return None
