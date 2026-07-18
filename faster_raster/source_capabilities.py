from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from faster_raster import __version__
from faster_raster.local_config import ConfigDocument
from faster_raster.local_diagnostics import run_doctor
from faster_raster.local_paths import LocalPaths, ensure_local_directories
from faster_raster.source_registry import load_registry


SOURCE_STATUSES = (
    "available",
    "available_unverified_auth",
    "credential_missing",
    "credential_present_unverified",
    "authentication_failed",
    "unreachable",
    "timeout",
    "rate_limited",
    "service_error",
    "unsupported_local_format",
    "unsupported_local_driver",
    "invalid_response",
    "probe_not_supported",
    "skipped_offline",
    "disabled_by_user",
    "future_unverified",
    "stale",
    "unknown",
)

ACCESS_CATEGORIES = (
    "static_verified",
    "service_discovered",
    "api_discovered",
    "credential_gated",
    "future_unverified",
)

PROBE_IMPLEMENTATION_VERSION = "1"
DEFAULT_GLOBAL_BYTE_CEILING = 10_000_000


@dataclass(frozen=True)
class SourceDefinition:
    source_id: str
    provider: str
    product: str
    access_category: str
    probe_strategy: str
    endpoint: str | None
    logical_assets: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    credential_env: str | None = None
    required_driver: str | None = "GTiff"
    format_name: str | None = None
    timeout_seconds: float = 8.0
    byte_ceiling: int = 262_144
    request_ceiling: int = 1
    selectable: bool = True


SOURCE_OVERRIDES: dict[str, dict[str, Any]] = {
    "usda_nass_cdl_imageserver": {
        "access_category": "service_discovered",
        "probe_strategy": "service_metadata",
        "logical_assets": ("cdl_classes", "cdl_color", "crop_classes"),
        "aliases": ("cdl", "usda_cdl"),
    },
    "generic_demo_cog": {
        "access_category": "future_unverified",
        "probe_strategy": "none",
        "aliases": ("demo_cog",),
        "selectable": False,
    },
    "annual_nlcd_aws_tile": {
        "access_category": "credential_gated",
        "probe_strategy": "none",
        "logical_assets": ("fractional_imperviousness",),
        "aliases": ("annual_nlcd_tile",),
        "selectable": False,
    },
    "annual_nlcd_aws_mosaic": {
        "access_category": "credential_gated",
        "probe_strategy": "none",
        "logical_assets": ("fractional_imperviousness",),
        "aliases": ("annual_nlcd_mosaic",),
        "selectable": False,
    },
    "prism_time_series_daily_zip": {
        "access_category": "static_verified",
        "probe_strategy": "http_range",
        "logical_assets": ("precipitation",),
        "aliases": ("prism",),
        "required_driver": None,
        "format_name": "ZIP containing BIL",
    },
    "copernicus_sentinel2_l2a_cdse_stac": {
        "access_category": "credential_gated",
        "probe_strategy": "api_discovery",
        "logical_assets": ("multispectral_imagery",),
        "aliases": ("sentinel2", "cdse"),
        "credential_env": "FASTERRASTER_CDSE_TOKEN",
        "required_driver": "GTiff",
    },
}

AGRICULTURAL_SOURCES: dict[str, SourceDefinition] = {
    "usgs_naip_imageserver": SourceDefinition(
        source_id="usgs_naip_imageserver",
        provider="USGS",
        product="NAIP natural imagery and NDVI",
        access_category="service_discovered",
        probe_strategy="service_metadata",
        endpoint="https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPImagery/ImageServer",
        logical_assets=("natural", "ndvi", "natural_imagery"),
        aliases=("naip",),
    ),
    "usgs_3dep_imageserver": SourceDefinition(
        source_id="usgs_3dep_imageserver",
        provider="USGS",
        product="3DEP elevation and hillshade",
        access_category="service_discovered",
        probe_strategy="service_metadata",
        endpoint="https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer",
        logical_assets=("hillshade", "terrain"),
        aliases=("3dep", "three_dep"),
    ),
}


def _template_endpoint(source_id: str, entry: Any) -> str | None:
    if entry.base_url:
        return str(entry.base_url)
    if not entry.url_template:
        return None
    values = {
        key: getattr(entry, key, None)
        for key in (
            "product_slug",
            "region",
            "h",
            "v",
            "product_code",
            "collection",
            "version",
            "variable",
            "yyyymmdd",
            "resolution",
            "temporal_frequency",
        )
    }
    values.update(
        {
            "year": "2023",
            "thematic_layer": "default",
            "tile_id": getattr(entry, "template_tile_id", None) or "h14v15",
        }
    )
    try:
        return str(entry.url_template).format(**values)
    except (KeyError, TypeError, ValueError):
        return None


def shipped_source_definitions() -> dict[str, SourceDefinition]:
    registry = load_registry()
    definitions: dict[str, SourceDefinition] = {}
    for source_id, entry in registry.sources.items():
        override = SOURCE_OVERRIDES.get(source_id, {})
        if override:
            category = str(override["access_category"])
            strategy = str(override["probe_strategy"])
        elif entry.adapter == "arcgis_imageserver":
            category, strategy = "service_discovered", "service_metadata"
        elif entry.adapter == "generic_https_template":
            category, strategy = "static_verified", "http_range"
        else:
            category, strategy = "future_unverified", "none"
        definitions[source_id] = SourceDefinition(
            source_id=source_id,
            provider=entry.provider,
            product=entry.product,
            access_category=category,
            probe_strategy=strategy,
            endpoint=_template_endpoint(source_id, entry),
            logical_assets=tuple(override.get("logical_assets", ())),
            aliases=tuple(override.get("aliases", ())),
            credential_env=override.get("credential_env"),
            required_driver=override.get("required_driver", "GTiff"),
            format_name=override.get("format_name", entry.default_format or entry.default_image_format),
            selectable=bool(override.get("selectable", True)),
        )
    definitions.update(AGRICULTURAL_SOURCES)
    return dict(sorted(definitions.items()))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


class BoundedHTTPTransport:
    def __init__(self, global_byte_ceiling: int, global_request_ceiling: int = 100) -> None:
        if global_byte_ceiling <= 0 or global_byte_ceiling > DEFAULT_GLOBAL_BYTE_CEILING:
            raise ValueError(f"global probe byte ceiling must be between 1 and {DEFAULT_GLOBAL_BYTE_CEILING}")
        self.global_byte_ceiling = global_byte_ceiling
        self.global_request_ceiling = global_request_ceiling
        self.bytes_transferred = 0
        self.requests_made = 0
        self._opener = urllib.request.build_opener(_NoRedirect())

    def request(
        self,
        url: str,
        *,
        method: str,
        timeout: float,
        byte_ceiling: int,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        if self.requests_made >= self.global_request_ceiling:
            raise RuntimeError("global probe request ceiling exceeded")
        remaining = self.global_byte_ceiling - self.bytes_transferred
        if remaining <= 0:
            raise RuntimeError("global probe byte ceiling exceeded")
        limit = min(byte_ceiling, remaining)
        safe_headers = {"User-Agent": "FasterRaster-Capability-Probe/1", "Accept": "application/json,*/*;q=0.1"}
        if headers:
            safe_headers.update(headers)
        request = urllib.request.Request(url, headers=safe_headers, method=method)
        self.requests_made += 1
        with self._opener.open(request, timeout=timeout) as response:
            body = response.read(limit + 1)
            if len(body) > limit:
                self.bytes_transferred += limit
                raise RuntimeError("per-source probe byte ceiling exceeded")
            self.bytes_transferred += len(body)
            return {
                "status_code": int(response.status),
                "headers": dict(response.headers.items()),
                "body": body,
                "bytes": len(body),
            }


def _safe_endpoint(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _driver_compatible(required: str | None, drivers: Iterable[str]) -> bool:
    if not required:
        return True
    available = {item.lower() for item in drivers}
    aliases = {required.lower()}
    if required.lower() in {"gtiff", "geotiff"}:
        aliases.update({"gtiff", "cog"})
    return bool(aliases & available)


def _base_result(definition: SourceDefinition, timestamp: str) -> dict[str, Any]:
    return {
        "source_id": definition.source_id,
        "provider": definition.provider,
        "product": definition.product,
        "access_category": definition.access_category,
        "probe_strategy": definition.probe_strategy,
        "probe_timestamp": timestamp,
        "probe_duration_seconds": 0.0,
        "probe_implementation_version": PROBE_IMPLEMENTATION_VERSION,
        "bytes_transferred": 0,
        "requests_made": 0,
        "status": "unknown",
        "credential_state": "not_required" if not definition.credential_env else "unknown",
        "credential_env": definition.credential_env,
        "format_compatibility": {
            "format": definition.format_name,
            "required_driver": definition.required_driver,
            "compatible": None,
        },
        "evidence": [],
        "warnings": [],
    }


def probe_source(
    definition: SourceDefinition,
    *,
    transport: Any,
    drivers: Iterable[str],
    offline: bool,
    environ: Mapping[str, str],
    disabled: bool = False,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    started = time.monotonic()
    timestamp = now().isoformat()
    result = _base_result(definition, timestamp)
    before_bytes = int(getattr(transport, "bytes_transferred", 0))
    before_requests = int(getattr(transport, "requests_made", 0))
    compatible = _driver_compatible(definition.required_driver, drivers)
    result["format_compatibility"]["compatible"] = compatible
    try:
        if disabled:
            result["status"] = "disabled_by_user"
            result["evidence"].append("disabled by local user configuration")
            return result
        if definition.access_category == "future_unverified" or not definition.selectable:
            result["status"] = "future_unverified"
            result["evidence"].append("registered for future work; not selectable")
            return result
        if not compatible:
            result["status"] = "unsupported_local_driver"
            result["evidence"].append(f"required GDAL driver is unavailable: {definition.required_driver}")
            return result
        credential: str | None = None
        if definition.credential_env:
            credential = environ.get(definition.credential_env)
            result["credential_state"] = "present" if credential else "missing"
            if not credential:
                result["status"] = "credential_missing"
                result["evidence"].append(f"expected environment variable is not set: {definition.credential_env}")
                return result
        if offline:
            result["status"] = "credential_present_unverified" if credential else "skipped_offline"
            result["evidence"].append("network probe skipped because offline mode is active")
            return result
        if not definition.endpoint or definition.probe_strategy == "none":
            result["status"] = "probe_not_supported"
            result["evidence"].append("no bounded probe is defined")
            return result

        endpoint = definition.endpoint
        method = "GET"
        headers: dict[str, str] = {}
        if definition.probe_strategy == "service_metadata":
            separator = "&" if "?" in endpoint else "?"
            endpoint = endpoint + separator + "f=pjson"
        elif definition.probe_strategy == "http_range":
            headers["Range"] = f"bytes=0-{max(0, definition.byte_ceiling - 1)}"
        if credential:
            headers["Authorization"] = "Bearer " + credential

        response = transport.request(
            endpoint,
            method=method,
            timeout=definition.timeout_seconds,
            byte_ceiling=definition.byte_ceiling,
            headers=headers,
        )
        code = int(response.get("status_code", 0))
        result["http_status"] = code
        result["evidence"].append(f"bounded {definition.probe_strategy} request returned HTTP {code}")
        result["evidence"].append(f"endpoint: {_safe_endpoint(definition.endpoint)}")
        if code in (401, 403):
            result["status"] = "authentication_failed"
        elif code == 429:
            result["status"] = "rate_limited"
        elif code >= 500:
            result["status"] = "service_error"
        elif not 200 <= code < 300:
            result["status"] = "unreachable"
        elif definition.probe_strategy in {"service_metadata", "api_discovery"}:
            try:
                payload = json.loads(bytes(response.get("body", b"")).decode("utf-8"))
                if not isinstance(payload, dict) or payload.get("error"):
                    result["status"] = "invalid_response"
                else:
                    result["status"] = "available"
                    result["evidence"].append("metadata response parsed as a JSON object")
            except (UnicodeDecodeError, json.JSONDecodeError):
                result["status"] = "invalid_response"
                result["evidence"].append("metadata response was not valid JSON")
        else:
            result["status"] = "available"
    except urllib.error.HTTPError as exc:
        result["http_status"] = exc.code
        if exc.code in (401, 403):
            result["status"] = "authentication_failed"
        elif exc.code == 429:
            result["status"] = "rate_limited"
        elif exc.code >= 500:
            result["status"] = "service_error"
        else:
            result["status"] = "unreachable"
        result["evidence"].append(f"bounded request returned HTTP {exc.code}")
    except (TimeoutError, socket.timeout):
        result["status"] = "timeout"
        result["evidence"].append("bounded request timed out")
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            result["status"] = "timeout"
            result["evidence"].append("bounded request timed out")
        else:
            result["status"] = "unreachable"
            result["evidence"].append(f"service could not be reached: {type(exc.reason).__name__}")
    except RuntimeError as exc:
        message = str(exc)
        result["status"] = "service_error"
        result["evidence"].append(message)
        if "ceiling" in message:
            result["warnings"].append("probe stopped at a configured safety ceiling")
    finally:
        result["bytes_transferred"] = int(getattr(transport, "bytes_transferred", 0)) - before_bytes
        result["requests_made"] = int(getattr(transport, "requests_made", 0)) - before_requests
        result["probe_duration_seconds"] = round(time.monotonic() - started, 3)
    return result


def write_profile_atomic(path: Path, profile: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(profile, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def load_capability_profile(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read capability profile {path}: {exc}") from exc
    if value.get("schema_version") != "fasterraster.capabilities/v1":
        raise ValueError(f"Unsupported capability profile schema in {path}")
    return value


def source_ttl_hours(record: Mapping[str, Any], config: ConfigDocument) -> float:
    ttl = config.capability.ttl
    if record.get("status") == "rate_limited":
        return ttl.rate_limit_hours
    if record.get("credential_state") in {"present", "missing"}:
        return ttl.credential_state_hours
    category = record.get("access_category")
    return {
        "static_verified": ttl.static_endpoint_hours,
        "service_discovered": ttl.service_discovered_hours,
        "api_discovered": ttl.api_discovered_hours,
        "credential_gated": ttl.credential_state_hours,
        "future_unverified": ttl.api_discovered_hours,
    }.get(str(category), ttl.local_environment_hours)


def source_evidence_state(
    record: Mapping[str, Any],
    config: ConfigDocument,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    try:
        timestamp = datetime.fromisoformat(str(record["probe_timestamp"]))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        age_seconds = max(0.0, (current - timestamp).total_seconds())
    except (KeyError, TypeError, ValueError):
        return {"stale": True, "age_seconds": None, "ttl_hours": source_ttl_hours(record, config)}
    ttl_hours = source_ttl_hours(record, config)
    return {"stale": age_seconds > ttl_hours * 3600, "age_seconds": age_seconds, "ttl_hours": ttl_hours}


def evaluate_sources(
    paths: LocalPaths,
    config: ConfigDocument,
    *,
    source_ids: Iterable[str] | None = None,
    offline: bool = False,
    keep_probe_artifacts: bool = False,
    global_byte_ceiling: int = DEFAULT_GLOBAL_BYTE_CEILING,
    definitions: Mapping[str, SourceDefinition] | None = None,
    transport: Any | None = None,
    doctor_report: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    ensure_local_directories(paths)
    env = os.environ if environ is None else environ
    catalog = dict(definitions or shipped_source_definitions())
    requested = list(source_ids or catalog.keys())
    alias_map = {alias: item.source_id for item in catalog.values() for alias in item.aliases}
    normalized: list[str] = []
    for source_id in requested:
        resolved = alias_map.get(source_id, source_id)
        if resolved not in catalog:
            raise ValueError(f"Unknown source: {source_id}")
        if resolved not in normalized:
            normalized.append(resolved)
    probe_parent = paths.cache_home / "probes"
    probe_parent.mkdir(parents=True, exist_ok=True)
    probe_dir = Path(tempfile.mkdtemp(prefix="evaluation-", dir=probe_parent))
    network = transport or BoundedHTTPTransport(global_byte_ceiling)
    previous = load_capability_profile(paths.capability_profile)
    started_at = now()
    local = dict(doctor_report or run_doctor(paths, offline=True, environ=env))
    driver_inventory = list(local.get("gdal", {}).get("drivers", []))
    records: dict[str, Any] = {}
    try:
        for source_id in normalized:
            definition = catalog[source_id]
            override = config.source_overrides.get(source_id)
            disabled = source_id in config.sources.denylist or bool(override and override.disabled)
            if override:
                definition = SourceDefinition(
                    **{
                        **asdict(definition),
                        "credential_env": override.credential_env or definition.credential_env,
                        "timeout_seconds": override.timeout_seconds or definition.timeout_seconds,
                        "byte_ceiling": override.byte_ceiling or definition.byte_ceiling,
                    }
                )
            records[source_id] = probe_source(
                definition,
                transport=network,
                drivers=driver_inventory,
                offline=offline or config.sources.offline,
                environ=env,
                disabled=disabled,
                now=now,
            )
        refreshed = now().isoformat()
        warnings = list(local.get("warnings", []))
        if offline or config.sources.offline:
            warnings.append("source evaluation ran offline; no network requests were made")
        profile = {
            "schema_version": "fasterraster.capabilities/v1",
            "fasterraster_version": __version__,
            "profile_name": config.profile.name,
            "created_at": previous.get("created_at", started_at.isoformat()) if previous else started_at.isoformat(),
            "last_refresh_at": refreshed,
            "machine_fingerprint": local.get("machine", {}),
            "python": {
                "version": local.get("machine", {}).get("python_version"),
                "implementation": local.get("machine", {}).get("python_implementation"),
            },
            "gdal": local.get("gdal", {}),
            "raster_drivers": driver_inventory,
            "local_resources": local.get("resources", {}),
            "local_checks": local.get("checks", {}),
            "recommended_execution_settings": local.get("recommendations", {}),
            "sources": records,
            "staleness_policy": config.capability.ttl.model_dump(mode="json"),
            "warnings": warnings,
            "final_bootstrap_status": "FAIL" if local.get("status") == "FAIL" else "WARN" if warnings else "PASS",
            "evaluation": {
                "offline": offline or config.sources.offline,
                "bytes_transferred": int(getattr(network, "bytes_transferred", 0)),
                "requests_made": int(getattr(network, "requests_made", 0)),
                "global_byte_ceiling": global_byte_ceiling,
                "temporary_artifacts_removed": not keep_probe_artifacts,
                "probe_implementation_version": PROBE_IMPLEMENTATION_VERSION,
            },
        }
        if keep_probe_artifacts:
            debug_summary = probe_dir / "probe_summary.json"
            debug_summary.write_text(json.dumps({"sources": records}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            profile["evaluation"]["artifact_directory"] = str(probe_dir)
        write_profile_atomic(paths.capability_profile, profile)
        return profile
    finally:
        if not keep_probe_artifacts:
            shutil.rmtree(probe_dir, ignore_errors=True)
            try:
                if not any(probe_parent.iterdir()):
                    probe_parent.rmdir()
            except OSError:
                pass
