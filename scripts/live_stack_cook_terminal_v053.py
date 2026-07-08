#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


AOI = {
    "lat": 39.805,
    "lon": -83.195,
    "bbox": [-83.20, 39.80, -83.19, 39.81],
    "date": "2023-01-01",
    "year": 2023,
}

HEADERS = {
    "User-Agent": "FasterRaster-live-stack-cook/0.5.3 (+bounded probe; no extraction)",
    "Accept": "*/*",
}


def urlencode(base: str, params: dict[str, Any]) -> str:
    return base + "?" + urllib.parse.urlencode(params, doseq=True, safe=",:/()")


def cdl_export_url() -> str:
    return urlencode(
        "https://pdi.scinet.usda.gov/image/rest/services/CDL_WM/ImageServer/exportImage",
        {
            "bbox": "-83.20,39.80,-83.19,39.81",
            "bboxSR": "4326",
            "imageSR": "4326",
            "size": "64,64",
            "format": "tiff",
            "f": "image",
        },
    )


def daymet_single_pixel_url() -> str:
    return urlencode(
        "https://daymet.ornl.gov/single-pixel/api/data",
        {
            "lat": "39.805",
            "lon": "-83.195",
            "vars": "prcp",
            "start": "2023-01-01",
            "end": "2023-01-01",
        },
    )


def tnm_products_url() -> str:
    return urlencode(
        "https://tnmaccess.nationalmap.gov/api/v1/products",
        {
            "datasets": "Digital Elevation Model (DEM) 1 meter",
            "bbox": "-83.20,39.80,-83.19,39.81",
            "prodFormats": "GeoTIFF",
            "outputFormat": "JSON",
        },
    )


def gfs_subset_candidates() -> list[str]:
    # NOMADS is real-time/recent. Try current and previous UTC dates, newest cycles first.
    now = datetime.now(timezone.utc)
    cycles = ["18", "12", "06", "00"]
    urls: list[str] = []
    for days_back in [0, 1]:
        ymd = (now - timedelta(days=days_back)).strftime("%Y%m%d")
        for cyc in cycles:
            urls.append(
                urlencode(
                    "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl",
                    {
                        "dir": f"/gfs.{ymd}/{cyc}/atmos",
                        "file": f"gfs.t{cyc}z.pgrb2.0p25.f000",
                        "lev_surface": "on",
                        "var_PRATE": "on",
                        "subregion": "",
                        "leftlon": "-83.20",
                        "rightlon": "-83.19",
                        "toplat": "39.81",
                        "bottomlat": "39.80",
                    },
                )
            )
    urls.append("https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl")
    return urls


def targets() -> list[dict[str, Any]]:
    return [
        {
            "source_id": "prism_daily_ppt",
            "endpoint_id": "prism_daily_zip",
            "kind": "static_zip",
            "urls": [
                "https://data.prism.oregonstate.edu/time_series/us/an/4km/ppt/daily/2023/prism_ppt_us_25m_20230101.zip",
                "https://data.prism.oregonstate.edu/time_series/us/an/4km/ppt/daily/2026/prism_ppt_us_25m_20260101.zip",
            ],
        },
        {
            "source_id": "daymet_single_pixel",
            "endpoint_id": "daymet_single_pixel_csv",
            "kind": "parameterized_rest_csv",
            "urls": [daymet_single_pixel_url()],
        },
        {
            "source_id": "usda_cdl",
            "endpoint_id": "cdl_tiny_imageserver_export",
            "kind": "arcgis_imageserver_tiff_export",
            "urls": [cdl_export_url()],
        },
        {
            "source_id": "chirps_daily",
            "endpoint_id": "chirps_20230101_tif_gz",
            "kind": "static_tif_gz",
            "urls": [
                "https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_daily/tifs/p05/2023/chirps-v2.0.2023.01.01.tif.gz"
            ],
        },
        {
            "source_id": "gridmet_daily",
            "endpoint_id": "gridmet_pr_2023_nc",
            "kind": "static_netcdf",
            "urls": ["https://www.northwestknowledge.net/metdata/data/pr_2023.nc"],
        },
        {
            "source_id": "terraclimate_monthly",
            "endpoint_id": "terraclimate_ppt_2023_nc",
            "kind": "static_netcdf_candidate",
            "urls": ["https://climate.northwestknowledge.net/TERRACLIMATE-DATA/TerraClimate_ppt_2023.nc"],
        },
        {
            "source_id": "worldclim_normals",
            "endpoint_id": "worldclim_10m_prec_zip",
            "kind": "static_zip_geotiff_bundle",
            "urls": ["https://geodata.ucdavis.edu/climate/worldclim/2_1/base/wc2.1_10m_prec.zip"],
        },
        {
            "source_id": "noaa_gfs_nomads",
            "endpoint_id": "gfs_prate_tiny_bbox_or_filter_page",
            "kind": "nomads_grib_filter",
            "urls": gfs_subset_candidates(),
        },
        {
            "source_id": "noaa_hrrr_open_data",
            "endpoint_id": "hrrr_20230101_idx",
            "kind": "public_s3_index_text",
            "urls": [
                "https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.20230101/conus/hrrr.t00z.wrfsfcf00.grib2.idx"
            ],
        },
        {
            "source_id": "noaa_mrms_open_data",
            "endpoint_id": "mrms_bucket_index",
            "kind": "public_s3_bucket_index",
            "urls": ["https://noaa-mrms-pds.s3.amazonaws.com/index.html"],
        },
        {
            "source_id": "usgs_3dep_tnm",
            "endpoint_id": "tnm_products_bbox_json",
            "kind": "metadata_api_json",
            "urls": [tnm_products_url()],
        },
        {
            "source_id": "nasa_cmr_metadata",
            "endpoint_id": "cmr_modis_collections_json",
            "kind": "metadata_api_json",
            "urls": ["https://cmr.earthdata.nasa.gov/search/collections.json?keyword=MODIS&page_size=5"],
        },
        {
            "source_id": "noaa_ncei_thredds",
            "endpoint_id": "ncei_thredds_catalog_xml",
            "kind": "thredds_catalog_xml",
            "urls": ["https://www.ncei.noaa.gov/thredds/catalog.xml"],
        },
    ]


def mostly_text(data: bytes) -> bool:
    if not data:
        return False
    sample = data[:2048]
    if b"\x00" in sample:
        return False
    printable = sum(1 for b in sample if b in b"\t\r\n" or 32 <= b <= 126)
    return printable / max(len(sample), 1) > 0.85


def text_preview(data: bytes, content_type: str | None) -> str | None:
    content_type = (content_type or "").lower()
    textish = any(token in content_type for token in ["text", "json", "xml", "csv", "html"])
    if not textish and not mostly_text(data):
        return None
    txt = data[:2048].decode("utf-8", errors="replace")
    lines = [line.rstrip() for line in txt.splitlines()[:8]]
    return "\n".join(lines)


def classify(status: int | None, bytes_read: int, truncated: bool, error: str | None) -> str:
    if status in (200, 206) and bytes_read > 0:
        if status == 206:
            return "pass_range_limited"
        if truncated:
            return "pass_bounded_truncated"
        return "pass_verified"
    if status in (401, 403):
        return "credential_or_access_gated"
    if status == 404:
        return "fail_endpoint_not_found"
    if status in (400, 422):
        return "fail_malformed_or_bad_params"
    if error:
        return "fail_error"
    return "fail_no_bytes"


def probe_url(url: str, max_bytes: int, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={**HEADERS, "Range": f"bytes=0-{max_bytes - 1}"},
        method="GET",
    )

    started = time.perf_counter()
    status: int | None = None
    headers: dict[str, str] = {}
    body = bytearray()
    error: str | None = None
    first_byte_seconds: float | None = None

    try:
        context = ssl.create_default_context()
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            status = int(response.status)
            headers = {k.lower(): v for k, v in response.headers.items()}

            while len(body) < max_bytes:
                chunk = response.read(min(8192, max_bytes - len(body)))
                if not chunk:
                    break
                if first_byte_seconds is None:
                    first_byte_seconds = time.perf_counter() - started
                body.extend(chunk)

            # One-byte lookahead detects servers that ignored Range.
            extra = response.read(1)
            extra_after_cap = bool(extra)

    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        headers = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
        try:
            err_body = exc.read(min(max_bytes, 8192))
            if err_body:
                body.extend(err_body)
                first_byte_seconds = time.perf_counter() - started
        except Exception:
            pass
        error = f"HTTPError: {exc.code} {exc.reason}"
        extra_after_cap = False

    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        extra_after_cap = False

    elapsed = time.perf_counter() - started
    content_length_raw = headers.get("content-length")
    try:
        content_length = int(content_length_raw) if content_length_raw is not None else None
    except ValueError:
        content_length = None

    content_range = headers.get("content-range")
    content_type = headers.get("content-type")
    truncated = bool(extra_after_cap)
    if content_length is not None and status != 206 and content_length > len(body):
        truncated = True

    sha256 = hashlib.sha256(body).hexdigest() if body else None
    result_class = classify(status, len(body), truncated, error)

    return {
        "url": url,
        "status": status,
        "result_class": result_class,
        "bytes_read": len(body),
        "max_bytes": max_bytes,
        "truncated": truncated,
        "range_limited": status == 206 or bool(content_range),
        "content_type": content_type,
        "content_length": content_length,
        "content_range": content_range,
        "sha256": sha256,
        "elapsed_seconds": round(elapsed, 6),
        "first_byte_seconds": round(first_byte_seconds, 6) if first_byte_seconds is not None else None,
        "first_16_bytes_hex": bytes(body[:16]).hex() if body else None,
        "text_preview": text_preview(bytes(body), content_type),
        "error": error,
    }


def try_target(target: dict[str, Any], max_bytes: int, timeout: int) -> dict[str, Any]:
    attempts = []
    selected = None

    for url in target["urls"]:
        result = probe_url(url, max_bytes=max_bytes, timeout=timeout)
        attempts.append(result)
        if result["result_class"].startswith("pass_"):
            selected = result
            break

    if selected is None:
        selected = attempts[-1] if attempts else {
            "result_class": "fail_no_attempts",
            "status": None,
            "bytes_read": 0,
            "error": "no URL candidates",
        }

    return {
        "source_id": target["source_id"],
        "endpoint_id": target["endpoint_id"],
        "kind": target["kind"],
        "selected_url": selected.get("url"),
        "result_class": selected.get("result_class"),
        "status": selected.get("status"),
        "bytes_read": selected.get("bytes_read", 0),
        "max_bytes": max_bytes,
        "truncated": selected.get("truncated"),
        "range_limited": selected.get("range_limited"),
        "content_type": selected.get("content_type"),
        "content_length": selected.get("content_length"),
        "content_range": selected.get("content_range"),
        "sha256": selected.get("sha256"),
        "elapsed_seconds": selected.get("elapsed_seconds"),
        "first_byte_seconds": selected.get("first_byte_seconds"),
        "first_16_bytes_hex": selected.get("first_16_bytes_hex"),
        "text_preview": selected.get("text_preview"),
        "error": selected.get("error"),
        "attempt_count": len(attempts),
        "attempts": attempts,
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = []
    lines.append("# FasterRaster live stack cook")
    lines.append("")
    lines.append(f"- created_at_utc: `{report['created_at_utc']}`")
    lines.append(f"- max_bytes_per_source: `{report['max_bytes_per_source']}`")
    lines.append(f"- target_date: `{report['target']['date']}`")
    lines.append(f"- target_bbox: `{report['target']['bbox']}`")
    lines.append(f"- endpoint_count: `{report['endpoint_count']}`")
    lines.append(f"- source_success_count: `{report['source_success_count']}`")
    lines.append(f"- endpoint_pass_count: `{report['endpoint_pass_count']}`")
    lines.append(f"- total_bytes_read: `{report['total_bytes_read']}`")
    lines.append("")
    lines.append("| Source | Endpoint | Class | HTTP | Bytes | Type | SHA256 short | Error |")
    lines.append("| --- | --- | --- | ---: | ---: | --- | --- | --- |")
    for r in report["results"]:
        sha = (r.get("sha256") or "")[:12]
        err = (r.get("error") or "").replace("|", "/")
        ctype = (r.get("content_type") or "").replace("|", "/")
        lines.append(
            f"| `{r['source_id']}` | `{r['endpoint_id']}` | `{r['result_class']}` | "
            f"{r.get('status')} | {r.get('bytes_read')} | {ctype} | `{sha}` | {err} |"
        )

    lines.append("")
    lines.append("## Text previews")
    for r in report["results"]:
        preview = r.get("text_preview")
        if preview:
            lines.append("")
            lines.append(f"### {r['source_id']} / {r['endpoint_id']}")
            lines.append("```text")
            lines.append(preview[:1500])
            lines.append("```")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-bytes", type=int, default=65536)
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--out-json", default="reports/live_stack_cook/live_stack_cook_v0_5_3.json")
    parser.add_argument("--out-md", default="reports/live_stack_cook/live_stack_cook_v0_5_3.md")
    args = parser.parse_args()

    all_targets = targets()
    results = []
    for i, target in enumerate(all_targets, 1):
        print(f"[{i:02d}/{len(all_targets):02d}] dipping {target['source_id']}::{target['endpoint_id']}")
        results.append(try_target(target, max_bytes=args.max_bytes, timeout=args.timeout))

    success_sources = sorted({r["source_id"] for r in results if str(r["result_class"]).startswith("pass_")})
    endpoint_pass_count = sum(1 for r in results if str(r["result_class"]).startswith("pass_"))
    total_bytes = sum(int(r.get("bytes_read") or 0) for r in results)

    report = {
        "report_id": "live_stack_cook_v0_5_3",
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "target": AOI,
        "max_bytes_per_source": args.max_bytes,
        "timeout_seconds": args.timeout,
        "policy": {
            "no_credentials": True,
            "no_extraction": True,
            "runtime_registry_modified": False,
            "source_failures_are_nonfatal": True,
            "bounded_read_only": True,
        },
        "endpoint_count": len(results),
        "source_success_count": len(success_sources),
        "successful_sources": success_sources,
        "endpoint_pass_count": endpoint_pass_count,
        "endpoint_fail_count": len(results) - endpoint_pass_count,
        "total_bytes_read": total_bytes,
        "results": results,
    }

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_markdown(report, out_md)

    print("")
    print("LIVE STACK COOK SUMMARY")
    print(f"endpoint_count:        {report['endpoint_count']}")
    print(f"source_success_count:  {report['source_success_count']}")
    print(f"endpoint_pass_count:   {report['endpoint_pass_count']}")
    print(f"endpoint_fail_count:   {report['endpoint_fail_count']}")
    print(f"total_bytes_read:      {report['total_bytes_read']}")
    print(f"json:                  {out_json}")
    print(f"markdown:              {out_md}")
    print("")
    print("RESULT TABLE")
    for r in results:
        print(
            f"{r['source_id']:<28} {r['result_class']:<28} "
            f"http={str(r.get('status')):<4} bytes={str(r.get('bytes_read')):<7} "
            f"type={(r.get('content_type') or '-')[:40]}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
