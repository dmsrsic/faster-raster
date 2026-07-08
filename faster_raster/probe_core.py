from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

RESULT_CLASSES = {
    "pass_verified",
    "pass_partial_content_verified",
    "fail_http",
    "fail_endpoint",
    "credential_gated",
    "malformed_request_expected",
    "adapter_needed",
    "skipped_policy",
    "unknown",
}
TEXT_CONTENT_HINTS = ("text/", "json", "xml", "html", "csv")
SECRET_QUERY_KEYS = {"token", "access_token", "api_key", "apikey", "password", "secret", "key"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def require_network_opt_in(allow_network: bool) -> None:
    if not allow_network:
        raise SystemExit("Refusing to run live probe without --allow-network.")


def redact_url(url: str) -> str:
    if "?" not in url:
        return url
    base, query = url.split("?", 1)
    redacted_parts = []
    for part in query.split("&"):
        key = part.split("=", 1)[0]
        if key.lower() in SECRET_QUERY_KEYS:
            redacted_parts.append(f"{key}=<REDACTED>")
        else:
            redacted_parts.append(part)
    return base + "?" + "&".join(redacted_parts)


def safe_headers(headers: Any) -> dict[str, str | None]:
    lowered = {str(key).lower(): value for key, value in headers.items()}
    return {
        "content_type": lowered.get("content-type"),
        "content_length": lowered.get("content-length"),
        "accept_ranges": lowered.get("accept-ranges"),
        "last_modified": lowered.get("last-modified"),
        "etag": lowered.get("etag"),
    }


def is_text_response(content_type: str | None) -> bool:
    if not content_type:
        return False
    lowered = content_type.lower()
    return any(hint in lowered for hint in TEXT_CONTENT_HINTS)


def read_bounded_response(
    response: Any,
    *,
    max_bytes: int,
    chunk_size: int = 16_384,
    start: float,
    content_type: str | None = None,
    text_preview_lines: int = 8,
    binary_preview: bool = False,
) -> dict[str, Any]:
    body = bytearray()
    first_byte_seconds: float | None = None
    while len(body) < max_bytes:
        chunk = response.read(min(chunk_size, max_bytes - len(body)))
        if not chunk:
            break
        if first_byte_seconds is None:
            first_byte_seconds = time.perf_counter() - start
        body.extend(chunk)
    text_preview: list[str] = []
    if body and (binary_preview or is_text_response(content_type)):
        text_preview = bytes(body).decode("utf-8", errors="replace").splitlines()[:text_preview_lines]
    return {
        "bytes_read": len(body),
        "truncated": len(body) >= max_bytes,
        "sha256": hashlib.sha256(bytes(body)).hexdigest() if body else None,
        "first_byte_seconds": round(first_byte_seconds, 6) if first_byte_seconds is not None else None,
        "text_preview": text_preview,
    }


def classify_probe_result(
    *,
    status_code: int | None,
    bytes_read: int,
    truncated: bool,
    error: str | None,
    metadata_probe: bool = False,
) -> str:
    if status_code == 401 or status_code == 403:
        return "credential_gated"
    if status_code == 400 and metadata_probe:
        return "malformed_request_expected"
    if status_code == 404:
        return "fail_endpoint"
    if error is not None:
        return "fail_http" if status_code is not None else "fail_endpoint"
    if status_code == 206 and bytes_read > 0:
        return "pass_partial_content_verified"
    if status_code == 200 and bytes_read > 0:
        return "pass_verified" if not truncated else "pass_partial_content_verified"
    if status_code is not None and 500 <= status_code <= 599:
        return "fail_http"
    return "unknown"


def probe_http(
    *,
    url: str,
    allow_network: bool,
    method: str = "GET",
    max_bytes: int = 65_536,
    timeout_seconds: int = 20,
    chunk_size: int = 16_384,
    metadata_probe: bool = False,
    opener: Callable[..., Any] = urlopen,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    require_network_opt_in(allow_network)
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    start = time.perf_counter()
    timestamp = utc_now_iso()
    status_code: int | None = None
    response_headers: dict[str, str | None] = {}
    error: str | None = None
    read_result = {"bytes_read": 0, "truncated": False, "sha256": None, "first_byte_seconds": None, "text_preview": []}
    request_headers = {"User-Agent": "FasterRaster-probe-core/0.4.1"}
    if method.upper() == "GET":
        request_headers["Range"] = f"bytes=0-{max_bytes - 1}"
    if headers:
        request_headers.update(headers)
    request = Request(url, headers=request_headers, method=method.upper())
    try:
        with opener(request, timeout=timeout_seconds) as response:
            status_code = response.status
            response_headers = safe_headers(response.headers)
            if method.upper() != "HEAD":
                read_result = read_bounded_response(
                    response,
                    max_bytes=max_bytes,
                    chunk_size=chunk_size,
                    start=start,
                    content_type=response_headers.get("content_type"),
                )
    except HTTPError as exc:
        status_code = exc.code
        response_headers = safe_headers(exc.headers)
        error = f"HTTPError: {exc.code} {exc.reason}"
        if method.upper() != "HEAD":
            read_result = read_bounded_response(
                exc,
                max_bytes=max_bytes,
                chunk_size=chunk_size,
                start=start,
                content_type=response_headers.get("content_type"),
            )
    except URLError as exc:
        error = f"URLError: {exc.reason}"
    except TimeoutError as exc:
        error = f"TimeoutError: {exc}"
    elapsed = time.perf_counter() - start
    result_class = classify_probe_result(
        status_code=status_code,
        bytes_read=read_result["bytes_read"],
        truncated=read_result["truncated"],
        error=error,
        metadata_probe=metadata_probe,
    )
    return {
        "result_class": result_class,
        "timestamp_utc": timestamp,
        "url": redact_url(url),
        "method": method.upper(),
        "http_status": status_code,
        "content_type": response_headers.get("content_type"),
        "content_length": response_headers.get("content_length"),
        "accept_ranges": response_headers.get("accept_ranges"),
        "bytes_read": read_result["bytes_read"],
        "truncated": read_result["truncated"],
        "sha256": read_result["sha256"],
        "elapsed_seconds": round(elapsed, 6),
        "first_byte_seconds": read_result["first_byte_seconds"],
        "text_preview": read_result["text_preview"],
        "error": error,
        "max_bytes": max_bytes,
        "network_opt_in": True,
    }


def skipped_result(*, reason: str, result_class: str = "skipped_policy", url: str | None = None) -> dict[str, Any]:
    if result_class not in RESULT_CLASSES:
        raise ValueError(f"unknown result_class: {result_class}")
    return {
        "result_class": result_class,
        "timestamp_utc": utc_now_iso(),
        "url": redact_url(url) if url else None,
        "method": None,
        "http_status": None,
        "content_type": None,
        "content_length": None,
        "accept_ranges": None,
        "bytes_read": 0,
        "truncated": False,
        "sha256": None,
        "elapsed_seconds": 0.0,
        "first_byte_seconds": None,
        "text_preview": [],
        "error": reason,
        "max_bytes": None,
        "network_opt_in": False,
    }


def stable_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"
