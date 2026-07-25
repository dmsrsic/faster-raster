from __future__ import annotations

import json
import math
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Literal

from faster_raster.ag_recipes import AgriculturalRecipe


ASSET_PATTERNS: dict[str, tuple[str, ...]] = {
    "natural": ("naip_*_natural_color.cog.tif",),
    "naip_multispectral": ("naip_*_multispectral.cog.tif",),
    "ndvi": ("naip_*_ndvi_color.cog.tif",),
    "cdl_classes": ("cdl_*_classes.cog.tif",),
    "cdl_color": ("cdl_*_color.cog.tif",),
    "hillshade": ("three_dep_hillshade.cog.tif", "*hillshade*.cog.tif"),
}
SOURCE_FAMILY = {
    "natural": "USGS_NAIP",
    "naip_multispectral": "USGS_NAIP",
    "ndvi": "USGS_NAIP",
    "cdl_classes": "USDA_CDL",
    "cdl_color": "USDA_CDL",
    "hillshade": "USGS_3DEP",
}
SEMANTICS = {
    "natural": "continuous",
    "naip_multispectral": "continuous_multiband_imagery",
    "ndvi": "continuous",
    "cdl_classes": "categorical",
    "cdl_color": "categorical",
    "hillshade": "continuous",
}
YEAR_RE = re.compile(r"(?:naip|cdl)_(\d{4})_")
SpatialRelationship = Literal["exact", "contains", "partial_overlap", "no_overlap"]
PlanAction = Literal[
    "reuse_direct",
    "reuse_crop",
    "reuse_reproject",
    "reuse_crop_reproject",
    "acquire",
    "acquire_and_mosaic",
    "reject",
]


@dataclass(frozen=True)
class AssetRecord:
    asset_name: str
    source_family: str
    temporal_key: int | None
    bbox_epsg_4326: tuple[float, float, float, float] | None
    extent_native: tuple[float, float, float, float] | None
    crs: str | None
    pixel_size: tuple[float, float] | None
    pixel_size_m: float | None
    width: int | None
    height: int | None
    nodata: tuple[float | int | None, ...]
    semantic_type: str
    checksum: str | None
    local_path: str
    originating_handoff: str
    validation_state: str
    validation_errors: tuple[str, ...]
    can_crop_locally: bool
    requires_reprojection: bool
    resolution_satisfies_recipe: bool | None = None

    def stable_source_reference(self) -> dict[str, str]:
        handoff = Path(self.originating_handoff)
        local = Path(self.local_path)
        try:
            relative = local.resolve().relative_to(handoff.resolve())
        except (OSError, ValueError):
            relative = Path(local.name)
        return {
            "source_handoff_id": handoff.name,
            "source_relative_path": relative.as_posix(),
        }

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop("local_path", None)
        result.pop("originating_handoff", None)
        result.update(self.stable_source_reference())
        return result


@dataclass(frozen=True)
class AssetDecision:
    asset_name: str
    action: PlanAction
    reason: str
    candidate: AssetRecord | None
    spatial_relationship: SpatialRelationship | None
    resampling: str
    tolerance_degrees: float
    rejected_candidates: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["candidate"] = self.candidate.to_dict() if self.candidate else None
        return result


def _crs_label(info: dict[str, Any]) -> str | None:
    coordinate_system = info.get("coordinateSystem") or {}
    wkt = str(coordinate_system.get("wkt") or "")
    matches = re.findall(r'(?:AUTHORITY|ID)\["EPSG",\s*"?(\d+)"?\]', wkt)
    if matches:
        return f"EPSG:{matches[-1]}"
    return wkt or None


def _polygon_bbox(value: Any) -> tuple[float, float, float, float] | None:
    try:
        coordinates = value["coordinates"]
        while coordinates and isinstance(coordinates[0][0], list):
            coordinates = coordinates[0]
        xs = [float(pair[0]) for pair in coordinates]
        ys = [float(pair[1]) for pair in coordinates]
        return min(xs), min(ys), max(xs), max(ys)
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _native_extent(info: dict[str, Any]) -> tuple[float, float, float, float] | None:
    transform = info.get("geoTransform")
    size = info.get("size")
    if not transform or not size or len(transform) < 6 or len(size) < 2:
        return None
    width, height = int(size[0]), int(size[1])
    corners = [
        (transform[0], transform[3]),
        (transform[0] + width * transform[1], transform[3] + width * transform[4]),
        (transform[0] + height * transform[2], transform[3] + height * transform[5]),
        (
            transform[0] + width * transform[1] + height * transform[2],
            transform[3] + width * transform[4] + height * transform[5],
        ),
    ]
    xs = [float(value[0]) for value in corners]
    ys = [float(value[1]) for value in corners]
    return min(xs), min(ys), max(xs), max(ys)


def _pixel_size_m(
    pixel_size: tuple[float, float] | None,
    crs: str | None,
    bbox: tuple[float, float, float, float] | None,
    wkt: str,
) -> float | None:
    if pixel_size is None:
        return None
    largest = max(pixel_size)
    if crs != "EPSG:4326" and ("LENGTHUNIT[\"metre\"" in wkt or crs == "EPSG:3857"):
        return largest
    if crs == "EPSG:4326" and bbox is not None:
        latitude = (bbox[1] + bbox[3]) / 2
        x_m = pixel_size[0] * 111_320 * max(0.01, math.cos(math.radians(latitude)))
        y_m = pixel_size[1] * 110_574
        return max(x_m, y_m)
    return None


def _manifest_evidence(handoff: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest_path = handoff / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, {}
    layers: dict[str, dict[str, Any]] = {}
    order = manifest.get("order") or {}
    for layer in manifest.get("layers", []):
        output = layer.get("output")
        if isinstance(output, str):
            layers[Path(output).name] = {
                **layer,
                "_manifest_cdl_year": order.get("cdl_year"),
                "_manifest_imagery_year": order.get(
                    "imagery_year", order.get("cdl_year")
                ),
                "_manifest_time_start": order.get("time_start"),
                "_manifest_time_end": order.get("time_end"),
            }
    return manifest, layers


def inspect_asset(
    path: Path,
    asset_name: str,
    handoff: Path,
    *,
    layer_evidence: dict[str, Any] | None = None,
    target_crs: str = "EPSG:3857",
    gdalinfo: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> AssetRecord:
    errors: list[str] = []
    info: dict[str, Any] = {}
    try:
        result = gdalinfo(
            ["gdalinfo", "-json", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
        info = json.loads(result.stdout)
    except Exception as exc:
        errors.append(f"gdalinfo_failed: {exc}")

    size = info.get("size") or [None, None]
    transform = info.get("geoTransform")
    pixel_size = None
    if transform and len(transform) >= 6:
        pixel_size = (abs(float(transform[1])), abs(float(transform[5])))
    crs = _crs_label(info)
    native_extent = _native_extent(info)
    bbox = _polygon_bbox(info.get("wgs84Extent"))
    if bbox is None and crs == "EPSG:4326":
        bbox = native_extent
    if bbox is None:
        errors.append("wgs84_extent_missing")
    if crs is None:
        errors.append("crs_missing")
    if pixel_size is None or not all(value > 0 for value in pixel_size):
        errors.append("pixel_size_invalid")
    if not size[0] or not size[1]:
        errors.append("raster_size_invalid")

    filename_year = YEAR_RE.search(path.name)
    filename_temporal_key = int(filename_year.group(1)) if filename_year else None
    layer_evidence = layer_evidence or {}
    manifest_temporal_key = layer_evidence.get(
        "_manifest_imagery_year"
        if asset_name in {"natural", "naip_multispectral", "ndvi"}
        else "_manifest_cdl_year"
    )
    try:
        manifest_temporal_key = (
            int(manifest_temporal_key)
            if manifest_temporal_key is not None
            else None
        )
    except (TypeError, ValueError):
        errors.append("manifest_temporal_key_invalid")
        manifest_temporal_key = None
    if (
        filename_temporal_key is not None
        and manifest_temporal_key is not None
        and filename_temporal_key != manifest_temporal_key
    ):
        errors.append(
            f"temporal_evidence_conflict:{filename_temporal_key}!={manifest_temporal_key}"
        )
    temporal_key = manifest_temporal_key or filename_temporal_key
    checksum = layer_evidence.get("output_sha256")
    bands = info.get("bands") or []
    if asset_name == "naip_multispectral":
        if len(bands) != 4:
            errors.append(f"band_count_{len(bands)}_is_not_4")
        data_types = {
            str(band.get("type") or "").lower() for band in bands
        }
        if data_types != {"byte"}:
            errors.append(f"band_types_are_{sorted(data_types)}_not_byte")
    nodata = tuple(band.get("noDataValue") for band in bands)
    wkt = str((info.get("coordinateSystem") or {}).get("wkt") or "")

    return AssetRecord(
        asset_name=asset_name,
        source_family=SOURCE_FAMILY[asset_name],
        temporal_key=temporal_key,
        bbox_epsg_4326=bbox,
        extent_native=native_extent,
        crs=crs,
        pixel_size=pixel_size,
        pixel_size_m=_pixel_size_m(pixel_size, crs, bbox, wkt),
        width=int(size[0]) if size[0] else None,
        height=int(size[1]) if size[1] else None,
        nodata=nodata,
        semantic_type=SEMANTICS[asset_name],
        checksum=str(checksum) if checksum else None,
        local_path=str(path.resolve()),
        originating_handoff=str(handoff.resolve()),
        validation_state="valid" if not errors else "invalid",
        validation_errors=tuple(errors),
        can_crop_locally=not errors and bbox is not None,
        requires_reprojection=crs != target_crs if crs else True,
    )


def discover_cached_assets(
    handoff_root: Path,
    *,
    inspector: Callable[..., AssetRecord] = inspect_asset,
) -> list[AssetRecord]:
    records: list[AssetRecord] = []
    if not handoff_root.is_dir():
        return records
    for handoff in sorted(handoff_root.iterdir(), key=lambda value: value.name):
        if not handoff.is_dir() or handoff.name.startswith((".", "_")):
            continue
        if any(token in handoff.name.lower() for token in ("tmp", "staging", "incomplete", "failed")):
            continue
        manifest, layers = _manifest_evidence(handoff)
        if manifest.get("operation_status") not in {"completed", "PASS"}:
            continue
        data = handoff / "data"
        if not data.is_dir():
            continue
        seen: set[Path] = set()
        for asset_name, patterns in ASSET_PATTERNS.items():
            for pattern in patterns:
                for path in sorted(data.glob(pattern)):
                    if path in seen or not path.is_file() or path.name.endswith(".sha256"):
                        continue
                    seen.add(path)
                    records.append(
                        inspector(
                            path,
                            asset_name,
                            handoff,
                            layer_evidence=layers.get(path.name),
                        )
                    )
    return records


def spatial_relationship(
    cached: tuple[float, float, float, float] | None,
    requested: tuple[float, float, float, float],
    *,
    tolerance: float = 1e-6,
) -> SpatialRelationship:
    if cached is None:
        return "no_overlap"
    if all(abs(left - right) <= tolerance for left, right in zip(cached, requested)):
        return "exact"
    if (
        cached[0] <= requested[0] + tolerance
        and cached[1] <= requested[1] + tolerance
        and cached[2] >= requested[2] - tolerance
        and cached[3] >= requested[3] - tolerance
    ):
        return "contains"
    intersects = not (
        cached[2] <= requested[0] + tolerance
        or cached[0] >= requested[2] - tolerance
        or cached[3] <= requested[1] + tolerance
        or cached[1] >= requested[3] - tolerance
    )
    return "partial_overlap" if intersects else "no_overlap"


def _resampling(recipe: AgriculturalRecipe, asset_name: str) -> str:
    if SEMANTICS[asset_name] == "categorical":
        return recipe.resampling.categorical
    if asset_name == "hillshade":
        return recipe.resampling.terrain or "bilinear"
    return recipe.resampling.imagery


def _candidate_rejections(
    record: AssetRecord,
    recipe: AgriculturalRecipe,
    requested_bbox: tuple[float, float, float, float],
    year: int,
    tolerance: float,
) -> tuple[list[str], SpatialRelationship]:
    reasons: list[str] = []
    relationship = spatial_relationship(record.bbox_epsg_4326, requested_bbox, tolerance=tolerance)
    if record.validation_state != "valid":
        reasons.extend(record.validation_errors or ("invalid_raster",))
    if record.asset_name in {
        "natural",
        "naip_multispectral",
        "ndvi",
        "cdl_classes",
        "cdl_color",
    }:
        if record.temporal_key != year:
            reasons.append(f"temporal_key_{record.temporal_key}_does_not_match_{year}")
    if record.asset_name in {"natural", "naip_multispectral", "ndvi"}:
        if record.pixel_size_m is None:
            reasons.append("metric_resolution_unknown")
        elif record.pixel_size_m > recipe.maximum_naip_pixel_size_m + 0.01:
            reasons.append(
                f"pixel_size_{record.pixel_size_m:g}_exceeds_{recipe.maximum_naip_pixel_size_m:g}"
            )
    if relationship not in {"exact", "contains"}:
        reasons.append(f"spatial_{relationship}")
    if record.semantic_type != SEMANTICS[record.asset_name]:
        reasons.append("semantic_type_mismatch")
    return reasons, relationship


def compile_asset_plan(
    recipe: AgriculturalRecipe,
    assets: Iterable[AssetRecord],
    requested_bbox: tuple[float, float, float, float],
    year: int,
    reuse_mode: Literal["auto", "only", "never"],
    *,
    imagery_year: int | None = None,
    target_crs: str = "EPSG:3857",
    tolerance: float = 1e-6,
) -> list[AssetDecision]:
    inventory = list(assets)
    decisions: list[AssetDecision] = []
    for asset_name in recipe.required_assets:
        resampling = _resampling(recipe, asset_name)
        if reuse_mode == "never":
            decisions.append(
                AssetDecision(
                    asset_name=asset_name,
                    action="acquire",
                    reason="reuse mode never requires fresh acquisition",
                    candidate=None,
                    spatial_relationship=None,
                    resampling=resampling,
                    tolerance_degrees=tolerance,
                )
            )
            continue

        compatible: list[tuple[int, float, str, AssetRecord, SpatialRelationship]] = []
        rejected: list[dict[str, Any]] = []
        requested_year = (
            imagery_year
            if imagery_year is not None
            and asset_name in {"natural", "naip_multispectral", "ndvi"}
            else year
        )
        for record in inventory:
            if record.asset_name != asset_name:
                continue
            reasons, relationship = _candidate_rejections(
                record, recipe, requested_bbox, requested_year, tolerance
            )
            if reasons:
                stable = record.stable_source_reference()
                rejected.append(
                    {
                        **stable,
                        "reasons": reasons,
                        "spatial_relationship": relationship,
                    }
                )
                continue
            compatible.append(
                (
                    0 if relationship == "exact" else 1,
                    record.pixel_size_m or float("inf"),
                    record.local_path,
                    record,
                    relationship,
                )
            )

        compatible.sort(key=lambda item: item[:3])
        if compatible:
            _, _, _, selected, relationship = compatible[0]
            reproject = selected.crs != target_crs
            if relationship == "exact" and not reproject:
                action: PlanAction = "reuse_direct"
            elif relationship == "contains" and not reproject:
                action = "reuse_crop"
            elif relationship == "exact":
                action = "reuse_reproject"
            else:
                action = "reuse_crop_reproject"
            reason = (
                f"compatible {relationship} coverage, exact temporal/semantic match, "
                "and acceptable resolution"
            )
            decisions.append(
                AssetDecision(
                    asset_name=asset_name,
                    action=action,
                    reason=reason,
                    candidate=selected,
                    spatial_relationship=relationship,
                    resampling=resampling,
                    tolerance_degrees=tolerance,
                    rejected_candidates=tuple(rejected),
                )
            )
        else:
            reason = "no compatible cached asset"
            if rejected:
                reason += "; candidates were rejected with recorded evidence"
            decisions.append(
                AssetDecision(
                    asset_name=asset_name,
                    action="reject" if reuse_mode == "only" else "acquire",
                    reason=reason,
                    candidate=None,
                    spatial_relationship=None,
                    resampling=resampling,
                    tolerance_degrees=tolerance,
                    rejected_candidates=tuple(rejected),
                )
            )
    return decisions


def asset_plan_document(
    recipe: AgriculturalRecipe,
    decisions: Iterable[AssetDecision],
    *,
    bbox: tuple[float, float, float, float],
    start: str,
    end: str,
    year: int,
    reuse_mode: str,
    imagery_year: int | None = None,
    requested_resolution_m: float | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "recipe_id": recipe.recipe_id,
        "recipe_schema_version": recipe.schema_version,
        "requested_bbox_epsg_4326": list(bbox),
        "requested_timeframe": {"start": start, "end": end},
        "requested_cdl_year": year,
        "requested_imagery_year": imagery_year if imagery_year is not None else year,
        "reuse_mode": reuse_mode,
        "spatial_tolerance_degrees": 1e-6,
        "effective_naip_resolution_m": (
            requested_resolution_m
            if requested_resolution_m is not None
            else recipe.defaults.naip_resolution_meters
        ),
        "assets": [decision.to_dict() for decision in decisions],
        "network_required_assets": [
            decision.asset_name
            for decision in decisions
            if decision.action in {"acquire", "acquire_and_mosaic"}
        ],
        "blocking_assets": [
            decision.asset_name for decision in decisions if decision.action == "reject"
        ],
    }
