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

from faster_raster.adapters.thredds_ncss import ThreddsNcssAdapter

DEFAULT_SPEC = "research/daymet_ncss_probe_spec.yaml"
DEFAULT_OUT = "reports/daymet_ncss_probe.json"
DEFAULT_MARKDOWN = "reports/daymet_ncss_probe.md"
DEFAULT_MAX_BYTES = 65_536
DEFAULT_CHUNK_SIZE = 16_384
DEFAULT_TIMEOUT_SECONDS = 20
UNRESOLVED_VALUES = {None, "", "needs_official_verification", "netcdf_needs_official_verification"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Opt-in bounded Daymet THREDDS/NCSS probe.")
    parser.add_argument("--allow-network", action="store_true", help="Required opt-in for live network access.")
    parser.add_argument("--spec", default=DEFAULT_SPEC)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--markdown", default=DEFAULT_MARKDOWN)
    parser.add_argument(
        "--metadata-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run only metadata probe by default. Use --no-metadata-only to run tiny subset after metadata succeeds.",
    )
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    return parser.parse_args()


def load_probe_spec(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError("Daymet probe spec must be a YAML mapping")
    return config


def safe_headers(headers: Any) -> dict[str, str | None]:
    lowered = {str(key).lower(): value for key, value in headers.items()}
    return {
        "content_type": lowered.get("content-type"),
        "content_length": lowered.get("content-length"),
        "accept_ranges": lowered.get("accept-ranges"),
        "last_modified": lowered.get("last-modified"),
        "etag": lowered.get("etag"),
    }


def read_bounded_response(response: Any, *, max_bytes: int, chunk_size: int, start: float) -> dict[str, Any]:
    body = bytearray()
    bytes_read = 0
    first_byte_seconds: float | None = None
    truncated = False
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
        body.extend(chunk)
        bytes_read += len(chunk)
    return {
        "bytes_read": bytes_read,
        "truncated": truncated,
        "sha256": hashlib.sha256(bytes(body)).hexdigest() if body else None,
        "first_byte_seconds": round(first_byte_seconds, 6) if first_byte_seconds is not None else None,
    }


def is_unresolved(value: Any) -> bool:
    return value in UNRESOLVED_VALUES or (isinstance(value, str) and "needs_official_verification" in value)


def encode_subset_url(endpoint: str, params: dict[str, Any]) -> str:
    clean_params = {key: value for key, value in sorted(params.items()) if not is_unresolved(value)}
    separator = "&" if "?" in endpoint else "?"
    return f"{endpoint}{separator}{urlencode(clean_params)}" if clean_params else endpoint


def build_stage_url(stage: dict[str, Any], descriptor: dict[str, Any]) -> tuple[str | None, str | None, dict[str, Any]]:
    endpoint = stage.get("endpoint") or descriptor.get("endpoint")
    if is_unresolved(endpoint):
        return None, "endpoint is needs_official_verification", {}
    params = stage.get("params") or {}
    unresolved_params = {key: value for key, value in params.items() if is_unresolved(value)}
    if unresolved_params:
        return None, f"unresolved params: {sorted(unresolved_params)}", params
    return encode_subset_url(str(endpoint), params), None, params


def probe_url(
    *,
    url: str,
    stage_name: str,
    max_bytes: int,
    chunk_size: int,
    timeout_seconds: int,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    started = utc_now_iso()
    start = time.perf_counter()
    status_code: int | None = None
    headers: dict[str, str | None] = {}
    error: str | None = None
    read_result = {"bytes_read": 0, "truncated": False, "sha256": None, "first_byte_seconds": None}
    requested_range = f"bytes=0-{max_bytes - 1}"
    request = Request(
        url,
        headers={
            "User-Agent": "FasterRaster-daymet-ncss-probe/0.3.1",
            "Range": requested_range,
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
    except URLError as exc:
        error = f"URLError: {exc.reason}"
    except TimeoutError as exc:
        error = f"TimeoutError: {exc}"
    elapsed = time.perf_counter() - start
    status = "PASS" if status_code in {200, 206} and read_result["bytes_read"] > 0 and error is None else "FAIL"
    return {
        "stage": stage_name,
        "stage_status": status,
        "endpoint": url,
        "requested_range": requested_range,
        "http_status": status_code,
        "content_type": headers.get("content_type"),
        "content_length": headers.get("content_length"),
        "bytes_read": read_result["bytes_read"],
        "truncated": read_result["truncated"],
        "sha256": read_result["sha256"],
        "elapsed_seconds": round(elapsed, 6),
        "first_byte_seconds": read_result["first_byte_seconds"],
        "error": error,
        "max_bytes": max_bytes,
        "timestamp_utc": started,
    }


def skipped_stage_result(stage_name: str, reason: str, *, endpoint: str | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "stage": stage_name,
        "stage_status": "SKIPPED",
        "endpoint": endpoint,
        "params": params or {},
        "http_status": None,
        "content_type": None,
        "content_length": None,
        "bytes_read": 0,
        "truncated": False,
        "sha256": None,
        "elapsed_seconds": 0.0,
        "first_byte_seconds": None,
        "error": reason,
        "max_bytes": None,
        "timestamp_utc": utc_now_iso(),
    }


def run_probe(
    *,
    spec_path: Path,
    allow_network: bool,
    metadata_only: bool,
    max_bytes: int,
    chunk_size: int,
    timeout_seconds: int,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    if not allow_network:
        raise SystemExit("Refusing to run Daymet NCSS probe without --allow-network.")
    if max_bytes <= 0:
        raise SystemExit("--max-bytes must be positive.")
    if chunk_size <= 0:
        raise SystemExit("--chunk-size must be positive.")

    config = load_probe_spec(spec_path)
    descriptor = ThreddsNcssAdapter().plan_probe_request(config)
    stages = config.get("probe_sequence", [])
    metadata_stage = next((stage for stage in stages if stage.get("mode") == "metadata"), None)
    subset_stage = next((stage for stage in stages if stage.get("mode") == "bounded_subset"), None)
    stage_results: list[dict[str, Any]] = []

    if metadata_stage is None:
        stage_results.append(skipped_stage_result("metadata", "metadata stage missing"))
    else:
        metadata_url, reason, params = build_stage_url(metadata_stage, descriptor)
        if metadata_url is None:
            stage_results.append(skipped_stage_result("metadata", reason or "metadata endpoint unresolved", endpoint=metadata_stage.get("endpoint"), params=params))
        else:
            stage_results.append(
                probe_url(
                    url=metadata_url,
                    stage_name="metadata",
                    max_bytes=max_bytes,
                    chunk_size=chunk_size,
                    timeout_seconds=timeout_seconds,
                    opener=opener,
                )
            )

    metadata_passed = bool(stage_results and stage_results[0]["stage_status"] == "PASS")
    if metadata_only:
        stage_results.append(skipped_stage_result("tiny_subset", "metadata-only mode"))
    elif not metadata_passed:
        stage_results.append(skipped_stage_result("tiny_subset", "metadata stage did not pass"))
    elif subset_stage is None:
        stage_results.append(skipped_stage_result("tiny_subset", "bounded_subset stage missing"))
    else:
        subset_url, reason, params = build_stage_url(subset_stage, descriptor)
        if subset_url is None:
            stage_results.append(skipped_stage_result("tiny_subset", reason or "subset endpoint unresolved", endpoint=subset_stage.get("endpoint"), params=params))
        else:
            stage_results.append(
                probe_url(
                    url=subset_url,
                    stage_name="tiny_subset",
                    max_bytes=max_bytes,
                    chunk_size=chunk_size,
                    timeout_seconds=timeout_seconds,
                    opener=opener,
                )
            )

    probe_status = "PASS" if metadata_passed and (metadata_only or stage_results[-1]["stage_status"] == "PASS") else "FAIL"
    return {
        "probe_status": probe_status,
        "timestamp_utc": utc_now_iso(),
        "source_id": descriptor["source_id"],
        "request_id": descriptor["request_id"],
        "network_opt_in": True,
        "metadata_only": metadata_only,
        "max_bytes": max_bytes,
        "descriptor": descriptor,
        "endpoint": descriptor.get("endpoint"),
        "params": descriptor.get("params"),
        "stage_results": stage_results,
    }


def write_json_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_markdown_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Daymet NCSS Probe Report",
        "",
        f"- Status: `{report['probe_status']}`",
        f"- Source ID: `{report['source_id']}`",
        f"- Request ID: `{report['request_id']}`",
        f"- Network opt-in: `{report['network_opt_in']}`",
        f"- Metadata only: `{report['metadata_only']}`",
        f"- Max bytes: `{report['max_bytes']}`",
        "",
        "| Stage | Result | HTTP | Bytes | Content-Type | Seconds | Error | Endpoint |",
        "| --- | --- | ---: | ---: | --- | ---: | --- | --- |",
    ]
    for stage in report["stage_results"]:
        lines.append(
            f"| `{stage['stage']}` | `{stage['stage_status']}` | {stage['http_status']} | {stage['bytes_read']} | "
            f"{stage['content_type'] or ''} | {stage['elapsed_seconds']} | {stage['error'] or ''} | `{stage['endpoint'] or ''}` |"
        )
    lines.extend(["", "## Next Recommended Action", ""])
    if report["probe_status"] == "PASS":
        lines.append("Metadata probe passed. Review endpoint metadata before enabling any subset probe or adapter promotion.")
    else:
        lines.append("Probe did not pass. Verify the official Daymet THREDDS/NCSS endpoint and query parameters before retrying.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

def main() -> int:
    args = parse_args()
    report = run_probe(
        spec_path=Path(args.spec),
        allow_network=args.allow_network,
        metadata_only=args.metadata_only,
        max_bytes=args.max_bytes,
        chunk_size=args.chunk_size,
        timeout_seconds=args.timeout_seconds,
    )
    write_json_report(Path(args.out), report)
    write_markdown_report(Path(args.markdown), report)
    print(json.dumps({"probe_status": report["probe_status"], "stage_count": len(report["stage_results"])}))
    return 0 if report["probe_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
