from __future__ import annotations

import hashlib
import json
import math
import subprocess
import urllib.request
import zlib
import struct
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter
from typing import Any
from urllib.parse import urlencode

from faster_raster import stack_preview
from faster_raster import preview_compositor
from faster_raster.task_builder import TASK_PREVIEWS_DIR, validate_task

CACHE_DIR = TASK_PREVIEWS_DIR / "cache"
DEFAULT_MAX_BYTES_PER_SOURCE = 65536
DEFAULT_MAX_PIXELS = 262144
DEFAULT_TIMEOUT_SECONDS = 25
DEFAULT_PREVIEW_SIZE = 512
DEFAULT_SAMPLE_GRID_SIZE = 3
DEFAULT_PREVIEW_EXPAND_FACTOR = 1.0
ALLOWED_CDL_RENDER_MODES = {"auto", "service_png", "manual_samples", "service_tiff"}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SUPPORTED_REAL_SOURCES = {"cdl_arcgis_tiny_export", "daymet_single_pixel_prcp_rest"}
CDL_EXPORT_CANDIDATE_ORDER = [
    ("no_time", "png32"),
    ("no_time", "png"),
    ("time_mid_year_epoch", "png32"),
    ("time_mid_year_epoch", "png"),
    ("time_year_interval", "png32"),
    ("time_year_interval", "png"),
    ("mosaic_year_eq", "png32"),
    ("mosaic_year_eq", "png"),
    ("time_year_string", "png32"),
    ("time_year_string", "png"),
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def redact_url(url: str) -> str:
    for key in ["token=", "password=", "secret=", "api_key=", "apikey=", "bearer="]:
        lower = url.lower()
        idx = lower.find(key)
        if idx >= 0:
            end = url.find("&", idx)
            if end < 0:
                end = len(url)
            return url[: idx + len(key)] + "REDACTED" + url[end:]
    return url


def centroid(bbox: list[float]) -> tuple[float, float]:
    return ((float(bbox[0]) + float(bbox[2])) / 2.0, (float(bbox[1]) + float(bbox[3])) / 2.0)


def first_year(task: dict[str, Any]) -> int | None:
    years = (task.get("time") or {}).get("years") or []
    return int(years[0]) if years else None


def effective_preview_size(preview_size: int, max_pixels: int) -> int:
    return max(1, min(int(preview_size), int(math.sqrt(max_pixels))))



def normalize_sample_grid_size(value: int | None) -> int:
    if value is None:
        value = DEFAULT_SAMPLE_GRID_SIZE
    value = int(value)
    if value < 1 or value > 7:
        raise ValueError("sample_grid_size must be between 1 and 7")
    return value


def normalize_preview_expand_factor(value: float | None) -> float:
    if value is None:
        value = DEFAULT_PREVIEW_EXPAND_FACTOR
    value = float(value)
    if value < 1.0 or value > 25.0:
        raise ValueError("preview_expand_factor must be between 1.0 and 25.0")
    return value


def normalize_cdl_render_mode(value: str | None) -> str:
    value = (value or "auto").strip().lower()
    if value not in ALLOWED_CDL_RENDER_MODES:
        raise ValueError(f"invalid cdl_render_mode: {value}")
    return value


def expanded_bbox(bbox: list[float], expand_factor: float) -> list[float]:
    factor = normalize_preview_expand_factor(expand_factor)
    min_x, min_y, max_x, max_y = [float(v) for v in bbox]
    cx, cy = centroid(bbox)
    half_w = (max_x - min_x) * factor / 2.0
    half_h = (max_y - min_y) * factor / 2.0
    return [round(cx - half_w, 8), round(cy - half_h, 8), round(cx + half_w, 8), round(cy + half_h, 8)]


def sample_points_for_bbox(bbox: list[float], grid_size: int) -> list[tuple[float, float]]:
    grid = normalize_sample_grid_size(grid_size)
    min_x, min_y, max_x, max_y = [float(v) for v in bbox]
    if grid == 1:
        return [centroid(bbox)]
    points: list[tuple[float, float]] = []
    for row in range(grid):
        y = min_y + (max_y - min_y) * row / (grid - 1)
        for col in range(grid):
            x = min_x + (max_x - min_x) * col / (grid - 1)
            points.append((round(x, 8), round(y, 8)))
    return points


def cdl_preview_url(
    task: dict[str, Any],
    *,
    max_pixels: int,
    preview_size: int = DEFAULT_PREVIEW_SIZE,
    image_format: str = "png32",
    preview_fetch_bbox: list[float] | None = None,
    time_strategy: str = "time_year_string",
) -> str:
    width = effective_preview_size(preview_size, max_pixels)
    height = width
    bbox_values = preview_fetch_bbox or task["aoi"]["bbox"]
    bbox = ",".join(f"{float(v):.8f}" for v in bbox_values)
    year = first_year(task) or 2023
    params = {
        "bbox": bbox,
        "bboxSR": "4326",
        "imageSR": "4326",
        "size": f"{width},{height}",
        "format": image_format,
        "transparent": "false",
        "f": "image",
    }
    if time_strategy == "time_year_string":
        params["time"] = str(year)
    elif time_strategy == "time_mid_year_epoch":
        params["time"] = str(int(datetime(year, 7, 1, tzinfo=timezone.utc).timestamp() * 1000))
    elif time_strategy == "time_year_interval":
        start = int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        end = int(datetime(year + 1, 1, 1, tzinfo=timezone.utc).timestamp() * 1000) - 1
        params["time"] = f"{start},{end}"
    elif time_strategy == "mosaic_year_eq":
        params["mosaicRule"] = json.dumps(
            {
                "mosaicMethod": "esriMosaicAttribute",
                "where": f"Year = {year}",
                "sortField": "Year",
                "sortValue": str(year),
            },
            separators=(",", ":"),
        )
    elif time_strategy != "no_time":
        raise ValueError(f"invalid CDL time strategy: {time_strategy}")
    return "https://pdi.scinet.usda.gov/image/rest/services/CDL_WM/ImageServer/exportImage?" + urlencode(sorted(params.items()))


def cdl_export_candidates(
    task: dict[str, Any],
    *,
    max_pixels: int,
    preview_size: int = DEFAULT_PREVIEW_SIZE,
    preview_fetch_bbox: list[float] | None = None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for index, (time_strategy, image_format) in enumerate(CDL_EXPORT_CANDIDATE_ORDER, start=1):
        url = cdl_preview_url(
            task,
            max_pixels=max_pixels,
            preview_size=preview_size,
            image_format=image_format,
            preview_fetch_bbox=preview_fetch_bbox,
            time_strategy=time_strategy,
        )
        candidates.append({
            "candidate_index": index,
            "candidate_id": f"{time_strategy}_{image_format}",
            "time_strategy": time_strategy,
            "format": image_format,
            "url": url,
            "url_redacted": redact_url(url),
        })
    return candidates


def cdl_attempt_urls(task: dict[str, Any], *, max_pixels: int, preview_size: int = DEFAULT_PREVIEW_SIZE, preview_fetch_bbox: list[float] | None = None, cdl_render_mode: str = "auto") -> list[str]:
    mode = normalize_cdl_render_mode(cdl_render_mode)
    if mode == "service_tiff":
        return [cdl_preview_url(task, max_pixels=max_pixels, preview_size=preview_size, image_format="tiff", preview_fetch_bbox=preview_fetch_bbox)]
    return [candidate["url"] for candidate in cdl_export_candidates(task, max_pixels=max_pixels, preview_size=preview_size, preview_fetch_bbox=preview_fetch_bbox)]


def cdl_identify_url(task: dict[str, Any], point: tuple[float, float], *, preview_fetch_bbox: list[float]) -> str:
    year = first_year(task) or 2023
    params = {
        "geometry": f"{point[0]:.8f},{point[1]:.8f}",
        "geometryType": "esriGeometryPoint",
        "sr": "4326",
        "returnGeometry": "false",
        "returnCatalogItems": "false",
        "f": "json",
        "time": str(year),
        "mapExtent": ",".join(f"{float(v):.8f}" for v in preview_fetch_bbox),
        "imageDisplay": "512,512,96",
        "tolerance": "1",
    }
    return "https://pdi.scinet.usda.gov/image/rest/services/CDL_WM/ImageServer/identify?" + urlencode(sorted(params.items()))


def parse_cdl_identify_payload(data: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(data.decode("utf-8", errors="replace"))
    except Exception:
        return {"value": None, "class_name": None, "meaningful": False, "raw": None}
    value = payload.get("value") or payload.get("pixelValue") or payload.get("name")
    class_name = payload.get("name")
    props = payload.get("properties") if isinstance(payload.get("properties"), dict) else {}
    if value is None:
        value = props.get("ClassName") or props.get("value") or props.get("Pixel Value")
    if class_name is None:
        class_name = props.get("ClassName") or props.get("Category")
    text = "" if value is None else str(value).strip().lower()
    meaningful = bool(text and text not in {"0", "255", "nodata", "no data", "none", "null", "nan"})
    return {"value": value, "class_name": class_name, "meaningful": meaningful, "raw": payload}


def verify_cdl_samples(task: dict[str, Any], *, preview_fetch_bbox: list[float], sample_grid_size: int, max_bytes_per_source: int, timeout_seconds: int) -> dict[str, Any]:
    points = sample_points_for_bbox(preview_fetch_bbox, sample_grid_size)
    sample_results = []
    attempted_urls = []
    values = []
    class_names = []
    for point in points:
        url = cdl_identify_url(task, point, preview_fetch_bbox=preview_fetch_bbox)
        attempted_urls.append(redact_url(url))
        try:
            fetched = read_bounded_url(url, max_bytes=max_bytes_per_source, timeout_seconds=timeout_seconds)
            parsed = parse_cdl_identify_payload(fetched["data"])
            sample_results.append({"point": [point[0], point[1]], "value": parsed["value"], "class_name": parsed["class_name"], "meaningful": parsed["meaningful"], "http_status": fetched["http_status"], "bytes_read": fetched["bytes_read"]})
            if parsed["meaningful"]:
                values.append(str(parsed["value"]))
                if parsed["class_name"]:
                    class_names.append(str(parsed["class_name"]))
        except Exception as exc:
            sample_results.append({"point": [point[0], point[1]], "value": None, "class_name": None, "meaningful": False, "error": str(exc)})
    unique_values = sorted(set(values))
    return {"sample_verification_attempted": True, "sample_grid_size": normalize_sample_grid_size(sample_grid_size), "sample_points_count": len(points), "sample_values_count": len(values), "unique_sample_values": unique_values, "sample_class_names": sorted(set(class_names)), "sample_results": sample_results, "attempted_urls_redacted": attempted_urls, "cdl_meaningful": bool(unique_values)}


def daymet_preview_url(task: dict[str, Any]) -> str:
    lon, lat = centroid(task["aoi"]["bbox"])
    year = first_year(task) or 2023
    params = {
        "lat": f"{lat:.6f}",
        "lon": f"{lon:.6f}",
        "vars": "prcp",
        "years": str(year),
    }
    return "https://daymet.ornl.gov/single-pixel/api/data?" + urlencode(sorted(params.items()))


def read_bounded_url(url: str, *, max_bytes: int, timeout_seconds: int) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "FasterRaster-real-preview/0.5.5"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        status = getattr(response, "status", None) or getattr(response, "code", None)
        content_type = response.headers.get("Content-Type", "")
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"byte cap exceeded: read more than {max_bytes} bytes")
    return {
        "http_status": status,
        "content_type": content_type,
        "data": data,
        "bytes_read": len(data),
        "sha256": _sha256(data),
    }


def _png_chunks(data: bytes):
    if not data.startswith(PNG_SIGNATURE):
        return
    offset = len(PNG_SIGNATURE)
    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        chunk_data = data[offset + 8:offset + 8 + length]
        yield kind, chunk_data
        offset += 12 + length
        if kind == b"IEND":
            break


def _unfilter_scanline(filter_type: int, scan: bytearray, prev: bytearray, bpp: int) -> bytearray:
    out = bytearray(scan)
    for i, value in enumerate(out):
        left = out[i - bpp] if i >= bpp else 0
        up = prev[i] if prev else 0
        up_left = prev[i - bpp] if prev and i >= bpp else 0
        if filter_type == 1:
            out[i] = (value + left) & 0xFF
        elif filter_type == 2:
            out[i] = (value + up) & 0xFF
        elif filter_type == 3:
            out[i] = (value + ((left + up) // 2)) & 0xFF
        elif filter_type == 4:
            p = left + up - up_left
            pa, pb, pc = abs(p - left), abs(p - up), abs(p - up_left)
            predictor = left if pa <= pb and pa <= pc else up if pb <= pc else up_left
            out[i] = (value + predictor) & 0xFF
    return out


def decode_png_pixels(data: bytes, *, max_sample_pixels: int = 65536) -> dict[str, Any]:
    width = height = bit_depth = color_type = None
    palette: list[tuple[int, int, int]] = []
    idat = bytearray()
    for kind, chunk in _png_chunks(data) or []:
        if kind == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", chunk[:10])
        elif kind == b"PLTE":
            palette = [tuple(chunk[i:i + 3]) for i in range(0, len(chunk), 3)]
        elif kind == b"IDAT":
            idat.extend(chunk)
    mode_by_type = {0: "L", 2: "RGB", 3: "P", 6: "RGBA"}
    mode = mode_by_type.get(color_type, f"PNG_COLOR_{color_type}")
    pixels: list[tuple[int, int, int, int]] = []
    if width is None or height is None or bit_depth != 8 or color_type not in {0, 2, 3, 6}:
        return {"width": width, "height": height, "mode": mode, "pixels": pixels}
    try:
        raw = zlib.decompress(bytes(idat))
        channels = {0: 1, 2: 3, 3: 1, 6: 4}[color_type]
        bpp = channels
        stride = width * channels
        prev = bytearray(stride)
        offset = 0
        step = max(1, (width * height) // max_sample_pixels)
        pixel_index = 0
        for _y in range(height):
            filter_type = raw[offset]
            offset += 1
            line = _unfilter_scanline(filter_type, bytearray(raw[offset:offset + stride]), prev, bpp)
            offset += stride
            prev = line
            for x in range(width):
                if pixel_index % step == 0:
                    base = x * channels
                    if color_type == 0:
                        v = line[base]; pixels.append((v, v, v, 255))
                    elif color_type == 2:
                        pixels.append((line[base], line[base + 1], line[base + 2], 255))
                    elif color_type == 3:
                        idx = line[base]
                        r, g, b = palette[idx] if idx < len(palette) else (idx, idx, idx)
                        pixels.append((r, g, b, 255))
                    elif color_type == 6:
                        pixels.append((line[base], line[base + 1], line[base + 2], line[base + 3]))
                pixel_index += 1
    except Exception:
        pixels = []
    return {"width": width, "height": height, "mode": mode, "pixels": pixels}


def diagnose_image(data: bytes, *, content_type: str | None = None, bytes_read: int | None = None) -> dict[str, Any]:
    decoded = decode_png_pixels(data)
    pixels = decoded.get("pixels") or []
    counter = Counter(pixels)
    unique_count = len(counter) if pixels else 0
    dominant = counter.most_common(1)[0] if counter else (((0, 0, 0, 0), 0))
    dominant_fraction = dominant[1] / len(pixels) if pixels else None
    nontransparent = sum(count for color, count in counter.items() if color[3] > 0)
    transparent_fraction = 1 - (nontransparent / len(pixels)) if pixels else None
    diversity_score = min(1.0, unique_count / 32.0) if pixels else 0.0
    notes: list[str] = []
    mostly_single = bool(pixels and (unique_count <= 2 or (dominant_fraction is not None and dominant_fraction > 0.95)))
    tiny = bool((decoded.get("width") and decoded["width"] < 32) or (decoded.get("height") and decoded["height"] < 32) or (bytes_read is not None and bytes_read < 1024))
    if not pixels:
        notes.append("image bytes could not be decoded for pixel diagnostics")
    if mostly_single:
        notes.append("mostly single class/color; AOI may be too small or uniform")
    if tiny:
        notes.append("preview image is very small or low byte count")
    return {
        "image_width": decoded.get("width"),
        "image_height": decoded.get("height"),
        "image_mode": decoded.get("mode"),
        "unique_color_count": unique_count,
        "dominant_color": list(dominant[0]),
        "dominant_color_fraction": dominant_fraction,
        "nontransparent_pixel_count": nontransparent,
        "transparent_pixel_fraction": transparent_fraction,
        "diversity_score": diversity_score,
        "is_probably_placeholder": mostly_single or (tiny and not pixels),
        "is_mostly_single_class": mostly_single,
        "diagnostic_notes": notes,
    }


def is_meaningful_export_image(diagnostics: dict[str, Any]) -> bool:
    dominant = diagnostics.get("dominant_color_fraction")
    return (
        (diagnostics.get("unique_color_count") or 0) > 2
        and (diagnostics.get("nontransparent_pixel_count") or 0) > 0
        and dominant is not None
        and dominant < 0.95
        and not diagnostics.get("is_probably_placeholder")
    )


def cache_response(task_id: str, source_id: str, data: bytes, content_type: str | None) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    sha = _sha256(data)
    suffix = ".png" if "png" in (content_type or "").lower() or data.startswith(PNG_SIGNATURE) else ".bin"
    path = CACHE_DIR / f"{task_id}_{source_id}_{sha[:16]}{suffix}"
    path.write_bytes(data)
    return path


def empty_source_result(source_id: str, theme: str | None = None) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "theme": theme,
        "attempted": False,
        "rendered": False,
        "render_kind": "semantic_fallback",
        "status": "planned",
        "url_redacted": None,
        "http_status": None,
        "bytes_read": 0,
        "content_type": None,
        "sha256": None,
        "cache_path": None,
        "image_width": None,
        "image_height": None,
        "image_mode": None,
        "unique_color_count": None,
        "dominant_color": None,
        "dominant_color_fraction": None,
        "nontransparent_pixel_count": None,
        "transparent_pixel_fraction": None,
        "diversity_score": None,
        "is_probably_placeholder": False,
        "is_mostly_single_class": False,
        "diagnostic_notes": [],
        "attempted_urls_redacted": [],
        "service_png_diagnostics": None,
        "sample_verification_attempted": False,
        "sample_grid_size": None,
        "sample_points_count": 0,
        "sample_values_count": 0,
        "unique_sample_values": [],
        "sample_class_names": [],
        "sample_results": [],
        "cdl_meaningful": None,
        "renderer_problem_suspected": False,
        "no_data_suspected": False,
        "preview_fetch_bbox": None,
        "error": None,
        "warning": None,
        "export_candidate_count": 0,
        "selected_export_candidate": None,
        "selected_export_time_strategy": None,
        "selected_export_format": None,
        "export_candidates": [],
        "export_cascade_run": False,
        "export_cascade_success": False,
        "export_cascade_reason": None,
    }


def plan_source(task: dict[str, Any], source_id: str, *, max_pixels: int, include_archives: bool, preview_size: int = DEFAULT_PREVIEW_SIZE, preview_fetch_bbox: list[float] | None = None, sample_grid_size: int = DEFAULT_SAMPLE_GRID_SIZE, cdl_render_mode: str = "auto") -> dict[str, Any]:
    result = empty_source_result(source_id)
    if source_id == "cdl_arcgis_tiny_export":
        result.update({
            "status": "supported_real_preview",
            "render_kind": "real_raster",
            "url_redacted": redact_url(cdl_attempt_urls(task, max_pixels=max_pixels, preview_size=preview_size, preview_fetch_bbox=preview_fetch_bbox, cdl_render_mode=cdl_render_mode)[0]),
            "attempted_urls_redacted": [redact_url(url) for url in cdl_attempt_urls(task, max_pixels=max_pixels, preview_size=preview_size, preview_fetch_bbox=preview_fetch_bbox, cdl_render_mode=cdl_render_mode)],
            "export_candidate_count": len(cdl_export_candidates(task, max_pixels=max_pixels, preview_size=preview_size, preview_fetch_bbox=preview_fetch_bbox)),
            "export_candidates": [{k: v for k, v in candidate.items() if k != "url"} for candidate in cdl_export_candidates(task, max_pixels=max_pixels, preview_size=preview_size, preview_fetch_bbox=preview_fetch_bbox)],
            "warning": "requires --allow-network to fetch tiny CDL preview",
            "sample_grid_size": sample_grid_size,
            "preview_fetch_bbox": preview_fetch_bbox or task["aoi"]["bbox"],
        })
    elif source_id == "daymet_single_pixel_prcp_rest":
        result.update({
            "status": "supported_real_preview",
            "render_kind": "real_point",
            "url_redacted": redact_url(daymet_preview_url(task)),
            "attempted_urls_redacted": [redact_url(daymet_preview_url(task))],
            "warning": "requires --allow-network to fetch tiny Daymet point preview",
        })
    elif source_id == "prism_daily_ppt_static_zip":
        warning = "archive_requires_explicit_include_archives"
        if include_archives:
            warning = "archive_preview_not_fetched_in_dry_run"
        result.update({"status": "semantic_fallback", "warning": warning})
    elif source_id == "usgs_3dep_dem":
        result.update({"status": "adapter_needed", "warning": "no_safe_tiny_dem_endpoint_yet"})
    else:
        result.update({"status": "semantic_fallback", "warning": "unsupported_source_for_real_preview"})
    return result


def fetch_cdl_source(
    task: dict[str, Any],
    result: dict[str, Any],
    *,
    max_bytes_per_source: int,
    max_pixels: int,
    timeout_seconds: int,
    cache_raw: bool,
    preview_size: int,
    preview_fetch_bbox: list[float] | None,
    cdl_verify_samples: bool,
    sample_grid_size: int,
) -> dict[str, Any]:
    candidates = cdl_export_candidates(task, max_pixels=max_pixels, preview_size=preview_size, preview_fetch_bbox=preview_fetch_bbox)
    result["export_cascade_run"] = True
    result["export_candidate_count"] = len(candidates)
    result["export_candidates"] = []
    result["attempted_urls_redacted"] = []
    result["preview_fetch_bbox"] = preview_fetch_bbox or task["aoi"]["bbox"]
    result["sample_grid_size"] = normalize_sample_grid_size(sample_grid_size)
    last_error: Exception | None = None
    last_diagnostics: dict[str, Any] | None = None

    for candidate in candidates:
        result["url_redacted"] = candidate["url_redacted"]
        result["attempted_urls_redacted"].append(candidate["url_redacted"])
        candidate_report = {k: v for k, v in candidate.items() if k != "url"}
        try:
            fetched = read_bounded_url(candidate["url"], max_bytes=max_bytes_per_source, timeout_seconds=timeout_seconds)
            diagnostics = diagnose_image(fetched["data"], content_type=fetched["content_type"], bytes_read=fetched["bytes_read"])
            last_diagnostics = diagnostics
            meaningful = is_meaningful_export_image(diagnostics)
            candidate_report.update({
                "http_status": fetched["http_status"],
                "content_type": fetched["content_type"],
                "bytes_read": fetched["bytes_read"],
                "sha256": fetched["sha256"],
                "meaningful": meaningful,
                **diagnostics,
            })
            result["export_candidates"].append(candidate_report)
            if meaningful:
                result.update({k: fetched[k] for k in ["http_status", "content_type", "bytes_read", "sha256"]})
                if cache_raw:
                    result["cache_path"] = str(cache_response(task["task_id"], result["source_id"], fetched["data"], fetched["content_type"]))
                result.update(diagnostics)
                result["service_png_diagnostics"] = diagnostics
                result["rendered"] = True
                result["real_raster_rendered"] = True
                result["render_kind"] = "real_raster"
                result["status"] = "real_raster_rendered"
                result["cdl_meaningful"] = True
                result["warning"] = None
                result["selected_export_candidate"] = candidate["candidate_id"]
                result["selected_export_time_strategy"] = candidate["time_strategy"]
                result["selected_export_format"] = candidate["format"]
                result["export_cascade_success"] = True
                result["export_cascade_reason"] = "selected_first_meaningful_export_image"
                return result
        except Exception as exc:
            last_error = exc
            candidate_report.update({"meaningful": False, "error": str(exc)})
            result["export_candidates"].append(candidate_report)
            continue

    result["export_cascade_success"] = False
    result["export_cascade_reason"] = "no_candidate_passed_meaningful_image_gate"
    if last_diagnostics:
        result.update(last_diagnostics)
        result["service_png_diagnostics"] = last_diagnostics

    if cdl_verify_samples and last_diagnostics is not None:
        verification = verify_cdl_samples(task, preview_fetch_bbox=result["preview_fetch_bbox"], sample_grid_size=sample_grid_size, max_bytes_per_source=max_bytes_per_source, timeout_seconds=timeout_seconds)
        result.update(verification)
        result["attempted_urls_redacted"] = (result.get("attempted_urls_redacted") or []) + verification["attempted_urls_redacted"]
        if verification["cdl_meaningful"]:
            result["rendered"] = True
            result["status"] = "real_data_verified_manual_samples"
            result["render_kind"] = "real_categorical_samples"
            result["real_raster_rendered"] = False
            result["real_point_or_sample_data_rendered"] = True
            result["renderer_problem_suspected"] = True
            result["no_data_suspected"] = False
            result["warning"] = "CDL export cascade found no meaningful image; identify samples found meaningful class values"
            return result

    if result.get("sample_verification_attempted") and not result.get("cdl_meaningful"):
        result["warning"] = "CDL preview response was single-color and identify returned no meaningful class values"

    if last_error and last_diagnostics is None:
        result["status"] = "fetch_failed"
        result["error"] = str(last_error)
        result["warning"] = "real fetch failed; semantic fallback used"
        return result

    result["rendered"] = False
    result["status"] = "no_data_or_placeholder"
    result["render_kind"] = "no_data_or_placeholder"
    result["real_raster_rendered"] = False
    result["cdl_meaningful"] = False
    result["renderer_problem_suspected"] = False
    result["no_data_suspected"] = True
    result["warning"] = result.get("warning") or "CDL export cascade found no meaningful image candidate"
    return result


def fetch_source(
    task: dict[str, Any],
    source_id: str,
    *,
    max_bytes_per_source: int,
    max_pixels: int,
    timeout_seconds: int,
    include_archives: bool,
    cache_raw: bool = True,
    preview_size: int = DEFAULT_PREVIEW_SIZE,
    preview_fetch_bbox: list[float] | None = None,
    cdl_verify_samples: bool = True,
    sample_grid_size: int = DEFAULT_SAMPLE_GRID_SIZE,
    cdl_render_mode: str = "auto",
) -> dict[str, Any]:
    result = plan_source(task, source_id, max_pixels=max_pixels, include_archives=include_archives, preview_size=preview_size, preview_fetch_bbox=preview_fetch_bbox, sample_grid_size=sample_grid_size, cdl_render_mode=cdl_render_mode)
    if source_id not in SUPPORTED_REAL_SOURCES:
        result["attempted"] = False
        return result
    urls = result.get("attempted_urls_redacted") or [result["url_redacted"]]
    result["attempted"] = True
    if source_id == "cdl_arcgis_tiny_export" and cdl_render_mode != "service_tiff":
        return fetch_cdl_source(
            task,
            result,
            max_bytes_per_source=max_bytes_per_source,
            max_pixels=max_pixels,
            timeout_seconds=timeout_seconds,
            cache_raw=cache_raw,
            preview_size=preview_size,
            preview_fetch_bbox=preview_fetch_bbox,
            cdl_verify_samples=cdl_verify_samples,
            sample_grid_size=sample_grid_size,
        )
    last_error: Exception | None = None
    for url in urls:
        result["url_redacted"] = url
        try:
            fetched = read_bounded_url(url, max_bytes=max_bytes_per_source, timeout_seconds=timeout_seconds)
            result.update({k: fetched[k] for k in ["http_status", "content_type", "bytes_read", "sha256"]})
            if cache_raw:
                result["cache_path"] = str(cache_response(task["task_id"], source_id, fetched["data"], fetched["content_type"]))
            if source_id == "cdl_arcgis_tiny_export":
                diagnostics = diagnose_image(fetched["data"], content_type=fetched["content_type"], bytes_read=fetched["bytes_read"])
                result.update(diagnostics)
                result["service_png_diagnostics"] = diagnostics
                result["sample_grid_size"] = normalize_sample_grid_size(sample_grid_size)
                result["preview_fetch_bbox"] = preview_fetch_bbox or task["aoi"]["bbox"]
                single_or_placeholder = diagnostics.get("is_mostly_single_class")
                if cdl_verify_samples and single_or_placeholder:
                    verification = verify_cdl_samples(task, preview_fetch_bbox=result["preview_fetch_bbox"], sample_grid_size=sample_grid_size, max_bytes_per_source=max_bytes_per_source, timeout_seconds=timeout_seconds)
                    result.update(verification)
                    result["attempted_urls_redacted"] = (result.get("attempted_urls_redacted") or []) + verification["attempted_urls_redacted"]
                    if verification["cdl_meaningful"]:
                        result["rendered"] = True
                        result["status"] = "real_data_verified_manual_samples"
                        result["render_kind"] = "real_categorical_samples"
                        result["real_raster_rendered"] = False
                        result["real_point_or_sample_data_rendered"] = True
                        result["renderer_problem_suspected"] = True
                        result["no_data_suspected"] = False
                        result["warning"] = "CDL service image was single-color; identify samples found meaningful class values"
                    else:
                        result["rendered"] = False
                        result["status"] = "no_data_or_placeholder"
                        result["render_kind"] = "no_data_or_placeholder"
                        result["real_raster_rendered"] = False
                        result["cdl_meaningful"] = False
                        result["renderer_problem_suspected"] = False
                        result["no_data_suspected"] = True
                        result["warning"] = "CDL preview response was single-color and identify returned no meaningful class values"
                    return result
                result["rendered"] = result["image_width"] is not None
                result["real_raster_rendered"] = result["rendered"]
                result["render_kind"] = "real_raster"
                result["status"] = "real_raster_rendered" if result["rendered"] else "fetch_open_failed"
                result["cdl_meaningful"] = result["rendered"]
                result["warning"] = "mostly single CDL class in tiny AOI" if result["is_mostly_single_class"] else None
                return result
            if source_id == "daymet_single_pixel_prcp_rest":
                result["rendered"] = True
                result["real_point_data_rendered"] = True
                result["render_kind"] = "real_point"
                result["status"] = "real_point_rendered"
                result["warning"] = None
                result["diagnostic_notes"] = ["point/time data rendered as annotation card"]
                return result
        except Exception as exc:
            last_error = exc
            continue
    result["rendered"] = False
    result["status"] = "fetch_failed"
    result["error"] = str(last_error) if last_error else "unknown fetch error"
    result["warning"] = "real fetch failed; semantic fallback used"
    return result


def _counts(source_results: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "real_raster_layer_count": sum(1 for r in source_results if r.get("render_kind") == "real_raster" and r.get("rendered")),
        "real_point_layer_count": sum(1 for r in source_results if r.get("render_kind") == "real_point" and r.get("rendered")),
        "real_sample_layer_count": sum(1 for r in source_results if r.get("render_kind") == "real_categorical_samples" and r.get("rendered")),
        "semantic_fallback_count": sum(1 for r in source_results if r.get("render_kind") == "semantic_fallback" or not r.get("rendered")),
        "skipped_count": sum(1 for r in source_results if not r.get("attempted")),
    }


def diagnostic_summary(source_results: list[dict[str, Any]]) -> list[str]:
    notes: list[str] = []
    for result in source_results:
        for note in result.get("diagnostic_notes") or []:
            notes.append(f"{result['source_id']}: {note}")
    return notes


def recommended_next_action(source_results: list[dict[str, Any]]) -> str:
    if any(r.get("render_kind") == "real_categorical_samples" and r.get("rendered") for r in source_results):
        return "inspect_cache_image"
    if not any(r.get("render_kind") == "real_raster" and r.get("rendered") for r in source_results):
        return "no_real_raster_rendered"
    if any(r.get("is_mostly_single_class") for r in source_results):
        return "expand_aoi_for_more_cdl_class_diversity"
    if any(r.get("cache_path") for r in source_results):
        return "inspect_cache_image"
    return "real_preview_ok"


def stack_compositor_fields(source_results: list[dict[str, Any]]) -> dict[str, Any]:
    return preview_compositor.compute_stack_opacity_plan(source_results)


def build_real_preview_plan(
    task: dict[str, Any],
    *,
    max_bytes_per_source: int = DEFAULT_MAX_BYTES_PER_SOURCE,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    include_archives: bool = False,
    preview_size: int = DEFAULT_PREVIEW_SIZE,
    cdl_verify_samples: bool = True,
    sample_grid_size: int = DEFAULT_SAMPLE_GRID_SIZE,
    grid_size: int | None = None,
    preview_expand_factor: float = DEFAULT_PREVIEW_EXPAND_FACTOR,
    cdl_render_mode: str = "auto",
) -> dict[str, Any]:
    sample_grid_size = normalize_sample_grid_size(grid_size if grid_size is not None else sample_grid_size)
    preview_expand_factor = normalize_preview_expand_factor(preview_expand_factor)
    cdl_render_mode = normalize_cdl_render_mode(cdl_render_mode)
    preview_fetch_bbox = expanded_bbox(task["aoi"]["bbox"], preview_expand_factor)
    summary = stack_preview.build_preview_summary(task)
    source_results = [
        plan_source(task, source_id, max_pixels=max_pixels, include_archives=include_archives, preview_size=preview_size, preview_fetch_bbox=preview_fetch_bbox, sample_grid_size=sample_grid_size, cdl_render_mode=cdl_render_mode)
        for source_id in task.get("sources", [])
    ]
    warnings = list(summary["warnings"])
    warnings.extend(result["warning"] for result in source_results if result.get("warning"))
    counts = _counts(source_results)
    stack_fields = stack_compositor_fields(source_results)
    return {
        "task_id": task["task_id"],
        "generated_at_utc": _utc_now(),
        "network_run": False,
        "real_fetch_attempted": False,
        "semantic_preview": True,
        "real_data_preview": True,
        "real_raster_data_rendered": False,
        "max_bytes_per_source": max_bytes_per_source,
        "max_pixels": max_pixels,
        "preview_size": effective_preview_size(preview_size, max_pixels),
        "cdl_verification_run": False,
        "cdl_export_cascade_run": any(result.get("export_cascade_run") for result in source_results),
        "cdl_export_cascade_success": any(result.get("export_cascade_success") for result in source_results),
        "cdl_selected_time_strategy": next((result.get("selected_export_time_strategy") for result in source_results if result.get("selected_export_time_strategy")), None),
        "cdl_selected_candidate": next((result.get("selected_export_candidate") for result in source_results if result.get("selected_export_candidate")), None),
        "cdl_verify_samples_planned": bool(cdl_verify_samples),
        "sample_grid_size": sample_grid_size,
        "preview_expand_factor": preview_expand_factor,
        "cdl_render_mode": cdl_render_mode,
        "preview_fetch_bbox": preview_fetch_bbox,
        "bbox": task["aoi"]["bbox"],
        "bbox_crs": task["aoi"]["bbox_crs"],
        "target_crs": task["target_grid"]["crs"],
        "source_count": len(task.get("sources", [])),
        "theme_count": len(task.get("themes", [])),
        "layers": summary["layers"],
        "source_results": source_results,
        "warnings": [w for w in warnings if w],
        "diagnostic_summary": [],
        "cdl_meaningful_preview": False,
        "recommended_next_action": "no_real_raster_rendered",
        **counts,
        **stack_fields,
        "png_path": None,
        "md_path": str(TASK_PREVIEWS_DIR / f"{task['task_id']}_real_preview_plan.md"),
    }


def _write_plan_reports(plan: dict[str, Any]) -> dict[str, Any]:
    TASK_PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = TASK_PREVIEWS_DIR / f"{plan['task_id']}_real_preview_plan.json"
    md_path = TASK_PREVIEWS_DIR / f"{plan['task_id']}_real_preview_plan.md"
    plan = {**plan, "json_path": str(json_path), "md_path": str(md_path)}
    json_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        f"# Real Preview Plan {plan['task_id']}",
        "",
        "- Network run: `False`",
        "- Real fetch attempted: `False`",
        f"- Max bytes/source: `{plan['max_bytes_per_source']}`",
        "",
        "## Source plan",
        "| Source | Status | Render kind | Warning |",
        "| --- | --- | --- | --- |",
    ]
    for result in plan["source_results"]:
        lines.append(f"| `{result['source_id']}` | `{result['status']}` | `{result['render_kind']}` | `{result.get('warning') or ''}` |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return plan


def _blank(width: int, height: int, color: list[int]) -> bytearray:
    return bytearray(color * width * height)


def _set_px(img: bytearray, width: int, height: int, x: int, y: int, color: list[int]) -> None:
    if 0 <= x < width and 0 <= y < height:
        i = (y * width + x) * 3
        img[i:i + 3] = bytes(color)


def _rect(img: bytearray, width: int, height: int, x: int, y: int, w: int, h: int, color: list[int]) -> None:
    for yy in range(y, y + h):
        for xx in range(x, x + w):
            _set_px(img, width, height, xx, yy, color)


def _text(img: bytearray, width: int, height: int, x: int, y: int, text: str, color: list[int], scale: int = 2) -> None:
    stack_preview._text(img, width, height, x, y, text[:95], color, scale)


def _write_png(path: Path, width: int, height: int, img: bytearray) -> None:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    raw = b"".join(b"\x00" + bytes(img[y * width * 3:(y + 1) * width * 3]) for y in range(height))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    path.write_bytes(png)


def _blend_px(img: bytearray, width: int, height: int, x: int, y: int, color: list[int], opacity: float) -> None:
    if 0 <= x < width and 0 <= y < height:
        i = (y * width + x) * 3
        current = img[i:i + 3]
        blended = [int(current[j] * (1 - opacity) + color[j] * opacity) for j in range(3)]
        img[i:i + 3] = bytes(blended)


def overlay_semantic_pattern(img: bytearray, width: int, height: int, x: int, y: int, w: int, h: int, result: dict[str, Any], opacity: float) -> None:
    source_id = result.get("source_id", "")
    if source_id == "prism_daily_ppt_static_zip":
        color = [40, 170, 210]
        for yy in range(h):
            for xx in range(w):
                if (xx + yy) % 18 < 4:
                    _blend_px(img, width, height, x + xx, y + yy, color, opacity)
    elif source_id == "usgs_3dep_dem":
        color = [100, 115, 130]
        for yy in range(0, h, 28):
            for xx in range(w):
                wave = int(8 * math.sin(xx / 28.0))
                for t in range(2):
                    _blend_px(img, width, height, x + xx, y + min(h - 1, yy + wave + t), color, opacity)
    else:
        color = [185, 145, 75]
        for yy in range(0, h, 16):
            for xx in range(0, w, 16):
                _blend_px(img, width, height, x + xx, y + yy, color, opacity)


def draw_cached_raster(img: bytearray, width: int, height: int, result: dict[str, Any], x: int, y: int, w: int, h: int) -> None:
    pixels = []
    if result.get("cache_path"):
        try:
            pixels = decode_png_pixels(Path(result["cache_path"]).read_bytes()).get("pixels") or []
        except Exception:
            pixels = []
    if not pixels:
        _rect(img, width, height, x, y, w, h, [180, 205, 175])
        return
    side = max(1, int(math.sqrt(len(pixels))))
    for yy in range(h):
        src_y = min(side - 1, int(yy / h * side))
        for xx in range(w):
            src_x = min(side - 1, int(xx / w * side))
            r, g, b, _a = pixels[min(len(pixels) - 1, src_y * side + src_x)]
            _set_px(img, width, height, x + xx, y + yy, [r, g, b])


def render_real_preview_png(task: dict[str, Any], report: dict[str, Any], png_path: Path) -> None:
    width, height = 1240, 820
    img = _blank(width, height, [245, 247, 250])
    _rect(img, width, height, 0, 0, width, 84, [74, 36, 86])
    _text(img, width, height, 28, 18, "FASTER RASTER REAL DATA PREVIEW", [255, 255, 255], 2)
    _text(img, width, height, 28, 52, "TINY BOUNDED LIVE PREVIEW", [225, 210, 235], 1)
    _rect(img, width, height, 30, 112, 600, 450, [226, 232, 239])
    raster_result = next((r for r in report["source_results"] if r.get("real_raster_rendered")), None)
    if raster_result:
        draw_cached_raster(img, width, height, raster_result, 58, 142, 540, 340)
        _text(img, width, height, 76, 150, "REAL CDL PIXELS", [255, 255, 255], 1)
        if raster_result.get("is_mostly_single_class"):
            _text(img, width, height, 88, 455, "MOSTLY SINGLE CDL CLASS IN TINY AOI", [90, 55, 40], 1)
    else:
        _rect(img, width, height, 58, 142, 540, 340, [210, 215, 222])
        _text(img, width, height, 150, 298, "NO REAL RASTER LAYER RENDERED", [92, 70, 70], 2)
    opacity_by_source = {item["source_id"]: item["opacity"] for item in report.get("layer_opacity_plan", [])}
    for result in report["source_results"]:
        if result is raster_result:
            continue
        if result.get("render_kind") == "semantic_fallback" or result.get("status") == "adapter_needed":
            overlay_semantic_pattern(img, width, height, 58, 142, 540, 340, result, opacity_by_source.get(result.get("source_id"), 0.3))
    for gx in range(58, 599, 90):
        _rect(img, width, height, gx, 142, 1, 340, [70, 92, 112])
    for gy in range(142, 483, 68):
        _rect(img, width, height, 58, gy, 540, 1, [70, 92, 112])
    stack_preview._border(img, width, height, 58, 142, 540, 340, [30, 45, 65], 3)
    _text(img, width, height, 58, 500, "PIXEL ZOOM", [35, 52, 72], 1)
    if raster_result:
        draw_cached_raster(img, width, height, raster_result, 58, 522, 130, 130)
        stack_preview._border(img, width, height, 58, 522, 130, 130, [30, 45, 65], 2)
    side_x, y = 662, 112
    badges = [f"NETWORK: {str(report['network_run']).upper()}", f"MAX BYTES/SOURCE: {report['max_bytes_per_source']}", "REAL DATA WHERE SUPPORTED"]
    for badge in badges:
        _rect(img, width, height, side_x, y, 500, 28, [235, 226, 242])
        _text(img, width, height, side_x + 10, y + 7, badge, [74, 36, 86], 1)
        y += 38
    _text(img, width, height, side_x, y + 8, "DIAGNOSTICS", [35, 52, 72], 2)
    y += 46
    diag_lines = ["NO REAL RASTER DIAGNOSTICS"]
    if raster_result:
        diag_lines = [
            f"DIM: {raster_result.get('image_width')}x{raster_result.get('image_height')} MODE {raster_result.get('image_mode')}",
            f"BYTES: {raster_result.get('bytes_read')} TYPE {raster_result.get('content_type')}",
            f"UNIQUE: {raster_result.get('unique_color_count')} DOM {raster_result.get('dominant_color_fraction')}",
            f"SHA: {(raster_result.get('sha256') or '')[:12]}",
            f"CACHE: {Path(raster_result.get('cache_path') or '').name}",
        ]
    for line in diag_lines:
        _text(img, width, height, side_x, y, line, [35, 52, 72], 1)
        y += 24
    y += 12
    _text(img, width, height, side_x, y, "SOURCE STATUS", [35, 52, 72], 2)
    y += 32
    for result in report["source_results"][:8]:
        color = [67, 150, 91] if result["rendered"] else [175, 135, 69] if result["attempted"] else [128, 135, 145]
        _rect(img, width, height, side_x, y, 18, 18, color)
        opacity = opacity_by_source.get(result.get("source_id"))
        opacity_text = "" if opacity is None else f" OP {opacity}"
        label = f"{result['source_id']} {result['status']} {result['bytes_read']}B{opacity_text}"
        _text(img, width, height, side_x + 28, y + 2, label, [35, 52, 72], 1)
        y += 26
    y += 8
    _text(img, width, height, side_x, y, "WARNINGS", [150, 70, 54], 2)
    y += 30
    for warning in report["warnings"][:8]:
        _text(img, width, height, side_x, y, warning, [150, 70, 54], 1)
        y += 22
    generated = report["generated_at_utc"]
    _text(img, width, height, 34, 746, f"BBOX {task['aoi']['bbox']} CRS {task['aoi']['bbox_crs']} TARGET {task['target_grid']['crs']}", [35, 52, 72], 1)
    _text(img, width, height, 34, 774, f"GENERATED {generated} BOUNDED PREVIEW NOT FULL PRODUCTION ACQUISITION NO SOURCE REGISTRY MUTATION", [35, 52, 72], 1)
    _write_png(png_path, width, height, img)


def write_debug_artifacts(report: dict[str, Any]) -> None:
    json_path = TASK_PREVIEWS_DIR / f"{report['task_id']}_real_stack_preview_diagnostics.json"
    md_path = TASK_PREVIEWS_DIR / f"{report['task_id']}_real_stack_preview_diagnostics.md"
    payload = {"task_id": report["task_id"], "diagnostic_summary": report["diagnostic_summary"], "source_results": report["source_results"]}
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [f"# Real Preview Diagnostics {report['task_id']}", ""]
    for result in report["source_results"]:
        lines.append(f"- `{result['source_id']}`: `{result['status']}` notes `{result.get('diagnostic_notes')}`")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def create_real_preview(
    task: dict[str, Any],
    *,
    allow_network: bool = False,
    max_bytes_per_source: int = DEFAULT_MAX_BYTES_PER_SOURCE,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    include_archives: bool = False,
    open_after_create: bool = False,
    preview_size: int = DEFAULT_PREVIEW_SIZE,
    debug_artifacts: bool = False,
    cache_raw: bool = True,
    cdl_verify_samples: bool = True,
    sample_grid_size: int = DEFAULT_SAMPLE_GRID_SIZE,
    grid_size: int | None = None,
    preview_expand_factor: float = DEFAULT_PREVIEW_EXPAND_FACTOR,
    cdl_render_mode: str = "auto",
) -> dict[str, Any]:
    errors = validate_task(task)
    if errors:
        raise ValueError("invalid task: " + "; ".join(errors))
    sample_grid_size = normalize_sample_grid_size(grid_size if grid_size is not None else sample_grid_size)
    preview_expand_factor = normalize_preview_expand_factor(preview_expand_factor)
    cdl_render_mode = normalize_cdl_render_mode(cdl_render_mode)
    preview_fetch_bbox = expanded_bbox(task["aoi"]["bbox"], preview_expand_factor)
    if not allow_network:
        return _write_plan_reports(build_real_preview_plan(task, max_bytes_per_source=max_bytes_per_source, max_pixels=max_pixels, include_archives=include_archives, preview_size=preview_size, cdl_verify_samples=cdl_verify_samples, sample_grid_size=sample_grid_size, preview_expand_factor=preview_expand_factor, cdl_render_mode=cdl_render_mode))

    summary = stack_preview.build_preview_summary(task)
    source_results = [
        fetch_source(
            task,
            source_id,
            max_bytes_per_source=max_bytes_per_source,
            max_pixels=max_pixels,
            timeout_seconds=timeout_seconds,
            include_archives=include_archives,
            cache_raw=cache_raw,
            preview_size=preview_size,
            preview_fetch_bbox=preview_fetch_bbox,
            cdl_verify_samples=cdl_verify_samples,
            sample_grid_size=sample_grid_size,
            cdl_render_mode=cdl_render_mode,
        )
        for source_id in task.get("sources", [])
    ]
    warnings = list(summary["warnings"])
    warnings.extend(result["warning"] for result in source_results if result.get("warning"))
    counts = _counts(source_results)
    stack_fields = stack_compositor_fields(source_results)
    generated_at_utc = _utc_now()
    TASK_PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    png_path = TASK_PREVIEWS_DIR / f"{task['task_id']}_real_stack_preview.png"
    json_path = TASK_PREVIEWS_DIR / f"{task['task_id']}_real_stack_preview.json"
    md_path = TASK_PREVIEWS_DIR / f"{task['task_id']}_real_stack_preview.md"
    report = {
        "task_id": task["task_id"],
        "generated_at_utc": generated_at_utc,
        "network_run": True,
        "real_fetch_attempted": any(result["attempted"] for result in source_results),
        "semantic_preview": True,
        "real_data_preview": True,
        "real_raster_data_rendered": counts["real_raster_layer_count"] > 0,
        "max_bytes_per_source": max_bytes_per_source,
        "max_pixels": max_pixels,
        "preview_size": effective_preview_size(preview_size, max_pixels),
        "cdl_verification_run": any(result.get("sample_verification_attempted") for result in source_results),
        "cdl_export_cascade_run": any(result.get("export_cascade_run") for result in source_results),
        "cdl_export_cascade_success": any(result.get("export_cascade_success") for result in source_results),
        "cdl_selected_time_strategy": next((result.get("selected_export_time_strategy") for result in source_results if result.get("selected_export_time_strategy")), None),
        "cdl_selected_candidate": next((result.get("selected_export_candidate") for result in source_results if result.get("selected_export_candidate")), None),
        "cdl_verify_samples_planned": bool(cdl_verify_samples),
        "sample_grid_size": sample_grid_size,
        "preview_expand_factor": preview_expand_factor,
        "cdl_render_mode": cdl_render_mode,
        "preview_fetch_bbox": preview_fetch_bbox,
        "bbox": task["aoi"]["bbox"],
        "bbox_crs": task["aoi"]["bbox_crs"],
        "target_crs": task["target_grid"]["crs"],
        "source_count": len(task.get("sources", [])),
        "theme_count": len(task.get("themes", [])),
        "layers": summary["layers"],
        "source_results": source_results,
        "warnings": [w for w in warnings if w],
        "diagnostic_summary": diagnostic_summary(source_results),
        "cdl_meaningful_preview": any(result.get("cdl_meaningful") for result in source_results),
        "recommended_next_action": recommended_next_action(source_results),
        "png_path": str(png_path),
        "md_path": str(md_path),
        "preview_json": str(json_path),
        **counts,
        **stack_fields,
    }
    render_real_preview_png(task, report, png_path)
    preview_compositor.write_stack_transparency_ledger(report, TASK_PREVIEWS_DIR / f"{task['task_id']}_stack_transparency_ledger.json")
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        f"# Real Data Stack Preview {task['task_id']}",
        "",
        f"- PNG: `{png_path}`",
        f"- Network run: `{report['network_run']}`",
        f"- Real raster data rendered: `{report['real_raster_data_rendered']}`",
        f"- Recommended next action: `{report['recommended_next_action']}`",
        "",
        "## Source results",
        "| Source | Attempted | Rendered | Kind | Bytes | Unique | Dominant | Status | Warning |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for result in source_results:
        lines.append(f"| `{result['source_id']}` | `{result['attempted']}` | `{result['rendered']}` | `{result['render_kind']}` | {result['bytes_read']} | {result.get('unique_color_count')} | {result.get('dominant_color_fraction')} | `{result['status']}` | `{result.get('warning') or ''}` |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if debug_artifacts:
        write_debug_artifacts(report)
    if open_after_create:
        stack_preview.open_preview(png_path)
    return report
