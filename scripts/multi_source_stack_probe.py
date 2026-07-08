from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml

RESULT_CLASSES = {
    "pass_verified",
    "fail_http",
    "fail_endpoint",
    "credential_gated",
    "adapter_needed",
    "skipped_policy",
    "existing_result_reused",
    "unsupported_temporal_key",
    "unsupported_spatial_key",
}

DEFAULT_SPEC = "research/multi_source_stack_probe_spec.yaml"
DEFAULT_OUT = "reports/multi_source_stack_probe.json"
DEFAULT_MARKDOWN = "reports/multi_source_stack_probe.md"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run bounded FasterRaster multi-source stack probe.")
    parser.add_argument("--allow-network", action="store_true", help="Required when spec contains live_bounded_probe sources.")
    parser.add_argument("--spec", default=DEFAULT_SPEC)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--markdown", default=DEFAULT_MARKDOWN)
    parser.add_argument("--timeout-seconds", type=int, default=20)
    return parser.parse_args()


def load_spec(path: Path) -> dict[str, Any]:
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise ValueError("stack probe spec must be a YAML mapping")
    return spec


def safe_headers(headers: Any) -> dict[str, str | None]:
    lowered = {str(key).lower(): value for key, value in headers.items()}
    return {
        "content_type": lowered.get("content-type"),
        "content_length": lowered.get("content-length"),
        "accept_ranges": lowered.get("accept-ranges"),
    }


def read_bounded_response(response: Any, *, max_bytes: int, start: float) -> dict[str, Any]:
    body = bytearray()
    first_byte_seconds: float | None = None
    while len(body) < max_bytes:
        chunk = response.read(min(16_384, max_bytes - len(body)))
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


def live_probe(source: dict[str, Any], *, timeout_seconds: int, opener: Callable[..., Any] = urlopen) -> dict[str, Any]:
    url = source.get("endpoint_or_url")
    if not url:
        return base_result(source, "fail_endpoint", error="missing endpoint_or_url")
    max_bytes = int(source.get("max_bytes") or 65_536)
    start = time.perf_counter()
    status_code: int | None = None
    headers: dict[str, str | None] = {}
    error: str | None = None
    read_result = {"bytes_read": 0, "truncated": False, "sha256": None, "first_byte_seconds": None}
    request = Request(
        url,
        headers={
            "User-Agent": "FasterRaster-multi-source-stack-probe/0.1",
            "Range": f"bytes=0-{max_bytes - 1}",
        },
    )
    try:
        with opener(request, timeout=timeout_seconds) as response:
            status_code = response.status
            headers = safe_headers(response.headers)
            read_result = read_bounded_response(response, max_bytes=max_bytes, start=start)
    except HTTPError as exc:
        status_code = exc.code
        headers = safe_headers(exc.headers)
        error = f"HTTPError: {exc.code} {exc.reason}"
        read_result = read_bounded_response(exc, max_bytes=max_bytes, start=start)
    except URLError as exc:
        error = f"URLError: {exc.reason}"
    except TimeoutError as exc:
        error = f"TimeoutError: {exc}"
    elapsed = time.perf_counter() - start
    expected = set(source.get("expected_success_status") or [200, 206])
    result_class = "pass_verified" if status_code in expected and read_result["bytes_read"] > 0 and error is None else "fail_http"
    result = base_result(source, result_class, error=error)
    result.update(
        {
            "http_status": status_code,
            "content_type": headers.get("content_type"),
            "content_length": headers.get("content_length"),
            "accept_ranges": headers.get("accept_ranges"),
            "bytes_read": read_result["bytes_read"],
            "truncated": read_result["truncated"],
            "sha256": read_result["sha256"],
            "elapsed_seconds": round(elapsed, 6),
            "first_byte_seconds": read_result["first_byte_seconds"],
        }
    )
    return result


def base_result(source: dict[str, Any], result_class: str, *, error: str | None = None) -> dict[str, Any]:
    if result_class not in RESULT_CLASSES:
        raise ValueError(f"unknown result class: {result_class}")
    return {
        "source_id": source["source_id"],
        "label": source.get("label"),
        "probe_type": source.get("probe_type"),
        "access_mode": source.get("access_mode"),
        "target_variable": source.get("target_variable"),
        "temporal_key": source.get("temporal_key"),
        "spatial_key": source.get("spatial_key"),
        "endpoint_or_url": source.get("endpoint_or_url"),
        "result_class": result_class,
        "skip_reason": source.get("skip_reason"),
        "error": error,
        "http_status": None,
        "content_type": None,
        "content_length": None,
        "accept_ranges": None,
        "bytes_read": 0,
        "truncated": False,
        "sha256": None,
        "elapsed_seconds": 0.0,
        "first_byte_seconds": None,
    }


def existing_result(source: dict[str, Any], root: Path) -> dict[str, Any]:
    report_path = root / source.get("existing_report", "")
    result = base_result(source, "existing_result_reused")
    if not report_path.exists():
        result["result_class"] = source.get("failure_gate_classification", "fail_endpoint")
        result["error"] = f"existing report not found: {report_path}"
        return result
    report = json.loads(report_path.read_text(encoding="utf-8"))
    result["reused_report"] = str(report_path)
    # Daymet NCSS report: retain bytes from all access-surface probes but classify gate.
    result["bytes_read"] = sum(item.get("bytes_read", 0) for item in report.get("results", []))
    result["result_class"] = source.get("failure_gate_classification", "existing_result_reused")
    result["existing_summary"] = report.get("summary")
    return result


def classify_without_probe(source: dict[str, Any]) -> dict[str, Any]:
    return base_result(source, source.get("failure_gate_classification", "skipped_policy"))


def run_stack(spec: dict[str, Any], *, allow_network: bool, timeout_seconds: int, root: Path, opener: Callable[..., Any] = urlopen) -> dict[str, Any]:
    sources = spec.get("sources", [])
    live_sources = [source for source in sources if source.get("probe_type") == "live_bounded_probe"]
    if live_sources and not allow_network:
        raise SystemExit("Refusing to run live stack probes without --allow-network.")
    results = []
    for source in sources:
        probe_type = source.get("probe_type")
        try:
            if probe_type == "live_bounded_probe":
                results.append(live_probe(source, timeout_seconds=timeout_seconds, opener=opener))
            elif probe_type == "existing_result_only":
                results.append(existing_result(source, root))
            elif probe_type == "classify_without_probe":
                results.append(classify_without_probe(source))
            else:
                results.append(base_result(source, "skipped_policy", error=f"unsupported probe_type: {probe_type}"))
        except Exception as exc:  # source failures are recorded, not global failures
            results.append(base_result(source, "fail_endpoint", error=f"{type(exc).__name__}: {exc}"))
    return build_report(spec, results)


def build_report(spec: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {name: sum(1 for result in results if result["result_class"] == name) for name in RESULT_CLASSES}
    live_results = [result for result in results if result["probe_type"] == "live_bounded_probe"]
    pass_results = [result for result in results if result["result_class"] == "pass_verified"]
    next_sources = [result["source_id"] for result in results if result["result_class"] in {"credential_gated", "adapter_needed"}]
    return {
        "stack_status": "COMPLETED",
        "timestamp_utc": utc_now_iso(),
        "target": spec["target"],
        "candidate_source_count": len(results),
        "live_probe_count": len(live_results),
        "pass_verified_count": counts["pass_verified"],
        "existing_result_reused_count": counts["existing_result_reused"],
        "credential_gated_count": counts["credential_gated"],
        "adapter_needed_count": counts["adapter_needed"],
        "skipped_count": counts["skipped_policy"],
        "fail_count": counts["fail_http"] + counts["fail_endpoint"],
        "total_bytes_read_live": sum(result["bytes_read"] for result in live_results),
        "total_bytes_counting_reused": sum(result["bytes_read"] for result in results),
        "highest_verified_stack": {
            "count": len(pass_results),
            "source_ids": [result["source_id"] for result in pass_results],
        },
        "next_sources_to_unlock": next_sources,
        "source_results": results,
    }


def write_reports(report: dict[str, Any], out: Path, markdown: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    markdown.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Multi-Source Stack Probe",
        "",
        f"- Stack status: `{report['stack_status']}`",
        f"- Candidate sources: `{report['candidate_source_count']}`",
        f"- Live probes: `{report['live_probe_count']}`",
        f"- Pass verified: `{report['pass_verified_count']}`",
        f"- Credential gated: `{report['credential_gated_count']}`",
        f"- Adapter needed: `{report['adapter_needed_count']}`",
        f"- Total live bytes: `{report['total_bytes_read_live']}`",
        f"- Total bytes including reused: `{report['total_bytes_counting_reused']}`",
        "",
        "| Source | Result | Probe Type | HTTP | Bytes | Content-Type | Note |",
        "| --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for result in report["source_results"]:
        note = result.get("error") or result.get("skip_reason") or ""
        lines.append(
            f"| `{result['source_id']}` | `{result['result_class']}` | `{result['probe_type']}` | "
            f"{result['http_status']} | {result['bytes_read']} | {result.get('content_type') or ''} | {note} |"
        )
    markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run FasterRaster bounded multi-source stack probe.")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--spec", default=DEFAULT_SPEC)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--markdown", default=DEFAULT_MARKDOWN)
    parser.add_argument("--timeout-seconds", type=int, default=20)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parent.parent
    spec = load_spec(root / args.spec)
    report = run_stack(spec, allow_network=args.allow_network, timeout_seconds=args.timeout_seconds, root=root)
    write_reports(report, root / args.out, root / args.markdown)
    print(json.dumps({
        "stack_status": report["stack_status"],
        "candidate_source_count": report["candidate_source_count"],
        "pass_verified_count": report["pass_verified_count"],
        "fail_count": report["fail_count"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
