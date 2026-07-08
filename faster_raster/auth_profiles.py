from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

AUTH_TYPES = {
    "none", "bearer_token_env", "basic_env", "netrc", "earthdata_login_placeholder",
    "copernicus_oauth_placeholder", "usgs_m2m_placeholder", "aws_requester_pays_placeholder",
}
IMPLEMENTATION_STATUSES = {"scaffold_only", "metadata_probe_only", "live_download_supported"}
SECRET_VALUE_RE = re.compile(r"(?i)(token|password|secret|apikey|api_key|bearer)[:=][A-Za-z0-9_./+=-]{8,}")
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def load_auth_profiles(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("auth_profiles"), list):
        raise ValueError("auth profile file must contain auth_profiles list")
    return data["auth_profiles"]


def walk_values(value: Any, prefix: str = ""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from walk_values(item, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_values(item, f"{prefix}[{index}]")
    else:
        yield prefix, value


def validate_auth_profile(profile: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = ["auth_profile_id", "provider", "auth_type", "required_env_vars", "optional_env_vars", "request_injection", "redaction_policy", "enabled_default", "implementation_status"]
    for key in required:
        if key not in profile:
            errors.append(f"missing required field: {key}")
    if profile.get("auth_type") not in AUTH_TYPES:
        errors.append(f"unsupported auth_type: {profile.get('auth_type')}")
    if profile.get("implementation_status") not in IMPLEMENTATION_STATUSES:
        errors.append(f"unsupported implementation_status: {profile.get('implementation_status')}")
    for env_key in list(profile.get("required_env_vars") or []) + list(profile.get("optional_env_vars") or []):
        if not isinstance(env_key, str) or not ENV_NAME_RE.match(env_key):
            errors.append(f"invalid env var reference: {env_key}")
    for key, value in walk_values(profile):
        if isinstance(value, str) and SECRET_VALUE_RE.search(value):
            errors.append(f"raw secret-like value rejected at {key}")
    if profile.get("enabled_default") is not False:
        errors.append("auth profiles must default to disabled")
    if profile.get("implementation_status") == "live_download_supported":
        errors.append("live authenticated downloads are not supported in v0.4")
    return errors


def validate_auth_profiles(profiles: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for profile in profiles:
        profile_id = profile.get("auth_profile_id")
        if profile_id in seen:
            errors.append(f"duplicate auth_profile_id: {profile_id}")
        seen.add(profile_id)
        errors.extend(f"{profile_id}: {err}" for err in validate_auth_profile(profile))
    return errors


def redact_auth_profile(profile: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(profile)
    redacted["required_env_vars"] = ["<ENV_REF>" for _ in profile.get("required_env_vars", [])]
    redacted["optional_env_vars"] = ["<ENV_REF>" for _ in profile.get("optional_env_vars", [])]
    return redacted


def assert_no_live_authenticated_request(profile: dict[str, Any]) -> None:
    if profile.get("auth_type") != "none":
        raise RuntimeError("authenticated live requests are scaffold-only in v0.4")
