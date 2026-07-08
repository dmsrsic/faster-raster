from __future__ import annotations

import string
from pathlib import Path

from faster_raster.schemas import RegistryEntry, ResearchSpec, SourceSpec
from faster_raster.tiling import plan_tiles


ALLOWED_PLACEHOLDERS = {
    "product_slug",
    "year",
    "yyyymmdd",
    "thematic_layer",
    "tile_id",
    "source_id",
    "registry_key",
    "default_format",
    "region",
    "h",
    "v",
    "product_code",
    "collection",
    "version",
    "variable",
    "resolution",
    "temporal_frequency",
}


def template_placeholders(template: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(template) if field}


def validate_template_placeholders(template: str) -> None:
    unknown = sorted(template_placeholders(template) - ALLOWED_PLACEHOLDERS)
    if unknown:
        raise ValueError(f"Unknown URL template placeholder(s): {unknown}")


class GenericHttpsTemplateAdapter:
    adapter_name = "generic_https_template"

    def plan(self, spec: ResearchSpec, source: SourceSpec, entry: RegistryEntry, spec_dir: Path) -> list[dict]:
        if not entry.url_template:
            raise ValueError(f"Source {source.id} missing url_template")
        validate_template_placeholders(entry.url_template)
        aoi_path = Path(spec.aoi.path)
        resolved_aoi = aoi_path if aoi_path.is_absolute() else spec_dir / aoi_path
        export_crs = entry.default_export_image_crs or entry.native_crs or spec.aoi.input_crs
        tiles = plan_tiles(
            resolved_aoi,
            source_crs=spec.aoi.input_crs,
            planning_crs=spec.aoi.input_crs,
            resolution_m=float(spec.target_grid.resolution_m),
            max_width=entry.max_width,
            max_height=entry.max_height,
        )
        if not entry.supports_tiling:
            tiles = tiles[:1]

        rows: list[dict] = []
        for year in sorted(source.years):
            for thematic_layer in sorted(source.thematic_layers):
                for tile in sorted(tiles, key=lambda item: item["tile_id"]):
                    request_id = f"{source.id}_{year}_{thematic_layer}_tile_{tile['tile_id']}"
                    values = {
                        "product_slug": entry.product_slug or source.registry_key,
                        "year": year,
                        "thematic_layer": thematic_layer,
                        "tile_id": entry.template_tile_id or tile["tile_id"],
                        "source_id": source.id,
                        "registry_key": source.registry_key,
                        "default_format": entry.default_format or "tif",
                        "region": entry.region or "",
                        "h": entry.h or "",
                        "v": entry.v or "",
                        "product_code": entry.product_code or "",
                        "collection": entry.collection or "",
                        "version": entry.version or "",
                        "variable": entry.variable or thematic_layer,
                        "yyyymmdd": entry.yyyymmdd or f"{year}0101",
                        "resolution": entry.resolution or "",
                        "temporal_frequency": entry.temporal_frequency or "",
                    }
                    rows.append(
                        {
                            "request_id": request_id,
                            "source_id": source.id,
                            "registry_key": source.registry_key,
                            "adapter": self.adapter_name,
                            "provider": entry.provider,
                            "product": entry.product,
                            "year": year,
                            "thematic_layer": thematic_layer,
                            "tile_id": tile["tile_id"],
                            "tile_row": tile["row"],
                            "tile_col": tile["col"],
                            "aoi_path": spec.aoi.path,
                            "source_aoi_bbox": tile["source_aoi_bbox"],
                            "source_aoi_crs": tile["source_aoi_crs"],
                            "bbox": tile["source_aoi_bbox"],
                            "bbox_crs": tile["source_aoi_crs"],
                            "export_image_crs": export_crs,
                            "target_grid_crs": spec.target_grid.crs,
                            "target_resolution_m": spec.target_grid.resolution_m,
                            "tile_width_pixels": tile["width_px"],
                            "tile_height_pixels": tile["height_px"],
                            "tile_planning_crs": tile["tile_planning_crs"],
                            "semantic_type": source.semantic_type,
                            "resampling": source.resampling,
                            "url": entry.url_template.format(**values),
                            "status": "planned",
                        }
                    )
        return rows
