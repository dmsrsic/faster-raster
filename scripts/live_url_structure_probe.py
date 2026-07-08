from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DEFAULT_MAX_BYTES = 65_536
DEFAULT_CHUNK_SIZE = 16_384
DEFAULT_TIMEOUT_SECONDS = 20


PROBES = [
    {
        "probe_id": "prism_daily_zip",
        "source_family": "PRISM",
        "kind": "static_https_zip",
        "url": "https://data.prism.oregonstate.edu/time_series/us/an/4km/ppt/daily/2026/prism_ppt_us_25m_20260101.zip",
        "notes": "Known PRISM daily zip URL; probe reads only a bounded prefix.",
    },
    {
        "probe_id": "nlcd_aws_tile",
        "source_family": "Annual NLCD",
        "kind": "static_https_tif_tile",
        "url": "https://usgs-landcover.s3.us-west-2.amazonaws.com/annual-nlcd/c1/v0/cu/tile/h14v15/Annual_NLCD_H14V15_FctImp_1985_CU_C1V0.tif",
        "notes": "Documented tile URL structure currently represented by generic_https_template.",
    },
    {
        "probe_id": "nlcd_aws_mosaic",
        "source_family": "Annual NLCD",
        "kind": "static_https_tif_mosaic",
        "url": "https://usgs-landcover.s3.us-west-2.amazonaws.com/annual-nlcd/c1/v0/cu/mosaic/Annual_NLCD_FctImp_1985_CU_C1V0.tif",
        "notes": "Large mosaic URL structure; bounded prefix only.",
    },
    {
        "probe_id": "cdl_imageserver_tiny_export",
        "source_family": "CDL / USDA Cropland Data Layer",
        "kind": "arcgis_imageserver_export_image",
        "url": "https://pdi.scinet.usda.gov/image/rest/services/CDL_WM/ImageServer/exportImage?bbox=-83.20000000,39.80000000,-83.19900000,39.80100000&bboxSR=4326&imageSR=3857&size=16,16&format=tiff&f=image&time=2023",
        "notes": "Tiny ArcGIS exportImage query. The bbox and size are intentionally small.",
    },
    {
        "probe_id": "daymet_ncss_tiny_query_experimental",
        "source_family": "Daymet",
        "kind": "thredds_ncss_query_experimental",
        "url": "https://thredds.daac.ornl.gov/thredds/ncss/grid/ornldaac/2129/daymet_v4_daily_na_prcp_2023.nc?var=prcp&north=40.1&west=-83.2&east=-83.1&south=40.0&disableProjSubset=on&horizStride=1&time_start=2023-01-01T12:00:00Z&time_end=2023-01-01T12:00:00Z&timeStride=1&accept=netcdf",
        "notes": "Experimental NCSS query candidate. Expected to be validated only as bounded response behavior, not a runtime contract.",
    },
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run explicit opt-in bounded probes across known URL structure families.")
    parser.add_argument("--allow-network", action="store_true", help="Required opt-in for live HTTPS access.")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--out-json", default="reports/live_url_structure_probe.json")
    parser.add_argument("--out-md", default="reports/live_url_structure_probe.md")
    return parser.parse_args()


def safe_headers(headers: Any) -> dict[str, str | None]:
    lowered = {key.lower(): value for key, value in headers.items()}
    return {
        "content_length": lowered.get("content-length"),
        "content_type": lowered.get("content-type"),
        "accept_ranges": lowered.get("accept-ranges"),
        "last_modified": lowered.get("last-modified"),
        "etag": lowered.get("etag"),
    }


def probe_one(probe: dict[str, str], *, max_bytes: int, chunk_size: int, timeout_seconds: int) -> dict[str, Any]:
    started = utc_now_iso()
    start = time.perf_counter()
    first_byte_seconds: float | None = None
    body = bytearray()
    bytes_read = 0
    status_code: int | None = None
    headers: dict[str, str | None] = {}
    truncated = False
    error: str | None = None
    requested_range = f"bytes=0-{max_bytes - 1}"

    request = Request(
        probe["url"],
        headers={
            "User-Agent": "FasterRaster-live-url-structure-probe/0.3.1",
            "Range": requested_range,
        },
    )

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status_code = response.status
            headers = safe_headers(response.headers)
            while True:
                remaining = max_bytes - bytes_read
                if remaining <= 0:
                    truncated = True
                    break
                chunk = response.read(min(chunk_size, remaining))
                if not chunk:
                    break
                if first_byte_seconds is None:
                    first_byte_seconds = time.perf_counter() - start
                bytes_read += len(chunk)
                body.extend(chunk)
    except HTTPError as exc:
        status_code = exc.code
        headers = safe_headers(exc.headers)
        error = f"HTTPError: {exc.code} {exc.reason}"
    except URLError as exc:
        error = f"URLError: {exc.reason}"
    except TimeoutError as exc:
        error = f"TimeoutError: {exc}"

    elapsed = time.perf_counter() - start
    content_length = headers.get("content_length")
    parsed = urlparse(probe["url"])
    sha256 = hashlib.sha256(bytes(body)).hexdigest() if body else None
    complete_under_bound = False
    if content_length and content_length.isdigit():
        complete_under_bound = int(content_length) <= max_bytes and not truncated

    return {
        **probe,
        "started_at_utc": started,
        "completed_at_utc": utc_now_iso(),
        "host": parsed.netloc,
        "requested_range": requested_range,
        "status_code": status_code,
        "response_headers": headers,
        "max_bytes": max_bytes,
        "chunk_size": chunk_size,
        "timeout_seconds": timeout_seconds,
        "bytes_read": bytes_read,
        "truncated": truncated,
        "complete_under_bound": complete_under_bound,
        "elapsed_seconds": round(elapsed, 6),
        "first_byte_seconds": round(first_byte_seconds, 6) if first_byte_seconds is not None else None,
        "throughput_mb_per_second": round((bytes_read / elapsed) / (1024 * 1024), 6) if elapsed > 0 else None,
        "sha256_prefix": sha256,
        "error": error,
        "diagnostic_status": "PASS" if status_code in {200, 206} and bytes_read > 0 and error is None else "FAIL",
    }


def write_report_json(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_report_md(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Live URL Structure Probe",
        "",
        "This is an explicit opt-in bounded streaming diagnostic. It does not update runtime registries, golden fixtures, or default diagnostics.",
        "",
        f"- Started: `{report['started_at_utc']}`",
        f"- Completed: `{report['completed_at_utc']}`",
        f"- Max bytes per URL: `{report['max_bytes_per_url']}`",
        f"- Probe count: `{report['probe_count']}`",
        f"- Pass count: `{report['pass_count']}`",
        f"- Fail count: `{report['fail_count']}`",
        "",
        "| Probe | Kind | Status | HTTP | Bytes | Content-Type | Error |",
        "| --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for item in report["probes"]:
        headers = item["response_headers"]
        lines.append(
            f"| `{item['probe_id']}` | `{item['kind']}` | `{item['diagnostic_status']}` | "
            f"{item['status_code']} | {item['bytes_read']} | {headers.get('content_type') or ''} | {item['error'] or ''} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    if not args.allow_network:
        raise SystemExit("Refusing to run live URL structure probes without --allow-network.")
    if args.max_bytes <= 0:
        raise SystemExit("--max-bytes must be positive.")
    if args.chunk_size <= 0:
        raise SystemExit("--chunk-size must be positive.")

    started = utc_now_iso()
    results = [
        probe_one(probe, max_bytes=args.max_bytes, chunk_size=args.chunk_size, timeout_seconds=args.timeout_seconds)
        for probe in PROBES
    ]
    report = {
        "started_at_utc": started,
        "completed_at_utc": utc_now_iso(),
        "max_bytes_per_url": args.max_bytes,
        "chunk_size": args.chunk_size,
        "timeout_seconds": args.timeout_seconds,
        "probe_count": len(results),
        "pass_count": sum(1 for item in results if item["diagnostic_status"] == "PASS"),
        "fail_count": sum(1 for item in results if item["diagnostic_status"] == "FAIL"),
        "probes": results,
    }
    write_report_json(Path(args.out_json), report)
    write_report_md(Path(args.out_md), report)
    print(json.dumps({key: report[key] for key in ["probe_count", "pass_count", "fail_count"]}, sort_keys=True))
    return 0 if report["pass_count"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
