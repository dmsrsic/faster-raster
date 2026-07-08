from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import yaml

SECRET_RE = re.compile(r"(?i)(token|password|secret|apikey|api_key|bearer)[:=][A-Za-z0-9_./+=-]{8,}")
CREDENTIALS = {"bearer_token", "earthdata_login", "copernicus_token", "usgs_m2m_token", "aws_requester_pays", "cookie_session", "unknown"}
MIRROR_PROVENANCE = {"institutional_mirror", "community_mirror", "rescued_archive_unverified"}
REQUIRED_FIELDS = [
    "source_id", "display_name", "provider", "source_family", "source_kind", "access_mode", "access_pattern_category",
    "provenance_class", "trust_level", "endpoint_or_catalog_url", "query_pattern", "deterministic_url_generation_direct",
    "deterministic_asset_resolution_after_discovery", "bounded_probe_appropriate", "credential_profile_id", "credential_requirement",
    "auth_not_implemented_reason", "expected_formats", "native_crs_or_grid", "target_crs_assumption", "temporal_key_structure",
    "spatial_key_structure", "nodata_metadata", "checksum_metadata", "rate_limit_caveats", "license_or_terms_url", "official_docs",
    "promotion_status", "last_probe_report", "notes",
]


def walk_values(value: Any, prefix: str = ""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from walk_values(item, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_values(item, f"{prefix}[{index}]")
    else:
        yield prefix, value


def load_atlas(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        raise ValueError("source atlas must contain sources list")
    return data


def lint_atlas(atlas: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, entry in enumerate(atlas.get("sources", [])):
        sid = entry.get("source_id", f"index_{index}")
        if sid in seen:
            findings.append({"source_id": sid, "severity": "error", "message": "duplicate source_id"})
        seen.add(sid)
        for field in REQUIRED_FIELDS:
            if field not in entry:
                findings.append({"source_id": sid, "severity": "error", "message": f"missing required field: {field}"})
        if entry.get("credential_requirement") in CREDENTIALS and not entry.get("credential_profile_id"):
            # unknown may be intentionally no profile only when research-only and auth reason exists
            if not (entry.get("credential_requirement") == "unknown" and entry.get("auth_not_implemented_reason")):
                findings.append({"source_id": sid, "severity": "error", "message": "credentialed source missing credential_profile_id"})
        if entry.get("provenance_class") in MIRROR_PROVENANCE and entry.get("trust_level") == "verified_live" and not entry.get("last_probe_report"):
            findings.append({"source_id": sid, "severity": "error", "message": "mirror/rescued verified_live requires last_probe_report"})
        if entry.get("deterministic_url_generation_direct") is True and not entry.get("endpoint_or_catalog_url"):
            findings.append({"source_id": sid, "severity": "error", "message": "direct URL generation requires endpoint_or_catalog_url"})
        if entry.get("promotion_status") == "runtime_supported" and entry.get("access_pattern_category") == "future_unverified":
            findings.append({"source_id": sid, "severity": "error", "message": "future_unverified source cannot be runtime_supported"})
        for path, value in walk_values(entry):
            if isinstance(value, str) and SECRET_RE.search(value):
                findings.append({"source_id": sid, "severity": "error", "message": f"raw secret-like string at {path}"})
    return findings


def summarize(atlas: dict[str, Any], findings: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "status": "PASS" if not findings else "FAIL",
        "source_count": len(atlas.get("sources", [])),
        "finding_count": len(findings),
        "findings": findings,
    }


def write_reports(summary: dict[str, Any], out: Path, markdown: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    markdown.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = ["# Source Atlas Lint", "", f"- Status: `{summary['status']}`", f"- Source count: `{summary['source_count']}`", f"- Findings: `{summary['finding_count']}`", ""]
    if summary["findings"]:
        lines.extend(["| Source | Severity | Message |", "| --- | --- | --- |"])
        for finding in summary["findings"]:
            lines.append(f"| `{finding['source_id']}` | `{finding['severity']}` | {finding['message']} |")
    markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--atlas", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--markdown", required=True)
    args = parser.parse_args()
    atlas = load_atlas(Path(args.atlas))
    summary = summarize(atlas, lint_atlas(atlas))
    write_reports(summary, Path(args.out), Path(args.markdown))
    print(json.dumps({"status": summary["status"], "source_count": summary["source_count"], "finding_count": summary["finding_count"]}, sort_keys=True))
    return 0 if summary["status"] == "PASS" else 1

if __name__ == "__main__":
    raise SystemExit(main())
