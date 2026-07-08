from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import yaml

DEFAULT_SPEC = "research/daymet_single_pixel_probe_spec.yaml"
DEFAULT_OUT = "reports/daymet_single_pixel_probe.json"
DEFAULT_MARKDOWN = "reports/daymet_single_pixel_probe.md"
DEFAULT_MAX_BYTES = 65_536
DEFAULT_CHUNK_SIZE = 16_384


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Opt-in bounded Daymet single-pixel REST probe.")
    parser.add_argument("--allow-network", action="store_true", help="Required opt-in for live network access.")
    parser.add_argument("--spec", default=DEFAULT_SPEC)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--markdown", default=DEFAULT_MARKDOWN)
    return parser.parse_args()


def load_probe_spec(path: Path) -> dict[str, Any]:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Daymet single-pixel probe spec must be a YAML mapping")
    return config


def query_items(config: dict[str, Any]) -> list[tuple[str, Any]]:
    scenario = config["scenario"]
    return [
        ("lat", scenario["lat"]),
        ("lon", scenario["lon"]),
        ("vars", scenario["vars"]),
        ("start", scenario["start"]),
        ("end", scenario["end"]),
    ]


def build_url(config: dict[str, Any]) -> str:
    endpoint = config["endpoint"]
    return f"{endpoint}?{urlencode(query_items(config))}"


def safe_headers(headers: Any) -> dict[str, str | None]:
    lowered = {str(key).lower(): value for key, value in headers.items()}
    return {
        "content_type": lowered.get("content-type"),
        "content_length": lowered.get("content-length"),
        "accept_ranges": lowered.get("accept-ranges"),
    }


def read_bounded_response(response: Any, *, max_bytes: int, chunk_size: int, start: float) -> dict[str, Any]:
    body = bytearray()
    first_byte_seconds: float | None = None
    while len(body) < max_bytes:
        chunk = response.read(min(chunk_size, max_bytes - len(body)))
        if not chunk:
            break
        if first_byte_seconds is None:
            first_byte_seconds = time.perf_counter() - start
        body.extend(chunk)
    text_preview = None
    if body:
        decoded = bytes(body).decode("utf-8", errors="replace")
        text_preview = decoded.splitlines()[:8]
    return {
        "bytes_read": len(body),
        "truncated": len(body) >= max_bytes,
        "sha256": hashlib.sha256(bytes(body)).hexdigest() if body else None,
        "first_byte_seconds": round(first_byte_seconds, 6) if first_byte_seconds is not None else None,
        "first_response_lines": text_preview or [],
    }


def probe(config: dict[str, Any], *, max_bytes: int, chunk_size: int, timeout_seconds: int, opener: Callable[..., Any] = urlopen) -> dict[str, Any]:
    if max_bytes <= 0:
        raise SystemExit("--max-bytes must be positive")
    url = build_url(config)
    start = time.perf_counter()
    status_code: int | None = None
    headers: dict[str, str | None] = {}
    error: str | None = None
    read_result = {
        "bytes_read": 0,
        "truncated": False,
        "sha256": None,
        "first_byte_seconds": None,
        "first_response_lines": [],
    }
    request = Request(
        url,
        headers={
            "User-Agent": "FasterRaster-daymet-single-pixel-probe/0.1",
            "Range": f"bytes=0-{max_bytes - 1}",
        },
    )
    try:
        with opener(request, timeout=timeout_seconds) as response:
            status_code = response.status
            headers = safe_headers(response.headers)
            read_result = read_bounded_response(response, max_bytes=max_bytes, chunk_size=chunk_size, start=start)
    except HTTPError as exc:
        status_code = exc.code
        headers = safe_headers(exc.headers)
        error = f"HTTPError: {exc.code} {exc.reason}"
        read_result = read_bounded_response(exc, max_bytes=max_bytes, chunk_size=chunk_size, start=start)
    except URLError as exc:
        error = f"URLError: {exc.reason}"
    except TimeoutError as exc:
        error = f"TimeoutError: {exc}"
    elapsed = time.perf_counter() - start
    probe_status = "PASS" if status_code in {200, 206} and read_result["bytes_read"] > 0 and error is None else "FAIL"
    return {
        "probe_status": probe_status,
        "timestamp_utc": utc_now_iso(),
        "source_id": config["source_id"],
        "network_opt_in": True,
        "endpoint": config["endpoint"],
        "url": url,
        "params": dict(query_items(config)),
        "http_status": status_code,
        "content_type": headers.get("content_type"),
        "content_length": headers.get("content_length"),
        "accept_ranges": headers.get("accept_ranges"),
        "bytes_read": read_result["bytes_read"],
        "truncated": read_result["truncated"],
        "sha256": read_result["sha256"],
        "elapsed_seconds": round(elapsed, 6),
        "first_byte_seconds": read_result["first_byte_seconds"],
        "first_response_lines": read_result["first_response_lines"],
        "error": error,
        "max_bytes": max_bytes,
    }


def write_json_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_markdown_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Daymet Single-Pixel Probe Report",
        "",
        f"- Status: `{report['probe_status']}`",
        f"- HTTP status: `{report['http_status']}`",
        f"- Content-Type: `{report['content_type']}`",
        f"- Bytes read: `{report['bytes_read']}`",
        f"- Truncated: `{report['truncated']}`",
        f"- SHA256: `{report['sha256']}`",
        f"- Elapsed seconds: `{report['elapsed_seconds']}`",
        f"- First byte seconds: `{report['first_byte_seconds']}`",
        f"- Error: `{report['error']}`",
        "",
        "## URL",
        "",
        f"`{report['url']}`",
        "",
        "## First Response Lines",
        "",
    ]
    for line in report["first_response_lines"]:
        lines.append("```text")
        lines.append(line)
        lines.append("```")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

def main() -> int:
    args = parse_args()
    if not args.allow_network:
        raise SystemExit("Refusing to run Daymet single-pixel probe without --allow-network.")
    config = load_probe_spec(Path(args.spec))
    report = probe(
        config,
        max_bytes=args.max_bytes,
        chunk_size=args.chunk_size,
        timeout_seconds=args.timeout_seconds,
    )
    write_json_report(Path(args.out), report)
    write_markdown_report(Path(args.markdown), report)
    print(json.dumps({"probe_status": report["probe_status"], "http_status": report["http_status"], "bytes_read": report["bytes_read"]}, sort_keys=True))
    return 0 if report["probe_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
