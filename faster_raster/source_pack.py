from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import stat
import string
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from faster_raster.adapter_contract import stable_json
from faster_raster.preview_templates import load_registry as load_preview_registry
from faster_raster.temporal_alternatives import (
    build_temporal_alternatives,
    select_temporal_candidate,
)


SOURCE_PACK_SCHEMA_VERSION = "fasterraster.source-pack/v1"
SOURCE_PLAN_SCHEMA_VERSION = "fasterraster.source-pack-plan/v1"
CREDENTIAL_SCHEMA_VERSION = "fasterraster.credential-requirement/v1"
PACK_ARCHIVE_SCHEMA_VERSION = "fasterraster.source-pack-archive/v1"
ADAPTER_FAMILIES = {
    "static_https_template",
    "arcgis_imageserver",
    "stac_search",
    "verified_local_raster",
}
AUTH_SCHEMES = {"none", "bearer", "api_key", "oauth2"}
ALLOWED_TEMPLATE_VARIABLES = {
    "year",
    "month",
    "day",
    "date",
    "yyyymmdd",
    "variable",
    "collection",
    "asset",
    "region",
    "resolution",
    "temporal_frequency",
}
ALLOWED_MEDIA_TYPES = {
    "application/geo+json",
    "application/json",
    "application/zip",
    "image/tiff",
    "image/tiff; application=geotiff",
}
EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "cache",
    "dist",
    "build",
}
SECRET_FILENAMES = {
    ".env",
    "credentials",
    "credentials.json",
    "secrets.json",
    "cookies.txt",
}
SECRET_KEYS = {
    "authorization",
    "authorization_header",
    "headers",
    "password",
    "secret",
    "client_secret",
    "token",
    "access_token",
    "refresh_token",
    "cookie",
    "cookies",
    "api_key_value",
    "signed_url",
}
SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "key",
    "password",
    "secret",
    "sig",
    "signature",
    "token",
    "x-amz-credential",
    "x-amz-signature",
}
MAX_PACK_FILE_BYTES = 4_000_000
MAX_PACK_TOTAL_BYTES = 16_000_000
MAX_PROBE_BYTES = 1_000_000


class AdapterContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family: Literal[
        "static_https_template",
        "arcgis_imageserver",
        "stac_search",
        "verified_local_raster",
    ]
    endpoint: str | None = None
    url_template: str | None = None
    local_path: str | None = None
    local_sha256: str | None = None
    media_types: list[str] = Field(min_length=1)
    asset_roles: list[str] = Field(min_length=1)


class CapabilityContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    planning: bool = True
    preview: bool = False
    materialization: bool = False
    analysis: bool = False
    temporal_discovery: bool = False


class SourceContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantic_type: Literal["categorical", "continuous"]
    crs: str
    resampling: str
    nodata: int | float | str | None = None
    mask_policy: Literal["explicit_nodata", "alpha_or_dataset_mask", "none"]


class AccessContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authentication_scheme: Literal["none", "bearer", "api_key", "oauth2"] = "none"
    credential_ref: str | None = None
    allowed_hosts: list[str] = Field(default_factory=list)
    redirect_hosts: list[str] = Field(default_factory=list)


class NetworkContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_requests: int = Field(default=1, ge=1, le=4)
    max_bytes: int = Field(default=65_536, ge=1, le=MAX_PROBE_BYTES)
    timeout_seconds: float = Field(default=8.0, gt=0, le=30)
    maximum_redirects: int = Field(default=0, ge=0, le=2)


class TemporalContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["exact", "static"] = "exact"
    requested: str | int | None = None
    tolerance_days: int | None = Field(default=None, ge=0, le=36_600)
    template_variables: dict[str, str | int] = Field(default_factory=dict)


class PreviewContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: str | None = None
    role: str | None = None
    theme: str | None = None
    target_crs: str | None = None


class SourcePackManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["fasterraster.source-pack/v1"]
    pack_id: str
    display_name: str
    description: str
    adapter: AdapterContract
    capabilities: CapabilityContract
    source: SourceContract
    access: AccessContract
    network: NetworkContract
    temporal: TemporalContract
    preview: PreviewContract


@dataclass(frozen=True)
class SourcePack:
    input_path: Path
    files: dict[str, bytes]
    manifest_name: str
    manifest: SourcePackManifest

    @property
    def source_pack_sha256(self) -> str:
        return hashlib.sha256(
            stable_json(self.manifest.model_dump(mode="json")).encode("utf-8")
        ).hexdigest()


def _canonical_hash(value: Mapping[str, Any], excluded: set[str] | None = None) -> str:
    payload = {
        key: item
        for key, item in value.items()
        if key not in (excluded or set())
    }
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


def _safe_member_name(name: str) -> str:
    if "\\" in name or "\x00" in name:
        raise ValueError(f"unsafe Source Pack path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"unsafe Source Pack path: {name!r}")
    if ":" in path.parts[0]:
        raise ValueError(f"unsafe Source Pack drive path: {name!r}")
    return path.as_posix()


def _read_directory(path: Path) -> dict[str, bytes]:
    root = path.resolve()
    files: dict[str, bytes] = {}
    total = 0
    for candidate in sorted(path.rglob("*")):
        relative = candidate.relative_to(path)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if candidate.is_symlink():
            raise ValueError(f"Source Pack symlinks are not allowed: {relative}")
        if not candidate.is_file():
            continue
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"Source Pack path escapes its root: {relative}")
        name = _safe_member_name(relative.as_posix())
        size = candidate.stat().st_size
        if size > MAX_PACK_FILE_BYTES:
            raise ValueError(f"Source Pack file exceeds {MAX_PACK_FILE_BYTES} bytes: {name}")
        total += size
        if total > MAX_PACK_TOTAL_BYTES:
            raise ValueError(f"Source Pack exceeds {MAX_PACK_TOTAL_BYTES} total bytes")
        files[name] = candidate.read_bytes()
    return files


def _read_archive(path: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    total = 0
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"invalid Source Pack archive: {exc}") from exc
    with archive:
        for info in sorted(archive.infolist(), key=lambda item: item.filename):
            if info.is_dir():
                continue
            name = _safe_member_name(info.filename)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError(f"Source Pack archive symlinks are not allowed: {name}")
            if info.file_size > MAX_PACK_FILE_BYTES:
                raise ValueError(f"Source Pack file exceeds {MAX_PACK_FILE_BYTES} bytes: {name}")
            total += info.file_size
            if total > MAX_PACK_TOTAL_BYTES:
                raise ValueError(f"Source Pack exceeds {MAX_PACK_TOTAL_BYTES} total bytes")
            if name in files:
                raise ValueError(f"duplicate Source Pack archive member: {name}")
            files[name] = archive.read(info)
    return files


def load_source_pack(path: Path) -> SourcePack:
    source = path.resolve()
    if source.is_dir():
        files = _read_directory(source)
    elif source.is_file() and zipfile.is_zipfile(source):
        files = _read_archive(source)
    else:
        raise ValueError(f"Source Pack is not a directory or ZIP archive: {path}")
    manifests = [name for name in ("sauce.yaml", "source-pack.yaml") if name in files]
    if len(manifests) != 1:
        raise ValueError("Source Pack requires exactly one sauce.yaml or source-pack.yaml")
    manifest_name = manifests[0]
    try:
        raw = yaml.safe_load(files[manifest_name].decode("utf-8"))
        manifest = SourcePackManifest.model_validate(raw)
    except (UnicodeDecodeError, yaml.YAMLError, ValidationError) as exc:
        raise ValueError(f"invalid Source Pack manifest: {exc}") from exc
    return SourcePack(source, files, manifest_name, manifest)


def _valid_host(host: str) -> str:
    value = host.strip().lower().rstrip(".")
    if not value or "*" in value or "/" in value or "@" in value or ":" in value:
        raise ValueError(f"invalid or unrestricted host {host!r}")
    if value == "localhost" or value.endswith(".local"):
        raise ValueError(f"local host is not allowed: {host!r}")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", value):
            raise ValueError(f"invalid host {host!r}")
    else:
        raise ValueError(f"literal IP hosts are not allowed: {host!r}")
    return value


def _url_errors(url: str, allowed_hosts: set[str]) -> list[str]:
    errors: list[str] = []
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        errors.append("source URLs must use HTTPS")
    if parsed.username or parsed.password:
        errors.append("credentials embedded in a URL are forbidden")
    host = (parsed.hostname or "").lower()
    if not host or host not in allowed_hosts:
        errors.append(f"URL host is not allowlisted: {host or '<missing>'}")
    for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in SENSITIVE_QUERY_KEYS:
            errors.append(f"secret-bearing query parameter is forbidden: {key}")
        if re.search(r"(?i)\bbearer\s+\S+|eyJ[A-Za-z0-9_-]{12,}\.", value):
            errors.append(f"token-looking query value is forbidden: {key}")
    return errors


def _template_fields(value: str) -> set[str]:
    fields: set[str] = set()
    try:
        for _, field, format_spec, conversion in string.Formatter().parse(value):
            if field is None:
                continue
            if not field or any(token in field for token in ".[]") or format_spec or conversion:
                raise ValueError("only simple, unformatted template variables are allowed")
            fields.add(field)
    except ValueError as exc:
        raise ValueError(f"invalid bounded URL template: {exc}") from exc
    return fields


def _secret_findings(files: Mapping[str, bytes]) -> list[str]:
    findings: list[str] = []

    def inspect(value: Any, location: str) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                key_text = str(key).lower()
                if key_text in SECRET_KEYS:
                    findings.append(f"{location}: secret-bearing field {key!r} is forbidden")
                inspect(item, f"{location}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                inspect(item, f"{location}[{index}]")
        elif isinstance(value, str):
            if re.search(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{12,}", value):
                findings.append(f"{location}: bearer-token-looking value is forbidden")
            if re.search(r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{8,}\.", value):
                findings.append(f"{location}: JWT-looking value is forbidden")
            if re.search(r"\bAKIA[0-9A-Z]{16}\b", value):
                findings.append(f"{location}: access-key-looking value is forbidden")

    for name, data in files.items():
        if PurePosixPath(name).name.lower() in SECRET_FILENAMES:
            findings.append(f"{name}: secret file names are forbidden")
        if len(data) > 1_000_000:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        parsed: Any = None
        try:
            if name.endswith((".yaml", ".yml")):
                parsed = yaml.safe_load(text)
            elif name.endswith(".json"):
                parsed = json.loads(text)
        except (yaml.YAMLError, json.JSONDecodeError):
            parsed = None
        if parsed is not None:
            inspect(parsed, name)
        else:
            inspect(text, name)
    return sorted(set(findings))


def validate_loaded_source_pack(pack: SourcePack) -> dict[str, Any]:
    manifest = pack.manifest
    errors: list[str] = []
    warnings: list[str] = []
    if not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", manifest.pack_id):
        errors.append("pack_id must be 3-64 lowercase letters, numbers, underscores, or hyphens")
    allowed_hosts: set[str] = set()
    for host in manifest.access.allowed_hosts:
        try:
            allowed_hosts.add(_valid_host(host))
        except ValueError as exc:
            errors.append(str(exc))
    redirect_hosts: set[str] = set()
    for host in manifest.access.redirect_hosts:
        try:
            redirect_hosts.add(_valid_host(host))
        except ValueError as exc:
            errors.append(str(exc))
    if not redirect_hosts.issubset(allowed_hosts):
        errors.append("redirect_hosts must be a subset of allowed_hosts")
    if manifest.network.maximum_redirects and not redirect_hosts:
        errors.append("maximum_redirects requires explicit redirect_hosts")
    adapter = manifest.adapter
    url = adapter.url_template or adapter.endpoint
    if adapter.family == "static_https_template" and not adapter.url_template:
        errors.append("static_https_template requires adapter.url_template")
    if adapter.family in {"arcgis_imageserver", "stac_search"} and not adapter.endpoint:
        errors.append(f"{adapter.family} requires adapter.endpoint")
    if adapter.family == "verified_local_raster":
        if not adapter.local_path or not adapter.local_sha256:
            errors.append("verified_local_raster requires local_path and local_sha256")
        if allowed_hosts or redirect_hosts:
            errors.append("verified_local_raster must not declare network hosts")
        if adapter.local_path:
            try:
                local_name = _safe_member_name(adapter.local_path)
            except ValueError as exc:
                errors.append(str(exc))
            else:
                if local_name not in pack.files:
                    errors.append(f"verified local raster is missing: {local_name}")
                elif adapter.local_sha256 and hashlib.sha256(pack.files[local_name]).hexdigest() != adapter.local_sha256:
                    errors.append("verified local raster SHA-256 does not match")
    elif url:
        errors.extend(_url_errors(url, allowed_hosts))
        if not allowed_hosts:
            errors.append("network source requires a nonempty allowed_hosts list")
        try:
            fields = _template_fields(url)
        except ValueError as exc:
            errors.append(str(exc))
            fields = set()
        declared = set(manifest.temporal.template_variables)
        if not fields.issubset(ALLOWED_TEMPLATE_VARIABLES):
            errors.append(
                "URL template uses unsupported variables: "
                + ", ".join(sorted(fields - ALLOWED_TEMPLATE_VARIABLES))
            )
        if not fields.issubset(declared):
            errors.append(
                "URL template variables lack bounded values: "
                + ", ".join(sorted(fields - declared))
            )
    if any(item not in ALLOWED_MEDIA_TYPES for item in adapter.media_types):
        errors.append("adapter declares an unsupported media type")
    source = manifest.source
    if not re.fullmatch(r"EPSG:[1-9][0-9]{2,5}", source.crs):
        errors.append("source.crs must be an explicit EPSG code")
    if source.semantic_type == "categorical" and source.resampling not in {"nearest", "mode"}:
        errors.append("categorical sources require nearest or mode resampling")
    if source.semantic_type == "continuous" and source.resampling not in {
        "nearest",
        "bilinear",
        "cubic",
        "average",
    }:
        errors.append("continuous source resampling is unsupported")
    if source.nodata is None and source.mask_policy == "none":
        errors.append("source must declare nodata or mask semantics")
    access = manifest.access
    if access.authentication_scheme == "none" and access.credential_ref:
        errors.append("credential_ref requires a non-none authentication_scheme")
    if access.authentication_scheme != "none":
        if not access.credential_ref or not re.fullmatch(
            r"[a-z][a-z0-9._-]{2,127}", access.credential_ref
        ):
            errors.append("credential_ref must be an opaque identifier with safe syntax")
    if manifest.capabilities.materialization:
        warnings.append(
            "materialization is a declared source capability; this public Source Pack runtime does not execute it"
        )
    preview = manifest.preview
    try:
        preview_registry = load_preview_registry()
    except ValueError as exc:
        errors.append(str(exc))
        preview_registry = {"templates": {}, "roles": {}}
    if preview.template_id and preview.template_id not in preview_registry["templates"]:
        errors.append(f"unknown preview template {preview.template_id!r}")
    if preview.role and preview.role not in preview_registry["roles"]:
        errors.append(f"unknown preview role {preview.role!r}")
    if preview.target_crs and not re.fullmatch(r"EPSG:[1-9][0-9]{2,5}", preview.target_crs):
        errors.append("preview.target_crs must be an explicit EPSG code")
    errors.extend(_secret_findings(pack.files))
    result = {
        "schema_version": "fasterraster.source-pack-validation/v1",
        "status": "PASS" if not errors else "FAIL",
        "pack_id": manifest.pack_id,
        "source_pack_sha256": pack.source_pack_sha256,
        "adapter_family": adapter.family,
        "credential_required": access.authentication_scheme != "none",
        "network_requests": 0,
        "network_bytes": 0,
        "checks": {
            "schema": not any("manifest" in item for item in errors),
            "hosts": not any("host" in item for item in errors),
            "secrets": not any("secret" in item.lower() or "token" in item.lower() for item in errors),
            "categorical_resampling": not any("categorical" in item for item in errors),
            "preview_compatibility": not any("preview" in item for item in errors),
        },
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
    }
    result["validation_sha256"] = _canonical_hash(result, {"validation_sha256"})
    return result


def validate_source_pack(path: Path) -> dict[str, Any]:
    try:
        return validate_loaded_source_pack(load_source_pack(path))
    except ValueError as exc:
        return {
            "schema_version": "fasterraster.source-pack-validation/v1",
            "status": "FAIL",
            "pack_id": None,
            "source_pack_sha256": None,
            "adapter_family": None,
            "credential_required": None,
            "network_requests": 0,
            "network_bytes": 0,
            "checks": {},
            "errors": [str(exc)],
            "warnings": [],
            "validation_sha256": None,
        }


def credential_requirement(manifest: SourcePackManifest) -> dict[str, Any] | None:
    access = manifest.access
    if access.authentication_scheme == "none":
        return None
    result = {
        "schema_version": CREDENTIAL_SCHEMA_VERSION,
        "authentication_scheme": access.authentication_scheme,
        "credential_ref": access.credential_ref,
        "allowed_hosts": sorted(access.allowed_hosts),
        "redirect_hosts": sorted(access.redirect_hosts),
        "resolver_capability_required": access.authentication_scheme,
        "resolved_secret_present": False,
    }
    result["credential_requirement_sha256"] = _canonical_hash(
        result,
        {"credential_requirement_sha256"},
    )
    return result


def _fixture(pack: SourcePack) -> dict[str, Any]:
    if "probe_fixture.json" not in pack.files:
        return {}
    try:
        value = json.loads(pack.files["probe_fixture.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid probe_fixture.json: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("probe_fixture.json must be an object")
    return value


def compile_source_pack_plan(
    path_or_pack: Path | SourcePack,
    *,
    requested_time: str | int | None = None,
    selected_time: str | None = None,
) -> dict[str, Any]:
    pack = (
        path_or_pack
        if isinstance(path_or_pack, SourcePack)
        else load_source_pack(path_or_pack)
    )
    validation = validate_loaded_source_pack(pack)
    if validation["status"] != "PASS":
        raise ValueError("Source Pack validation failed: " + "; ".join(validation["errors"]))
    manifest = pack.manifest
    fixture = _fixture(pack)
    requested = (
        str(requested_time)
        if requested_time is not None
        else (
            str(manifest.temporal.requested)
            if manifest.temporal.requested is not None
            else None
        )
    )
    temporal_alternatives = None
    temporal_resolution = None
    resolved_time = requested
    available = [str(item) for item in fixture.get("available_times") or []]
    if manifest.temporal.mode == "exact" and requested and available and requested not in available:
        metadata_by_time = {
            str(item.get("candidate_time")): item
            for item in fixture.get("candidate_metadata") or []
            if isinstance(item, Mapping) and item.get("candidate_time") is not None
        }
        temporal_alternatives = build_temporal_alternatives(
            requested,
            [
                {"candidate_time": item, **dict(metadata_by_time.get(item) or {})}
                for item in available
            ],
            source_id=manifest.pack_id,
            provider=str(fixture.get("provider") or "") or None,
            product=str(fixture.get("product") or "") or None,
            processing_family=str(fixture.get("processing_family") or "") or None,
            tolerance_days=manifest.temporal.tolerance_days,
            search_metadata={"fixture_sha256": hashlib.sha256(pack.files["probe_fixture.json"]).hexdigest()},
        )
        if selected_time is not None:
            temporal_resolution = select_temporal_candidate(
                temporal_alternatives,
                str(selected_time),
            )
            resolved_time = temporal_resolution["selected_time"]
    requirement = credential_requirement(manifest)
    if temporal_alternatives and temporal_resolution is None:
        status = temporal_alternatives["status"]
    elif requirement is not None:
        status = "CREDENTIAL_REQUIRED"
    else:
        status = "READY"
    stable = {
        "schema_version": SOURCE_PLAN_SCHEMA_VERSION,
        "pack_id": manifest.pack_id,
        "source_pack_sha256": pack.source_pack_sha256,
        "status": status,
        "adapter": {
            "family": manifest.adapter.family,
            "public_adapter_id": {
                "static_https_template": "generic_https_template",
                "arcgis_imageserver": "arcgis_imageserver",
                "stac_search": "stac_api",
                "verified_local_raster": "verified_local_raster",
            }[manifest.adapter.family],
            "media_types": manifest.adapter.media_types,
            "asset_roles": manifest.adapter.asset_roles,
        },
        "capabilities": manifest.capabilities.model_dump(mode="json"),
        "requested_time": requested,
        "resolved_time": resolved_time,
        "temporal_alternatives": temporal_alternatives,
        "temporal_resolution": temporal_resolution,
        "credential_requirement": requirement,
        "network_policy": {
            "default": "disabled",
            "max_requests": manifest.network.max_requests,
            "max_bytes": manifest.network.max_bytes,
            "allowed_hosts": sorted(manifest.access.allowed_hosts),
            "redirect_hosts": sorted(manifest.access.redirect_hosts),
        },
        "preview": manifest.preview.model_dump(mode="json"),
        "original_pack_unchanged": True,
    }
    stable["plan_sha256"] = _canonical_hash(stable, {"plan_sha256"})
    return stable


def explain_source_pack(path: Path) -> dict[str, Any]:
    pack = load_source_pack(path)
    validation = validate_loaded_source_pack(pack)
    if validation["status"] != "PASS":
        raise ValueError("Source Pack validation failed: " + "; ".join(validation["errors"]))
    plan = compile_source_pack_plan(pack)
    manifest = pack.manifest
    return {
        "schema_version": "fasterraster.source-pack-explanation/v1",
        "pack_id": manifest.pack_id,
        "display_name": manifest.display_name,
        "description": manifest.description,
        "source_pack_sha256": pack.source_pack_sha256,
        "status": plan["status"],
        "adapter_family": manifest.adapter.family,
        "can": [
            field
            for field, enabled in manifest.capabilities.model_dump().items()
            if enabled
        ],
        "cannot": [
            field
            for field, enabled in manifest.capabilities.model_dump().items()
            if not enabled
        ]
        + ["execute arbitrary code", "resolve public credentials"],
        "credential_requirement": plan["credential_requirement"],
        "temporal": {
            "mode": manifest.temporal.mode,
            "requested": manifest.temporal.requested,
            "selection_required": plan["status"] == "AWAITING_TEMPORAL_SELECTION",
        },
        "preview": manifest.preview.model_dump(mode="json"),
        "network_requests": 0,
        "network_bytes": 0,
        "validation_sha256": validation["validation_sha256"],
        "plan_sha256": plan["plan_sha256"],
    }


def test_source_pack(path: Path) -> dict[str, Any]:
    pack = load_source_pack(path)
    validation = validate_loaded_source_pack(pack)
    if validation["status"] != "PASS":
        return {
            "schema_version": "fasterraster.source-pack-test/v1",
            "status": "FAIL",
            "network_requests": 0,
            "errors": validation["errors"],
        }
    if "golden_plan.json" not in pack.files:
        return {
            "schema_version": "fasterraster.source-pack-test/v1",
            "status": "FAIL",
            "network_requests": 0,
            "errors": ["golden_plan.json is required for offline Source Pack tests"],
        }
    expected = json.loads(pack.files["golden_plan.json"].decode("utf-8"))
    actual = compile_source_pack_plan(pack)
    passed = expected == actual
    return {
        "schema_version": "fasterraster.source-pack-test/v1",
        "status": "PASS" if passed else "FAIL",
        "pack_id": pack.manifest.pack_id,
        "network_requests": 0,
        "expected_plan_sha256": expected.get("plan_sha256"),
        "actual_plan_sha256": actual.get("plan_sha256"),
        "errors": [] if passed else ["golden plan does not match deterministic compilation"],
    }


def _render_endpoint(pack: SourcePack) -> str:
    manifest = pack.manifest
    value = manifest.adapter.url_template or manifest.adapter.endpoint
    if not value:
        raise ValueError("Source Pack has no network endpoint")
    rendered = value.format_map(
        {key: str(item) for key, item in manifest.temporal.template_variables.items()}
    )
    if manifest.adapter.family == "arcgis_imageserver":
        rendered += ("&" if "?" in rendered else "?") + "f=pjson"
    elif manifest.adapter.family == "stac_search":
        rendered = rendered.rstrip("/") + "/collections?limit=1"
    return rendered


class _BoundedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, hosts: set[str], maximum: int) -> None:
        self.hosts = hosts
        self.maximum = maximum
        self.count = 0

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        host = (urllib.parse.urlsplit(newurl).hostname or "").lower()
        if self.count >= self.maximum or host not in self.hosts:
            raise urllib.error.HTTPError(
                req.full_url,
                code,
                "redirect host or count is not authorized",
                headers,
                fp,
            )
        self.count += 1
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def probe_source_pack(
    path: Path,
    *,
    allow_network: bool,
    urlopen: Any | None = None,
) -> dict[str, Any]:
    pack = load_source_pack(path)
    validation = validate_loaded_source_pack(pack)
    if validation["status"] != "PASS":
        raise ValueError("Source Pack validation failed: " + "; ".join(validation["errors"]))
    if not allow_network:
        raise ValueError("bounded Source Pack probe requires explicit --allow-network")
    requirement = credential_requirement(pack.manifest)
    if requirement is not None:
        raise ValueError(
            "credential resolver capability is required; public probing stopped before network access"
        )
    if pack.manifest.adapter.family == "verified_local_raster":
        raise ValueError("verified_local_raster has no network probe")
    url = _render_endpoint(pack)
    errors = _url_errors(url, set(pack.manifest.access.allowed_hosts))
    if errors:
        raise ValueError("; ".join(errors))
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "FasterRaster-SourcePack-Probe/1",
            "Range": f"bytes=0-{pack.manifest.network.max_bytes - 1}",
            "Accept": ",".join(pack.manifest.adapter.media_types),
        },
        method="GET",
    )
    redirect_handler = _BoundedRedirectHandler(
        set(pack.manifest.access.redirect_hosts),
        pack.manifest.network.maximum_redirects,
    )
    opener = urllib.request.build_opener(redirect_handler)
    open_request = urlopen or opener.open
    with open_request(request, timeout=pack.manifest.network.timeout_seconds) as response:
        body = response.read(pack.manifest.network.max_bytes + 1)
        if len(body) > pack.manifest.network.max_bytes:
            raise ValueError("Source Pack probe exceeded its byte ceiling")
        status_value = getattr(response, "status", None)
        if status_value is None:
            status_value = response.getcode()
        status_code = int(status_value)
        content_type = str(response.headers.get("Content-Type") or "").split(";")[0]
    parsed = urllib.parse.urlsplit(url)
    result = {
        "schema_version": "fasterraster.source-pack-probe/v1",
        "status": "PASS" if 200 <= status_code < 300 else "FAIL",
        "pack_id": pack.manifest.pack_id,
        "source_pack_sha256": pack.source_pack_sha256,
        "request_count": 1,
        "bytes_transferred": len(body),
        "byte_ceiling": pack.manifest.network.max_bytes,
        "http_status": status_code,
        "content_type": content_type or "unknown",
        "request_url_redacted": urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, "", "")
        ),
        "redirect_count": redirect_handler.count,
        "materialized_asset": False,
    }
    result["probe_evidence_sha256"] = _canonical_hash(
        result,
        {"probe_evidence_sha256"},
    )
    return result


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(dict(value), stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def scaffold_source_pack(destination: Path, *, force: bool = False) -> Path:
    target = destination
    if target.suffix != ".sauce":
        target = target.with_name(target.name + ".sauce")
    if target.exists() and not force:
        raise ValueError(f"refusing to overwrite existing Source Pack: {target}; use --force")
    target.mkdir(parents=True, exist_ok=True)
    pack_id = re.sub(r"[^a-z0-9_-]+", "-", target.stem.lower()).strip("-_")
    if len(pack_id) < 3:
        pack_id = "my-source"
    manifest = {
        "schema_version": SOURCE_PACK_SCHEMA_VERSION,
        "pack_id": pack_id,
        "display_name": pack_id.replace("-", " ").title(),
        "description": "Declarative bounded HTTPS raster source.",
        "adapter": {
            "family": "static_https_template",
            "url_template": "https://example.com/rasters/{year}.tif",
            "media_types": ["image/tiff"],
            "asset_roles": ["data"],
        },
        "capabilities": {
            "planning": True,
            "preview": True,
            "materialization": False,
            "analysis": False,
            "temporal_discovery": True,
        },
        "source": {
            "semantic_type": "continuous",
            "crs": "EPSG:4326",
            "resampling": "bilinear",
            "nodata": -9999,
            "mask_policy": "explicit_nodata",
        },
        "access": {
            "authentication_scheme": "none",
            "credential_ref": None,
            "allowed_hosts": ["example.com"],
            "redirect_hosts": [],
        },
        "network": {
            "max_requests": 1,
            "max_bytes": 65536,
            "timeout_seconds": 8,
            "maximum_redirects": 0,
        },
        "temporal": {
            "mode": "exact",
            "requested": "2023",
            "tolerance_days": 800,
            "template_variables": {"year": "2023"},
        },
        "preview": {
            "template_id": "general_multisource_v1",
            "role": "environmental_context",
            "theme": "climate_continuous",
            "target_crs": "EPSG:4326",
        },
    }
    _write_text(target / "sauce.yaml", yaml.safe_dump(manifest, sort_keys=False))
    _write_text(
        target / "probe_fixture.json",
        json.dumps(
            {
                "provider": "Example provider",
                "product": pack_id,
                "available_times": ["2022", "2023"],
                "candidate_metadata": [
                    {
                        "candidate_time": "2022",
                        "coverage_fraction": 1.0,
                        "verification_status": "verified",
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    _write_text(
        target / "README.md",
        "# Source Pack\n\n"
        "Validate offline with `fr sauce validate .` and replace the example "
        "host only after reviewing its media type, CRS, nodata, temporal, and "
        "byte-ceiling contracts.\n",
    )
    plan = compile_source_pack_plan(target)
    _write_text(
        target / "golden_plan.json",
        json.dumps(plan, indent=2, sort_keys=True) + "\n",
    )
    return target


def pack_source_pack(path: Path, output: Path) -> dict[str, Any]:
    pack = load_source_pack(path)
    validation = validate_loaded_source_pack(pack)
    if validation["status"] != "PASS":
        raise ValueError("Source Pack validation failed: " + "; ".join(validation["errors"]))
    destination = output
    if output.suffix.lower() != ".zip":
        output.mkdir(parents=True, exist_ok=True)
        destination = output / f"{pack.manifest.pack_id}.sauce.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    files = {
        name: data
        for name, data in pack.files.items()
        if name != "CHECKSUMS.sha256"
    }
    checksums = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n"
        for name, data in sorted(files.items())
    ).encode("utf-8")
    archive_files = {**files, "CHECKSUMS.sha256": checksums}
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=destination.parent,
    )
    os.close(descriptor)
    try:
        with zipfile.ZipFile(
            temporary_name,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for name, data in sorted(archive_files.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        os.replace(temporary_name, destination)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    result = {
        "schema_version": PACK_ARCHIVE_SCHEMA_VERSION,
        "status": "PASS",
        "pack_id": pack.manifest.pack_id,
        "source_pack_sha256": pack.source_pack_sha256,
        "archive_path": str(destination),
        "archive_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "archive_bytes": destination.stat().st_size,
        "file_count": len(archive_files),
        "deterministic_timestamp": "1980-01-01T00:00:00Z",
    }
    return result
