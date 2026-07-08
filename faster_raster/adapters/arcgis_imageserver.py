from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from faster_raster.crs import epsg_number, transform_bbox
from faster_raster.schemas import RegistryEntry, ResearchSpec, SourceSpec
from faster_raster.tiling import plan_tiles


def year_params(entry: RegistryEntry, year: int) -> dict[str, str]:
    strategy = entry.year_parameter_strategy
    if strategy == "time_value":
        return {entry.time_param: entry.time_value.format(year=year)}
    if strategy == "mosaic_rule_by_attribute":
        raise NotImplementedError("mosaic_rule_by_attribute is reserved as a planned future year strategy")
    raise ValueError(f"Unsupported year_parameter_strategy: {strategy}")


def request_bbox_for_policy(tile: dict, entry: RegistryEntry) -> tuple[list[float], str]:
    if entry.bbox_request_policy == "preserve_input_bbox_with_bboxsr":
        return tile["source_aoi_bbox"], tile["source_aoi_crs"]
    if entry.bbox_request_policy == "project_bbox_to_service_crs":
        return transform_bbox(tile["source_aoi_bbox"], tile["source_aoi_crs"], entry.service_crs), entry.service_crs
    raise ValueError(f"Unsupported bbox_request_policy: {entry.bbox_request_policy}")


class ArcgisImageServerAdapter:
    adapter_name = "arcgis_imageserver"

    def plan(self, spec: ResearchSpec, source: SourceSpec, entry: RegistryEntry, spec_dir: Path) -> list[dict]:
        rows: list[dict] = []
        aoi_path = Path(spec.aoi.path)
        resolved_aoi = aoi_path if aoi_path.is_absolute() else spec_dir / aoi_path
        tiles = plan_tiles(
            resolved_aoi,
            source_crs=spec.aoi.input_crs,
            planning_crs=entry.default_export_image_crs or entry.service_crs,
            resolution_m=float(spec.target_grid.resolution_m),
            max_width=entry.max_width,
            max_height=entry.max_height,
        )

        for year in sorted(source.years):
            for thematic_layer in sorted(source.thematic_layers):
                for tile in sorted(tiles, key=lambda item: item["tile_id"]):
                    request_id = f"{source.id}_{year}_{thematic_layer}_tile_{tile['tile_id']}"
                    request_bbox, request_bbox_crs = request_bbox_for_policy(tile, entry)
                    export_image_crs = entry.default_export_image_crs or entry.service_crs
                    params = {
                        entry.bbox_param: ",".join(f"{value:.8f}" for value in request_bbox),
                        entry.bbox_crs_param: epsg_number(request_bbox_crs),
                        entry.image_crs_param: epsg_number(export_image_crs),
                        entry.size_param: f"{tile['width_px']},{tile['height_px']}",
                        entry.format_param: entry.default_image_format,
                        entry.response_format_param: entry.default_response_format,
                        **year_params(entry, year),
                    }
                    encoded = urlencode(sorted(params.items()))
                    url = f"{entry.base_url.rstrip('/')}/{entry.operation}?{encoded}"
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
                            "bbox": request_bbox,
                            "bbox_crs": request_bbox_crs,
                            "export_image_crs": export_image_crs,
                            "target_grid_crs": spec.target_grid.crs,
                            "target_resolution_m": spec.target_grid.resolution_m,
                            "tile_width_pixels": tile["width_px"],
                            "tile_height_pixels": tile["height_px"],
                            "tile_planning_crs": tile["tile_planning_crs"],
                            "semantic_type": source.semantic_type,
                            "resampling": source.resampling,
                            "url": url,
                            "status": "planned",
                        }
                    )
        return rows
