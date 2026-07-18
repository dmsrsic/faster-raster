from __future__ import annotations

import json
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from faster_raster.local_paths import LocalPaths


CONFIG_SCHEMA_VERSION = "fasterraster.config/v1"
SECRET_KEY_PARTS = {"password", "token", "api_key", "apikey", "authorization", "cookie", "secret"}


class ConfigError(ValueError):
    pass


class ProfileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="default", pattern=r"^[A-Za-z0-9_.-]+$")


class PathsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cache_root: str | None = None
    state_root: str | None = None
    temporary_root: str | None = None


class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reuse_mode: Literal["auto", "only", "never"] = "auto"
    default_byte_ceiling: int = Field(default=250_000_000, gt=0, le=20_000_000_000)
    service_tile_size: int = Field(default=1800, ge=64, le=10_000)
    maximum_parallel_tasks: int = Field(default=1, ge=1, le=256)


class PreviewConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    open_when_complete: bool = False
    opener: str | None = None


class SourcesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preference_order: list[str] = Field(default_factory=list)
    allowlist: list[str] = Field(default_factory=list)
    denylist: list[str] = Field(default_factory=list)
    offline: bool = False

    @field_validator("preference_order", "allowlist", "denylist")
    @classmethod
    def unique_source_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("source lists must not contain duplicates")
        return values


class TTLConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    local_environment_hours: float = Field(default=24.0, gt=0)
    static_endpoint_hours: float = Field(default=168.0, gt=0)
    service_discovered_hours: float = Field(default=72.0, gt=0)
    api_discovered_hours: float = Field(default=24.0, gt=0)
    credential_state_hours: float = Field(default=24.0, gt=0)
    rate_limit_hours: float = Field(default=1.0, gt=0)


class CapabilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ttl: TTLConfig = Field(default_factory=TTLConfig)


class SourceOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    disabled: bool = False
    credential_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    timeout_seconds: float | None = Field(default=None, gt=0, le=120)
    byte_ceiling: int | None = Field(default=None, gt=0, le=10_000_000)


class ConfigDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[CONFIG_SCHEMA_VERSION] = CONFIG_SCHEMA_VERSION
    profile: ProfileConfig = Field(default_factory=ProfileConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    preview: PreviewConfig = Field(default_factory=PreviewConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    capability: CapabilityConfig = Field(default_factory=CapabilityConfig)
    source_overrides: dict[str, SourceOverride] = Field(default_factory=dict)


def _reject_secret_values(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized != "credential_env" and any(part in normalized for part in SECRET_KEY_PARTS):
                raise ConfigError(f"credentials are not allowed in configuration: {'.'.join(path + (str(key),))}")
            _reject_secret_values(child, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_values(child, path + (str(index),))


def _toml_loads(text: str) -> dict[str, Any]:
    try:
        import tomllib

        return tomllib.loads(text)
    except ModuleNotFoundError:
        try:
            import toml  # type: ignore[import-not-found]

            return toml.loads(text)
        except ModuleNotFoundError as exc:
            raise ConfigError("TOML support requires Python 3.11+ or the 'toml' package") from exc


def load_config_file(path: Path | None) -> ConfigDocument:
    if path is None or not path.is_file():
        return ConfigDocument()
    try:
        raw = _toml_loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"Unable to read configuration {path}: {exc}") from exc
    _reject_secret_values(raw)
    try:
        return ConfigDocument.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration {path}: {exc}") from exc


def default_config(paths: LocalPaths) -> ConfigDocument:
    return ConfigDocument(
        paths=PathsConfig(
            cache_root=str(paths.cache_home),
            state_root=str(paths.state_home),
            temporary_root=str(paths.temporary_root),
        )
    )


def _deep_merge(left: dict[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(left)
    for key, value in right.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def resolved_config_document(paths: LocalPaths) -> tuple[ConfigDocument, list[Path]]:
    merged = default_config(paths).model_dump(mode="json")
    files: list[Path] = []
    for path in (paths.user_config, paths.project_config):
        if path is not None and path.is_file():
            document = load_config_file(path)
            merged = _deep_merge(merged, document.model_dump(mode="json", exclude_unset=True))
            files.append(path)
    try:
        return ConfigDocument.model_validate(merged), files
    except ValidationError as exc:
        raise ConfigError(f"Invalid merged configuration: {exc}") from exc


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if value is None:
        raise TypeError("TOML has no null scalar")
    if isinstance(value, list):
        return "[" + ", ".join(_toml_scalar(item) for item in value) + "]"
    raise TypeError(f"unsupported TOML value: {type(value).__name__}")


def dump_config_toml(document: ConfigDocument) -> str:
    raw = document.model_dump(mode="json")
    lines = [f"schema_version = {_toml_scalar(raw.pop('schema_version'))}", ""]

    def emit_table(name: str, values: dict[str, Any]) -> None:
        lines.append(f"[{name}]")
        for key, value in values.items():
            if value is not None and not isinstance(value, dict):
                lines.append(f"{key} = {_toml_scalar(value)}")
        lines.append("")
        for key, value in values.items():
            if isinstance(value, dict):
                emit_table(f"{name}.{key}", value)

    for section, values in raw.items():
        if isinstance(values, dict):
            emit_table(section, values)
    return "\n".join(lines).rstrip() + "\n"


def write_config_atomic(path: Path, document: ConfigDocument) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dump_config_toml(document)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def apply_config_updates(document: ConfigDocument, updates: Mapping[str, Any]) -> ConfigDocument:
    merged = _deep_merge(document.model_dump(mode="json"), updates)
    _reject_secret_values(merged)
    try:
        return ConfigDocument.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration update: {exc}") from exc


def normalized_config_paths(document: ConfigDocument, paths: LocalPaths) -> dict[str, Path]:
    return {
        "cache_root": Path(document.paths.cache_root or paths.cache_home).expanduser(),
        "state_root": Path(document.paths.state_root or paths.state_home).expanduser(),
        "temporary_root": Path(document.paths.temporary_root or paths.temporary_root).expanduser(),
    }
