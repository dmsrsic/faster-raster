from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import shutil
import tempfile
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import rasterio
from PIL import Image, ImageDraw, ImageFont
from affine import Affine
from rasterio.enums import Resampling
from rasterio.shutil import copy as raster_copy
from rasterio.transform import from_origin
from rasterio.warp import calculate_default_transform, reproject, transform_bounds

from faster_raster.prism_harmonization import (
    compute_harmonization_plan_sha256,
    plan_prism_harmonization,
    verify_harmonization_plan,
)
from faster_raster.raster_harmonization import execute_raster_harmonization


WORKFLOW_ID = "prism_dem_ndvi_correlation_audit"
PRISM_SOURCE_ID = "prism_daily_ppt_static_zip"
NAIP_SOURCE_ID = "usgs_naip_imageserver"
CDL_SOURCE_ID = "usda_nass_cdl_imageserver"
ELEVATION_SOURCE_ID = "usgs_3dep_imageserver"
THREEDEP_ENDPOINT = (
    "https://elevation.nationalmap.gov/arcgis/rest/services/"
    "3DEPElevation/ImageServer"
)
DEFAULT_TARGET_CRS = "EPSG:5070"
DEFAULT_TARGET_RESOLUTION_M = 4000.0
DEFAULT_NAIP_RESOLUTION_M = 30.0
DEFAULT_ELEVATION_RESOLUTION_M = 30.0
DEFAULT_PRISM_OBJECT_CAP = 16 * 1024 * 1024
DEFAULT_PRISM_RASTER_CAP = 64 * 1024 * 1024
DEFAULT_MAX_PRECIPITATION_DAYS = 31
DEFAULT_MINIMUM_VALID_CELLS = 12
NODATA = -9999.0


class EnvironmentalCorrelationError(RuntimeError):
    """Raised when the environmental correlation workflow cannot remain auditable."""


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _date_range(start: date, end: date) -> list[date]:
    if end < start:
        raise EnvironmentalCorrelationError(
            "precipitation_end_must_not_precede_start"
        )
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _resolved_values(workfile: Any, paths: Any, cli_overrides: Mapping[str, Any] | None) -> dict[str, Any]:
    from faster_raster.local_config import normalized_config_paths, resolved_config_document

    config, config_files = resolved_config_document(paths)
    normalized = normalized_config_paths(config, paths)
    overrides = dict(cli_overrides or {})
    resolution = (
        overrides.get("resolution_m")
        if overrides.get("resolution_m") is not None
        else workfile.spec.processing.resolution_m
    )
    maximum_download_mb = (
        overrides.get("maximum_download_mb")
        if overrides.get("maximum_download_mb") is not None
        else workfile.spec.limits.maximum_download_mb
    )
    service_tile_size = (
        overrides.get("service_tile_size")
        if overrides.get("service_tile_size") is not None
        else workfile.spec.processing.service_tile_size
    )
    open_when_complete = (
        overrides.get("open_when_complete")
        if overrides.get("open_when_complete") is not None
        else workfile.spec.outputs.open_when_complete
    )
    reuse_mode = str(
        overrides.get("reuse_mode")
        if overrides.get("reuse_mode") is not None
        else workfile.spec.data.reuse
    )
    return {
        "schema_version": "fasterraster.resolved-config/v1",
        "workfile": str(workfile.path),
        "workflow": WORKFLOW_ID,
        "configuration_files": [str(path) for path in config_files],
        "precedence": [
            "cli_override",
            "workfile",
            "project_configuration",
            "user_configuration",
            "workflow_defaults",
            "source_defaults",
        ],
        "values": {
            "reuse_mode": {
                "value": reuse_mode,
                "origin": "cli_override" if "reuse_mode" in overrides else "workfile",
                "key": "data.reuse",
            },
            "maximum_download_mb": {
                "value": float(maximum_download_mb),
                "origin": "cli_override" if "maximum_download_mb" in overrides else "workfile",
                "key": "maximum_download_mb",
            },
            "service_tile_size": {
                "value": int(service_tile_size or config.execution.service_tile_size),
                "origin": "cli_override" if "service_tile_size" in overrides else "workfile_or_configuration",
                "key": "service_tile_size",
            },
            "resolution_m": {
                "value": float(resolution or DEFAULT_TARGET_RESOLUTION_M),
                "origin": "cli_override" if "resolution_m" in overrides else "workfile_or_workflow_default",
                "key": "processing.resolution_m",
            },
            "open_when_complete": {
                "value": bool(open_when_complete),
                "origin": "cli_override" if "open_when_complete" in overrides else "workfile",
                "key": "outputs.open_when_complete",
            },
            "cache_root": {
                "value": str(normalized["cache_root"]),
                "origin": "resolved_configuration",
                "key": "paths.cache_root",
            },
            "state_root": {
                "value": str(normalized["state_root"]),
                "origin": "resolved_configuration",
                "key": "paths.state_root",
            },
            "temporary_root": {
                "value": str(normalized["temporary_root"]),
                "origin": "resolved_configuration",
                "key": "paths.temporary_root",
            },
        },
    }


def _source_decision(logical_asset: str, source_id: str, product: str) -> dict[str, Any]:
    return {
        "logical_asset": logical_asset,
        "display_name": product,
        "candidates_considered": [
            {
                "source_id": source_id,
                "provider": source_id.split("_")[0].upper(),
                "product": product,
                "capability_status": "runtime_supported",
                "rejected": False,
                "rejection_reasons": [],
            }
        ],
        "candidates_rejected": [],
        "selected_source": source_id,
        "selected_capability_status": "runtime_supported",
        "selected_fallback": False,
        "provisional": False,
        "live_execution_must_revalidate": True,
        "blocking_reason": None,
    }


def compile_environmental_correlation_plan(
    repository_root: Path,
    workfile: Any,
    paths: Any,
    *,
    cli_overrides: Mapping[str, Any] | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    if workfile.spec.workflow_id != WORKFLOW_ID:
        raise EnvironmentalCorrelationError("wrong_workflow_for_environmental_plan")
    settings = workfile.spec.correlation
    if settings is None:
        raise EnvironmentalCorrelationError("correlation_contract_is_required")
    days = _date_range(settings.precipitation_start, settings.precipitation_end)
    resolved = _resolved_values(workfile, paths, cli_overrides)
    target_resolution = float(resolved["values"]["resolution_m"]["value"])
    maximum_download_bytes = int(
        float(resolved["values"]["maximum_download_mb"]["value"]) * 1_000_000
    )
    blocking_reasons: list[str] = []
    if not workfile.spec.data.allow_network:
        blocking_reasons.append("data.allow_network must be true")
    if not workfile.spec.data.allow_materialization:
        blocking_reasons.append("data.allow_materialization must be true")
    if resolved["values"]["reuse_mode"]["value"] != "never":
        blocking_reasons.append(
            "the first public correlation workflow requires data.reuse: never"
        )
    if len(days) > settings.maximum_precipitation_days:
        blocking_reasons.append(
            "precipitation window exceeds correlation.maximum_precipitation_days"
        )
    if not 1000.0 <= target_resolution <= 10_000.0:
        blocking_reasons.append(
            "processing.resolution_m must be between 1000 and 10000 for PRISM correlation"
        )
    if maximum_download_bytes < len(days) * 2_000_000:
        blocking_reasons.append(
            "download ceiling is below the minimum expected PRISM transfer"
        )
    selected_sources = {
        "precipitation": PRISM_SOURCE_ID,
        "elevation": ELEVATION_SOURCE_ID,
        "ndvi": NAIP_SOURCE_ID,
        "crop_context": CDL_SOURCE_ID,
    }
    source_resolution = {
        "schema_version": "fasterraster.source-resolution/v1",
        "workfile": str(workfile.path),
        "profile_path": None,
        "network_requests": 0,
        "decisions": [
            _source_decision("precipitation", PRISM_SOURCE_ID, "PRISM daily precipitation"),
            _source_decision("elevation", ELEVATION_SOURCE_ID, "USGS 3DEP elevation"),
            _source_decision("naip_multispectral", NAIP_SOURCE_ID, "Raw four-band NAIP imagery"),
            _source_decision("cdl_classes", CDL_SOURCE_ID, "USDA CDL classes"),
        ],
        "blocking": bool(blocking_reasons),
    }
    rows = [
        {
            "data": "Accumulated precipitation",
            "logical_asset": "precipitation",
            "source": PRISM_SOURCE_ID,
            "local_asset_readiness": "missing",
            "remote_source_status": "runtime_supported",
            "remote_source_required": True,
            "remote_source_blocking": False,
            "action": "acquire",
            "reason": f"{len(days)} guarded daily PRISM packages",
            "provisional": False,
            "reused": False,
            "acquired": True,
        },
        {
            "data": "Elevation",
            "logical_asset": "elevation",
            "source": ELEVATION_SOURCE_ID,
            "local_asset_readiness": "missing",
            "remote_source_status": "runtime_supported",
            "remote_source_required": True,
            "remote_source_blocking": False,
            "action": "acquire",
            "reason": "bounded 3DEP elevation export",
            "provisional": False,
            "reused": False,
            "acquired": True,
        },
        {
            "data": "NAIP-derived NDVI",
            "logical_asset": "naip_multispectral",
            "source": NAIP_SOURCE_ID,
            "local_asset_readiness": "missing",
            "remote_source_status": "runtime_supported",
            "remote_source_required": True,
            "remote_source_blocking": False,
            "action": "acquire_and_derive",
            "reason": "raw four-band NAIP followed by local NDVI calculation",
            "provisional": False,
            "reused": False,
            "acquired": True,
        },
        {
            "data": "Crop context",
            "logical_asset": "cdl_classes",
            "source": CDL_SOURCE_ID,
            "local_asset_readiness": "missing",
            "remote_source_status": "runtime_supported",
            "remote_source_required": True,
            "remote_source_blocking": False,
            "action": "acquire",
            "reason": "same-year USDA CDL context, excluded from correlation coefficients",
            "provisional": False,
            "reused": False,
            "acquired": True,
        },
    ]
    plan: dict[str, Any] = {
        "schema_version": "fasterraster.study-plan/v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "workfile": str(workfile.path),
        "study_name": workfile.spec.name,
        "workflow": WORKFLOW_ID,
        "offline_planning": True,
        "network_requests": 0,
        "blocking": bool(blocking_reasons),
        "blocking_reasons": blocking_reasons,
        "rows": rows,
        "source_resolution": source_resolution,
        "asset_plan": {
            "schema_version": "fasterraster.environmental-correlation-asset-plan/v1",
            "assets": rows,
            "selected_sources": selected_sources,
            "precipitation_dates": [item.isoformat() for item in days],
            "target_crs": DEFAULT_TARGET_CRS,
            "target_resolution_m": target_resolution,
            "naip_analysis_resolution_m": settings.naip_analysis_resolution_m,
            "elevation_resolution_m": settings.elevation_resolution_m,
            "minimum_valid_cells": settings.minimum_valid_cells,
            "scientific_claim": (
                "Exploratory spatial association among accumulated PRISM precipitation, "
                "USGS 3DEP elevation, and NAIP-derived NDVI on one declared common grid."
            ),
            "unsupported_claims": [
                "causal precipitation effect on vegetation",
                "independent ground-truth accuracy",
                "iid statistical significance despite spatial autocorrelation",
                "field-scale precipitation inference from the 4 km PRISM surface",
            ],
        },
        "resolved_config": resolved,
        "maximum_download_bytes": maximum_download_bytes,
        "runtime_request": {
            "request_bbox_epsg_4326": list(workfile.spec.area.bbox),
            "imagery_timeframe": {
                "start": workfile.spec.time.start.isoformat(),
                "end": workfile.spec.time.end.isoformat(),
            },
            "imagery_year": workfile.spec.time.crop_year,
            "cdl_year": workfile.spec.time.crop_year,
            "precipitation_start": settings.precipitation_start.isoformat(),
            "precipitation_end": settings.precipitation_end.isoformat(),
        },
    }
    plan["plan_contract_sha256"] = _stable_hash(
        {
            key: value
            for key, value in plan.items()
            if key not in {"created_at", "workfile", "plan_contract_sha256"}
        }
    )
    destination = output_dir or (
        Path(resolved["values"]["state_root"]["value"])
        / "plans"
        / workfile.spec.name
    )
    destination.mkdir(parents=True, exist_ok=True)
    _write_json(destination / "resolved_config.json", resolved)
    _write_json(destination / "source_resolution.json", source_resolution)
    _write_json(destination / "plan.json", plan)
    (destination / "plan.md").write_text(
        "\n".join(
            [
                f"# {workfile.spec.name}",
                "",
                f"- Workflow: `{WORKFLOW_ID}`",
                f"- Blocking: `{plan['blocking']}`",
                f"- PRISM dates: `{len(days)}`",
                f"- Target grid: `{DEFAULT_TARGET_CRS}` at `{target_resolution:g} m`",
                "",
                "This is an offline plan. No raster pixels were transferred.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    plan["artifacts"] = {
        "directory": str(destination),
        "resolved_config": str(destination / "resolved_config.json"),
        "source_resolution": str(destination / "source_resolution.json"),
        "plan": str(destination / "plan.json"),
    }
    return plan


def _request_bytes(url: str, *, timeout_seconds: int, max_bytes: int) -> tuple[bytes, Mapping[str, str]]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "FasterRaster/1.0 environmental-correlation"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        status = getattr(response, "status", None) or response.getcode()
        if status >= 400:
            raise EnvironmentalCorrelationError(f"http_status_{status}")
        length = response.headers.get("Content-Length")
        if length is not None and int(length) > max_bytes:
            raise EnvironmentalCorrelationError("response_exceeds_byte_ceiling")
        data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise EnvironmentalCorrelationError("response_exceeds_byte_ceiling")
        return data, dict(response.headers.items())


def _write_cog(
    destination: Path,
    values: np.ndarray,
    *,
    transform: Affine,
    crs: str,
    nodata: float = NODATA,
    tags: Mapping[str, str] | None = None,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = destination.with_name(destination.name + ".working.tif")
    temporary = destination.with_name(destination.name + ".tmp.tif")
    for path in (raw, temporary):
        path.unlink(missing_ok=True)
    with rasterio.open(
        raw,
        "w",
        driver="GTiff",
        width=values.shape[1],
        height=values.shape[0],
        count=1,
        dtype="float32",
        crs=crs,
        transform=transform,
        nodata=nodata,
        tiled=True,
        blockxsize=512,
        blockysize=512,
        compress="DEFLATE",
        predictor=3,
        BIGTIFF="IF_SAFER",
    ) as dataset:
        dataset.write(values.astype(np.float32, copy=False), 1)
        dataset.update_tags(AREA_OR_POINT="Area", **dict(tags or {}))
    raster_copy(
        raw,
        temporary,
        driver="COG",
        compress="DEFLATE",
        blocksize=512,
        overview_resampling="average",
        predictor="FLOATING_POINT",
        BIGTIFF="IF_SAFER",
        NUM_THREADS="1",
    )
    raw.unlink(missing_ok=True)
    os.replace(temporary, destination)
    return destination


def _acquire_elevation(
    destination: Path,
    *,
    bbox_epsg_4326: Sequence[float],
    resolution_m: float,
    max_bytes: int,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    bounds = transform_bounds(
        "EPSG:4326",
        DEFAULT_TARGET_CRS,
        *bbox_epsg_4326,
        densify_pts=21,
    )
    left = math.floor(bounds[0] / resolution_m) * resolution_m
    bottom = math.floor(bounds[1] / resolution_m) * resolution_m
    right = math.ceil(bounds[2] / resolution_m) * resolution_m
    top = math.ceil(bounds[3] / resolution_m) * resolution_m
    width = int(round((right - left) / resolution_m))
    height = int(round((top - bottom) / resolution_m))
    if width <= 0 or height <= 0 or width > 8000 or height > 8000:
        raise EnvironmentalCorrelationError("elevation_export_dimensions_are_unsafe")
    query = urllib.parse.urlencode(
        {
            "bbox": f"{left},{bottom},{right},{top}",
            "bboxSR": "5070",
            "imageSR": "5070",
            "size": f"{width},{height}",
            "format": "tiff",
            "pixelType": "F32",
            "interpolation": "RSP_BilinearInterpolation",
            "f": "image",
        }
    )
    url = f"{THREEDEP_ENDPOINT}/exportImage?{query}"
    payload, headers = _request_bytes(
        url,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
    )
    if payload[:1] in {b"{", b"["}:
        raise EnvironmentalCorrelationError("elevation_service_returned_json_error")
    if payload[:4] not in {b"II*\x00", b"MM\x00*"}:
        raise EnvironmentalCorrelationError("elevation_response_is_not_tiff")
    with tempfile.TemporaryDirectory(prefix="fr-elevation-") as temporary:
        source = Path(temporary) / "elevation.tif"
        source.write_bytes(payload)
        with rasterio.open(source) as dataset:
            if dataset.count != 1 or dataset.crs is None:
                raise EnvironmentalCorrelationError("elevation_raster_contract_invalid")
            values = dataset.read(indexes=(1,), masked=False)[0].astype(np.float32, copy=False)
            nodata = dataset.nodata
            if nodata is not None:
                values[values == float(nodata)] = NODATA
            values[~np.isfinite(values)] = NODATA
            transform = dataset.transform
            crs = dataset.crs.to_string()
        _write_cog(
            destination,
            values,
            transform=transform,
            crs=crs,
            tags={
                "FASTERRASTER_SOURCE_ID": ELEVATION_SOURCE_ID,
                "FASTERRASTER_VARIABLE": "elevation",
                "FASTERRASTER_UNITS": "meters",
            },
        )
    return {
        "source_id": ELEVATION_SOURCE_ID,
        "request_url_sha256": hashlib.sha256(url.encode("utf-8")).hexdigest(),
        "HTTP status": 200,
        "Content-Type": headers.get("Content-Type"),
        "bytes": len(payload),
        "output": destination.relative_to(destination.parents[1]).as_posix(),
        "output_sha256": _sha256(destination),
        "width": width,
        "height": height,
        "resolution_m": resolution_m,
        "crs": DEFAULT_TARGET_CRS,
    }


def _derive_ndvi_5070(
    source_path: Path,
    destination: Path,
    *,
    resolution_m: float,
) -> dict[str, Any]:
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_PAM_ENABLED="NO"):
        with rasterio.open(source_path) as source:
            if source.count < 4 or source.crs is None:
                raise EnvironmentalCorrelationError("naip_multispectral_contract_invalid")
            transform, width, height = calculate_default_transform(
                source.crs,
                DEFAULT_TARGET_CRS,
                source.width,
                source.height,
                *source.bounds,
                resolution=resolution_m,
            )
            red = np.full((height, width), np.nan, dtype=np.float32)
            nir = np.full((height, width), np.nan, dtype=np.float32)
            for band, destination_array in ((1, red), (4, nir)):
                reproject(
                    source=rasterio.band(source, band),
                    destination=destination_array,
                    src_transform=source.transform,
                    src_crs=source.crs,
                    src_nodata=source.nodata,
                    dst_transform=transform,
                    dst_crs=DEFAULT_TARGET_CRS,
                    dst_nodata=np.nan,
                    resampling=Resampling.bilinear,
                    init_dest_nodata=True,
                    num_threads=1,
                )
    denominator = nir + red
    valid = np.isfinite(red) & np.isfinite(nir) & (np.abs(denominator) > 1e-6)
    ndvi = np.full(red.shape, NODATA, dtype=np.float32)
    ndvi[valid] = np.clip(
        (nir[valid] - red[valid]) / denominator[valid],
        -1.0,
        1.0,
    ).astype(np.float32)
    _write_cog(
        destination,
        ndvi,
        transform=transform,
        crs=DEFAULT_TARGET_CRS,
        tags={
            "FASTERRASTER_SOURCE_ID": NAIP_SOURCE_ID,
            "FASTERRASTER_VARIABLE": "ndvi",
            "FASTERRASTER_UNITS": "unitless",
            "FASTERRASTER_FORMULA": "(nir-red)/(nir+red)",
            "FASTERRASTER_RED_BAND": "1",
            "FASTERRASTER_NIR_BAND": "4",
        },
    )
    return {
        "source_id": NAIP_SOURCE_ID,
        "source_raster_sha256": _sha256(source_path),
        "output_sha256": _sha256(destination),
        "valid_pixel_count": int(np.count_nonzero(valid)),
        "width": int(ndvi.shape[1]),
        "height": int(ndvi.shape[0]),
        "resolution_m": resolution_m,
        "crs": DEFAULT_TARGET_CRS,
    }


def _generic_plan(
    source_path: Path,
    *,
    aoi_bbox: Sequence[float],
    target_resolution: float,
    source_id: str,
    source_product: str,
    variable: str,
    units: str,
    resampling_method: str,
) -> dict[str, Any]:
    plan = plan_prism_harmonization(
        source_path,
        aoi_bbox=aoi_bbox,
        aoi_crs="EPSG:4326",
        target_crs=DEFAULT_TARGET_CRS,
        target_resolution=target_resolution,
        target_origin=(0.0, 0.0),
        resampling_method=resampling_method,
        max_output_pixels=5_000_000,
    )
    plan.update(
        {
            "source_id": source_id,
            "source_product": source_product,
            "variable": variable,
            "units": units,
        }
    )
    plan["harmonization_plan_sha256"] = compute_harmonization_plan_sha256(plan)
    verification = verify_harmonization_plan(plan)
    if verification["verification_status"] != "PASS":
        raise EnvironmentalCorrelationError("generic_harmonization_plan_failed")
    return plan



def _grid_signature(plan: Mapping[str, Any]) -> dict[str, Any]:
    grid = plan.get("target_grid") or {}
    crs = grid.get("crs") or {}
    return {
        "crs": crs.get("string"),
        "width": grid.get("width"),
        "height": grid.get("height"),
        "resolution": grid.get("resolution"),
        "transform": grid.get("transform"),
        "bounds": grid.get("bounds"),
        "dtype": grid.get("dtype"),
        "nodata": grid.get("nodata"),
    }

def _read_values(path: Path) -> tuple[np.ndarray, Affine, str, float | None]:
    with rasterio.open(path) as dataset:
        return (
            dataset.read(indexes=(1,), masked=False)[0].astype(np.float64),
            dataset.transform,
            dataset.crs.to_string() if dataset.crs else "",
            dataset.nodata,
        )


def _valid_mask(values: np.ndarray, nodata: float | None) -> np.ndarray:
    mask = np.isfinite(values)
    if nodata is not None:
        mask &= values != float(nodata)
    return mask


def _average_ranks(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(values.size, dtype=np.float64)
    index = 0
    while index < values.size:
        end = index + 1
        while end < values.size and values[order[end]] == values[order[index]]:
            end += 1
        rank = (index + end - 1) / 2.0 + 1.0
        ranks[order[index:end]] = rank
        index = end
    return ranks


def _correlation(first: np.ndarray, second: np.ndarray) -> float | None:
    if first.size < 2 or np.std(first) == 0 or np.std(second) == 0:
        return None
    return float(np.corrcoef(first, second)[0, 1])


def _partial_correlation(x: np.ndarray, y: np.ndarray, control: np.ndarray) -> float | None:
    design = np.column_stack([np.ones(control.size), control])
    x_residual = x - design @ np.linalg.lstsq(design, x, rcond=None)[0]
    y_residual = y - design @ np.linalg.lstsq(design, y, rcond=None)[0]
    return _correlation(x_residual, y_residual)


def correlation_report(
    precipitation: np.ndarray,
    elevation: np.ndarray,
    ndvi: np.ndarray,
    *,
    precipitation_nodata: float | None,
    elevation_nodata: float | None,
    ndvi_nodata: float | None,
    minimum_valid_cells: int,
) -> dict[str, Any]:
    mask = (
        _valid_mask(precipitation, precipitation_nodata)
        & _valid_mask(elevation, elevation_nodata)
        & _valid_mask(ndvi, ndvi_nodata)
    )
    ppt = precipitation[mask].astype(np.float64)
    dem = elevation[mask].astype(np.float64)
    veg = ndvi[mask].astype(np.float64)
    if ppt.size < minimum_valid_cells:
        raise EnvironmentalCorrelationError(
            f"insufficient_common_grid_cells:{ppt.size}<{minimum_valid_cells}"
        )
    pairs = {
        "precipitation__ndvi": (ppt, veg),
        "elevation__ndvi": (dem, veg),
        "precipitation__elevation": (ppt, dem),
    }
    pearson = {name: _correlation(*values) for name, values in pairs.items()}
    spearman = {
        name: _correlation(_average_ranks(values[0]), _average_ranks(values[1]))
        for name, values in pairs.items()
    }
    standardized = np.column_stack(
        [
            np.ones(ppt.size),
            (ppt - ppt.mean()) / (ppt.std() or 1.0),
            (dem - dem.mean()) / (dem.std() or 1.0),
        ]
    )
    target = (veg - veg.mean()) / (veg.std() or 1.0)
    coefficients = np.linalg.lstsq(standardized, target, rcond=None)[0]
    fitted = standardized @ coefficients
    residual = target - fitted
    total_ss = float(np.sum((target - target.mean()) ** 2))
    r_squared = None if total_ss == 0 else float(1.0 - np.sum(residual**2) / total_ss)
    return {
        "schema_version": "fasterraster.environmental-correlation/v1",
        "status": "PASS",
        "common_valid_cell_count": int(ppt.size),
        "methods": {
            "pearson": pearson,
            "spearman_rank": spearman,
            "partial_correlation": {
                "precipitation__ndvi_controlling_elevation": _partial_correlation(
                    ppt, veg, dem
                )
            },
            "standardized_linear_model": {
                "formula": "ndvi_z ~ precipitation_z + elevation_z",
                "intercept": float(coefficients[0]),
                "precipitation_coefficient": float(coefficients[1]),
                "elevation_coefficient": float(coefficients[2]),
                "r_squared": r_squared,
            },
        },
        "descriptive_statistics": {
            "precipitation_mm": {
                "minimum": float(ppt.min()),
                "maximum": float(ppt.max()),
                "mean": float(ppt.mean()),
                "standard_deviation": float(ppt.std()),
            },
            "elevation_m": {
                "minimum": float(dem.min()),
                "maximum": float(dem.max()),
                "mean": float(dem.mean()),
                "standard_deviation": float(dem.std()),
            },
            "ndvi": {
                "minimum": float(veg.min()),
                "maximum": float(veg.max()),
                "mean": float(veg.mean()),
                "standard_deviation": float(veg.std()),
            },
        },
        "interpretation_guard": {
            "exploratory_only": True,
            "p_values_computed": False,
            "reason": (
                "Grid cells are spatially autocorrelated and the analysis does not "
                "claim independent observations or causal effects."
            ),
        },
        "sample_arrays": {
            "precipitation": ppt,
            "elevation": dem,
            "ndvi": veg,
        },
    }


def _ramp(values: np.ndarray, nodata: float | None, colors: Sequence[tuple[int, int, int]]) -> Image.Image:
    valid = _valid_mask(values, nodata)
    normalized = np.zeros(values.shape, dtype=np.float64)
    if np.any(valid):
        low, high = np.quantile(values[valid], [0.02, 0.98])
        if high <= low:
            high = low + 1.0
        normalized[valid] = np.clip((values[valid] - low) / (high - low), 0.0, 1.0)
    positions = normalized * (len(colors) - 1)
    lower = np.floor(positions).astype(int)
    upper = np.minimum(lower + 1, len(colors) - 1)
    fraction = positions - lower
    palette = np.asarray(colors, dtype=np.float64)
    rgb = palette[lower] * (1.0 - fraction[..., None]) + palette[upper] * fraction[..., None]
    rgb[~valid] = (32, 32, 32)
    return Image.fromarray(rgb.astype(np.uint8), mode="RGB")


def _rgb_context(path: Path, size: tuple[int, int]) -> Image.Image:
    width, height = size
    with rasterio.open(path) as source:
        bands = source.read(
            indexes=(1, 2, 3),
            out_shape=(3, height, width),
            resampling=Resampling.bilinear,
        ).astype(np.float32)
    output = np.zeros((height, width, 3), dtype=np.uint8)
    for index in range(3):
        values = bands[index]
        finite = np.isfinite(values)
        if not np.any(finite):
            continue
        low, high = np.quantile(values[finite], [0.02, 0.98])
        if high <= low:
            high = low + 1.0
        output[..., index] = np.clip((values - low) / (high - low) * 255, 0, 255).astype(np.uint8)
    return Image.fromarray(output, mode="RGB")


def _cdl_context(path: Path, size: tuple[int, int]) -> Image.Image:
    width, height = size
    with rasterio.open(path) as source:
        values = source.read(
            indexes=(1,),
            out_shape=(1, height, width),
            resampling=Resampling.nearest,
            masked=False,
        )[0].astype(np.int32)
    rgb = np.zeros((height, width, 3), dtype=np.uint8)
    rgb[..., 0] = (values * 53 + 31) % 256
    rgb[..., 1] = (values * 97 + 71) % 256
    rgb[..., 2] = (values * 193 + 17) % 256
    rgb[values == 0] = (35, 35, 35)
    return Image.fromarray(rgb, mode="RGB")


def _scatter_panel(
    precipitation: np.ndarray,
    elevation: np.ndarray,
    ndvi: np.ndarray,
    *,
    size: tuple[int, int],
    report: Mapping[str, Any],
) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    margin = 55
    x0, y0, x1, y1 = margin, margin, width - 25, height - 70
    draw.rectangle((x0, y0, x1, y1), outline="black", width=2)
    if precipitation.size:
        px_min, px_max = float(precipitation.min()), float(precipitation.max())
        nd_min, nd_max = float(ndvi.min()), float(ndvi.max())
        el_min, el_max = float(elevation.min()), float(elevation.max())
        if px_max <= px_min:
            px_max = px_min + 1.0
        if nd_max <= nd_min:
            nd_max = nd_min + 1.0
        if el_max <= el_min:
            el_max = el_min + 1.0
        for x_value, y_value, color_value in zip(
            precipitation, ndvi, elevation, strict=True
        ):
            x = x0 + (float(x_value) - px_min) / (px_max - px_min) * (x1 - x0)
            y = y1 - (float(y_value) - nd_min) / (nd_max - nd_min) * (y1 - y0)
            shade = int(40 + 200 * (float(color_value) - el_min) / (el_max - el_min))
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(shade, 80, 255 - shade // 2))
    pearson = report["methods"]["pearson"]["precipitation__ndvi"]
    partial = report["methods"]["partial_correlation"][
        "precipitation__ndvi_controlling_elevation"
    ]
    draw.text((x0, 14), "NDVI vs accumulated precipitation", fill="black")
    draw.text((x0, height - 48), "Accumulated precipitation (mm)", fill="black")
    draw.text((8, y0), "NDVI", fill="black")
    draw.text(
        (x0, height - 27),
        f"Pearson r={pearson if pearson is not None else 'undefined'}  "
        f"partial r={partial if partial is not None else 'undefined'}",
        fill="black",
    )
    return image


def _panel(canvas: Image.Image, image: Image.Image, box: tuple[int, int, int, int], title: str) -> None:
    draw = ImageDraw.Draw(canvas)
    left, top, right, bottom = box
    draw.rectangle(box, fill=(245, 245, 245), outline=(80, 80, 80), width=2)
    draw.text((left + 10, top + 8), title, fill="black")
    inner = (left + 10, top + 34, right - 10, bottom - 10)
    resized = image.resize((inner[2] - inner[0], inner[3] - inner[1]), Image.Resampling.BILINEAR)
    canvas.paste(resized, (inner[0], inner[1]))


def render_preview(
    destination: Path,
    *,
    naip_path: Path,
    cdl_path: Path,
    precipitation: np.ndarray,
    elevation: np.ndarray,
    ndvi: np.ndarray,
    report: Mapping[str, Any],
    precipitation_nodata: float | None = NODATA,
    elevation_nodata: float | None = NODATA,
    ndvi_nodata: float | None = NODATA,
) -> Path:
    canvas = Image.new("RGB", (1800, 1200), (225, 225, 225))
    panel_width = 580
    panel_height = 540
    _panel(canvas, _rgb_context(naip_path, (520, 450)), (15, 55, 595, 595), "NAIP natural-color context")
    _panel(canvas, _cdl_context(cdl_path, (520, 450)), (610, 55, 1190, 595), "USDA CDL crop context")
    _panel(
        canvas,
        _ramp(precipitation, precipitation_nodata, [(250, 250, 255), (80, 150, 230), (20, 45, 120)]),
        (1205, 55, 1785, 595),
        "Accumulated PRISM precipitation",
    )
    _panel(
        canvas,
        _ramp(elevation, elevation_nodata, [(30, 90, 40), (210, 180, 90), (245, 245, 245)]),
        (15, 615, 595, 1155),
        "USGS 3DEP elevation",
    )
    _panel(
        canvas,
        _ramp(ndvi, ndvi_nodata, [(130, 70, 40), (230, 220, 120), (20, 125, 45)]),
        (610, 615, 1190, 1155),
        "NAIP-derived NDVI",
    )
    samples = report["sample_arrays"]
    _panel(
        canvas,
        _scatter_panel(
            samples["precipitation"],
            samples["elevation"],
            samples["ndvi"],
            size=(520, 450),
            report=report,
        ),
        (1205, 615, 1785, 1155),
        "Exploratory common-grid association",
    )
    draw = ImageDraw.Draw(canvas)
    draw.text((18, 15), "FasterRaster PRISM × DEM × NDVI correlation audit", fill="black")
    draw.text(
        (18, 1172),
        "Exploratory spatial association only; no causal or iid significance claim.",
        fill="black",
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, format="PNG", optimize=True)
    return destination


@contextmanager
def _temporary_prism_runtime_roots():
    from faster_raster import local_executor, materialization, task_compiler

    bindings = [
        (task_compiler, "REPORT_ROOT"),
        (task_compiler, "TASK_COMPILE_ROOT"),
        (task_compiler, "EXECUTION_PACKAGE_ROOT"),
        (local_executor, "COMPILE_ROOT"),
        (local_executor, "PACKAGE_ROOT"),
        (local_executor, "RUN_ROOT"),
        (materialization, "COMPILE_ROOT"),
        (materialization, "PACKAGE_ROOT"),
        (materialization, "MATERIALIZATION_ROOT"),
    ]
    previous = [(module, name, getattr(module, name)) for module, name in bindings]
    try:
        yield
    finally:
        for module, name, value in previous:
            setattr(module, name, value)


@contextmanager
def _temporary_task_roots(task_builder_module: Any, root: Path):
    previous = (
        task_builder_module.TASKS_DIR,
        task_builder_module.TASK_REPORTS_DIR,
        task_builder_module.TASK_PREVIEWS_DIR,
    )
    task_builder_module.TASKS_DIR = root / "tasks"
    task_builder_module.TASK_REPORTS_DIR = root / "reports" / "task_builder"
    task_builder_module.TASK_PREVIEWS_DIR = root / "reports" / "task_previews"
    try:
        task_builder_module.TASKS_DIR.mkdir(parents=True, exist_ok=True)
        yield
    finally:
        (
            task_builder_module.TASKS_DIR,
            task_builder_module.TASK_REPORTS_DIR,
            task_builder_module.TASK_PREVIEWS_DIR,
        ) = previous


def _prism_daily_harmonized(
    root: Path,
    workspace: Path,
    *,
    day: date,
    bbox: Sequence[float],
    target_resolution: float,
    max_object_bytes: int,
) -> tuple[dict[str, Any], Path, np.ndarray, dict[str, Any]]:
    from faster_raster import task_builder
    from faster_raster.prism_canary import run_canary
    from faster_raster.task_builder import deterministic_yaml

    task_id = f"prism_environmental_{day.strftime('%Y%m%d')}"
    task = {
        "task_id": task_id,
        "name": f"PRISM environmental correlation {day.isoformat()}",
        "description": "Guarded daily PRISM input for a normal environmental correlation cook.",
        "aoi": {"bbox": list(bbox), "bbox_crs": "EPSG:4326"},
        "target_grid": {"crs": DEFAULT_TARGET_CRS, "resolution_m": target_resolution},
        "time": {"years": [day.year], "dates": [day.isoformat()]},
        "themes": ["precipitation", "environmental_correlation"],
        "sources": [PRISM_SOURCE_ID],
        "preview": {"color_scheme": "default", "open_after_create": False},
        "notes": [],
    }
    with _temporary_task_roots(task_builder, workspace), _temporary_prism_runtime_roots():
        (task_builder.TASKS_DIR / f"{task_id}.yaml").write_text(
            deterministic_yaml(task), encoding="utf-8"
        )
        summary = run_canary(
            repo_root=root,
            workspace=workspace / "canary",
            task_id=task_id,
            execute=True,
            allow_network=True,
            allow_materialization=True,
            probe_bytes=65_536,
            max_object_bytes=max_object_bytes,
            max_total_bytes=max_object_bytes,
            max_raster_bytes=DEFAULT_PRISM_RASTER_CAP,
            timeout_seconds=120,
        )
    raster_path = Path(summary["raster_artifact_path"])
    plan = plan_prism_harmonization(
        raster_path,
        aoi_bbox=bbox,
        aoi_crs="EPSG:4326",
        target_crs=DEFAULT_TARGET_CRS,
        target_resolution=target_resolution,
        target_origin=(0.0, 0.0),
        resampling_method="bilinear",
        max_output_pixels=5_000_000,
        source_raster_sha256=summary["raster_sha256"],
    )
    receipt = execute_raster_harmonization(
        plan,
        artifact_root=workspace / "harmonized" / "sha256",
        staging_root=workspace / "harmonized" / "staging",
        receipt_path=workspace / "harmonization_receipt.json",
        max_output_bytes=128 * 1024 * 1024,
    )
    output = Path(receipt["output_artifact_path"])
    with rasterio.open(output) as dataset:
        values = dataset.read(indexes=(1,), masked=False)[0].astype(np.float64)
    stable = {
        "date": day.isoformat(),
        "source_id": PRISM_SOURCE_ID,
        "probe_bytes": 65_536,
        "object_size_bytes": int(summary["object_size_bytes"]),
        "whole_object_sha256": summary["whole_object_sha256"],
        "raster_sha256": summary["raster_sha256"],
        "raster_profile_sha256": summary["raster_profile"]["raster_profile_sha256"],
        "harmonization_plan_sha256": plan["harmonization_plan_sha256"],
        "harmonized_output_sha256": receipt["output_sha256"],
        "harmonized_receipt_sha256": receipt["harmonization_receipt_sha256"],
        "target_grid": plan["target_grid"],
        "status": "PASS",
    }
    return stable, output, values, plan


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def _regenerate_checksums(root: Path) -> Path:
    destination = root / "checksums.sha256"
    with destination.open("w", encoding="utf-8") as stream:
        for path in sorted(root.rglob("*")):
            if path.is_file() and path != destination and "_work" not in path.parts:
                stream.write(f"{_sha256(path)}  {path.relative_to(root).as_posix()}\n")
    return destination


def execute_environmental_correlation(
    repository_root: Path,
    *,
    workfile: Any,
    plan: Mapping[str, Any],
    open_preview: bool = False,
) -> Path:
    if plan.get("blocking"):
        raise EnvironmentalCorrelationError(
            "environmental correlation plan is blocked: "
            + "; ".join(plan.get("blocking_reasons") or [])
        )
    settings = workfile.spec.correlation
    if settings is None:
        raise EnvironmentalCorrelationError("correlation_contract_is_required")
    values = plan["resolved_config"]["values"]
    maximum_download_bytes = int(plan["maximum_download_bytes"])
    target_resolution = float(values["resolution_m"]["value"])
    bbox = tuple(float(item) for item in workfile.spec.area.bbox)
    days = _date_range(settings.precipitation_start, settings.precipitation_end)
    from faster_raster.ag_execution import (
        _run_selective_acquisition,
        configured_handoff_root,
        handoff_transaction,
    )
    from faster_raster.ag_recipes import load_named_recipe

    handoff_root = configured_handoff_root(repository_root)
    final = handoff_root / f"{workfile.spec.name}_{_utc_stamp()}"
    with handoff_transaction(final) as staging:
        work = staging / "_work"
        data = staging / "data"
        receipts = staging / "receipts"
        analysis = staging / "analysis"
        preview_dir = staging / "preview" / WORKFLOW_ID
        for directory in (work, data, receipts, analysis, preview_dir):
            directory.mkdir(parents=True, exist_ok=True)
        _write_json(staging / "asset_plan.json", plan["asset_plan"])
        _write_json(staging / "source_resolution.json", plan["source_resolution"])
        _write_json(staging / "resolved_config.json", plan["resolved_config"])

        recipe = load_named_recipe(repository_root, "crop_terrain_relationship")
        acquisition = _run_selective_acquisition(
            repository_root,
            staging,
            ["naip_multispectral", "cdl_classes"],
            name=workfile.spec.name,
            bbox=bbox,
            start=workfile.spec.time.start.isoformat(),
            end=workfile.spec.time.end.isoformat(),
            year=workfile.spec.time.crop_year,
            imagery_year=workfile.spec.time.crop_year,
            recipe=recipe,
            max_total_bytes=maximum_download_bytes,
            service_tile_size=int(values["service_tile_size"]["value"]),
            naip_resolution_m=settings.naip_analysis_resolution_m,
        )
        naip_path = data / f"naip_{workfile.spec.time.crop_year}_multispectral.cog.tif"
        cdl_path = data / f"cdl_{workfile.spec.time.crop_year}_classes.cog.tif"
        if not naip_path.is_file() or not cdl_path.is_file():
            raise EnvironmentalCorrelationError("naip_or_cdl_acquisition_missing")

        daily_receipts: list[dict[str, Any]] = []
        accumulated: np.ndarray | None = None
        valid_all: np.ndarray | None = None
        target_plan: dict[str, Any] | None = None
        total_prism_bytes = 0
        for day in days:
            remaining = maximum_download_bytes - int(acquisition.get("network_bytes", 0)) - total_prism_bytes
            if remaining <= 65_536:
                raise EnvironmentalCorrelationError("network_ceiling_exhausted_before_prism_completion")
            daily_workspace = work / "prism" / day.strftime("%Y%m%d")
            stable, daily_output, daily_values, daily_plan = _prism_daily_harmonized(
                repository_root,
                daily_workspace,
                day=day,
                bbox=bbox,
                target_resolution=target_resolution,
                max_object_bytes=min(DEFAULT_PRISM_OBJECT_CAP, remaining),
            )
            total_prism_bytes += stable["object_size_bytes"] + stable["probe_bytes"]
            if total_prism_bytes + int(acquisition.get("network_bytes", 0)) > maximum_download_bytes:
                raise EnvironmentalCorrelationError("network_ceiling_exceeded")
            if target_plan is None:
                target_plan = daily_plan
                accumulated = np.zeros_like(daily_values, dtype=np.float64)
                valid_all = np.ones_like(daily_values, dtype=bool)
            elif _grid_signature(daily_plan) != _grid_signature(target_plan):
                raise EnvironmentalCorrelationError("daily_prism_target_grids_do_not_match")
            assert accumulated is not None and valid_all is not None
            valid = np.isfinite(daily_values) & (daily_values != NODATA)
            valid_all &= valid
            accumulated[valid] += daily_values[valid]
            published_daily = (
                data
                / "prism_daily"
                / f"prism_{day.strftime('%Y%m%d')}_common_grid.cog.tif"
            )
            _copy_file(daily_output, published_daily)
            if _sha256(published_daily) != stable["harmonized_output_sha256"]:
                raise EnvironmentalCorrelationError(
                    "published_daily_prism_sha256_mismatch"
                )
            stable["published_harmonized_output"] = (
                published_daily.relative_to(staging).as_posix()
            )
            daily_receipts.append(stable)
            _write_json(receipts / "prism" / f"{day.strftime('%Y%m%d')}.json", stable)
        if target_plan is None or accumulated is None or valid_all is None:
            raise EnvironmentalCorrelationError("no_prism_days_were_executed")
        accumulated[~valid_all] = NODATA
        target_transform = Affine(*target_plan["target_grid"]["transform"])
        precipitation_path = data / (
            f"prism_{days[0].strftime('%Y%m%d')}_{days[-1].strftime('%Y%m%d')}_"
            "accumulated_precipitation.cog.tif"
        )
        _write_cog(
            precipitation_path,
            accumulated.astype(np.float32),
            transform=target_transform,
            crs=DEFAULT_TARGET_CRS,
            tags={
                "FASTERRASTER_SOURCE_ID": PRISM_SOURCE_ID,
                "FASTERRASTER_VARIABLE": "accumulated_precipitation",
                "FASTERRASTER_UNITS": "millimeters",
                "FASTERRASTER_DAY_COUNT": str(len(days)),
            },
        )

        elevation_source = work / "three_dep_elevation_30m.cog.tif"
        elevation_request = _acquire_elevation(
            elevation_source,
            bbox_epsg_4326=bbox,
            resolution_m=settings.elevation_resolution_m,
            max_bytes=max(1_000_000, maximum_download_bytes - int(acquisition.get("network_bytes", 0)) - total_prism_bytes),
        )
        if (
            int(acquisition.get("network_bytes", 0))
            + total_prism_bytes
            + elevation_request["bytes"]
            > maximum_download_bytes
        ):
            raise EnvironmentalCorrelationError("network_ceiling_exceeded")

        ndvi_source = work / "naip_ndvi_epsg5070.cog.tif"
        ndvi_derivation = _derive_ndvi_5070(
            naip_path,
            ndvi_source,
            resolution_m=settings.naip_analysis_resolution_m,
        )
        elevation_plan = _generic_plan(
            elevation_source,
            aoi_bbox=bbox,
            target_resolution=target_resolution,
            source_id=ELEVATION_SOURCE_ID,
            source_product="USGS 3DEP elevation",
            variable="elevation",
            units="meters",
            resampling_method="average",
        )
        ndvi_plan = _generic_plan(
            ndvi_source,
            aoi_bbox=bbox,
            target_resolution=target_resolution,
            source_id=NAIP_SOURCE_ID,
            source_product="NAIP-derived NDVI",
            variable="ndvi",
            units="unitless",
            resampling_method="average",
        )
        for candidate in (elevation_plan, ndvi_plan):
            if _grid_signature(candidate) != _grid_signature(target_plan):
                raise EnvironmentalCorrelationError("environmental_target_grids_do_not_match")
        elevation_receipt = execute_raster_harmonization(
            elevation_plan,
            artifact_root=data / "harmonized" / "elevation" / "sha256",
            staging_root=work / "harmonized" / "elevation" / "staging",
            max_output_bytes=128 * 1024 * 1024,
        )
        ndvi_receipt = execute_raster_harmonization(
            ndvi_plan,
            artifact_root=data / "harmonized" / "ndvi" / "sha256",
            staging_root=work / "harmonized" / "ndvi" / "staging",
            max_output_bytes=128 * 1024 * 1024,
        )
        elevation_artifact = Path(elevation_receipt["output_artifact_path"])
        ndvi_artifact = Path(ndvi_receipt["output_artifact_path"])
        elevation_path = data / "three_dep_elevation_mean_common_grid.cog.tif"
        ndvi_path = data / f"naip_{workfile.spec.time.crop_year}_ndvi_mean_common_grid.cog.tif"
        _copy_file(elevation_artifact, elevation_path)
        _copy_file(ndvi_artifact, ndvi_path)
        elevation_public_receipt = dict(elevation_receipt)
        elevation_public_receipt["output_artifact_path"] = elevation_artifact.relative_to(staging).as_posix()
        elevation_public_receipt["receipt_path"] = "receipts/elevation_harmonization.json"
        ndvi_public_receipt = dict(ndvi_receipt)
        ndvi_public_receipt["output_artifact_path"] = ndvi_artifact.relative_to(staging).as_posix()
        ndvi_public_receipt["receipt_path"] = "receipts/ndvi_harmonization.json"
        _write_json(
            receipts / "elevation_harmonization.json",
            elevation_public_receipt,
        )
        _write_json(
            receipts / "ndvi_harmonization.json",
            ndvi_public_receipt,
        )
        from faster_raster.raster_harmonization import verify_harmonization_receipt

        if verify_harmonization_receipt(
            receipts / "elevation_harmonization.json"
        )["verification_status"] != "PASS":
            raise EnvironmentalCorrelationError(
                "published_elevation_harmonization_receipt_failed"
            )
        if verify_harmonization_receipt(
            receipts / "ndvi_harmonization.json"
        )["verification_status"] != "PASS":
            raise EnvironmentalCorrelationError(
                "published_ndvi_harmonization_receipt_failed"
            )
        elevation_source_receipt = {
            **{key: value for key, value in elevation_request.items() if key != "output"},
            "source_raster_sha256": _sha256(elevation_source),
            "harmonized_output": elevation_path.relative_to(staging).as_posix(),
        }
        _write_json(
            receipts / "elevation_source.json",
            elevation_source_receipt,
        )
        _write_json(
            receipts / "ndvi_derivation.json",
            {
                **ndvi_derivation,
                "harmonized_output": ndvi_path.relative_to(staging).as_posix(),
            },
        )

        precipitation, _, _, precipitation_nodata = _read_values(precipitation_path)
        elevation, _, _, elevation_nodata = _read_values(elevation_path)
        ndvi, _, _, ndvi_nodata = _read_values(ndvi_path)
        report = correlation_report(
            precipitation,
            elevation,
            ndvi,
            precipitation_nodata=precipitation_nodata,
            elevation_nodata=elevation_nodata,
            ndvi_nodata=ndvi_nodata,
            minimum_valid_cells=settings.minimum_valid_cells,
        )
        sample_arrays = report.pop("sample_arrays")
        naip_acquisition_dates = (
            acquisition.get("naip", {}).get("selected_acquisition_dates", [])
            if isinstance(acquisition.get("naip"), dict)
            else []
        )
        report.update(
            {
                "study_name": workfile.spec.name,
                "bbox_epsg_4326": list(bbox),
                "target_crs": DEFAULT_TARGET_CRS,
                "target_resolution_m": target_resolution,
                "precipitation_period": {
                    "start": days[0].isoformat(),
                    "end": days[-1].isoformat(),
                    "day_count": len(days),
                    "aggregation": "sum_of_daily_depths_after_common_grid_harmonization",
                },
                "imagery_year": workfile.spec.time.crop_year,
                "naip_acquisition_dates": naip_acquisition_dates,
                "temporal_alignment": {
                    "status": "recorded_not_assumed",
                    "precipitation_window_is_immediate_antecedent_to_naip": False,
                    "note": (
                        "The declared precipitation window is compared with the "
                        "selected NAIP observation, but immediate antecedence is not "
                        "assumed unless independently established from the recorded dates."
                    ),
                },
                "cdl_year": workfile.spec.time.crop_year,
                "scientific_claim": plan["asset_plan"]["scientific_claim"],
                "unsupported_claims": plan["asset_plan"]["unsupported_claims"],
            }
        )
        _write_json(analysis / "correlation_summary.json", report)
        with (analysis / "pairwise_correlations.csv").open(
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.writer(handle)
            writer.writerow(["method", "pair", "correlation"])
            for method in ("pearson", "spearman_rank"):
                for pair, value in report["methods"][method].items():
                    writer.writerow([method, pair, "" if value is None else value])
            for pair, value in report["methods"]["partial_correlation"].items():
                writer.writerow(["partial_correlation", pair, "" if value is None else value])
        with (analysis / "common_grid_samples.csv").open(
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.writer(handle)
            writer.writerow(["sample_index", "precipitation_mm", "elevation_m", "ndvi"])
            for index, values_row in enumerate(
                zip(
                    sample_arrays["precipitation"],
                    sample_arrays["elevation"],
                    sample_arrays["ndvi"],
                    strict=True,
                )
            ):
                writer.writerow([index, *[float(item) for item in values_row]])
        preview_path = preview_dir / f"{WORKFLOW_ID}_4k.png"
        render_preview(
            preview_path,
            naip_path=naip_path,
            cdl_path=cdl_path,
            precipitation=precipitation,
            elevation=elevation,
            ndvi=ndvi,
            report={**report, "sample_arrays": sample_arrays},
            precipitation_nodata=precipitation_nodata,
            elevation_nodata=elevation_nodata,
            ndvi_nodata=ndvi_nodata,
        )

        total_network = (
            int(acquisition.get("network_bytes", 0))
            + total_prism_bytes
            + int(elevation_request["bytes"])
        )
        assets = [
            {
                "asset_name": "naip_multispectral",
                "action": "acquire",
                "reason": "raw four-band source for local NDVI derivation",
                "source_contract": "USGS NAIP ImageServer/exportImage",
                "output_path": naip_path.relative_to(staging).as_posix(),
                "bytes_downloaded": sum(
                    int(item.get("bytes", 0))
                    for item in acquisition.get("requests", [])
                    if str(item.get("label", "")).startswith("naip_multispectral_")
                ),
                "bytes_reused": 0,
                "sha256": _sha256(naip_path),
                "validation_result": "PASS",
            },
            {
                "asset_name": "cdl_classes",
                "action": "acquire",
                "reason": "same-year crop context, not a correlation predictor",
                "source_contract": "USDA CDL ImageServer/exportImage",
                "output_path": cdl_path.relative_to(staging).as_posix(),
                "bytes_downloaded": sum(
                    int(item.get("bytes", 0))
                    for item in acquisition.get("requests", [])
                    if str(item.get("label", "")).startswith("cdl_raw_")
                ),
                "bytes_reused": 0,
                "sha256": _sha256(cdl_path),
                "validation_result": "PASS",
            },
            {
                "asset_name": "elevation",
                "action": "acquire",
                "reason": "bounded 3DEP elevation export and common-grid mean",
                "source_contract": "USGS 3DEP ImageServer/exportImage",
                "output_path": elevation_path.relative_to(staging).as_posix(),
                "bytes_downloaded": int(elevation_request["bytes"]),
                "bytes_reused": 0,
                "sha256": _sha256(elevation_path),
                "validation_result": "PASS",
            },
            {
                "asset_name": "precipitation",
                "action": "acquire",
                "reason": f"guarded {len(days)}-day PRISM accumulation",
                "source_contract": "PRISM deterministic daily ZIP product pipeline",
                "output_path": precipitation_path.relative_to(staging).as_posix(),
                "bytes_downloaded": total_prism_bytes,
                "bytes_reused": 0,
                "sha256": _sha256(precipitation_path),
                "validation_result": "PASS",
            },
        ]
        generated_paths = sorted(
            {
                path.relative_to(staging).as_posix()
                for path in staging.rglob("*")
                if path.is_file() and "_work" not in path.parts
            }
            | {"workflow_receipt.json", "manifest.json", "checksums.sha256"}
        )
        receipt = {
            "schema_version": "fasterraster.environmental-correlation-receipt/v1",
            "status": "PASS",
            "final_status": "PASS",
            "workflow": WORKFLOW_ID,
            "requested_name": workfile.spec.name,
            "requested_bbox_epsg_4326": list(bbox),
            "requested_timeframe": {
                "start": workfile.spec.time.start.isoformat(),
                "end": workfile.spec.time.end.isoformat(),
            },
            "requested_cdl_year": workfile.spec.time.crop_year,
            "precipitation_period": report["precipitation_period"],
            "actual_naip_acquisition_dates": naip_acquisition_dates,
            "temporal_alignment": report["temporal_alignment"],
            "target_grid": target_plan["target_grid"],
            "assets": assets,
            "total_network_bytes": total_network,
            "total_reused_bytes": 0,
            "generated_output_paths": generated_paths,
            "correlation_summary": "analysis/correlation_summary.json",
            "preview": preview_path.relative_to(staging).as_posix(),
            "scientific_claim": report["scientific_claim"],
            "unsupported_claims": report["unsupported_claims"],
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        _write_json(staging / "workflow_receipt.json", receipt)
        manifest = {
            "schema_version": 2,
            "operation_status": "completed",
            "verification_status": "PASS",
            "workflow": WORKFLOW_ID,
            "order": {
                "name": workfile.spec.name,
                "bbox_epsg_4326": list(bbox),
                "imagery_year": workfile.spec.time.crop_year,
                "naip_acquisition_dates": naip_acquisition_dates,
                "temporal_alignment": {
                    "status": "recorded_not_assumed",
                    "precipitation_window_is_immediate_antecedent_to_naip": False,
                    "note": (
                        "The declared precipitation window is compared with the "
                        "selected NAIP observation, but immediate antecedence is not "
                        "assumed unless independently established from the recorded dates."
                    ),
                },
                "cdl_year": workfile.spec.time.crop_year,
                "precipitation_start": days[0].isoformat(),
                "precipitation_end": days[-1].isoformat(),
                "network_ceiling_bytes": maximum_download_bytes,
            },
            "layers": [
                {
                    "name": item["asset_name"],
                    "output": item["output_path"],
                    "output_bytes": (staging / item["output_path"]).stat().st_size,
                    "output_sha256": item["sha256"],
                }
                for item in assets
            ]
            + [
                {
                    "name": "ndvi",
                    "output": ndvi_path.relative_to(staging).as_posix(),
                    "output_bytes": ndvi_path.stat().st_size,
                    "output_sha256": _sha256(ndvi_path),
                }
            ],
            "network_bytes": total_network,
            "reused_bytes": 0,
            "requests": acquisition.get("requests", []),
            "prism_daily_receipts": [
                f"receipts/prism/{day.strftime('%Y%m%d')}.json" for day in days
            ],
            "correlation_summary": "analysis/correlation_summary.json",
            "preview": preview_path.relative_to(staging).as_posix(),
            "warnings": [
                "Correlations are exploratory spatial associations without iid p-values.",
                "PRISM precipitation is a 4 km gridded depth surface and is not field-scale truth.",
                "NAIP, precipitation, and elevation represent different observation processes and timescales.",
                "The declared precipitation window is not assumed to be immediately antecedent to the selected NAIP acquisition date.",
            ],
            "blocking_failures": [],
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        _write_json(staging / "manifest.json", manifest)
        shutil.rmtree(work, ignore_errors=True)
        _regenerate_checksums(staging)
        preview_relative = preview_path.relative_to(staging)
    final_preview = final / preview_relative
    if open_preview:
        from faster_raster.ag_execution import _open_final_preview

        _open_final_preview(final_preview)
    print("===== FASTERRASTER ENVIRONMENTAL CORRELATION: PASS =====")
    print(f"handoff: {final}")
    print(f"network_bytes: {total_network}")
    print(f"common_valid_cells: {report['common_valid_cell_count']}")
    print(f"preview: {final_preview}")
    print(f"correlation_summary: {final / 'analysis' / 'correlation_summary.json'}")
    return final_preview


def run_self_check() -> dict[str, Any]:
    rows, columns = 8, 8
    elevation = np.arange(rows * columns, dtype=np.float64).reshape(rows, columns)
    precipitation = 10.0 + elevation * 0.25
    ndvi = 0.2 + precipitation * 0.01 - elevation * 0.0005
    report = correlation_report(
        precipitation,
        elevation,
        ndvi,
        precipitation_nodata=NODATA,
        elevation_nodata=NODATA,
        ndvi_nodata=NODATA,
        minimum_valid_cells=12,
    )
    with tempfile.TemporaryDirectory(prefix="fr-environmental-self-check-") as temporary:
        root = Path(temporary)
        transform = from_origin(0, 8, 1, 1)
        ppt_path = _write_cog(
            root / "ppt.tif",
            precipitation.astype(np.float32),
            transform=transform,
            crs=DEFAULT_TARGET_CRS,
        )
        with rasterio.open(ppt_path) as dataset:
            cog_ok = (
                dataset.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG"
                and dataset.count == 1
            )
        naip = root / "naip.tif"
        with rasterio.open(
            naip,
            "w",
            driver="GTiff",
            width=columns,
            height=rows,
            count=4,
            dtype="uint8",
            crs=DEFAULT_TARGET_CRS,
            transform=transform,
        ) as dataset:
            for band in range(1, 5):
                dataset.write(
                    np.full((rows, columns), 30 * band, dtype=np.uint8),
                    band,
                )
        cdl = root / "cdl.tif"
        with rasterio.open(
            cdl,
            "w",
            driver="GTiff",
            width=columns,
            height=rows,
            count=1,
            dtype="uint16",
            crs=DEFAULT_TARGET_CRS,
            transform=transform,
            nodata=0,
        ) as dataset:
            dataset.write(
                (np.arange(rows * columns).reshape(rows, columns) % 20 + 1).astype(np.uint16),
                1,
            )
        preview = render_preview(
            root / "preview.png",
            naip_path=naip,
            cdl_path=cdl,
            precipitation=precipitation,
            elevation=elevation,
            ndvi=ndvi,
            report=report,
        )
        preview_ok = preview.is_file() and Image.open(preview).size == (1800, 1200)
    checks = {
        "status": "PASS",
        "valid_cell_count": report["common_valid_cell_count"],
        "pearson_precipitation_ndvi": report["methods"]["pearson"][
            "precipitation__ndvi"
        ],
        "partial_precipitation_ndvi": report["methods"]["partial_correlation"][
            "precipitation__ndvi_controlling_elevation"
        ],
        "cog_write": cog_ok,
        "preview_render": preview_ok,
        "no_p_values": report["interpretation_guard"]["p_values_computed"] is False,
    }
    if not cog_ok or not preview_ok or report["common_valid_cell_count"] != 64:
        raise EnvironmentalCorrelationError("self_check_failed")
    return checks


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if not args.self_check:
        parser.error("only --self-check is available; normal execution is through fr cook")
    print(json.dumps(run_self_check(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
