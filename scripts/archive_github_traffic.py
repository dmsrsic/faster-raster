"""Sanitize GitHub traffic aggregates for a dedicated metrics archive.

The script never writes to the main branch and never stores raw API responses.
It accepts a fixture for offline tests; network use is opt-in with a token.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

API_ROOT = "https://api.github.com/repos/dmsrsic/faster-raster/traffic"
MAX_RESPONSE_BYTES = 512 * 1024
METRIC_KEYS = {
    "repository_clone_count",
    "github_unique_cloners",
    "repository_page_views",
    "github_unique_visitors",
}


def _validate_series(section: dict[str, Any], key: str) -> None:
    if key not in section:
        return
    series = section[key]
    if not isinstance(series, list):
        raise ValueError("traffic payload series must be an array")
    for entry in series:
        if not isinstance(entry, dict) or set(entry) != {"timestamp", "count", "uniques"}:
            raise ValueError("traffic payload series contains an invalid entry")
        timestamp = entry["timestamp"]
        if not isinstance(timestamp, str):
            raise ValueError("traffic payload series contains an invalid timestamp")
        try:
            dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("traffic payload series contains an invalid timestamp") from exc
        if any(
            isinstance(entry[field], bool)
            or not isinstance(entry[field], int)
            or entry[field] < 0
            for field in ("count", "uniques")
        ):
            raise ValueError("traffic payload series contains invalid aggregate metrics")


def _get(path: str) -> Any:
    token = os.environ.get("FASTER_RASTER_GITHUB_TRAFFIC_TOKEN")
    if not token:
        raise RuntimeError("FASTER_RASTER_GITHUB_TRAFFIC_TOKEN is not configured")
    request = urllib.request.Request(
        f"{API_ROOT}/{path}",
        headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}", "User-Agent": "fasterraster-metrics-archive/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError("GitHub traffic request failed") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise RuntimeError("GitHub traffic response exceeded the byte limit")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("GitHub traffic response was not valid JSON") from exc


def sanitize(payload: dict[str, Any], *, date: str | None = None, gap_note: str = "") -> dict[str, Any]:
    day = date or dt.date.today().isoformat()
    try:
        if dt.date.fromisoformat(day).isoformat() != day:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("traffic archive date must be canonical YYYY-MM-DD") from exc
    if not isinstance(payload, dict) or set(payload) != {"clones", "views"}:
        raise ValueError("traffic payload must contain only clones and views")
    clones = payload["clones"]
    views = payload["views"]
    if not isinstance(clones, dict) or not isinstance(views, dict):
        raise ValueError("traffic payload sections must be objects")
    if set(clones) - {"count", "uniques", "clones"} or set(views) - {"count", "uniques", "views"}:
        raise ValueError("traffic payload contains unknown fields")
    _validate_series(clones, "clones")
    _validate_series(views, "views")
    metrics = {
        "repository_clone_count": clones.get("count"),
        "github_unique_cloners": clones.get("uniques"),
        "repository_page_views": views.get("count"),
        "github_unique_visitors": views.get("uniques"),
    }
    if set(metrics) != METRIC_KEYS or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in metrics.values()
    ):
        raise ValueError("traffic payload contains invalid aggregate metrics")
    semantic_note = "GitHub reports a rolling window; these aggregates do not represent people, installations, or active users."
    combined_note = f"{gap_note.strip()}; {semantic_note}" if gap_note.strip() else semantic_note
    return {
        "schema_version": "fasterraster.adoption-metric/v1",
        "date": day,
        "source": "github-traffic-api",
        "metrics": {
            **metrics,
        },
        "gap_note": combined_note,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--date")
    args = parser.parse_args(argv)
    if not args.fixture and not os.environ.get("FASTER_RASTER_GITHUB_TRAFFIC_TOKEN"):
        return 0
    if args.fixture:
        try:
            payload = json.loads(args.fixture.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            print(f"invalid traffic fixture: {exc}")
            return 0
        gap = "offline fixture"
    else:
        try:
            payload = {"clones": _get("clones"), "views": _get("views")}
            gap = ""
        except RuntimeError as exc:
            print(str(exc))
            return 0
    try:
        record = sanitize(payload, date=args.date, gap_note=gap)
    except ValueError as exc:
        print(str(exc))
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
