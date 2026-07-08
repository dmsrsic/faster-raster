from __future__ import annotations

from typing import Protocol

from faster_raster.schemas import RegistryEntry, ResearchSpec, SourceSpec


class SourceAdapter(Protocol):
    adapter_name: str

    def plan(self, spec: ResearchSpec, source: SourceSpec, entry: RegistryEntry, spec_dir) -> list[dict]:
        ...

