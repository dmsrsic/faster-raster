from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_MAX_BYTES = 65_536
DEFAULT_OUT_JSON = "reports/daymet_access_surface_probe.json"
DEFAULT_OUT_MD = "reports/daymet_access_surface_probe.md"

ENDPOINTS = [
    {
        "name": "thredds_catalog_xml",
        "endpoint": "https://thredds.daac.ornl.gov/thredds/catalog/ornldaac/1840/catalog.xml",
    },
    {
        "name": "thredds_catalog_html",
        "endpoint": "https://thredds.daac.ornl.gov/thredds/catalog/ornldaac/1840/catalog.html",
    },
    {
        "name": "thredds_dataset_catalog_page",
        "endpoint": "https://thredds.daac.ornl.gov/thredds/catalog/ornldaac/1840/daymet_v4_daily_na_prcp_2023.nc.html",
    },
    {
        "name": "ncss_dataset_form",
        "endpoint": "https://thredds.daac.ornl.gov/thredds/ncss/ornldaac/1840/daymet_v4_daily_na_prcp_2023.nc/dataset.html",
    },
    {
        "name": "raw_ncss_no_query",
        "endpoint": "https://thredds.daac.ornl.gov/thredds/ncss/ornldaac/1840/daymet_v4_daily_na_prcp_2023.nc",
    },
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Classify Daymet THREDDS/NCSS access surfaces.")
    parser.add_argument("--allow-network", action="store_true", help="Required opt-in for live network access.")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument("--out-json", default=DEFAULT_OUT_JSON)
    parser.add_argument("--out-md", default=DEFAULT_OUT_MD)
    return parser.parse_args()


def safe_headers(headers: Any) -> dict[str, str | None]:
    lowered = {str(key).lower(): value for key, value in headers.items()}
    return {
        "content_type": lowered.get("content-type"),
        "content_length": lowered.get("content-length"),
        "accept_ranges": lowered.get("accept-ranges"),
    }


def read_bounded(stream: Any, *, max_bytes: int, start: float) -> dict[str, Any]:
    body = bytearray()
    first_byte_seconds: float | None = None
    while len(body) < max_bytes:
        chunk = stream.read(min(16_384, max_bytes - len(body)))
        if not chunk:
            break
        if first_byte_seconds is None:
            first_byte_seconds = time.perf_counter() - start
        body.extend(chunk)
    return {
        "bytes_read": len(body),
        "truncated": len(body) >= max_bytes,
        "sha256": hashlib.sha256(bytes(body)).hexdigest() if body else None,
        "first_byte_seconds": round(first_byte_seconds, 6) if first_byte_seconds is not None else None,
    }


def classify(status: int | None, endpoint_name: str, error: str | None) -> str:
    if status == 200 or status == 206:
        return "public_metadata_access"
    if status == 401:
        return "unauthorized"
    if status == 404:
        return "not_found"
    if status == 400:
        return "malformed_request_expected"
    if status is not None and 500 <= status <= 599:
        return "service_error"
    if endpoint_name == "raw_ncss_no_query" and status in {400, 404, 405}:
        return "malformed_request_expected"
    if error and "Bad Request" in error:
        return "malformed_request_expected"
    return "unknown"


def probe_endpoint(endpoint: dict[str, str], *, max_bytes: int, timeout_seconds: int) -> dict[str, Any]:
    start = time.perf_counter()
    status: int | None = None
    headers: dict[str, str | None] = {}
    error: str | None = None
    read_result = {"bytes_read": 0, "truncated": False, "sha256": None, "first_byte_seconds": None}
    request = Request(
        endpoint["endpoint"],
        headers={
            "User-Agent": "FasterRaster-daymet-access-surface-probe/0.1",
            "Range": f"bytes=0-{max_bytes - 1}",
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status = response.status
            headers = safe_headers(response.headers)
            read_result = read_bounded(response, max_bytes=max_bytes, start=start)
    except HTTPError as exc:
        status = exc.code
        headers = safe_headers(exc.headers)
        error = f"HTTPError: {exc.code} {exc.reason}"
        read_result = read_bounded(exc, max_bytes=max_bytes, start=start)
    except URLError as exc:
        error = f"URLError: {exc.reason}"
    except TimeoutError as exc:
        error = f"TimeoutError: {exc}"

    elapsed = time.perf_counter() - start
    return {
        "name": endpoint["name"],
        "endpoint": endpoint["endpoint"],
        "http_status": status,
        "content_type": headers.get("content_type"),
        "content_length": headers.get("content_length"),
        "accept_ranges": headers.get("accept_ranges"),
        "bytes_read": read_result["bytes_read"],
        "truncated": read_result["truncated"],
        "sha256": read_result["sha256"],
        "elapsed_seconds": round(elapsed, 6),
        "first_byte_seconds": read_result["first_byte_seconds"],
        "error": error,
        "classification": classify(status, endpoint["name"], error),
    }


def write_reports(report: dict[str, Any], json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# Daymet Access Surface Probe",
        "",
        f"- Timestamp UTC: `{report['timestamp_utc']}`",
        f"- Max bytes per endpoint: `{report['max_bytes']}`",
        "",
        "| Name | Classification | HTTP | Bytes | Content-Type | Error | Endpoint |",
        "| --- | --- | ---: | ---: | --- | --- | --- |",
    ]
    for result in report["results"]:
        lines.append(
            f"| `{result['name']}` | `{result['classification']}` | {result['http_status']} | "
            f"{result['bytes_read']} | {result['content_type'] or ''} | {result['error'] or ''} | "
            f"`{result['endpoint']}` |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    if not args.allow_network:
        raise SystemExit("Refusing to run access-surface probe without --allow-network.")
    if args.max_bytes <= 0:
        raise SystemExit("--max-bytes must be positive.")

    results = [
        probe_endpoint(endpoint, max_bytes=args.max_bytes, timeout_seconds=args.timeout_seconds)
        for endpoint in ENDPOINTS
    ]
    report = {
        "timestamp_utc": utc_now_iso(),
        "network_opt_in": True,
        "max_bytes": args.max_bytes,
        "results": results,
        "summary": {
            "public_metadata_access": [item["name"] for item in results if item["classification"] == "public_metadata_access"],
            "unauthorized": [item["name"] for item in results if item["classification"] == "unauthorized"],
            "not_found": [item["name"] for item in results if item["classification"] == "not_found"],
            "malformed_request_expected": [item["name"] for item in results if item["classification"] == "malformed_request_expected"],
            "service_error": [item["name"] for item in results if item["classification"] == "service_error"],
            "unknown": [item["name"] for item in results if item["classification"] == "unknown"],
        },
    }
    write_reports(report, Path(args.out_json), Path(args.out_md))
    print(json.dumps(report["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

