from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from faster_raster.probe_core import probe_http, skipped_result, stable_json

SAFE_PROMOTION_STATUSES = {"experimental_probe_supported", "research_only"}
SAFE_ACCESS_CATEGORIES = {"static_verified", "service_discovered", "api_discovered", "future_unverified"}
UNKNOWN_ENDPOINT_VALUES = {None, "", "unknown", "needs_official_verification"}


def load_atlas(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        raise ValueError("atlas must contain sources list")
    return data


def find_source(atlas: dict[str, Any], source_id: str) -> dict[str, Any]:
    for source in atlas["sources"]:
        if source.get("source_id") == source_id:
            return source
    raise ValueError(f"source_id not found in atlas: {source_id}")


def policy_check(source: dict[str, Any]) -> str | None:
    if source.get("credential_requirement") != "none":
        return f"credential_requirement is not none: {source.get('credential_requirement')}"
    if source.get("bounded_probe_appropriate") is not True:
        return "bounded_probe_appropriate is false"
    if source.get("endpoint_or_catalog_url") in UNKNOWN_ENDPOINT_VALUES:
        return "endpoint_or_catalog_url is missing or unknown"
    if source.get("promotion_status") not in SAFE_PROMOTION_STATUSES:
        return f"promotion_status is not probe-safe: {source.get('promotion_status')}"
    if source.get("access_pattern_category") not in SAFE_ACCESS_CATEGORIES:
        return f"access_pattern_category is not probe-safe: {source.get('access_pattern_category')}"
    return None


def run_atlas_probe(
    atlas: dict[str, Any],
    *,
    source_id: str,
    allow_network: bool,
    max_bytes: int,
    timeout_seconds: int = 20,
    opener=None,
) -> dict[str, Any]:
    source = find_source(atlas, source_id)
    reason = policy_check(source)
    if reason:
        core = skipped_result(reason=reason, result_class="skipped_policy", url=source.get("endpoint_or_catalog_url"))
    else:
        kwargs = {
            "url": source["endpoint_or_catalog_url"],
            "allow_network": allow_network,
            "max_bytes": max_bytes,
            "timeout_seconds": timeout_seconds,
            "metadata_probe": True,
        }
        if opener is not None:
            kwargs["opener"] = opener
        core = probe_http(**kwargs)
    return {
        "source_id": source_id,
        "display_name": source.get("display_name"),
        "provider": source.get("provider"),
        "access_mode": source.get("access_mode"),
        "access_pattern_category": source.get("access_pattern_category"),
        "promotion_status": source.get("promotion_status"),
        "credential_requirement": source.get("credential_requirement"),
        "bounded_probe_appropriate": source.get("bounded_probe_appropriate"),
        "probe": core,
    }


def write_reports(report: dict[str, Any], out: Path, markdown: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    markdown.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(stable_json(report), encoding="utf-8")
    probe = report["probe"]
    lines = [
        "# Atlas Source Probe",
        "",
        f"- Source: `{report['source_id']}`",
        f"- Result: `{probe['result_class']}`",
        f"- HTTP status: `{probe['http_status']}`",
        f"- Bytes read: `{probe['bytes_read']}`",
        f"- Content-Type: `{probe['content_type']}`",
        f"- Error: `{probe['error']}`",
        f"- URL: `{probe['url']}`",
        "",
    ]
    markdown.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an opt-in bounded probe for a source atlas entry.")
    parser.add_argument("--atlas", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--max-bytes", type=int, default=65_536)
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument("--out")
    parser.add_argument("--markdown")
    args = parser.parse_args()
    report = run_atlas_probe(
        load_atlas(Path(args.atlas)),
        source_id=args.source_id,
        allow_network=args.allow_network,
        max_bytes=args.max_bytes,
        timeout_seconds=args.timeout_seconds,
    )
    out = Path(args.out) if args.out else Path(f"reports/atlas_probe_{args.source_id}.json")
    markdown = Path(args.markdown) if args.markdown else Path(f"reports/atlas_probe_{args.source_id}.md")
    write_reports(report, out, markdown)
    print(json.dumps({"source_id": args.source_id, "result_class": report["probe"]["result_class"]}, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
