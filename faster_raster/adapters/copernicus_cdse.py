from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from faster_raster import copernicus_auth
from faster_raster.task_builder import load_task

CDSE_STAC_ENDPOINT = "https://stac.dataspace.copernicus.eu/v1/"
SENTINEL2_L2A_SOURCE_ID = "copernicus_sentinel2_l2a_cdse_stac"
SENTINEL2_L2A_COLLECTION = "sentinel-2-l2a"
REPORTS_DIR = Path("reports/copernicus")


def build_cdse_stac_search_url(endpoint: str = CDSE_STAC_ENDPOINT) -> str:
    return endpoint.rstrip("/") + "/search"


def build_cdse_stac_search_payload(task: dict[str, Any], *, collection: str = SENTINEL2_L2A_COLLECTION, cloud_cover_max: int = 30, max_items: int = 10) -> dict[str, Any]:
    years = (task.get("time") or {}).get("years") or []
    year = int(years[0]) if years else 2023
    datetime_range = f"{year}-01-01T00:00:00Z/{year}-12-31T23:59:59Z"
    return {
        "collections": [collection],
        "bbox": (task.get("aoi") or {}).get("bbox"),
        "datetime": datetime_range,
        "limit": int(max_items),
        "query": {"eo:cloud_cover": {"lte": cloud_cover_max}},
        "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}],
    }


def parse_cdse_stac_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    features = payload.get("features") if isinstance(payload, dict) else []
    return [item for item in features or [] if isinstance(item, dict)]


def select_best_sentinel2_item(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not items:
        return None
    return sorted(items, key=lambda item: ((item.get("properties") or {}).get("eo:cloud_cover", 999), item.get("id", "")))[0]


def summarize_sentinel2_item(item: dict[str, Any]) -> dict[str, Any]:
    props = item.get("properties") or {}
    assets = item.get("assets") or {}
    asset_keys = sorted(assets.keys())
    lower_keys = {key.lower() for key in asset_keys}
    return {
        "id": item.get("id"),
        "collection": item.get("collection") or props.get("collection"),
        "datetime": props.get("datetime"),
        "eo_cloud_cover": props.get("eo:cloud_cover"),
        "cloud_cover": props.get("eo:cloud_cover"),
        "platform": props.get("platform"),
        "constellation": props.get("constellation"),
        "instruments": props.get("instruments"),
        "s2_mgrs_tile": props.get("s2:mgrs_tile") or props.get("mgrs:tile"),
        "bbox": item.get("bbox"),
        "asset_keys": asset_keys,
        "has_visual_asset": bool({"visual", "true_color", "overview"} & lower_keys),
        "has_red_band": bool({"red", "b04", "b4"} & lower_keys),
        "has_green_band": bool({"green", "b03", "b3"} & lower_keys),
        "has_blue_band": bool({"blue", "b02", "b2"} & lower_keys),
        "has_nir_band": bool({"nir", "b08", "b8"} & lower_keys),
        "hrefs_redacted": True,
    }


def build_sentinel2_search_plan(task: dict[str, Any], *, cloud_cover_max: int = 30) -> dict[str, Any]:
    auth = copernicus_auth.load_cdse_auth_from_env()
    payload = build_cdse_stac_search_payload(task, cloud_cover_max=cloud_cover_max)
    return {
        "task_id": task["task_id"],
        "source_id": SENTINEL2_L2A_SOURCE_ID,
        "collection": SENTINEL2_L2A_COLLECTION,
        "endpoint": CDSE_STAC_ENDPOINT,
        "search_url": build_cdse_stac_search_url(),
        "bbox": (task.get("aoi") or {}).get("bbox"),
        "datetime_range": payload["datetime"],
        "cloud_cover_max": cloud_cover_max,
        "credential_required": True,
        "auth_present": auth.auth_present,
        "auth_method": auth.auth_method,
        "network_run": False,
        "query_payload": payload,
        "headers_preview": copernicus_auth.redact_headers(copernicus_auth.build_cdse_headers(auth)),
        "warnings": ["Dry-run search plan only; no live CDSE STAC request was made."],
        "next_live_command": f"faster-raster copernicus sentinel search {task['task_id']} --allow-network --auth-profile cdse_local --plain",
    }


def write_search_plan_reports(plan: dict[str, Any]) -> dict[str, str]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    base = f"{plan['task_id']}_sentinel2_l2a_search_plan"
    json_path = REPORTS_DIR / f"{base}.json"
    md_path = REPORTS_DIR / f"{base}.md"
    json_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        f"# Copernicus Sentinel-2 L2A Search Plan {plan['task_id']}",
        "",
        f"- Source: `{plan['source_id']}`",
        f"- Collection: `{plan['collection']}`",
        f"- Endpoint: `{plan['endpoint']}`",
        f"- Network run: `{plan['network_run']}`",
        f"- Auth present: `{plan['auth_present']}`",
        f"- BBox: `{plan['bbox']}`",
        f"- Datetime: `{plan['datetime_range']}`",
        f"- Next live command: `{plan['next_live_command']}`",
        "",
        "No credentials are written to this report.",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json_path": str(json_path), "md_path": str(md_path)}


def create_search_plan(task_id: str, *, cloud_cover_max: int = 30) -> dict[str, Any]:
    plan = build_sentinel2_search_plan(load_task(task_id), cloud_cover_max=cloud_cover_max)
    return {**plan, **write_search_plan_reports(plan)}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_bounded_stac_search(payload: dict[str, Any], headers: dict[str, str], *, timeout_seconds: int, max_bytes: int, endpoint: str = CDSE_STAC_ENDPOINT) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        build_cdse_stac_search_url(endpoint),
        data=data,
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read(max_bytes + 1)
            status = getattr(response, "status", None) or getattr(response, "code", None)
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        body = exc.read(max_bytes + 1)
        status = exc.code
        content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
    if len(body) > max_bytes:
        raise ValueError(f"byte cap exceeded: read more than {max_bytes} bytes")
    return {"http_status": status, "content_type": content_type, "data": body, "bytes_read": len(body), "response_sha256": _sha256(body)}


def build_search_live_report(
    task: dict[str, Any],
    *,
    collection: str = SENTINEL2_L2A_COLLECTION,
    cloud_cover_max: int = 30,
    max_items: int = 5,
    timeout_seconds: int = 25,
    max_bytes: int = 1_000_000,
    fields_minimal: bool = False,
) -> dict[str, Any]:
    auth = copernicus_auth.load_cdse_auth_from_env()
    headers = copernicus_auth.build_cdse_headers(auth)
    payload = build_cdse_stac_search_payload(task, collection=collection, cloud_cover_max=cloud_cover_max, max_items=max_items)
    warnings: list[str] = []
    errors: list[str] = []
    items: list[dict[str, Any]] = []
    http_status = None
    bytes_read = 0
    response_sha256 = None
    try:
        fetched = read_bounded_stac_search(payload, headers, timeout_seconds=timeout_seconds, max_bytes=max_bytes)
        http_status = fetched["http_status"]
        bytes_read = fetched["bytes_read"]
        response_sha256 = fetched["response_sha256"]
        if http_status in {401, 403}:
            warnings.append("CDSE STAC search rejected the request; credentials may be required or insufficient.")
        parsed = json.loads(fetched["data"].decode("utf-8", errors="replace")) if fetched["data"] else {}
        items = [summarize_sentinel2_item(item) for item in parse_cdse_stac_items(parsed)[:max_items]]
    except Exception as exc:
        errors.append(str(exc))
    report = {
        "task_id": task["task_id"],
        "source_id": SENTINEL2_L2A_SOURCE_ID,
        "collection": collection,
        "endpoint": build_cdse_stac_search_url(),
        "network_run": True,
        "auth_present": auth.auth_present,
        "authorization_header_redacted": "Bearer <REDACTED>" if auth.access_token else None,
        "bbox": (task.get("aoi") or {}).get("bbox"),
        "datetime_range": payload["datetime"],
        "cloud_cover_max": cloud_cover_max,
        "max_items": max_items,
        "fields_minimal": fields_minimal,
        "http_status": http_status,
        "bytes_read": bytes_read,
        "response_sha256": response_sha256,
        "item_count": len(items),
        "items": items,
        "warnings": warnings,
        "errors": errors,
        "no_downloads": True,
        "query_payload": payload,
    }
    return report


def write_search_live_reports(report: dict[str, Any]) -> dict[str, str]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    base = f"{report['task_id']}_sentinel2_l2a_search_live"
    json_path = REPORTS_DIR / f"{base}.json"
    md_path = REPORTS_DIR / f"{base}.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        f"# Copernicus Sentinel-2 L2A Search Live {report['task_id']}",
        "",
        f"- Network run: `{report['network_run']}`",
        f"- HTTP status: `{report['http_status']}`",
        f"- Auth present: `{report['auth_present']}`",
        f"- Items: `{report['item_count']}`",
        f"- No downloads: `{report['no_downloads']}`",
        "",
        "Only STAC JSON metadata was requested. No product assets or Sentinel pixels were downloaded.",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json_path": str(json_path), "md_path": str(md_path)}


def create_search_live(
    task_id: str,
    *,
    collection: str = SENTINEL2_L2A_COLLECTION,
    cloud_cover_max: int = 30,
    max_items: int = 5,
    timeout_seconds: int = 25,
    max_bytes: int = 1_000_000,
    fields_minimal: bool = False,
) -> dict[str, Any]:
    report = build_search_live_report(load_task(task_id), collection=collection, cloud_cover_max=cloud_cover_max, max_items=max_items, timeout_seconds=timeout_seconds, max_bytes=max_bytes, fields_minimal=fields_minimal)
    return {**report, **write_search_live_reports(report)}
