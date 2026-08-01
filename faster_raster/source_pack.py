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
from math import isfinite
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal, Mapping, Sequence

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
SOURCE_PACK_SCHEMA_VERSION_V2 = "fasterraster.source-pack/v2"
SOURCE_PLAN_SCHEMA_VERSION_V2 = "fasterraster.source-pack-plan/v2"
CREDENTIAL_SCHEMA_VERSION_V2 = "fasterraster.credential-requirement/v2"
PACK_ARCHIVE_SCHEMA_VERSION = "fasterraster.source-pack-archive/v1"
MATERIALIZATION_REQUEST_SCHEMA_VERSION = (
    "fasterraster.source-materialization-request/v1"
)
MATERIALIZATION_REQUEST_SCHEMA_VERSION_V2 = (
    "fasterraster.source-materialization-request/v2"
)
ADAPTER_FAMILIES = {
    "static_https_template",
    "arcgis_imageserver",
    "stac_search",
    "verified_local_raster",
}
AUTH_SCHEMES = {"none", "bearer", "api_key", "oauth2"}
ASSET_ACCESS_MODES = {
    "direct_https",
    "s3_public",
    "s3_requester_pays",
    "brokered_signed_https",
    "bearer_https",
    "s3_compatible_credentialed",
}
V2_CREDENTIAL_SCHEMES = {
    "aws_sigv4",
    "ephemeral_https_signer",
    "oauth2_bearer",
    "s3_compatible",
}
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
    "image/tiff; application=geotiff; profile=cloud-optimized",
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


class SourceContractV2(SourceContract):
    mask_policy: Literal[
        "explicit_nodata",
        "alpha_or_dataset_mask",
        "all_valid",
        "none",
    ]


class AccessContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authentication_scheme: Literal["none", "bearer", "api_key", "oauth2"] = "none"
    credential_ref: str | None = None
    allowed_hosts: list[str] = Field(default_factory=list)
    redirect_hosts: list[str] = Field(default_factory=list)
    asset_hosts: list[str] = Field(default_factory=list)


class NetworkContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_requests: int = Field(default=1, ge=1, le=4)
    max_bytes: int = Field(default=65_536, ge=1, le=MAX_PROBE_BYTES)
    max_asset_bytes: int = Field(default=64_000_000, ge=1, le=1_000_000_000)
    max_total_bytes: int = Field(default=128_000_000, ge=1, le=2_000_000_000)
    timeout_seconds: float = Field(default=8.0, gt=0, le=30)
    maximum_redirects: int = Field(default=0, ge=0, le=2)
    max_parallel_requests: int = Field(default=1, ge=1, le=8)


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


class AdapterContractV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family: Literal[
        "static_https_template",
        "arcgis_imageserver",
        "stac_search",
        "verified_local_raster",
        "earth_engine_compute",
    ]
    endpoint: str | None = None
    url_template: str | None = None
    local_path: str | None = None
    local_sha256: str | None = None
    media_types: list[str] = Field(min_length=1)
    asset_roles: list[str] = Field(min_length=1)


class AccessContractV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authentication_scheme: Literal["none", "bearer", "api_key", "oauth2"] = "none"
    allowed_hosts: list[str] = Field(default_factory=list)
    redirect_hosts: list[str] = Field(default_factory=list)
    asset_hosts: list[str] = Field(default_factory=list)
    asset_host_suffixes: list[str] = Field(default_factory=list)
    resolver_hosts: list[str] = Field(default_factory=list)
    resolver_host_suffixes: list[str] = Field(default_factory=list)


class StableHrefIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    href_policy: Literal[
        "selected_stac_asset",
        "unsigned_selected_stac_asset",
    ]


class StableS3Identity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheme: Literal["s3"] = "s3"
    bucket: str
    region: str
    key_policy: Literal["selected_stac_asset"]


class ResolverContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheme: Literal["ephemeral_https_signer"]
    endpoint: str
    method: Literal["GET"] = "GET"
    href_parameter: Literal["href"] = "href"
    response_field: Literal["href"] = "href"


class DirectHttpsAssetAccess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["direct_https"]
    stable_identity: StableHrefIdentity


class PublicS3AssetAccess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["s3_public"]
    stable_identity: StableS3Identity


class RequesterPaysBilling(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["requester_pays"]
    explicit_study_consent_required: Literal[True] = True
    explicit_runtime_permission_required: Literal[True] = True


class RequesterPaysS3AssetAccess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["s3_requester_pays"]
    stable_identity: StableS3Identity
    credential_scheme: Literal["aws_sigv4"] = "aws_sigv4"
    billing: RequesterPaysBilling


class BrokeredSignedHttpsAssetAccess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["brokered_signed_https"]
    stable_identity: StableHrefIdentity
    resolver: ResolverContract


class BearerHttpsAssetAccess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["bearer_https"]
    stable_identity: StableHrefIdentity
    credential_scheme: Literal["oauth2_bearer"] = "oauth2_bearer"


class S3CompatibleCredentialedAssetAccess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["s3_compatible_credentialed"]
    stable_identity: StableS3Identity
    endpoint: str
    credential_scheme: Literal["s3_compatible"] = "s3_compatible"
    executable: Literal[False] = False


AssetAccessContract = Annotated[
    DirectHttpsAssetAccess
    | PublicS3AssetAccess
    | RequesterPaysS3AssetAccess
    | BrokeredSignedHttpsAssetAccess
    | BearerHttpsAssetAccess
    | S3CompatibleCredentialedAssetAccess,
    Field(discriminator="mode"),
]


class EarthEngineBandContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    semantic_type: Literal["categorical", "continuous"]
    data_type: str


class EarthEngineContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    dataset_type: Literal["image", "image_collection"]
    bands: list[EarthEngineBandContract] = Field(min_length=1)
    allowed_operations: list[
        Literal[
            "load_image",
            "load_collection",
            "filter_bounds",
            "filter_date",
            "sort_acquisition_time",
            "tie_break_system_index",
            "select_first",
            "select_bands",
        ]
    ] = Field(min_length=1)
    credential_scheme: Literal["google_adc"] = "google_adc"
    max_uncompressed_response_bytes: int = Field(gt=0, le=48_000_000)
    max_width: int = Field(gt=0, le=32_000)
    max_height: int = Field(gt=0, le=32_000)
    max_bands: int = Field(gt=0, le=1_024)


class SourcePackManifestV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["fasterraster.source-pack/v2"]
    pack_id: str
    display_name: str
    description: str
    adapter: AdapterContractV2
    capabilities: CapabilityContract
    source: SourceContractV2
    access: AccessContractV2
    asset_access: AssetAccessContract | None = None
    network: NetworkContract
    temporal: TemporalContract
    preview: PreviewContract
    family_contract: dict[str, Any]
    earth_engine: EarthEngineContract | None = None


@dataclass(frozen=True)
class SourcePack:
    input_path: Path
    files: dict[str, bytes]
    manifest_name: str
    manifest: SourcePackManifest | SourcePackManifestV2

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
        schema_version = raw.get("schema_version") if isinstance(raw, Mapping) else None
        manifest_model = (
            SourcePackManifestV2
            if schema_version == SOURCE_PACK_SCHEMA_VERSION_V2
            else SourcePackManifest
        )
        manifest = manifest_model.model_validate(raw)
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


def _valid_host_suffix(host: str) -> str:
    value = _valid_host(host)
    if "." not in value:
        raise ValueError(f"host suffix must contain a public DNS boundary: {host!r}")
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
                if (
                    key_text == "authorization"
                    and isinstance(item, Mapping)
                    and set(item).issubset(
                        {
                            "credential_ref",
                            "project_ref",
                            "allow_chargeable_access",
                        }
                    )
                ):
                    inspect(item, f"{location}.{key}")
                    continue
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


def _family_contract(
    pack: SourcePack,
    *,
    errors: list[str],
    warnings: list[str],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Validate public evidence and adapter-family semantics without discovery."""
    fixture = _fixture(pack)
    identity = fixture.get("identity")
    if not isinstance(identity, Mapping):
        identity = {
            "provider": fixture.get("provider"),
            "product": fixture.get("product"),
            "collection": fixture.get("collection"),
        }
    identity = {
        "provider": str(identity.get("provider") or ""),
        "product": str(identity.get("product") or ""),
        "collection": (
            str(identity["collection"])
            if identity.get("collection") is not None
            else None
        ),
    }
    if not identity["provider"] or not identity["product"]:
        warnings.append("provider evidence is incomplete: provider and product are required")

    evidence = fixture.get("provider_evidence")
    if not isinstance(evidence, Mapping):
        evidence = {}
    evidence_status = str(evidence.get("status") or "incomplete")
    official_documentation = evidence.get("official_documentation") or []
    if not isinstance(official_documentation, list):
        errors.append("provider_evidence.official_documentation must be an array")
        official_documentation = []
    invalid_evidence_urls = [
        str(item)
        for item in official_documentation
        if not isinstance(item, str)
        or urllib.parse.urlsplit(item).scheme != "https"
        or not urllib.parse.urlsplit(item).hostname
    ]
    if invalid_evidence_urls:
        errors.append("provider evidence URLs must be absolute HTTPS URLs")
    if evidence_status not in {"complete", "incomplete", "synthetic"}:
        errors.append("provider_evidence.status must be complete, incomplete, or synthetic")
    if evidence_status == "complete" and not official_documentation:
        errors.append("complete provider evidence requires official_documentation")
    evidence_result = {
        "status": evidence_status,
        "official_documentation": sorted(str(item) for item in official_documentation),
        "evidence_sha256": _canonical_hash(
            {
                "identity": identity,
                "provider_evidence": dict(evidence),
            }
        ),
    }

    raw_contract = (
        pack.manifest.family_contract
        if isinstance(pack.manifest, SourcePackManifestV2)
        else fixture.get("family_contract")
    )
    if not isinstance(raw_contract, Mapping):
        raw_contract = {}
    contract = dict(raw_contract)
    adapter = pack.manifest.adapter
    source = pack.manifest.source
    temporal = pack.manifest.temporal

    if adapter.family == "static_https_template":
        if adapter.endpoint or adapter.local_path or adapter.local_sha256:
            errors.append("static_https_template accepts only adapter.url_template")
        if temporal.mode == "exact" and temporal.requested is None:
            errors.append("exact static HTTPS sources require temporal.requested")
        if "application/zip" in adapter.media_types:
            archive = contract.get("archive")
            if not isinstance(archive, Mapping):
                errors.append("ZIP products require a family_contract.archive selection contract")
            else:
                if archive.get("format") != "zip":
                    errors.append("archive format must be zip")
                member_pattern = str(archive.get("member_pattern") or "")
                if (
                    not member_pattern
                    or member_pattern.startswith(("/", "\\"))
                    or ".." in PurePosixPath(member_pattern).parts
                ):
                    errors.append("archive member_pattern must be a safe relative pattern")
                if archive.get("selection") not in {"single_match", "lexicographic_first"}:
                    errors.append("archive selection must be deterministic")
                companions = archive.get("required_companions")
                if not isinstance(companions, list):
                    errors.append("archive required_companions must be an array")
                else:
                    for companion in companions:
                        companion_pattern = str(companion or "")
                        if (
                            not companion_pattern
                            or companion_pattern.startswith(("/", "\\"))
                            or ".." in PurePosixPath(
                                companion_pattern.replace("{stem}", "primary")
                            ).parts
                            or set(_template_fields(companion_pattern)) - {"stem"}
                        ):
                            errors.append(
                                "archive companion patterns must be safe relative {stem} templates"
                            )
                archive_limits = {
                    "maximum_members": (1, 10_000),
                    "maximum_uncompressed_bytes": (1, 2_000_000_000),
                    "maximum_compression_ratio": (1, 10_000),
                }
                for field, (minimum, maximum) in archive_limits.items():
                    value = archive.get(field)
                    if (
                        not isinstance(value, int)
                        or isinstance(value, bool)
                        or not minimum <= value <= maximum
                    ):
                        errors.append(f"archive {field} is outside supported bounds")
                compression_methods = archive.get("allowed_compression_methods")
                if (
                    not isinstance(compression_methods, list)
                    or not compression_methods
                    or compression_methods
                    != sorted(set(str(item) for item in compression_methods))
                    or not set(compression_methods).issubset({"stored", "deflated"})
                ):
                    errors.append(
                        "archive allowed_compression_methods must be a canonical safe subset"
                    )
    elif adapter.family == "arcgis_imageserver":
        parsed = urllib.parse.urlsplit(adapter.endpoint or "")
        if not parsed.path.rstrip("/").endswith("/ImageServer"):
            errors.append("arcgis_imageserver endpoint must end in /ImageServer")
        if parsed.query or parsed.fragment:
            errors.append("arcgis_imageserver endpoint must not contain query or fragment data")
        if contract.get("operation") != "exportImage":
            errors.append("arcgis_imageserver family_contract.operation must be exportImage")
        for field in ("bbox_crs", "export_crs"):
            if not re.fullmatch(r"EPSG:[1-9][0-9]{2,5}", str(contract.get(field) or "")):
                errors.append(f"arcgis_imageserver {field} must be an explicit EPSG code")
        dimensions = contract.get("maximum_dimensions")
        if (
            not isinstance(dimensions, Mapping)
            or not isinstance(dimensions.get("width"), int)
            or not isinstance(dimensions.get("height"), int)
            or not 1 <= int(dimensions["width"]) <= 16_384
            or not 1 <= int(dimensions["height"]) <= 16_384
        ):
            errors.append("arcgis_imageserver requires bounded maximum_dimensions")
        if contract.get("temporal_parameter") not in {"time", "mosaicRule"}:
            errors.append("arcgis_imageserver requires an explicit temporal_parameter")
        fixed_query = contract.get("fixed_query_parameters")
        if not isinstance(fixed_query, Mapping) or fixed_query != {
            "adjustAspectRatio": "false",
            "f": "image",
            "format": "tiff",
        }:
            errors.append(
                "arcgis_imageserver fixed_query_parameters must request a TIFF image "
                "without server-side aspect-ratio expansion"
            )
        if contract.get("tiling_order") != "row_major_exact_grid":
            errors.append(
                "arcgis_imageserver tiling_order must be row_major_exact_grid"
            )
        estimated_bytes = contract.get("estimated_bytes_per_pixel")
        if (
            not isinstance(estimated_bytes, int)
            or isinstance(estimated_bytes, bool)
            or not 1 <= estimated_bytes <= 64
        ):
            errors.append(
                "arcgis_imageserver estimated_bytes_per_pixel is outside supported bounds"
            )
        query_order = contract.get("query_parameter_order")
        expected_query_keys = {
            "adjustAspectRatio",
            "bbox",
            "bboxSR",
            "f",
            "format",
            "imageSR",
            "size",
            str(contract.get("temporal_parameter") or ""),
        }
        if (
            not isinstance(query_order, list)
            or query_order != sorted(set(str(item) for item in query_order))
            or set(query_order) != expected_query_keys
        ):
            errors.append(
                "ArcGIS query parameters require the exact canonical key set"
            )
        if source.semantic_type == "categorical" and source.resampling not in {"nearest", "mode"}:
            errors.append("ArcGIS categorical exports require nearest or mode resampling")
    elif adapter.family == "stac_search":
        parsed = urllib.parse.urlsplit(adapter.endpoint or "")
        if parsed.query or parsed.fragment:
            errors.append("stac_search endpoint must be a root URL without query or fragment data")
        search_path = str(contract.get("search_path") or "")
        if not search_path.startswith("/") or ".." in PurePosixPath(search_path).parts:
            errors.append("stac_search requires a safe absolute search_path")
        if not identity["collection"]:
            errors.append("stac_search requires collection identity")
        if contract.get("temporal_representation") not in {"datetime", "interval"}:
            errors.append("stac_search requires datetime or interval temporal representation")
        if contract.get("bbox_crs") != "EPSG:4326":
            errors.append("stac_search bbox_crs must be EPSG:4326")
        selection = contract.get("asset_selection")
        if not isinstance(selection, Mapping):
            errors.append("stac_search requires deterministic asset_selection")
        else:
            required_roles = selection.get("required_roles")
            if not isinstance(required_roles, list) or not required_roles:
                errors.append("stac_search asset_selection requires required_roles")
            elif not set(str(item) for item in required_roles).issubset(adapter.asset_roles):
                errors.append("STAC required asset roles must be declared by adapter.asset_roles")
            required_media = selection.get("required_media_types")
            if not isinstance(required_media, list) or not required_media:
                errors.append("stac_search asset_selection requires required_media_types")
            elif not set(str(item) for item in required_media).issubset(adapter.media_types):
                errors.append("STAC required media types must be declared by adapter.media_types")
            if selection.get("item_order") != ["datetime", "id"]:
                errors.append("STAC item selection order must be ['datetime', 'id']")
            item_limit = selection.get("item_limit")
            if not isinstance(item_limit, int) or not 1 <= item_limit <= 100:
                errors.append("STAC item_limit must be between 1 and 100")
        if not pack.manifest.access.asset_hosts and not getattr(
            pack.manifest.access,
            "asset_host_suffixes",
            [],
        ):
            errors.append(
                "stac_search requires a separate nonempty asset host scope"
            )
        if sorted(contract.get("asset_host_scope") or []) != sorted(
            pack.manifest.access.asset_hosts
        ):
            errors.append("STAC asset_host_scope must exactly match access.asset_hosts")
    elif adapter.family == "verified_local_raster":
        if adapter.endpoint or adapter.url_template:
            errors.append("verified_local_raster must not declare a network endpoint")
        if not re.fullmatch(r"[a-f0-9]{64}", str(adapter.local_sha256 or "")):
            errors.append("verified_local_raster requires a lowercase SHA-256 checksum")
        raster_identity = contract.get("raster_identity")
        if not isinstance(raster_identity, Mapping):
            errors.append("verified_local_raster requires family_contract.raster_identity")
        else:
            expected = {
                "media_types": sorted(adapter.media_types),
                "asset_roles": sorted(adapter.asset_roles),
                "crs": source.crs,
                "semantic_type": source.semantic_type,
                "nodata": source.nodata,
                "mask_policy": source.mask_policy,
                "resampling": source.resampling,
            }
            observed = {
                "media_types": sorted(raster_identity.get("media_types") or []),
                "asset_roles": sorted(raster_identity.get("asset_roles") or []),
                "crs": raster_identity.get("crs"),
                "semantic_type": raster_identity.get("semantic_type"),
                "nodata": raster_identity.get("nodata"),
                "mask_policy": raster_identity.get("mask_policy"),
                "resampling": raster_identity.get("resampling"),
            }
            if observed != expected:
                errors.append(
                    "verified local raster identity must match the explicit source contract"
                )
        delivery = contract.get("delivery")
        if not isinstance(delivery, Mapping):
            errors.append("verified_local_raster requires a delivery contract")
        elif delivery != {
            "mode": "source_pack_member",
            "member": adapter.local_path,
            "sha256": adapter.local_sha256,
        }:
            errors.append(
                "verified local delivery must exactly identify the checksum-pinned pack member"
            )
    elif adapter.family == "earth_engine_compute":
        if not isinstance(pack.manifest, SourcePackManifestV2):
            errors.append("earth_engine_compute requires a v2 Source Pack")
        else:
            if adapter.endpoint or adapter.url_template or adapter.local_path:
                errors.append(
                    "earth_engine_compute must not declare a download endpoint or path"
                )
            earth_engine = pack.manifest.earth_engine
            if earth_engine is None:
                errors.append("earth_engine_compute requires earth_engine metadata")
            else:
                band_names = [item.name for item in earth_engine.bands]
                if band_names != sorted(set(band_names)):
                    errors.append("Earth Engine band names must be canonical and unique")
                if not set(adapter.asset_roles).issubset(set(band_names)):
                    errors.append(
                        "Earth Engine adapter roles must be declared dataset bands"
                    )
                if (
                    earth_engine.dataset_type == "image_collection"
                    and "select_first" not in earth_engine.allowed_operations
                ):
                    errors.append(
                        "Earth Engine image collections require deterministic terminal selection"
                    )
                if source.semantic_type == "categorical" and source.resampling != "nearest":
                    errors.append(
                        "Earth Engine categorical sources require nearest resampling"
                    )
    return identity, evidence_result, contract


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
    asset_hosts: set[str] = set()
    for host in manifest.access.asset_hosts:
        try:
            asset_hosts.add(_valid_host(host))
        except ValueError as exc:
            errors.append(str(exc))
    asset_host_suffixes: set[str] = set()
    resolver_hosts: set[str] = set()
    resolver_host_suffixes: set[str] = set()
    if isinstance(manifest, SourcePackManifestV2):
        for host in manifest.access.asset_host_suffixes:
            try:
                asset_host_suffixes.add(_valid_host_suffix(host))
            except ValueError as exc:
                errors.append(str(exc))
        for host in manifest.access.resolver_hosts:
            try:
                resolver_hosts.add(_valid_host(host))
            except ValueError as exc:
                errors.append(str(exc))
        for host in manifest.access.resolver_host_suffixes:
            try:
                resolver_host_suffixes.add(_valid_host_suffix(host))
            except ValueError as exc:
                errors.append(str(exc))
    adapter = manifest.adapter
    url = adapter.url_template or adapter.endpoint
    if adapter.family == "static_https_template" and not adapter.url_template:
        errors.append("static_https_template requires adapter.url_template")
    if adapter.family in {"arcgis_imageserver", "stac_search"} and not adapter.endpoint:
        errors.append(f"{adapter.family} requires adapter.endpoint")
    if adapter.family == "verified_local_raster":
        if not adapter.local_path or not adapter.local_sha256:
            errors.append("verified_local_raster requires local_path and local_sha256")
        if allowed_hosts or redirect_hosts or asset_hosts:
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
    elif adapter.family == "earth_engine_compute":
        if not isinstance(manifest, SourcePackManifestV2):
            errors.append("earth_engine_compute requires a v2 Source Pack")
        if allowed_hosts or redirect_hosts or asset_hosts or asset_host_suffixes:
            errors.append(
                "earth_engine_compute source metadata must not declare asset transport hosts"
            )
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
    if source.mask_policy == "explicit_nodata" and source.nodata is None:
        errors.append("explicit_nodata mask semantics require a nodata value")
    if source.mask_policy == "all_valid" and source.nodata is not None:
        errors.append("all_valid mask semantics require nodata to be null")
    if source.nodata is None and source.mask_policy == "none":
        errors.append("source must declare nodata or mask semantics")
    identity, provider_evidence, family_contract = _family_contract(
        pack,
        errors=errors,
        warnings=warnings,
    )
    access = manifest.access
    if isinstance(manifest, SourcePackManifestV2):
        asset_access = manifest.asset_access
        if adapter.family == "stac_search" and asset_access is None:
            errors.append("v2 STAC sources require a first-class asset_access contract")
        if adapter.family != "stac_search" and asset_access is not None:
            errors.append("asset_access is currently valid only for stac_search")
        if isinstance(asset_access, BrokeredSignedHttpsAssetAccess):
            resolver_url_errors = _url_errors(
                asset_access.resolver.endpoint,
                resolver_hosts,
            )
            errors.extend(resolver_url_errors)
        if isinstance(asset_access, (PublicS3AssetAccess, RequesterPaysS3AssetAccess)):
            s3_identity = asset_access.stable_identity
            if not re.fullmatch(
                r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]",
                s3_identity.bucket,
            ):
                errors.append("S3 bucket identity is invalid")
            if not re.fullmatch(
                r"[a-z]{2}(?:-gov)?-[a-z]+-[1-9]",
                s3_identity.region,
            ):
                errors.append("S3 region identity is invalid")
        if isinstance(asset_access, S3CompatibleCredentialedAssetAccess):
            errors.extend(_url_errors(asset_access.endpoint, asset_hosts))
        if access.authentication_scheme != "none":
            errors.append(
                "v2 Source Packs declare operation credentials in asset_access, "
                "not a plan-wide access authentication scheme"
            )
    else:
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
    provider_evidence_complete = provider_evidence["status"] in {
        "complete",
        "synthetic",
    } and bool(identity["provider"]) and bool(identity["product"])
    family_contract_valid = not errors
    result = {
        "schema_version": "fasterraster.source-pack-validation/v1",
        "status": "PASS" if not errors else "FAIL",
        "pack_id": manifest.pack_id,
        "source_pack_sha256": pack.source_pack_sha256,
        "adapter_family": adapter.family,
        "structural_status": "SCHEMA_VALID",
        "family_contract_status": "VALID" if family_contract_valid else "INVALID",
        "provider_evidence_status": (
            "COMPLETE" if provider_evidence_complete else "INCOMPLETE"
        ),
        "planning_readiness": (
            "READY_FOR_OFFLINE_DETERMINISTIC_PLANNING"
            if not errors and provider_evidence_complete
            else "BLOCKED_BEFORE_NETWORK"
        ),
        "credential_required": credential_requirement(manifest) is not None,
        "network_requests": 0,
        "network_bytes": 0,
        "checks": {
            "schema": not any("manifest" in item for item in errors),
            "hosts": not any("host" in item for item in errors),
            "family_contract": family_contract_valid,
            "provider_evidence_complete": provider_evidence_complete,
            "secrets": not any("secret" in item.lower() or "token" in item.lower() for item in errors),
            "categorical_resampling": not any("categorical" in item for item in errors),
            "preview_compatibility": not any("preview" in item for item in errors),
        },
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "identity": identity,
        "provider_evidence": provider_evidence,
        "family_contract": family_contract,
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
            "structural_status": "SCHEMA_INVALID",
            "family_contract_status": "NOT_EVALUATED",
            "provider_evidence_status": "NOT_EVALUATED",
            "planning_readiness": "BLOCKED_BEFORE_NETWORK",
            "credential_required": None,
            "network_requests": 0,
            "network_bytes": 0,
            "checks": {},
            "errors": [str(exc)],
            "warnings": [],
            "identity": {},
            "provider_evidence": {},
            "family_contract": {},
            "validation_sha256": None,
        }


def credential_requirement(
    manifest: SourcePackManifest | SourcePackManifestV2,
) -> dict[str, Any] | None:
    if isinstance(manifest, SourcePackManifestV2):
        asset_access = manifest.asset_access
        if isinstance(asset_access, RequesterPaysS3AssetAccess):
            scheme = "aws_sigv4"
            operations = ["asset"]
            resolver_capability = "aws_sigv4_requester_pays"
        elif isinstance(asset_access, BearerHttpsAssetAccess):
            scheme = "oauth2_bearer"
            operations = ["asset"]
            resolver_capability = "host_bound_bearer"
        elif isinstance(asset_access, BrokeredSignedHttpsAssetAccess):
            scheme = "ephemeral_https_signer"
            operations = ["resolver"]
            resolver_capability = asset_access.resolver.scheme
        elif isinstance(asset_access, S3CompatibleCredentialedAssetAccess):
            scheme = "s3_compatible"
            operations = ["asset"]
            resolver_capability = "s3_compatible_credentials"
        elif manifest.adapter.family == "earth_engine_compute":
            scheme = "google_adc"
            operations = ["compute"]
            resolver_capability = "google_adc_project_bound"
        else:
            return None
        result = {
            "schema_version": CREDENTIAL_SCHEMA_VERSION_V2,
            "credential_scheme": scheme,
            "operations": operations,
            "exact_hosts": {
                "catalogue": sorted(manifest.access.allowed_hosts),
                "resolver": sorted(manifest.access.resolver_hosts),
                "asset": sorted(manifest.access.asset_hosts),
            },
            "host_suffixes": {
                "resolver": sorted(manifest.access.resolver_host_suffixes),
                "asset": sorted(manifest.access.asset_host_suffixes),
            },
            "resolver_capability_required": resolver_capability,
            "resolved_secret_present": False,
        }
        result["credential_requirement_sha256"] = _canonical_hash(
            result,
            {"credential_requirement_sha256"},
        )
        return result
    access = manifest.access
    if access.authentication_scheme == "none":
        return None
    result = {
        "schema_version": CREDENTIAL_SCHEMA_VERSION,
        "authentication_scheme": access.authentication_scheme,
        "credential_ref": access.credential_ref,
        "allowed_hosts": sorted(access.allowed_hosts),
        "redirect_hosts": sorted(access.redirect_hosts),
        "asset_hosts": sorted(access.asset_hosts),
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
    provider_evidence_complete = validation["provider_evidence_status"] == "COMPLETE"
    if not provider_evidence_complete:
        status = "BLOCKED_PROVIDER_EVIDENCE"
    elif temporal_alternatives and temporal_resolution is None:
        status = temporal_alternatives["status"]
    elif requirement is not None:
        status = "CREDENTIAL_REQUIRED"
    else:
        status = "READY"
    endpoint_contract: dict[str, Any]
    if manifest.adapter.family == "verified_local_raster":
        endpoint_contract = {
            "kind": "verified_local_reference",
            "local_reference": manifest.adapter.local_path,
            "local_sha256": manifest.adapter.local_sha256,
            "family_contract": validation["family_contract"],
        }
    elif manifest.adapter.family == "earth_engine_compute":
        assert isinstance(manifest, SourcePackManifestV2)
        endpoint_contract = {
            "kind": "earth_engine_compute",
            "earth_engine": manifest.earth_engine.model_dump(mode="json")
            if manifest.earth_engine is not None
            else None,
            "family_contract": validation["family_contract"],
        }
    else:
        endpoint_contract = {
            "kind": manifest.adapter.family,
            "endpoint": manifest.adapter.endpoint,
            "url_template": manifest.adapter.url_template,
            "rendered_request_url": (
                _render_endpoint(pack)
                if manifest.adapter.family == "static_https_template"
                else None
            ),
            "family_contract": validation["family_contract"],
        }
    executable = status in {"READY", "CREDENTIAL_REQUIRED"}
    blocked_details = []
    if status == "BLOCKED_PROVIDER_EVIDENCE":
        blocked_details.append(
            "Complete official provider evidence before compiling executable work."
        )
    if status in {"AWAITING_TEMPORAL_SELECTION", "NO_TEMPORAL_ALTERNATIVES"}:
        blocked_details.append(
            "Select an explicit temporal candidate; the requested time was not changed."
        )
    is_v2 = isinstance(manifest, SourcePackManifestV2)
    source_content_identity = {
        "identity": validation["identity"],
        "source_contract": manifest.source.model_dump(mode="json"),
        "requested_time": requested,
        "resolved_time": resolved_time,
        "family_contract": validation["family_contract"],
    }
    stable = {
        "schema_version": (
            SOURCE_PLAN_SCHEMA_VERSION_V2 if is_v2 else SOURCE_PLAN_SCHEMA_VERSION
        ),
        "pack_id": manifest.pack_id,
        "source_pack_sha256": pack.source_pack_sha256,
        "status": status,
        "executable": executable,
        "blocked_before_network": not executable,
        "blocked_details": blocked_details,
        "identity": validation["identity"],
        "provider_evidence": validation["provider_evidence"],
        "adapter": {
            "family": manifest.adapter.family,
            "public_adapter_id": {
                "static_https_template": "generic_https_template",
                "arcgis_imageserver": "arcgis_imageserver",
                "stac_search": "stac_api",
                "verified_local_raster": "verified_local_raster",
                "earth_engine_compute": "earth_engine_compute",
            }[manifest.adapter.family],
            "media_types": manifest.adapter.media_types,
            "asset_roles": manifest.adapter.asset_roles,
        },
        "endpoint_contract": endpoint_contract,
        "capabilities": manifest.capabilities.model_dump(mode="json"),
        "public_capability": {
            "status": "experimental",
            "release": "Unreleased",
            "public_execution": "validation_and_bounded_probe_only",
            "private_execution_available_from_public_repository": False,
        },
        "source_contract": manifest.source.model_dump(mode="json"),
        "requested_time": requested,
        "resolved_time": resolved_time,
        "temporal_contract": manifest.temporal.model_dump(mode="json"),
        "temporal_alternatives": temporal_alternatives,
        "temporal_resolution": temporal_resolution,
        "credential_requirement": requirement,
        "network_policy": {
            "default": "disabled",
            "max_requests": manifest.network.max_requests,
            "max_bytes": manifest.network.max_bytes,
            "max_asset_bytes": manifest.network.max_asset_bytes,
            "max_total_bytes": manifest.network.max_total_bytes,
            "timeout_seconds": manifest.network.timeout_seconds,
            "maximum_redirects": manifest.network.maximum_redirects,
            "max_parallel_requests": manifest.network.max_parallel_requests,
            "request_hosts": sorted(manifest.access.allowed_hosts),
            "redirect_hosts": sorted(manifest.access.redirect_hosts),
            "asset_hosts": sorted(manifest.access.asset_hosts),
            **(
                {
                    "asset_host_suffixes": sorted(
                        manifest.access.asset_host_suffixes
                    ),
                    "resolver_hosts": sorted(manifest.access.resolver_hosts),
                    "resolver_host_suffixes": sorted(
                        manifest.access.resolver_host_suffixes
                    ),
                }
                if is_v2
                else {}
            ),
        },
        "preview": manifest.preview.model_dump(mode="json"),
        "validation": {
            "schema": validation["structural_status"],
            "family_contract": validation["family_contract_status"],
            "provider_evidence": validation["provider_evidence_status"],
            "offline_planning": validation["planning_readiness"],
            "validation_sha256": validation["validation_sha256"],
        },
        "original_pack_unchanged": True,
        **(
            {
                "source_content_identity_sha256": _canonical_hash(
                    source_content_identity
                ),
                "asset_access": (
                    manifest.asset_access.model_dump(mode="json")
                    if manifest.asset_access is not None
                    else None
                ),
                "evidence_bundle_sha256": validation["provider_evidence"][
                    "evidence_sha256"
                ]
            }
            if is_v2
            else {}
        ),
    }
    stable["plan_sha256"] = _canonical_hash(stable, {"plan_sha256"})
    return stable


def _load_json_object(
    source: Path | Mapping[str, Any],
    *,
    contract_name: str,
) -> dict[str, Any]:
    if isinstance(source, Path):
        try:
            value = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"unable to read {contract_name}: {exc}") from exc
    else:
        value = dict(source)
    if not isinstance(value, dict):
        raise ValueError(f"{contract_name} must be a JSON object")
    return value


def _validated_frozen_source_plan(
    source: Path | Mapping[str, Any],
) -> dict[str, Any]:
    plan = _load_json_object(source, contract_name="frozen Source Pack plan")
    if plan.get("schema_version") not in {
        SOURCE_PLAN_SCHEMA_VERSION,
        SOURCE_PLAN_SCHEMA_VERSION_V2,
    }:
        raise ValueError(
            f"unsupported Source Pack plan schema version: {plan.get('schema_version')!r}"
        )
    expected_hash = plan.get("plan_sha256")
    if (
        not isinstance(expected_hash, str)
        or not re.fullmatch(r"[a-f0-9]{64}", expected_hash)
        or expected_hash != _canonical_hash(plan, {"plan_sha256"})
    ):
        raise ValueError("Source Pack plan SHA-256 mismatch")
    if (
        plan.get("status") not in {"READY", "CREDENTIAL_REQUIRED"}
        or plan.get("executable") is not True
        or plan.get("blocked_before_network") is not False
    ):
        raise ValueError("Source Pack plan is blocked or non-executable")
    validation = plan.get("validation")
    required_validation = {
        "schema": "SCHEMA_VALID",
        "family_contract": "VALID",
        "provider_evidence": "COMPLETE",
        "offline_planning": "READY_FOR_OFFLINE_DETERMINISTIC_PLANNING",
    }
    if not isinstance(validation, Mapping) or any(
        validation.get(field) != expected
        for field, expected in required_validation.items()
    ):
        raise ValueError("Source Pack plan validation state is not executable")
    if plan.get("temporal_alternatives") is not None and plan.get(
        "temporal_resolution"
    ) is None:
        raise ValueError("Source Pack plan has unresolved temporal alternatives")
    return plan


def compile_source_materialization_request(
    source_plan: Path | Mapping[str, Any],
    *,
    requested_asset_roles: Sequence[str],
    full_object: bool = False,
    bbox: Sequence[int | float] | None = None,
    bbox_crs: str | None = None,
    output_width: int | None = None,
    output_height: int | None = None,
    output_crs: str | None = None,
    output_transform: Sequence[int | float] | None = None,
    credential_ref: str | None = None,
    project_ref: str | None = None,
    allow_chargeable_access: bool = False,
    max_network_requests: int | None = None,
    max_network_bytes: int | None = None,
    max_compute_requests: int = 0,
) -> dict[str, Any]:
    """Compile deterministic per-study intent without network access."""
    plan = _validated_frozen_source_plan(source_plan)
    if plan.get("schema_version") == SOURCE_PLAN_SCHEMA_VERSION_V2:
        return _compile_source_materialization_request_v2(
            plan,
            requested_asset_roles=requested_asset_roles,
            full_object=full_object,
            bbox=bbox,
            bbox_crs=bbox_crs,
            output_width=output_width,
            output_height=output_height,
            output_crs=output_crs,
            output_transform=output_transform,
            credential_ref=credential_ref,
            project_ref=project_ref,
            allow_chargeable_access=allow_chargeable_access,
            max_network_requests=max_network_requests,
            max_network_bytes=max_network_bytes,
            max_compute_requests=max_compute_requests,
        )
    adapter = plan.get("adapter")
    if not isinstance(adapter, Mapping):
        raise ValueError("Source Pack plan adapter is missing")
    family = str(adapter.get("family") or "")
    declared_roles = [str(item) for item in adapter.get("asset_roles") or []]
    roles = sorted(set(str(item) for item in requested_asset_roles))
    if not roles:
        raise ValueError("requested_asset_roles must be nonempty")
    if any(not role or role not in declared_roles for role in roles):
        raise ValueError("requested asset roles must be declared by the Source Pack plan")

    spatial_families = {"stac_search", "arcgis_imageserver"}
    full_object_families = {"static_https_template", "verified_local_raster"}
    if family in spatial_families:
        if full_object:
            raise ValueError(f"{family} requires an explicit spatial bbox")
        if bbox is None or len(bbox) != 4:
            raise ValueError(f"{family} requires a four-value bbox")
        try:
            canonical_bbox = [float(item) for item in bbox]
        except (TypeError, ValueError) as exc:
            raise ValueError("bbox values must be finite numbers") from exc
        if not all(isfinite(item) for item in canonical_bbox):
            raise ValueError("bbox values must be finite numbers")
        if (
            canonical_bbox[0] >= canonical_bbox[2]
            or canonical_bbox[1] >= canonical_bbox[3]
        ):
            raise ValueError("bbox bounds must be strictly increasing")
        endpoint = plan.get("endpoint_contract")
        family_contract = (
            endpoint.get("family_contract")
            if isinstance(endpoint, Mapping)
            else None
        )
        required_crs = (
            family_contract.get("bbox_crs")
            if isinstance(family_contract, Mapping)
            else None
        )
        if (
            not isinstance(bbox_crs, str)
            or not re.fullmatch(r"EPSG:[1-9][0-9]{2,5}", bbox_crs)
            or bbox_crs != required_crs
        ):
            raise ValueError(
                f"bbox_crs must exactly match the frozen family contract {required_crs!r}"
            )
        spatial_request: dict[str, Any] = {
            "mode": "bbox",
            "bbox": canonical_bbox,
            "bbox_crs": bbox_crs,
        }
    elif family in full_object_families:
        if not full_object:
            raise ValueError(f"{family} requires an explicit full-object request")
        if bbox is not None or bbox_crs is not None:
            raise ValueError(f"{family} does not accept a spatial bbox")
        spatial_request = {"mode": "full_object"}
    else:
        raise ValueError(f"unsupported Source Pack adapter family: {family!r}")

    if family == "arcgis_imageserver":
        if (
            not isinstance(output_width, int)
            or isinstance(output_width, bool)
            or not isinstance(output_height, int)
            or isinstance(output_height, bool)
            or not 1 <= output_width <= 16_384
            or not 1 <= output_height <= 16_384
        ):
            raise ValueError(
                "ImageServer output width and height must be integers from 1 to 16384"
            )
        output_shape: dict[str, int] | None = {
            "width": output_width,
            "height": output_height,
        }
    else:
        if output_width is not None or output_height is not None:
            raise ValueError(f"{family} does not accept an output shape")
        output_shape = None

    stable = {
        "schema_version": MATERIALIZATION_REQUEST_SCHEMA_VERSION,
        "pack_id": plan["pack_id"],
        "source_plan_sha256": plan["plan_sha256"],
        "requested_asset_roles": roles,
        "spatial_request": spatial_request,
        "output_shape": output_shape,
        "original_source_plan_unchanged": True,
    }
    stable["materialization_request_sha256"] = _canonical_hash(
        stable,
        {"materialization_request_sha256"},
    )
    return stable


def _opaque_reference(value: str | None, *, name: str) -> str | None:
    if value is None:
        return None
    if not re.fullmatch(r"[a-z][a-z0-9._-]{2,127}", value):
        raise ValueError(f"{name} must be an opaque identifier with safe syntax")
    if re.search(
        r"(?i)(secret|password|private[_-]?key|access[_-]?token|session[_-]?token)",
        value,
    ):
        raise ValueError(f"{name} must not describe secret material")
    return value


def _compiled_earth_engine_selection(
    plan: Mapping[str, Any],
    *,
    roles: list[str],
    bbox: list[float],
) -> list[dict[str, Any]]:
    endpoint = plan.get("endpoint_contract")
    earth_engine = (
        endpoint.get("earth_engine")
        if isinstance(endpoint, Mapping)
        else None
    )
    if not isinstance(earth_engine, Mapping):
        raise ValueError("Earth Engine plan metadata is missing")
    dataset_id = str(earth_engine.get("dataset_id") or "")
    dataset_type = str(earth_engine.get("dataset_type") or "")
    allowed = set(str(item) for item in earth_engine.get("allowed_operations") or [])
    operations: list[dict[str, Any]]
    if dataset_type == "image":
        operations = [
            {"operation": "load_image", "dataset_id": dataset_id},
            {"operation": "select_bands", "bands": roles},
        ]
    elif dataset_type == "image_collection":
        operations = [
            {"operation": "load_collection", "dataset_id": dataset_id},
            {"operation": "filter_bounds", "bbox": bbox},
        ]
        requested_time = str(plan.get("resolved_time") or "")
        if "/" in requested_time:
            start, end = requested_time.split("/", 1)
            if not start or not end:
                raise ValueError("Earth Engine date interval is incomplete")
            operations.append(
                {
                    "operation": "filter_date",
                    "start": start,
                    "end": end,
                }
            )
        operations.extend(
            [
                {
                    "operation": "sort_acquisition_time",
                    "property": "system:time_start",
                    "direction": "ascending",
                },
                {
                    "operation": "tie_break_system_index",
                    "property": "system:index",
                    "direction": "ascending",
                },
                {"operation": "select_first"},
                {"operation": "select_bands", "bands": roles},
            ]
        )
    else:
        raise ValueError("unsupported Earth Engine dataset type")
    selected = {str(item["operation"]) for item in operations}
    if not selected.issubset(allowed):
        missing = ", ".join(sorted(selected - allowed))
        raise ValueError(
            "Earth Engine selection requires undeclared operations: " + missing
        )
    return operations


def _compile_source_materialization_request_v2(
    plan: Mapping[str, Any],
    *,
    requested_asset_roles: Sequence[str],
    full_object: bool,
    bbox: Sequence[int | float] | None,
    bbox_crs: str | None,
    output_width: int | None,
    output_height: int | None,
    output_crs: str | None,
    output_transform: Sequence[int | float] | None,
    credential_ref: str | None,
    project_ref: str | None,
    allow_chargeable_access: bool,
    max_network_requests: int | None,
    max_network_bytes: int | None,
    max_compute_requests: int,
) -> dict[str, Any]:
    adapter = plan.get("adapter")
    if not isinstance(adapter, Mapping):
        raise ValueError("Source Pack plan adapter is missing")
    family = str(adapter.get("family") or "")
    declared_roles = [str(item) for item in adapter.get("asset_roles") or []]
    roles = sorted(set(str(item) for item in requested_asset_roles))
    if not roles or any(role not in declared_roles for role in roles):
        raise ValueError("requested asset roles must be a declared nonempty subset")

    if family in {"stac_search", "arcgis_imageserver", "earth_engine_compute"}:
        if full_object or bbox is None or len(bbox) != 4:
            raise ValueError(f"{family} requires a four-value bbox")
        try:
            canonical_bbox = [float(item) for item in bbox]
        except (TypeError, ValueError) as exc:
            raise ValueError("bbox values must be finite numbers") from exc
        if (
            not all(isfinite(item) for item in canonical_bbox)
            or canonical_bbox[0] >= canonical_bbox[2]
            or canonical_bbox[1] >= canonical_bbox[3]
        ):
            raise ValueError("bbox must contain finite, strictly increasing bounds")
        family_contract = plan["endpoint_contract"]["family_contract"]
        required_crs = (
            "EPSG:4326"
            if family == "earth_engine_compute"
            else family_contract.get("bbox_crs")
        )
        if bbox_crs != required_crs:
            raise ValueError(
                f"bbox_crs must exactly match the frozen family contract {required_crs!r}"
            )
        spatial_request: dict[str, Any] = {
            "mode": "bbox",
            "bbox": canonical_bbox,
            "bbox_crs": bbox_crs,
        }
    elif family in {"static_https_template", "verified_local_raster"}:
        if not full_object or bbox is not None or bbox_crs is not None:
            raise ValueError(f"{family} requires an explicit full-object request")
        spatial_request = {"mode": "full_object"}
    else:
        raise ValueError(f"unsupported Source Pack adapter family: {family!r}")

    grid_values = (
        output_width,
        output_height,
        output_crs,
        output_transform,
    )
    if family == "earth_engine_compute" and any(item is None for item in grid_values):
        raise ValueError("earth_engine_compute requires a complete output grid")
    if any(item is not None for item in grid_values):
        if any(item is None for item in grid_values):
            raise ValueError(
                "output grid requires width, height, CRS, and six-value transform"
            )
        if (
            not isinstance(output_width, int)
            or isinstance(output_width, bool)
            or not isinstance(output_height, int)
            or isinstance(output_height, bool)
            or not 1 <= output_width <= 32_000
            or not 1 <= output_height <= 32_000
        ):
            raise ValueError("output grid dimensions are outside supported bounds")
        if not isinstance(output_crs, str) or not re.fullmatch(
            r"EPSG:[1-9][0-9]{2,5}",
            output_crs,
        ):
            raise ValueError("output grid CRS must be an explicit EPSG code")
        if output_transform is None or len(output_transform) != 6:
            raise ValueError("output grid transform must contain six values")
        transform = [float(item) for item in output_transform]
        if not all(isfinite(item) for item in transform):
            raise ValueError("output grid transform values must be finite")
        output_grid: dict[str, Any] | None = {
            "crs": output_crs,
            "transform": transform,
            "width": output_width,
            "height": output_height,
        }
    else:
        output_grid = None

    earth_engine_selection = (
        _compiled_earth_engine_selection(
            plan,
            roles=roles,
            bbox=canonical_bbox,
        )
        if family == "earth_engine_compute"
        else None
    )

    requirement = plan.get("credential_requirement")
    scheme = (
        str(requirement.get("credential_scheme"))
        if isinstance(requirement, Mapping)
        else None
    )
    credential = _opaque_reference(credential_ref, name="credential_ref")
    project = _opaque_reference(project_ref, name="project_ref")
    credential_required = scheme in {
        "aws_sigv4",
        "google_adc",
        "oauth2_bearer",
        "s3_compatible",
    }
    if credential_required and credential is None:
        raise ValueError(f"{scheme} requires an opaque credential_ref")
    if not credential_required and credential is not None:
        raise ValueError("credential_ref is not permitted for this source plan")
    if family == "earth_engine_compute" and project is None:
        raise ValueError("earth_engine_compute requires an opaque project_ref")
    if family != "earth_engine_compute" and project is not None:
        raise ValueError("project_ref is valid only for earth_engine_compute")

    access = plan.get("asset_access")
    access_mode = str(access.get("mode")) if isinstance(access, Mapping) else None
    chargeable = access_mode == "s3_requester_pays"
    if chargeable and allow_chargeable_access is not True:
        raise ValueError(
            "Requester Pays materialization requires explicit study consent"
        )

    policy = plan.get("network_policy")
    if not isinstance(policy, Mapping):
        raise ValueError("Source Pack plan network policy is missing")
    provider_request_limit = int(policy["max_requests"])
    provider_byte_limit = int(policy["max_total_bytes"])
    request_limit = (
        provider_request_limit
        if max_network_requests is None
        else int(max_network_requests)
    )
    byte_limit = (
        provider_byte_limit
        if max_network_bytes is None
        else int(max_network_bytes)
    )
    if (
        not 0 <= request_limit <= provider_request_limit
        or not 0 <= byte_limit <= provider_byte_limit
    ):
        raise ValueError("materialization limits may narrow but not widen the plan")
    if family == "earth_engine_compute":
        if not 1 <= max_compute_requests <= provider_request_limit:
            raise ValueError("Earth Engine compute request ceiling is invalid")
    elif max_compute_requests != 0:
        raise ValueError("compute requests are valid only for earth_engine_compute")

    authorization = {
        "credential_ref": credential,
        "project_ref": project,
        "allow_chargeable_access": bool(allow_chargeable_access),
    }
    limits = {
        "max_network_requests": request_limit,
        "max_network_bytes": byte_limit,
        "max_compute_requests": int(max_compute_requests),
    }
    content_inputs = {
        "source_content_identity_sha256": plan[
            "source_content_identity_sha256"
        ],
        "requested_asset_roles": roles,
        "spatial_request": spatial_request,
        "output_grid": output_grid,
        "earth_engine_selection": earth_engine_selection,
    }
    stable = {
        "schema_version": MATERIALIZATION_REQUEST_SCHEMA_VERSION_V2,
        "pack_id": plan["pack_id"],
        "source_plan_sha256": plan["plan_sha256"],
        "requested_asset_roles": roles,
        "spatial_request": spatial_request,
        "output_grid": output_grid,
        "earth_engine_selection": earth_engine_selection,
        "authorization": authorization,
        "limits": limits,
        "materialization_content_sha256": _canonical_hash(content_inputs),
        "original_source_plan_unchanged": True,
    }
    stable["materialization_request_sha256"] = _canonical_hash(
        stable,
        {"materialization_request_sha256"},
    )
    return stable


def validate_source_materialization_request(
    source_plan: Path | Mapping[str, Any],
    request: Path | Mapping[str, Any],
) -> dict[str, Any]:
    plan = _validated_frozen_source_plan(source_plan)
    value = _load_json_object(
        request,
        contract_name="Source Pack materialization request",
    )
    if value.get("schema_version") == MATERIALIZATION_REQUEST_SCHEMA_VERSION_V2:
        return _validate_v2_materialization_request(plan, value)
    expected_keys = {
        "schema_version",
        "pack_id",
        "source_plan_sha256",
        "requested_asset_roles",
        "spatial_request",
        "output_shape",
        "original_source_plan_unchanged",
        "materialization_request_sha256",
    }
    if set(value) != expected_keys:
        raise ValueError("materialization request contains unknown or missing fields")
    if value.get("schema_version") != MATERIALIZATION_REQUEST_SCHEMA_VERSION:
        raise ValueError(
            "unsupported Source Pack materialization-request schema version"
        )
    if value.get("pack_id") != plan.get("pack_id"):
        raise ValueError("materialization request pack_id differs from the plan")
    if value.get("source_plan_sha256") != plan.get("plan_sha256"):
        raise ValueError("materialization request references a different plan hash")
    if value.get("original_source_plan_unchanged") is not True:
        raise ValueError("materialization request must preserve the frozen source plan")
    expected_hash = value.get("materialization_request_sha256")
    if (
        not isinstance(expected_hash, str)
        or not re.fullmatch(r"[a-f0-9]{64}", expected_hash)
        or expected_hash
        != _canonical_hash(value, {"materialization_request_sha256"})
    ):
        raise ValueError("materialization request SHA-256 mismatch")
    spatial = value.get("spatial_request")
    if not isinstance(spatial, Mapping):
        raise ValueError("materialization request spatial_request is missing")
    mode = spatial.get("mode")
    compiled = compile_source_materialization_request(
        plan,
        requested_asset_roles=value.get("requested_asset_roles") or [],
        full_object=mode == "full_object",
        bbox=spatial.get("bbox") if mode == "bbox" else None,
        bbox_crs=spatial.get("bbox_crs") if mode == "bbox" else None,
        output_width=(
            value["output_shape"].get("width")
            if isinstance(value.get("output_shape"), Mapping)
            else None
        ),
        output_height=(
            value["output_shape"].get("height")
            if isinstance(value.get("output_shape"), Mapping)
            else None
        ),
    )
    if value != compiled:
        raise ValueError("materialization request is not canonical")
    return value


def _validate_v2_materialization_request(
    plan: Mapping[str, Any],
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if plan.get("schema_version") != SOURCE_PLAN_SCHEMA_VERSION_V2:
        raise ValueError("v2 materialization request requires a v2 source plan")
    expected_keys = {
        "schema_version",
        "pack_id",
        "source_plan_sha256",
        "requested_asset_roles",
        "spatial_request",
        "output_grid",
        "earth_engine_selection",
        "authorization",
        "limits",
        "materialization_content_sha256",
        "original_source_plan_unchanged",
        "materialization_request_sha256",
    }
    if set(value) != expected_keys:
        raise ValueError("v2 materialization request contains unknown or missing fields")
    if value.get("pack_id") != plan.get("pack_id"):
        raise ValueError("materialization request pack_id differs from the plan")
    if value.get("source_plan_sha256") != plan.get("plan_sha256"):
        raise ValueError("materialization request references a different plan hash")
    if value.get("original_source_plan_unchanged") is not True:
        raise ValueError("materialization request must preserve the frozen source plan")
    request_hash = value.get("materialization_request_sha256")
    content_hash = value.get("materialization_content_sha256")
    if (
        not isinstance(request_hash, str)
        or not re.fullmatch(r"[a-f0-9]{64}", request_hash)
        or request_hash
        != _canonical_hash(value, {"materialization_request_sha256"})
    ):
        raise ValueError("materialization request SHA-256 mismatch")
    if not isinstance(content_hash, str) or not re.fullmatch(
        r"[a-f0-9]{64}",
        content_hash,
    ):
        raise ValueError("materialization content SHA-256 is malformed")
    spatial = value.get("spatial_request")
    output_grid = value.get("output_grid")
    authorization = value.get("authorization")
    limits = value.get("limits")
    if not all(
        isinstance(item, Mapping)
        for item in (spatial, authorization, limits)
    ):
        raise ValueError("v2 materialization request objects are incomplete")
    compiled = compile_source_materialization_request(
        plan,
        requested_asset_roles=value.get("requested_asset_roles") or [],
        full_object=spatial.get("mode") == "full_object",
        bbox=spatial.get("bbox") if spatial.get("mode") == "bbox" else None,
        bbox_crs=(
            spatial.get("bbox_crs") if spatial.get("mode") == "bbox" else None
        ),
        output_width=(
            output_grid.get("width")
            if isinstance(output_grid, Mapping)
            else None
        ),
        output_height=(
            output_grid.get("height")
            if isinstance(output_grid, Mapping)
            else None
        ),
        output_crs=(
            output_grid.get("crs")
            if isinstance(output_grid, Mapping)
            else None
        ),
        output_transform=(
            output_grid.get("transform")
            if isinstance(output_grid, Mapping)
            else None
        ),
        credential_ref=authorization.get("credential_ref"),
        project_ref=authorization.get("project_ref"),
        allow_chargeable_access=bool(
            authorization.get("allow_chargeable_access")
        ),
        max_network_requests=limits.get("max_network_requests"),
        max_network_bytes=limits.get("max_network_bytes"),
        max_compute_requests=int(limits.get("max_compute_requests") or 0),
    )
    if dict(value) != compiled:
        raise ValueError("v2 materialization request is not canonical")
    return dict(value)


def compile_source_pack_handoff(
    path: Path,
    output: Path,
    *,
    requested_time: str | int | None = None,
    selected_time: str | None = None,
) -> dict[str, Any]:
    """Write the existing v1 plan as a frozen, public-safe execution handoff."""
    plan = compile_source_pack_plan(
        path,
        requested_time=requested_time,
        selected_time=selected_time,
    )
    if not plan["executable"]:
        details = "; ".join(plan["blocked_details"]) or str(plan["status"])
        raise ValueError(f"blocked Source Pack cannot compile executable work: {details}")
    destination = output
    if output.suffix.lower() != ".json":
        destination = output / f"{plan['pack_id']}.source-pack-plan.json"
    write_json_atomic(destination, plan)
    return {
        "schema_version": "fasterraster.source-pack-handoff-compile/v1",
        "status": "PASS",
        "handoff_schema_version": plan["schema_version"],
        "pack_id": plan["pack_id"],
        "source_pack_sha256": plan["source_pack_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "handoff_path": str(destination),
        "handoff_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "credential_resolved": False,
        "network_requests": 0,
    }


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
        "executable": plan["executable"],
        "blocked_before_network": plan["blocked_before_network"],
        "blocked_details": plan["blocked_details"],
        "structural_status": validation["structural_status"],
        "family_contract_status": validation["family_contract_status"],
        "provider_evidence_status": validation["provider_evidence_status"],
        "offline_planning_status": validation["planning_readiness"],
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
        "public_capability": plan["public_capability"],
        "handoff_schema_version": plan["schema_version"],
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
