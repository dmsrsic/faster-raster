from __future__ import annotations

import argparse
import hashlib
import json
import time
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one explicit opt-in bounded HTTPS streaming probe."
    )
    parser.add_argument("--allow-network", action="store_true", help="Required opt-in for live HTTPS access.")
    parser.add_argument("--url", required=True, help="The single URL to stream.")
    parser.add_argument("--max-bytes", type=int, default=5_000_000)
    parser.add_argument("--chunk-size", type=int, default=65_536)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-md", required=True)
    return parser.parse_args()


def looks_like_zip(data: bytes, content_type: str | None) -> bool:
    if data.startswith(b"PK\x03\x04") or data.startswith(b"PK\x05\x06") or data.startswith(b"PK\x07\x08"):
        return True
    if content_type and "zip" in content_type.lower():
        return True
    return False


def validate_zip(data: bytes) -> tuple[bool | None, int | None, list[str], str | None]:
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            bad_member = archive.testzip()
            names = archive.namelist()
            if bad_member is not None:
                return False, len(names), names[:20], f"zip member failed CRC check: {bad_member}"
            return True, len(names), names[:20], None
    except zipfile.BadZipFile as exc:
        return False, None, [], str(exc)


def write_json(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def md_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "<br>".join(str(item) for item in value)
    return str(value)


def write_markdown(path: Path, report: dict[str, Any], passed: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "url",
        "started_at_utc",
        "completed_at_utc",
        "status_code",
        "content_length_header",
        "content_type",
        "last_modified",
        "etag",
        "max_bytes",
        "chunk_size",
        "bytes_read",
        "truncated",
        "complete",
        "elapsed_seconds",
        "first_byte_seconds",
        "throughput_bytes_per_second",
        "throughput_mb_per_second",
        "sha256",
        "zip_valid",
        "zip_entry_count",
        "zip_entries_preview",
        "error",
    ]
    lines = [
        "# Live Download Probe",
        "",
        "| Metric | Value |",
        "| --- | --- |",
    ]
    for field in fields:
        lines.append(f"| `{field}` | {md_value(report.get(field))} |")
    lines.extend(["", f"**Result:** {'PASS' if passed else 'FAIL'}", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def run_probe(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    if not args.allow_network:
        raise SystemExit("Refusing to run live probe without --allow-network.")
    if args.max_bytes <= 0:
        raise SystemExit("--max-bytes must be positive.")
    if args.chunk_size <= 0:
        raise SystemExit("--chunk-size must be positive.")

    started = utc_now_iso()
    start_time = time.perf_counter()
    first_byte_seconds: float | None = None
    status_code: int | None = None
    headers: dict[str, str] = {}
    bytes_read = 0
    truncated = False
    body = bytearray()
    error: str | None = None

    request = Request(args.url, headers={"User-Agent": "FasterRaster-live-download-probe/0.2.1"})

    try:
        with urlopen(request, timeout=30) as response:
            status_code = response.status
            headers = {key.lower(): value for key, value in response.headers.items()}

            while True:
                remaining_before_limit = args.max_bytes - bytes_read
                read_size = min(args.chunk_size, remaining_before_limit + 1)
                chunk = response.read(read_size)
                if not chunk:
                    break
                if first_byte_seconds is None:
                    first_byte_seconds = time.perf_counter() - start_time

                bytes_read += len(chunk)
                body.extend(chunk)
                if bytes_read > args.max_bytes:
                    truncated = True
                    break

    except HTTPError as exc:
        status_code = exc.code
        headers = {key.lower(): value for key, value in exc.headers.items()}
        error = f"HTTPError: {exc.code} {exc.reason}"
    except URLError as exc:
        error = f"URLError: {exc.reason}"
    except TimeoutError as exc:
        error = f"TimeoutError: {exc}"

    elapsed = time.perf_counter() - start_time
    completed = utc_now_iso()
    content_length_raw = headers.get("content-length")
    content_length = int(content_length_raw) if content_length_raw and content_length_raw.isdigit() else None
    if content_length is not None and content_length > args.max_bytes:
        truncated = True

    complete = bool(status_code == 200 and bytes_read > 0 and not truncated and error is None)
    sha256 = hashlib.sha256(bytes(body)).hexdigest() if bytes_read > 0 else None
    throughput_bps = bytes_read / elapsed if elapsed > 0 else None
    throughput_mbps = throughput_bps / (1024 * 1024) if throughput_bps is not None else None

    zip_valid: bool | None = None
    zip_entry_count: int | None = None
    zip_entries_preview: list[str] = []
    zip_error: str | None = None
    content_type = headers.get("content-type")
    if complete and looks_like_zip(bytes(body), content_type):
        zip_valid, zip_entry_count, zip_entries_preview, zip_error = validate_zip(bytes(body))
        if zip_error:
            error = zip_error if error is None else f"{error}; {zip_error}"

    report: dict[str, Any] = {
        "url": args.url,
        "started_at_utc": started,
        "completed_at_utc": completed,
        "status_code": status_code,
        "content_length_header": content_length,
        "content_type": content_type,
        "last_modified": headers.get("last-modified"),
        "etag": headers.get("etag"),
        "max_bytes": args.max_bytes,
        "chunk_size": args.chunk_size,
        "bytes_read": bytes_read,
        "truncated": truncated,
        "complete": complete,
        "elapsed_seconds": round(elapsed, 6),
        "first_byte_seconds": round(first_byte_seconds, 6) if first_byte_seconds is not None else None,
        "throughput_bytes_per_second": round(throughput_bps, 3) if throughput_bps is not None else None,
        "throughput_mb_per_second": round(throughput_mbps, 6) if throughput_mbps is not None else None,
        "sha256": sha256,
        "zip_valid": zip_valid,
        "zip_entry_count": zip_entry_count,
        "zip_entries_preview": zip_entries_preview,
        "error": error,
    }

    passed = (
        report["status_code"] == 200
        and report["bytes_read"] > 0
        and not report["truncated"]
        and report["sha256"] is not None
        and report["complete"]
        and (report["zip_valid"] in (None, True))
        and report["error"] is None
    )
    return report, passed


def main() -> int:
    args = parse_args()
    report, passed = run_probe(args)
    write_json(Path(args.out_json), report)
    write_markdown(Path(args.out_md), report, passed)
    print(f"live_download_probe {'PASS' if passed else 'FAIL'}")
    print(f"status_code={report['status_code']}")
    print(f"bytes_read={report['bytes_read']}")
    print(f"sha256={report['sha256']}")
    print(f"json={args.out_json}")
    print(f"markdown={args.out_md}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
