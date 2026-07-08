from __future__ import annotations

from pathlib import Path

import yaml

from faster_raster.schemas import RegistryEntry, SourceRegistry


DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "configs" / "source_registry.yaml"


def load_registry(path: Path | None = None) -> SourceRegistry:
    registry_path = path or DEFAULT_REGISTRY_PATH
    with registry_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return SourceRegistry.model_validate(raw)


def get_registry_entry(registry_key: str, registry: SourceRegistry) -> RegistryEntry:
    try:
        return registry.sources[registry_key]
    except KeyError as exc:
        raise ValueError(f"Unknown registry_key: {registry_key}") from exc

