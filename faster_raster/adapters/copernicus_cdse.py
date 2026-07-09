from __future__ import annotations

import json
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


def build_cdse_stac_search_payload(task: dict[str, Any], *, collection: str = SENTINEL2_L2A_COLLECTION, cloud_cover_max: int = 30) -> dict[str, Any]:
    years = (task.get("time") or {}).get("years") or []
    year = int(years[0]) if years else 2023
    datetime_range = f"{year}-01-01T00:00:00Z/{year}-12-31T23:59:59Z"
    return {
        "collections": [collection],
        "bbox": (task.get("aoi") or {}).get("bbox"),
        "datetime": datetime_range,
        "limit": 10,
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
    return {
        "id": item.get("id"),
        "datetime": props.get("datetime"),
        "cloud_cover": props.get("eo:cloud_cover"),
        "asset_keys": sorted(assets.keys()),
        "collection": item.get("collection") or props.get("collection"),
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
