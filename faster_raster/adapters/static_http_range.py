from __future__ import annotations

import hashlib
import json
import os
import string
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from faster_raster.content_magic import detect_content_magic

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WAVE1_CONFIG = PROJECT_ROOT / "configs/static_http_range_wave1.yaml"
DEFAULT_REPORT_DIR = Path(os.environ.get("FASTERRASTER_REPORT_ROOT", "reports")) / "static_http_range"
DEFAULT_MAX_BYTES = 65_536
DEFAULT_TIMEOUT_SECONDS = 20
WAVE1_SOURCE_IDS = {
    "prism_daily_ppt_static_zip",
    "chirps_daily_precipitation",
    "gridmet_daily",
    "terraclimate_monthly",
    "worldclim_bioclim_normals",
}
RUNNABLE_CLASSIFICATION = "runnable"
FIXTURE_CLASSIFICATION = "fixture_only"


def portable_project_path(path: Path | str) -> str:
    candidate = Path(path)
    try:
        return candidate.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return str(candidate)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _classified(spec: dict[str, Any], classification: str) -> dict[str, Any]:
    item = dict(spec)
    item["classification"] = classification
    return item


def load_wave1_specs(path: Path | str = DEFAULT_WAVE1_CONFIG, *, include_fixtures: bool = True) -> list[dict[str, Any]]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"static range config must contain runnable_sources and contract_fixtures: {path}")
    if "sources" in data:
        sources = data.get("sources")
        if not isinstance(sources, list):
            raise ValueError(f"static range config sources must be a list: {path}")
        return [_classified(spec, RUNNABLE_CLASSIFICATION) for spec in sources]
    runnable = data.get("runnable_sources")
    fixtures = data.get("contract_fixtures", [])
    if not isinstance(runnable, list) or not isinstance(fixtures, list):
        raise ValueError(f"static range config must contain runnable_sources and contract_fixtures lists: {path}")
    specs = [_classified(spec, RUNNABLE_CLASSIFICATION) for spec in runnable]
    if include_fixtures:
        specs.extend(_classified(spec, FIXTURE_CLASSIFICATION) for spec in fixtures)
    return specs


def load_runnable_specs(path: Path | str = DEFAULT_WAVE1_CONFIG) -> list[dict[str, Any]]:
    return [spec for spec in load_wave1_specs(path, include_fixtures=False) if spec.get("classification") == RUNNABLE_CLASSIFICATION]


def load_fixture_specs(path: Path | str = DEFAULT_WAVE1_CONFIG) -> list[dict[str, Any]]:
    return [spec for spec in load_wave1_specs(path, include_fixtures=True) if spec.get("classification") == FIXTURE_CLASSIFICATION]


def template_variables(template: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(template) if field}


def params_from_defaults(spec: dict[str, Any], params: dict[str, Any] | None = None) -> dict[str, Any]:
    values = dict(spec.get("default_params") or {})
    values.update(params or {})
    return values


def render_static_url(spec: dict[str, Any], params: dict[str, Any] | None = None) -> str:
    values = params_from_defaults(spec, params)
    required = set(spec.get("required_params") or template_variables(spec["url_template"]))
    missing = sorted(name for name in required if values.get(name) in (None, ""))
    if missing:
        raise ValueError(f"missing required URL parameter(s) for {spec['source_id']}: {missing}")
    return spec["url_template"].format(**values)


def build_range_headers(max_bytes: int) -> dict[str, str]:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    return {"Range": f"bytes=0-{max_bytes - 1}"}


def _redact_url(url: str) -> str:
    return url.split("?", 1)[0] if "?" in url else url


def _expected_values(value: Any) -> set[str]:
    if isinstance(value, list):
        return {str(item) for item in value}
    if value is None:
        return set()
    return {str(value)}


def _base_row(spec: dict[str, Any], *, url: str | None, allow_network: bool, max_bytes: int, generated_at_utc: str) -> dict[str, Any]:
    return {
        "source_id": spec["source_id"],
        "source_label": spec.get("source_label", spec["source_id"]),
        "classification": spec.get("classification", RUNNABLE_CLASSIFICATION),
        "url_redacted": _redact_url(url or spec.get("url_template", "")),
        "network_run": bool(allow_network),
        "dry_run": not allow_network,
        "attempted": False,
        "http_status": None,
        "content_type": None,
        "bytes_read": 0,
        "sha256": None,
        "sha256_short": None,
        "range_requested": True,
        "range_honored": False,
        "expected_magic": spec.get("expected_magic"),
        "detected_magic": None,
        "expected_content_family": spec.get("expected_content_family"),
        "detected_content_family": None,
        "required_params": spec.get("required_params", []),
        "default_params": spec.get("default_params", {}),
        "url_template": spec.get("url_template"),
        "status": "skipped_dry_run" if not allow_network else "skipped_requires_network",
        "quality": "planned" if not allow_network else "not_run",
        "warning": None,
        "error": None,
        "max_bytes": max_bytes,
        "generated_at_utc": generated_at_utc,
    }


def fixture_result_row(spec: dict[str, Any], *, max_bytes: int | None = None, generated_at_utc: str | None = None) -> dict[str, Any]:
    limit = int(max_bytes or spec.get("max_bytes") or DEFAULT_MAX_BYTES)
    return {
        "source_id": spec["source_id"],
        "source_label": spec.get("source_label", spec["source_id"]),
        "classification": FIXTURE_CLASSIFICATION,
        "url_redacted": spec.get("unresolved_url_template_metadata") or "",
        "network_run": False,
        "dry_run": True,
        "attempted": False,
        "http_status": spec.get("historical_http_status"),
        "content_type": spec.get("historical_content_type"),
        "bytes_read": spec.get("historical_bytes_read", 0),
        "sha256": None,
        "sha256_short": spec.get("historical_sha256_short"),
        "range_requested": True,
        "range_honored": spec.get("historical_http_status") == 206,
        "expected_magic": spec.get("expected_magic"),
        "detected_magic": spec.get("historical_detected_magic"),
        "expected_content_family": spec.get("expected_content_family"),
        "detected_content_family": spec.get("historical_detected_magic"),
        "required_params": spec.get("required_params", []),
        "default_params": spec.get("default_params", {}),
        "url_template": None,
        "status": "fixture_only",
        "quality": "contract_fixture",
        "warning": "Historical bounded ZIP evidence preserved; current deterministic endpoint unresolved.",
        "error": None,
        "max_bytes": limit,
        "generated_at_utc": generated_at_utc or utc_now(),
        "execution_status": spec.get("execution_status"),
        "live_probe_enabled": spec.get("live_probe_enabled", False),
        "historical_http_status": spec.get("historical_http_status"),
        "historical_bytes_read": spec.get("historical_bytes_read"),
        "historical_content_type": spec.get("historical_content_type"),
        "historical_detected_magic": spec.get("historical_detected_magic"),
        "historical_sha256_short": spec.get("historical_sha256_short"),
        "historical_observed_at_utc": spec.get("historical_observed_at_utc"),
        "current_endpoint_status": spec.get("current_endpoint_status"),
        "promotion_status": spec.get("promotion_status"),
        "reason": spec.get("reason"),
        "next_unlock": spec.get("next_unlock"),
    }


def _classify_success(row: dict[str, Any], expected_magic: set[str], expected_family: set[str], content_length: str | None) -> None:
    magic_ok = not expected_magic or row["detected_magic"] in expected_magic
    family_ok = not expected_family or row["detected_content_family"] in expected_family
    if not magic_ok or not family_ok:
        row["status"] = "fail_magic"
        row["quality"] = "failed"
        row["error"] = f"expected magic={sorted(expected_magic)} family={sorted(expected_family)}"
        return
    truncated = False
    if content_length and content_length.isdigit():
        truncated = int(content_length) > row["bytes_read"]
    if row["bytes_read"] >= row["max_bytes"] and row["range_honored"]:
        truncated = True
    if truncated:
        row["status"] = "pass_bounded_truncated"
        row["quality"] = "candidate"
    elif row["range_honored"]:
        row["status"] = "pass_range_limited"
        row["quality"] = "candidate"
    else:
        row["status"] = "pass_verified"
        row["quality"] = "ready_for_fixture"


def probe_static_http_range(
    spec: dict[str, Any],
    params: dict[str, Any] | None = None,
    *,
    allow_network: bool,
    max_bytes: int | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    generated_at_utc = utc_now()
    limit = int(max_bytes or spec.get("max_bytes") or DEFAULT_MAX_BYTES)
    if spec.get("classification") == FIXTURE_CLASSIFICATION or spec.get("execution_status") == "fixture_only" or spec.get("live_probe_enabled") is False:
        return fixture_result_row(spec, max_bytes=limit, generated_at_utc=generated_at_utc)
    try:
        url = render_static_url(spec, params)
    except ValueError as exc:
        row = _base_row(spec, url=None, allow_network=allow_network, max_bytes=limit, generated_at_utc=generated_at_utc)
        row.update({"status": "fail_policy", "quality": "failed", "error": str(exc)})
        return row
    row = _base_row(spec, url=url, allow_network=allow_network, max_bytes=limit, generated_at_utc=generated_at_utc)
    if not allow_network:
        row["status"] = "skipped_dry_run"
        row["quality"] = "planned"
        return row

    request = urllib.request.Request(url, headers=build_range_headers(limit), method="GET")
    row["attempted"] = True
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = getattr(response, "status", None) or response.getcode()
            headers = response.headers
            content_type = headers.get("Content-Type")
            content_range = headers.get("Content-Range")
            content_length = headers.get("Content-Length")
            data = response.read(limit)
    except urllib.error.HTTPError as exc:
        row.update({
            "http_status": exc.code,
            "content_type": exc.headers.get("Content-Type") if exc.headers else None,
            "status": "fail_http",
            "quality": "failed",
            "error": str(exc),
        })
        return row
    except Exception as exc:
        row.update({"status": "fail_http", "quality": "failed", "error": str(exc)})
        return row

    row["http_status"] = int(status) if status is not None else None
    row["content_type"] = content_type
    row["bytes_read"] = len(data)
    digest = hashlib.sha256(data).hexdigest()
    row["sha256"] = digest
    row["sha256_short"] = digest[:12]
    row["range_honored"] = row["http_status"] == 206 or bool(content_range)
    magic = detect_content_magic(data, content_type)
    row["detected_magic"] = magic.magic
    row["detected_content_family"] = magic.content_family
    if row["http_status"] is None or row["http_status"] >= 400:
        row["status"] = "fail_http"
        row["quality"] = "failed"
        row["error"] = f"HTTP status {row['http_status']}"
        return row
    _classify_success(row, _expected_values(spec.get("expected_magic")), _expected_values(spec.get("expected_content_family")), content_length)
    if not row["range_honored"]:
        row["warning"] = "server did not explicitly honor Range"
    return row


def _select_specs(specs: list[dict[str, Any]], source_ids: list[str] | None = None) -> list[dict[str, Any]]:
    if not source_ids:
        return specs
    wanted = set(source_ids)
    found = [spec for spec in specs if spec["source_id"] in wanted]
    missing = sorted(wanted - {spec["source_id"] for spec in found})
    if missing:
        raise ValueError(f"unknown static range source_id(s): {missing}")
    return found


def summarize_static_range(results: list[dict[str, Any]], fixtures: list[dict[str, Any]], *, allow_network: bool) -> dict[str, Any]:
    attempted = [row for row in results if row.get("attempted")]
    network_run = any(row.get("network_run") for row in results)
    pass_count = sum(1 for row in results if str(row.get("status", "")).startswith("pass_"))
    fail_count = sum(1 for row in results if str(row.get("status", "")).startswith("fail_"))
    if allow_network and results and fail_count == 0 and pass_count == len(results):
        decision = "wave1_adapter_live_validated"
    elif fail_count:
        decision = "endpoint_failed" if any(row["status"] == "fail_http" for row in results) else "needs_magic_fix"
    elif pass_count:
        decision = "ready_for_fixture"
    else:
        decision = "not_promoted"
    return {
        "runnable_source_count": len(results),
        "fixture_source_count": len(fixtures),
        "attempted_source_count": len(attempted),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "fixture_count": len(fixtures),
        "network_run": network_run,
        "decision": decision,
    }


def build_static_range_payload(results: list[dict[str, Any]], fixtures: list[dict[str, Any]], *, allow_network: bool, artifacts: dict[str, str] | None = None) -> dict[str, Any]:
    payload = {
        "results": results,
        "fixtures": fixtures,
        **summarize_static_range(results, fixtures, allow_network=allow_network),
    }
    if artifacts is not None:
        payload["artifacts"] = artifacts
    return payload


def probe_wave1_sources(
    task: dict[str, Any] | None = None,
    source_ids: list[str] | None = None,
    *,
    allow_network: bool = False,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    config_path: Path | str = DEFAULT_WAVE1_CONFIG,
) -> dict[str, Any]:
    specs = load_wave1_specs(config_path)
    if task is not None and source_ids is None:
        source_ids = [source_id for source_id in task.get("sources", []) if source_id in WAVE1_SOURCE_IDS]
    selected = _select_specs(specs, source_ids)
    results = []
    fixtures = []
    for spec in selected:
        row = probe_static_http_range(spec, allow_network=allow_network, max_bytes=max_bytes, timeout_seconds=timeout_seconds)
        if row["status"] == "fixture_only":
            fixtures.append(row)
        else:
            results.append(row)
    return build_static_range_payload(results, fixtures, allow_network=allow_network)


def source_plan_rows(config_path: Path | str = DEFAULT_WAVE1_CONFIG) -> list[dict[str, Any]]:
    rows = []
    for spec in load_wave1_specs(config_path):
        rows.append({
            "source_id": spec["source_id"],
            "source_label": spec["source_label"],
            "classification": spec.get("classification", RUNNABLE_CLASSIFICATION),
            "expected_magic": spec.get("expected_magic"),
            "expected_content_family": spec.get("expected_content_family"),
            "required_params": spec.get("required_params", []),
            "default_params": spec.get("default_params", {}),
            "max_bytes": spec.get("max_bytes"),
            "url_template": spec.get("url_template"),
            "current_endpoint_status": spec.get("current_endpoint_status"),
            "promotion_status": spec.get("promotion_status"),
        })
    return rows


def write_static_range_report(payload_or_results: dict[str, Any] | list[dict[str, Any]], out_json: Path | str, out_md: Path | str) -> dict[str, str]:
    out_json = Path(out_json)
    out_md = Path(out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload_or_results, dict):
        payload = dict(payload_or_results)
    else:
        payload = build_static_range_payload(payload_or_results, [], allow_network=any(row.get("network_run") for row in payload_or_results))
    results = payload.get("results", [])
    fixtures = payload.get("fixtures", [])
    runnable_count = payload.get("runnable_source_count", len(results))
    runnable_label = "source" if runnable_count == 1 else "sources"
    fixture_count = payload.get("fixture_source_count", len(fixtures))
    out_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    title = "Static HTTP Range Plan" if all(row["dry_run"] for row in results) else "Static HTTP Range Results"
    lines = [
        f"# {title}",
        "",
        f"runnable_source_count: {payload.get('runnable_source_count', len(results))}",
        f"fixture_source_count: {payload.get('fixture_source_count', len(fixtures))}",
        f"attempted_source_count: {payload.get('attempted_source_count', 0)}",
        f"pass_count: {payload.get('pass_count', 0)}",
        f"fail_count: {payload.get('fail_count', 0)}",
        f"fixture_count: {payload.get('fixture_count', len(fixtures))}",
        f"network_run: {payload.get('network_run', False)}",
        f"decision: {payload.get('decision', 'not_promoted')}",
        "",
        (
            f"Live validation passed for {runnable_count} selected runnable {runnable_label}. "
            f"Contract fixtures reported separately: {fixture_count}."
            if payload.get("decision") == "wave1_adapter_live_validated"
            else (
                f"The static_http_range adapter evaluated {runnable_count} selected runnable {runnable_label}. "
                f"Contract fixtures: {fixture_count}. "
                f"Decision: {payload.get('decision', 'not_promoted')}."
            )
        ),
        "",
        "| Source | Status | HTTP | Bytes | Magic | Family | Quality |",
        "| --- | --- | ---: | ---: | --- | --- | --- |",
    ]
    for row in results:
        lines.append(
            f"| `{row['source_id']}` | `{row['status']}` | `{row['http_status']}` | `{row['bytes_read']}` | "
            f"`{row['detected_magic'] or row['expected_magic']}` | `{row['detected_content_family'] or row['expected_content_family']}` | `{row['quality']}` |"
        )
    if fixtures:
        lines.extend(["", "## Contract Fixtures", "", "| Source | Status | Historical evidence | Current endpoint |", "| --- | --- | --- | --- |"])
        for row in fixtures:
            evidence = f"{row.get('historical_content_type')} / {row.get('historical_detected_magic')} / {row.get('historical_sha256_short')}"
            lines.append(f"| `{row['source_id']}` | `{row['status']}` | `{evidence}` | `{row.get('current_endpoint_status')}` |")
    if all(row["dry_run"] for row in results):
        lines.extend(["", "## Dry-Run Source Plan", "", "| Source | Expected magic | Expected family | Required params | Default params | URL/template |", "| --- | --- | --- | --- | --- | --- |"])
        for row in results:
            lines.append(
                f"| `{row['source_id']}` | `{row['expected_magic']}` | `{row['expected_content_family']}` | "
                f"`{row.get('required_params', [])}` | `{row.get('default_params', {})}` | `{row.get('url_template') or row['url_redacted']}` |"
            )
    lines.extend(["", "## Content Families", "", "| Source | Expected | Detected |", "| --- | --- | --- |"])
    for row in results:
        lines.append(f"| `{row['source_id']}` | `{row['expected_content_family']}` | `{row['detected_content_family']}` |")
    lines.extend(["", "## Magic Validation", "", "| Source | Expected | Detected |", "| --- | --- | --- |"])
    for row in results:
        lines.append(f"| `{row['source_id']}` | `{row['expected_magic']}` | `{row['detected_magic']}` |")
    ready = [row["source_id"] for row in results if row["status"].startswith("pass_")]
    failures = [row for row in results if row["status"].startswith("fail_")]
    lines.extend(["", "## Strongest Candidates", ""])
    lines.extend(f"- `{source_id}`" for source_id in ready[:10])
    if not ready:
        lines.append("- None")
    lines.extend(["", "## Failures/Cautions", ""])
    cautions = failures + [row for row in results if row.get("warning")]
    lines.extend(f"- `{row['source_id']}`: {row.get('error') or row.get('warning')}" for row in cautions)
    if not cautions:
        lines.append("- None")
    decision = payload.get("decision", "not_promoted")
    lines.extend(["", "## Decision", "", f"`{decision}`", "", "## Next Live Command", "", "```bash", "faster-raster range wave1 --allow-network --max-bytes 65536 --plain", "```"])
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(out_json), "md": str(out_md)}


def static_range_availability(source_ids: list[str]) -> dict[str, Any]:
    runnable_ids = {spec["source_id"] for spec in load_runnable_specs()}
    fixture_ids = {spec["source_id"] for spec in load_fixture_specs()}
    available = sorted(source_id for source_id in source_ids if source_id in runnable_ids)
    fixtures = sorted(source_id for source_id in source_ids if source_id in fixture_ids)
    missing = sorted(source_id for source_id in source_ids if source_id not in runnable_ids and source_id not in fixture_ids)
    return {
        "static_range_adapter_available": bool(available),
        "static_range_wave1_available_sources": available,
        "static_range_wave1_fixture_sources": fixtures,
        "static_range_wave1_missing_sources": missing,
    }
