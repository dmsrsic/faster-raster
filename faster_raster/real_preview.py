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
DEFAULT_PREVIEW_LAYOUT = "clean"
ALLOWED_PREVIEW_LAYOUTS = {"clean", "cockpit", "report"}
PREVIEW_UX_VERSION = "0.5.9"
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


def normalize_preview_layout(value: str | None) -> str:
    value = (value or DEFAULT_PREVIEW_LAYOUT).strip().lower()
    if value not in ALLOWED_PREVIEW_LAYOUTS:
        raise ValueError(f"invalid preview layout: {value}")
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
    preview_layout: str = DEFAULT_PREVIEW_LAYOUT,
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


def visual_source_labels(source_results: list[dict[str, Any]]) -> dict[str, str]:
    labels = {
        "prism_daily_ppt_static_zip": "PRISM",
        "cdl_arcgis_tiny_export": "CDL",
        "usgs_3dep_dem": "3DEP",
        "copernicus_sentinel2_l2a_cdse_stac": "Sentinel-2",
    }
    return {result["source_id"]: labels.get(result["source_id"], result["source_id"]) for result in source_results if result.get("source_id")}


def selected_cdl_candidate_summary(source_results: list[dict[str, Any]]) -> dict[str, Any] | None:
    result = next((item for item in source_results if item.get("source_id") == "cdl_arcgis_tiny_export"), None)
    if not result or not result.get("selected_export_candidate"):
        return None
    return {
        "selected_candidate": result.get("selected_export_candidate"),
        "selected_time_strategy": result.get("selected_export_time_strategy"),
        "selected_format": result.get("selected_export_format"),
        "bytes": result.get("bytes_read"),
        "unique_colors": result.get("unique_color_count"),
        "dominant_fraction": result.get("dominant_color_fraction"),
        "sha_short": (result.get("sha256") or "")[:12],
    }


def sentinel_live_report_path(task_id: str) -> Path:
    return Path("reports/copernicus") / f"{task_id}_sentinel2_l2a_search_live.json"


def sentinel_live_summary(task_id: str) -> dict[str, Any]:
    path = sentinel_live_report_path(task_id)
    if not path.exists():
        return {
            "sentinel_stac_live_result_present": False,
            "sentinel_stac_item_count": 0,
            "sentinel_best_cloud_cover": None,
            "sentinel_auth_present": None,
            "sentinel_pixels_rendered": False,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "sentinel_stac_live_result_present": True,
            "sentinel_stac_item_count": 0,
            "sentinel_best_cloud_cover": None,
            "sentinel_auth_present": None,
            "sentinel_pixels_rendered": False,
            "sentinel_live_result_error": "could not parse local Sentinel search-live JSON",
        }
    items = payload.get("items") or []
    cloud_values = [item.get("eo_cloud_cover") for item in items if item.get("eo_cloud_cover") is not None]
    return {
        "sentinel_stac_live_result_present": True,
        "sentinel_stac_item_count": int(payload.get("item_count", len(items)) or 0),
        "sentinel_best_cloud_cover": min(cloud_values) if cloud_values else None,
        "sentinel_auth_present": payload.get("auth_present"),
        "sentinel_pixels_rendered": False,
    }


def preview_ux_fields(task_id: str, source_results: list[dict[str, Any]], layout: str) -> dict[str, Any]:
    labels = visual_source_labels(source_results)
    return {
        "preview_layout": layout,
        "visual_source_labels": labels,
        "map_panel_render_mode": "selected_base_raster_with_translucent_semantic_overlays",
        "base_raster_fit_mode": "nearest_neighbor_contain",
        "base_raster_was_tiled": False,
        "preview_ux_version": PREVIEW_UX_VERSION,
        "selected_cdl_candidate_summary": selected_cdl_candidate_summary(source_results),
        **sentinel_live_summary(task_id),
    }


def stack_compositor_fields(source_results: list[dict[str, Any]]) -> dict[str, Any]:
    return preview_compositor.compute_stack_opacity_plan(source_results, visual_source_labels(source_results))


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
    preview_layout: str = DEFAULT_PREVIEW_LAYOUT,
) -> dict[str, Any]:
    sample_grid_size = normalize_sample_grid_size(grid_size if grid_size is not None else sample_grid_size)
    preview_expand_factor = normalize_preview_expand_factor(preview_expand_factor)
    cdl_render_mode = normalize_cdl_render_mode(cdl_render_mode)
    preview_layout = normalize_preview_layout(preview_layout)
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
    ux_fields = preview_ux_fields(task["task_id"], source_results, preview_layout)
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
        **ux_fields,
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
        "Canonical source ids are preserved in JSON; visual labels are display-only.",
        "",
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
    decoded: dict[str, Any] = {}
    if result.get("cache_path"):
        try:
            decoded = decode_png_pixels(Path(result["cache_path"]).read_bytes(), max_sample_pixels=1048576)
        except Exception:
            decoded = {}
    pixels = decoded.get("pixels") or []
    src_w = int(decoded.get("width") or 0)
    src_h = int(decoded.get("height") or 0)
    if not pixels or src_w <= 0 or src_h <= 0 or len(pixels) < src_w * src_h:
        _rect(img, width, height, x, y, w, h, [180, 205, 175])
        return
    scale = max(1, min(w // src_w if src_w else 1, h // src_h if src_h else 1))
    fit_w = min(w, src_w * scale)
    fit_h = min(h, src_h * scale)
    if fit_w < w and fit_h < h:
        # Very small rasters get a larger pixel-art fit while preserving aspect ratio.
        scale = max(1, min(w // src_w, h // src_h))
        fit_w = min(w, src_w * scale)
        fit_h = min(h, src_h * scale)
    if fit_w <= 0 or fit_h <= 0:
        fit_w, fit_h = w, h
    x0 = x + max(0, (w - fit_w) // 2)
    y0 = y + max(0, (h - fit_h) // 2)
    _rect(img, width, height, x, y, w, h, [226, 232, 239])
    for yy in range(fit_h):
        src_y = min(src_h - 1, int(yy / fit_h * src_h))
        for xx in range(fit_w):
            src_x = min(src_w - 1, int(xx / fit_w * src_w))
            r, g, b, _a = pixels[src_y * src_w + src_x]
            _set_px(img, width, height, x0 + xx, y0 + yy, [r, g, b])


def _wrap_lines(text: str, limit: int) -> list[str]:
    words = str(text).split()
    lines: list[str] = []
    current = ""
    for word in words:
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= limit:
            current += " " + word
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def _section_title(img: bytearray, width: int, height: int, x: int, y: int, title: str, color: list[int]) -> int:
    _text(img, width, height, x, y, title.upper(), color, 1)
    return y + 22


def render_real_preview_png(task: dict[str, Any], report: dict[str, Any], png_path: Path) -> None:
    layout = normalize_preview_layout(report.get("preview_layout"))
    width, height = 1320, 860
    if layout == "cockpit":
        bg, header, header_text, panel, ink, muted, accent = [245, 247, 250], [74, 36, 86], [255, 255, 255], [226, 232, 239], [35, 52, 72], [75, 88, 105], [74, 36, 86]
        title = "FASTER RASTER REAL DATA PREVIEW"
        subtitle = "TINY BOUNDED LIVE PREVIEW"
    elif layout == "report":
        bg, header, header_text, panel, ink, muted, accent = [250, 250, 248], [255, 255, 255], [28, 37, 48], [238, 241, 244], [28, 37, 48], [84, 96, 110], [36, 105, 138]
        title = "FasterRaster Real Preview Report"
        subtitle = "Bounded evidence preview, not production acquisition"
    else:
        bg, header, header_text, panel, ink, muted, accent = [247, 249, 251], [232, 238, 244], [24, 37, 54], [226, 232, 239], [30, 45, 65], [84, 96, 110], [41, 116, 145]
        title = "FasterRaster Real Preview"
        subtitle = "Clean bounded preview with CDL base and semantic overlays"

    img = _blank(width, height, bg)
    _rect(img, width, height, 0, 0, width, 78, header)
    _text(img, width, height, 32, 18, title, header_text, 2)
    _text(img, width, height, 34, 52, subtitle, muted if layout != "cockpit" else [225, 210, 235], 1)

    map_x, map_y, map_w, map_h = 34, 106, 790, 560
    _rect(img, width, height, map_x, map_y, map_w, map_h, panel)
    raster_result = next((r for r in report["source_results"] if r.get("real_raster_rendered")), None)
    if raster_result:
        draw_cached_raster(img, width, height, raster_result, map_x + 24, map_y + 28, map_w - 48, map_h - 72)
        _text(img, width, height, map_x + 34, map_y + 40, "CDL REAL BASE", [255, 255, 255] if layout == "cockpit" else ink, 1)
    else:
        _rect(img, width, height, map_x + 24, map_y + 28, map_w - 48, map_h - 72, [210, 215, 222])
        _text(img, width, height, map_x + 210, map_y + 260, "NO REAL RASTER LAYER RENDERED", [92, 70, 70], 2)

    opacity_by_source = {item["source_id"]: item["opacity"] for item in report.get("layer_opacity_plan", [])}
    for result in report["source_results"]:
        if result is raster_result:
            continue
        if result.get("render_kind") == "semantic_fallback" or result.get("status") in {"adapter_needed", "planned"}:
            overlay_semantic_pattern(img, width, height, map_x + 24, map_y + 28, map_w - 48, map_h - 72, result, opacity_by_source.get(result.get("source_id"), 0.25))
    for gx in range(map_x + 24, map_x + map_w - 20, 112):
        _rect(img, width, height, gx, map_y + 28, 1, map_h - 72, [190, 200, 210])
    for gy in range(map_y + 28, map_y + map_h - 40, 86):
        _rect(img, width, height, map_x + 24, gy, map_w - 48, 1, [190, 200, 210])
    stack_preview._border(img, width, height, map_x + 24, map_y + 28, map_w - 48, map_h - 72, ink, 2)

    _text(img, width, height, map_x + 24, map_y + map_h - 32, "bounded preview | no fake basemap | Sentinel pixels not downloaded", muted, 1)
    inset_x, inset_y = 58, 694
    _text(img, width, height, inset_x, inset_y - 22, "PIXEL ZOOM", ink, 1)
    if raster_result:
        draw_cached_raster(img, width, height, raster_result, inset_x, inset_y, 112, 112)
        stack_preview._border(img, width, height, inset_x, inset_y, 112, 112, ink, 2)

    side_x, y = 858, 106
    labels = report.get("visual_source_labels") or {}
    badge_color = [238, 244, 248] if layout != "cockpit" else [235, 226, 242]
    for badge in [f"NETWORK {str(report['network_run']).upper()}", f"MAX BYTES {report['max_bytes_per_source']}", "REAL DATA WHERE SUPPORTED", "NOT FULL ACQUISITION"]:
        _rect(img, width, height, side_x, y, 410, 28, badge_color)
        _text(img, width, height, side_x + 10, y + 7, badge, accent, 1)
        y += 34

    y = _section_title(img, width, height, side_x, y + 8, "Raster diagnostics", ink)
    if raster_result:
        summary = report.get("selected_cdl_candidate_summary") or {}
        diag_lines = [
            f"CDL candidate {summary.get('selected_time_strategy')} {summary.get('selected_format')}",
            f"bytes {summary.get('bytes')} unique {summary.get('unique_colors')} dom {summary.get('dominant_fraction')}",
            f"sha {summary.get('sha_short')}",
        ]
    else:
        diag_lines = ["No real raster diagnostics yet"]
    for line in diag_lines:
        _text(img, width, height, side_x, y, line, ink, 1)
        y += 21

    y = _section_title(img, width, height, side_x, y + 12, "Source stack", ink)
    for result in report["source_results"][:8]:
        label = labels.get(result["source_id"], result["source_id"])
        color = [67, 150, 91] if result["rendered"] else [175, 135, 69] if result["attempted"] else [128, 135, 145]
        _rect(img, width, height, side_x, y + 2, 14, 14, color)
        opacity = opacity_by_source.get(result.get("source_id"))
        op = "" if opacity is None else f" op {opacity}"
        _text(img, width, height, side_x + 22, y, f"{label} {result['status']}{op}", ink, 1)
        y += 22

    if report.get("sentinel_stac_live_result_present"):
        _text(img, width, height, side_x + 22, y, f"Sentinel-2 STAC items {report.get('sentinel_stac_item_count')} best cloud {report.get('sentinel_best_cloud_cover')}", ink, 1)
        y += 22
        _text(img, width, height, side_x + 22, y, f"auth {report.get('sentinel_auth_present')} no Sentinel pixels downloaded", muted, 1)
        y += 24

    y = _section_title(img, width, height, side_x, y + 8, "Opacity ledger", ink)
    for line in (report.get("opacity_ledger_text") or [])[:7]:
        _text(img, width, height, side_x, y, line, ink, 1)
        y += 20

    y = _section_title(img, width, height, side_x, y + 8, "Warnings", [150, 70, 54])
    for warning in report["warnings"][:7]:
        for line in _wrap_lines(warning, 58)[:2]:
            _text(img, width, height, side_x, y, line, [150, 70, 54], 1)
            y += 18

    footer = f"BBOX {task['aoi']['bbox']} | TARGET {task['target_grid']['crs']} | GENERATED {report['generated_at_utc']}"
    _text(img, width, height, 206, 738, footer, muted, 1)
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
    preview_layout: str = DEFAULT_PREVIEW_LAYOUT,
) -> dict[str, Any]:
    errors = validate_task(task)
    if errors:
        raise ValueError("invalid task: " + "; ".join(errors))
    sample_grid_size = normalize_sample_grid_size(grid_size if grid_size is not None else sample_grid_size)
    preview_expand_factor = normalize_preview_expand_factor(preview_expand_factor)
    cdl_render_mode = normalize_cdl_render_mode(cdl_render_mode)
    preview_layout = normalize_preview_layout(preview_layout)
    preview_fetch_bbox = expanded_bbox(task["aoi"]["bbox"], preview_expand_factor)
    if not allow_network:
        return _write_plan_reports(build_real_preview_plan(task, max_bytes_per_source=max_bytes_per_source, max_pixels=max_pixels, include_archives=include_archives, preview_size=preview_size, cdl_verify_samples=cdl_verify_samples, sample_grid_size=sample_grid_size, preview_expand_factor=preview_expand_factor, cdl_render_mode=cdl_render_mode, preview_layout=preview_layout))

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
    ux_fields = preview_ux_fields(task["task_id"], source_results, preview_layout)
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
        **ux_fields,
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
        f"- Preview layout: `{report['preview_layout']}`",
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
