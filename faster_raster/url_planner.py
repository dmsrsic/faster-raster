from __future__ import annotations

from pathlib import Path

from faster_raster.adapters.arcgis_imageserver import ArcgisImageServerAdapter
from faster_raster.adapters.generic_https_template import GenericHttpsTemplateAdapter
from faster_raster.schemas import ResearchSpec, SourceRegistry
from faster_raster.validation import validate_or_raise


ADAPTERS = {
    "arcgis_imageserver": ArcgisImageServerAdapter(),
    "generic_https_template": GenericHttpsTemplateAdapter(),
}


def plan_urls(spec: ResearchSpec, registry: SourceRegistry, spec_path: Path) -> list[dict]:
    validate_or_raise(spec, registry)
    rows: list[dict] = []
    for source in sorted(spec.sources, key=lambda item: item.id):
        entry = registry.sources[source.registry_key]
        adapter = ADAPTERS.get(entry.adapter)
        if adapter is None:
            raise ValueError(f"Unsupported adapter for v0: {entry.adapter}")
        rows.extend(adapter.plan(spec, source, entry, spec_path.parent))
    return sorted(
        rows,
        key=lambda row: (row["source_id"], row["year"], row["thematic_layer"], row["tile_id"]),
    )
