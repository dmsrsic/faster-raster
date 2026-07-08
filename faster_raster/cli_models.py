from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from faster_raster import __version__
from faster_raster.auth_profiles import load_auth_profiles, redact_auth_profile, validate_auth_profiles

DEFAULT_ATLAS = Path("research/source_atlas_v0_4.yaml")
DEFAULT_STACK = Path("reports/multi_source_stack_probe.json")
DEFAULT_MATRIX = Path("reports/source_stack_matrix.json")
DEFAULT_UNLOCKS = Path("reports/source_unlock_plan.json")
DEFAULT_AUTH = Path("configs/auth_profiles.example.yaml")

STATUS_LABELS = {
    "pass_verified": "verified_now",
    "existing_result_reused": "reused_existing_result",
    "credential_gated": "credential_gated",
    "adapter_needed": "adapter_needed",
    "mirror_candidate": "mirror_candidate",
    "future_unverified": "future_unverified",
    "blocked": "blocked",
    "fail_http": "failed_probe",
    "fail_endpoint": "failed_probe",
    "skipped_policy": "skipped_policy",
    "not_in_stack_probe": "future_unverified",
}

@dataclass(frozen=True)
class CliPaths:
    atlas: Path = DEFAULT_ATLAS
    stack: Path = DEFAULT_STACK
    matrix: Path = DEFAULT_MATRIX
    unlocks: Path = DEFAULT_UNLOCKS
    auth: Path = DEFAULT_AUTH


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"expected file is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"expected file is missing: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected YAML mapping: {path}")
    return data


def load_atlas(path: Path = DEFAULT_ATLAS) -> dict[str, Any]:
    data = read_yaml(path)
    if not isinstance(data.get("sources"), list):
        raise ValueError("atlas must contain sources list")
    return data


def load_sources(path: Path = DEFAULT_ATLAS) -> list[dict[str, Any]]:
    return load_atlas(path)["sources"]


def load_stack(path: Path = DEFAULT_STACK) -> dict[str, Any]:
    return read_json(path)


def load_matrix(path: Path = DEFAULT_MATRIX) -> list[dict[str, Any]]:
    data = read_json(path)
    return data.get("rows", [])


def load_unlocks(path: Path = DEFAULT_UNLOCKS) -> list[dict[str, Any]]:
    data = read_json(path)
    return data.get("unlock_plan", [])


def load_auth(path: Path = DEFAULT_AUTH) -> list[dict[str, Any]]:
    return load_auth_profiles(path)


def source_by_id(sources: list[dict[str, Any]], source_id: str) -> dict[str, Any]:
    for source in sources:
        if source.get("source_id") == source_id:
            return source
    raise KeyError(f"unknown source_id: {source_id}")


def unlock_by_source(unlocks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["source_id"]: item for item in unlocks}


def matrix_by_source(matrix: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {item["source_id"]: item for item in matrix}


def source_status(source: dict[str, Any], matrix_row: dict[str, Any] | None = None) -> str:
    if matrix_row:
        result = matrix_row.get("probe_result_class")
        if result in STATUS_LABELS:
            label = STATUS_LABELS[result]
            if label != "future_unverified":
                return label
    if source.get("credential_requirement") != "none" or source.get("access_pattern_category") == "credential_gated":
        return "credential_gated"
    if source.get("promotion_status") == "blocked_by_adapter":
        return "adapter_needed"
    if source.get("access_pattern_category") == "mirror_candidate":
        return "mirror_candidate"
    if source.get("promotion_status") in {"blocked_by_auth", "blocked_by_adapter"}:
        return "blocked"
    if source.get("access_pattern_category") == "future_unverified":
        return "future_unverified"
    if source.get("trust_level") == "verified_live":
        return "verified_now"
    return "future_unverified"


def source_row(source: dict[str, Any], unlock: dict[str, Any] | None = None, matrix_row: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "source_id": source["source_id"],
        "display_name": source["display_name"],
        "provider": source["provider"],
        "source_family": source.get("source_family"),
        "access_mode": source["access_mode"],
        "access_pattern_category": source["access_pattern_category"],
        "provenance_class": source["provenance_class"],
        "trust_level": source["trust_level"],
        "credential_requirement": source["credential_requirement"],
        "promotion_status": source["promotion_status"],
        "status": source_status(source, matrix_row),
        "next_unlock_step": (unlock or {}).get("recommended_action") or (matrix_row or {}).get("next_unlock_step"),
    }


def filter_sources(
    sources: list[dict[str, Any]],
    *,
    category: str | None = None,
    provider: str | None = None,
    credential: str | None = None,
    promotion_status: str | None = None,
    verified_only: bool = False,
    gated_only: bool = False,
    adapter_needed_only: bool = False,
    mirror_only: bool = False,
    matrix: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    matrix_map = matrix_by_source(matrix or [])
    result = []
    for source in sources:
        status = source_status(source, matrix_map.get(source["source_id"]))
        if category and source.get("access_pattern_category") != category:
            continue
        if provider and provider.lower() not in source.get("provider", "").lower():
            continue
        if credential and source.get("credential_requirement") != credential:
            continue
        if promotion_status and source.get("promotion_status") != promotion_status:
            continue
        if verified_only and status != "verified_now":
            continue
        if gated_only and status != "credential_gated":
            continue
        if adapter_needed_only and status != "adapter_needed":
            continue
        if mirror_only and status != "mirror_candidate":
            continue
        result.append(source)
    return result


def search_sources(sources: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    q = query.lower()
    result = []
    for source in sources:
        haystack = " ".join(
            str(value) for value in [
                source.get("source_id"), source.get("display_name"), source.get("provider"), source.get("source_family"), source.get("notes"), " ".join(source.get("expected_formats", []))
            ]
        ).lower()
        if q in haystack:
            result.append(source)
    return result


def stack_summary(stack: dict[str, Any], atlas: dict[str, Any] | None = None) -> dict[str, Any]:
    sources = (atlas or {}).get("sources", [])
    mirror_count = sum(1 for source in sources if source.get("access_pattern_category") == "mirror_candidate")
    future_count = sum(1 for source in sources if source.get("access_pattern_category") == "future_unverified")
    return {
        "candidate_source_count": stack.get("candidate_source_count", len(sources)),
        "verified_live_source_count": stack.get("pass_verified_count", 0),
        "credential_gated_count": stack.get("credential_gated_count", 0),
        "adapter_needed_count": stack.get("adapter_needed_count", 0),
        "mirror_candidate_count": mirror_count,
        "future_unverified_count": future_count,
        "highest_verified_stack_count": (stack.get("highest_verified_stack") or {}).get("count", stack.get("pass_verified_count", 0)),
        "live_bytes_read": stack.get("total_bytes_read_live", 0),
        "reused_bytes": stack.get("total_bytes_counting_reused", 0) - stack.get("total_bytes_read_live", 0),
        "fail_count": stack.get("fail_count", 0),
        "runtime_drift_status": "not_checked_by_cli",
    }


def auth_rows(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [redact_auth_profile(profile) for profile in profiles]


def auth_validation_report(profiles: list[dict[str, Any]]) -> dict[str, Any]:
    errors = validate_auth_profiles(profiles)
    return {"status": "PASS" if not errors else "FAIL", "error_count": len(errors), "errors": errors}


def doctor_report(paths: CliPaths = CliPaths()) -> dict[str, Any]:
    checks = []
    for name, path in [("atlas", paths.atlas), ("stack", paths.stack), ("matrix", paths.matrix), ("unlocks", paths.unlocks), ("auth", paths.auth)]:
        checks.append({"name": name, "path": str(path), "exists": path.exists()})
    status = "PASS" if all(item["exists"] for item in checks) else "FAIL"
    return {"status": status, "package_version": __version__, "checks": checks}


def gridmet_dry_run_classification(report: dict[str, Any]) -> str:
    error = (report.get("probe") or {}).get("error") or ""
    if "endpoint_or_catalog_url is missing" in error:
        return "blocked_by_endpoint_uncertainty"
    return (report.get("probe") or {}).get("result_class", "unknown")
