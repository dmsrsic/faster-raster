from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

import numpy as np
import rasterio
from faster_raster.development_sources import (
    ANNUAL_NLCD_MAPPING,
    DevelopmentMapping,
)
from affine import Affine
from rasterio.crs import CRS
from rasterio.vrt import WarpedVRT
from rasterio.warp import Resampling, transform, transform_bounds
from rasterio.windows import Window


TARGET_CRS = "EPSG:5070"
TARGET_RESOLUTION_M = 30.0
LAND_COVER_NODATA = ANNUAL_NLCD_MAPPING.nodata_code
IMPERvious_NODATA = 250.0
MAPPING_VERSION = ANNUAL_NLCD_MAPPING.mapping_id
VALID_LAND_COVER_CODES = ANNUAL_NLCD_MAPPING.valid_codes
DEVELOPED_RANK = dict(ANNUAL_NLCD_MAPPING.developed_ranks)
SOURCE_CLASS_LABELS = dict(ANNUAL_NLCD_MAPPING.class_labels)
ABSTRACT_STATE_LABELS = (
    "non_developed",
    "developed_open_space",
    "developed_low_intensity",
    "developed_medium_intensity",
    "developed_high_intensity",
)
CHANGE_CODE_INFO = {
    0: ("invalid_comparison", "One or both land-cover values are nodata or outside the official legend."),
    1: ("stable_non_developed", "Both valid values are the same non-developed source class."),
    2: ("stable_developed", "Both valid values have the same developed intensity."),
    3: ("new_development", "A valid non-developed value changes to a developed state in the active mapping contract."),
    4: ("apparent_development_loss", "A developed state in the active mapping contract changes to valid non-developed."),
    5: ("development_intensity_increase", "Both values are developed and the intensity rank increases."),
    6: ("development_intensity_decrease", "Both values are developed and the intensity rank decreases."),
    7: ("other_valid_land_cover_change", "Both values are non-developed but the source land-cover class changes."),
}


class HumanDevelopmentError(RuntimeError):
    pass


@dataclass(frozen=True)
class TargetGrid:
    crs: str
    transform: Affine
    width: int
    height: int
    resolution_m: float
    source_alignment_origin: tuple[float, float]
    snap_policy: str = "outward_to_first_epoch_affine_origin"

    @property
    def pixel_area_m2(self) -> float:
        return abs(self.transform.a * self.transform.e - self.transform.b * self.transform.d)

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            {
                "crs": self.crs,
                "transform": list(self.transform)[:6],
                "width": self.width,
                "height": self.height,
                "resolution_m": self.resolution_m,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "crs": self.crs,
            "transform": list(self.transform)[:6],
            "width": self.width,
            "height": self.height,
            "resolution_m": self.resolution_m,
            "pixel_area_m2": self.pixel_area_m2,
            "snap_policy": self.snap_policy,
            "source_alignment_origin": list(self.source_alignment_origin),
            "fingerprint_sha256": self.fingerprint,
        }


def resolve_local_path(workfile_path: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    return (candidate if candidate.is_absolute() else workfile_path.parent / candidate).resolve()


def inspect_raster(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise HumanDevelopmentError(f"pinned raster does not exist: {path}")
    try:
        with rasterio.open(path) as dataset:
            if dataset.count < 1:
                raise HumanDevelopmentError(f"pinned raster has no bands: {path}")
            return {
                "path": str(path),
                "bytes": path.stat().st_size,
                "driver": dataset.driver,
                "width": dataset.width,
                "height": dataset.height,
                "count": dataset.count,
                "dtype": dataset.dtypes[0],
                "nodata": dataset.nodata,
                "crs": dataset.crs.to_string() if dataset.crs else None,
                "transform": list(dataset.transform)[:6],
                "bounds": list(dataset.bounds),
            }
    except (OSError, rasterio.errors.RasterioError) as exc:
        raise HumanDevelopmentError(f"unable to read pinned raster {path}: {exc}") from exc


def build_target_grid(
    bbox_epsg4326: Sequence[float],
    first_epoch_path: Path,
    *,
    resolution_m: float = TARGET_RESOLUTION_M,
) -> TargetGrid:
    if resolution_m != TARGET_RESOLUTION_M:
        raise HumanDevelopmentError("human-development target resolution must be exactly 30 metres")
    with rasterio.open(first_epoch_path) as first:
        if first.crs is None:
            raise HumanDevelopmentError("first epoch raster must declare a CRS")
        if CRS.from_user_input(first.crs) == CRS.from_string(TARGET_CRS):
            origin_x, origin_y = first.transform.c, first.transform.f
        else:
            xs, ys = transform(first.crs, TARGET_CRS, [first.transform.c], [first.transform.f])
            origin_x, origin_y = xs[0], ys[0]
    left, bottom, right, top = transform_bounds(
        "EPSG:4326",
        TARGET_CRS,
        *[float(value) for value in bbox_epsg4326],
        densify_pts=21,
    )
    col_min = math.floor((left - origin_x) / resolution_m)
    col_max = math.ceil((right - origin_x) / resolution_m)
    row_min = math.floor((origin_y - top) / resolution_m)
    row_max = math.ceil((origin_y - bottom) / resolution_m)
    width = col_max - col_min
    height = row_max - row_min
    if width <= 0 or height <= 0:
        raise HumanDevelopmentError("AOI produced an empty target grid")
    if width * height > 100_000_000:
        raise HumanDevelopmentError("AOI target grid exceeds the 100,000,000-pixel safety ceiling")
    aligned_x = origin_x + col_min * resolution_m
    aligned_y = origin_y - row_min * resolution_m
    return TargetGrid(
        crs=TARGET_CRS,
        transform=Affine(resolution_m, 0.0, aligned_x, 0.0, -resolution_m, aligned_y),
        width=width,
        height=height,
        resolution_m=resolution_m,
        source_alignment_origin=(origin_x, origin_y),
    )


def build_service_target_grid(
    bbox_epsg4326: Sequence[float],
    *,
    resolution_m: float = TARGET_RESOLUTION_M,
) -> TargetGrid:
    if resolution_m != TARGET_RESOLUTION_M:
        raise HumanDevelopmentError("human-development target resolution must be exactly 30 metres")
    left, bottom, right, top = transform_bounds(
        "EPSG:4326", TARGET_CRS, *[float(value) for value in bbox_epsg4326], densify_pts=21
    )
    aligned_left = math.floor(left / resolution_m) * resolution_m
    aligned_bottom = math.floor(bottom / resolution_m) * resolution_m
    aligned_right = math.ceil(right / resolution_m) * resolution_m
    aligned_top = math.ceil(top / resolution_m) * resolution_m
    return TargetGrid(
        TARGET_CRS,
        Affine(resolution_m, 0, aligned_left, 0, -resolution_m, aligned_top),
        int(round((aligned_right - aligned_left) / resolution_m)),
        int(round((aligned_top - aligned_bottom) / resolution_m)),
        resolution_m,
        (0.0, 0.0),
        "outward_to_global_epsg5070_30m_origin",
    )


def iter_windows(width: int, height: int, block_size: int) -> Iterator[Window]:
    for row_off in range(0, height, block_size):
        for col_off in range(0, width, block_size):
            yield Window(
                col_off=col_off,
                row_off=row_off,
                width=min(block_size, width - col_off),
                height=min(block_size, height - row_off),
            )


def _mapping_luts(mapping: DevelopmentMapping) -> tuple[np.ndarray, np.ndarray]:
    valid_lut = np.zeros(256, dtype=np.bool_)
    valid_lut[list(mapping.valid_codes)] = True
    rank_lut = np.full(256, -1, dtype=np.int8)
    rank_lut[list(mapping.valid_codes)] = 0
    for code, rank in mapping.developed_ranks.items():
        rank_lut[code] = rank
    return valid_lut, rank_lut


def valid_land_cover(values: np.ndarray, mapping: DevelopmentMapping = ANNUAL_NLCD_MAPPING) -> np.ndarray:
    numeric = np.asarray(values)
    in_range = (numeric >= 0) & (numeric <= 255)
    result = np.zeros(numeric.shape, dtype=np.bool_)
    valid_lut, _ = _mapping_luts(mapping)
    result[in_range] = valid_lut[numeric[in_range].astype(np.uint8)]
    return result


def development_rank(values: np.ndarray, mapping: DevelopmentMapping = ANNUAL_NLCD_MAPPING) -> np.ndarray:
    numeric = np.asarray(values)
    result = np.full(numeric.shape, -1, dtype=np.int8)
    in_range = (numeric >= 0) & (numeric <= 255)
    _, rank_lut = _mapping_luts(mapping)
    result[in_range] = rank_lut[numeric[in_range].astype(np.uint8)]
    return result


def classify_change(before: np.ndarray, after: np.ndarray, mapping: DevelopmentMapping = ANNUAL_NLCD_MAPPING) -> np.ndarray:
    before_values = np.asarray(before)
    after_values = np.asarray(after)
    if before_values.shape != after_values.shape:
        raise HumanDevelopmentError("change inputs must have identical shapes")
    before_rank = development_rank(before_values, mapping)
    after_rank = development_rank(after_values, mapping)
    valid = (before_rank >= 0) & (after_rank >= 0)
    result = np.zeros(before_values.shape, dtype=np.uint8)
    both_non_developed = valid & (before_rank == 0) & (after_rank == 0)
    result[both_non_developed & (before_values == after_values)] = 1
    result[both_non_developed & (before_values != after_values)] = 7
    result[valid & (before_rank > 0) & (before_rank == after_rank)] = 2
    result[valid & (before_rank == 0) & (after_rank > 0)] = 3
    result[valid & (before_rank > 0) & (after_rank == 0)] = 4
    result[valid & (before_rank > 0) & (after_rank > before_rank)] = 5
    result[valid & (after_rank > 0) & (after_rank < before_rank)] = 6
    return result


def _metric(count: int, pixel_area_m2: float, valid_count: int) -> dict[str, Any]:
    area_m2 = count * pixel_area_m2
    return {
        "pixels": int(count),
        "square_metres": area_m2,
        "hectares": area_m2 / 10_000.0,
        "square_kilometres": area_m2 / 1_000_000.0,
        "percentage_of_valid_comparison": (100.0 * count / valid_count) if valid_count else None,
    }


def summarize_change(
    before: np.ndarray,
    after: np.ndarray,
    *,
    pixel_area_m2: float,
    elapsed_years: int,
    mapping: DevelopmentMapping = ANNUAL_NLCD_MAPPING,
) -> dict[str, Any]:
    if elapsed_years <= 0:
        raise HumanDevelopmentError("elapsed years must be positive")
    codes = classify_change(before, after, mapping)
    counts = np.bincount(codes.ravel(), minlength=8)
    valid_count = int(counts[1:].sum())
    metrics = {
        str(code): {
            "code": code,
            "name": CHANGE_CODE_INFO[code][0],
            "description": CHANGE_CODE_INFO[code][1],
            **_metric(int(counts[code]), pixel_area_m2, valid_count),
        }
        for code in range(8)
    }
    gain = _metric(int(counts[3]), pixel_area_m2, valid_count)
    loss = _metric(int(counts[4]), pixel_area_m2, valid_count)
    increase = _metric(int(counts[5]), pixel_area_m2, valid_count)
    decrease = _metric(int(counts[6]), pixel_area_m2, valid_count)
    net_pixels = int(counts[3] - counts[4])
    net = _metric(net_pixels, pixel_area_m2, valid_count)
    return {
        "change_codes": metrics,
        "valid_comparison": _metric(valid_count, pixel_area_m2, valid_count),
        "invalid_comparison": _metric(int(counts[0]), pixel_area_m2, valid_count),
        "gross_development_gain": gain,
        "apparent_development_loss": loss,
        "net_development_change": net,
        "development_intensity_increase": increase,
        "development_intensity_decrease": decrease,
        "elapsed_years": elapsed_years,
        "annualized": {
            "gross_gain_square_metres_per_year": gain["square_metres"] / elapsed_years,
            "apparent_loss_square_metres_per_year": loss["square_metres"] / elapsed_years,
            "net_change_square_metres_per_year": net["square_metres"] / elapsed_years,
        },
    }


def _raster_profile(grid: TargetGrid, dtype: str, nodata: float | int) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "driver": "GTiff",
        "width": grid.width,
        "height": grid.height,
        "count": 1,
        "dtype": dtype,
        "crs": grid.crs,
        "transform": grid.transform,
        "nodata": nodata,
        "compress": "deflate",
        "predictor": 2 if dtype.startswith("float") else 1,
        "BIGTIFF": "IF_SAFER",
    }
    if grid.width >= 16 and grid.height >= 16:
        profile.update(
            tiled=True,
            blockxsize=min(512, max(16, grid.width // 16 * 16)),
            blockysize=min(512, max(16, grid.height // 16 * 16)),
        )
    return profile


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def harmonize_epoch(
    *,
    year: int,
    land_cover_path: Path,
    imperviousness_path: Path | None,
    destination: Path,
    grid: TargetGrid,
    window_size: int,
    mapping: DevelopmentMapping = ANNUAL_NLCD_MAPPING,
) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True)
    land_output = destination / "land_cover.tif"
    valid_output = destination / "valid_mask.tif"
    counts = np.zeros(256, dtype=np.int64)
    with rasterio.open(land_cover_path) as source:
        with WarpedVRT(
            source,
            crs=grid.crs,
            transform=grid.transform,
            width=grid.width,
            height=grid.height,
            resampling=Resampling.nearest,
            src_nodata=source.nodata if source.nodata is not None else mapping.nodata_code,
            nodata=mapping.nodata_code,
        ) as warped:
            with rasterio.open(land_output, "w", **_raster_profile(grid, "uint8", mapping.nodata_code)) as land_sink:
                with rasterio.open(valid_output, "w", **_raster_profile(grid, "uint8", 0)) as valid_sink:
                    for window in iter_windows(grid.width, grid.height, window_size):
                        values = warped.read((1,), window=window, out_dtype="uint8")[0]
                        valid = valid_land_cover(values, mapping)
                        land_sink.write(values, 1, window=window)
                        valid_sink.write(valid.astype(np.uint8), 1, window=window)
                        counts += np.bincount(values[valid].ravel(), minlength=256)
    valid_count = int(counts.sum())
    developed_count = int(sum(counts[code] for code in mapping.developed_ranks))
    class_counts = {str(code): int(counts[code]) for code in mapping.valid_codes}
    statistics = {
        "schema_version": "fasterraster.human-development-epoch-statistics/v1",
        "year": year,
        "mapping_id": mapping.mapping_id,
        "mapping_contract_sha256": mapping.sha256,
        "valid_land_cover": _metric(valid_count, grid.pixel_area_m2, valid_count),
        "developed_land": _metric(developed_count, grid.pixel_area_m2, valid_count),
        "non_developed_land": _metric(valid_count - developed_count, grid.pixel_area_m2, valid_count),
        "source_class_counts": class_counts,
        "developed_class_counts": {str(code): int(counts[code]) for code in mapping.developed_ranks},
    }
    impervious_output: Path | None = None
    if imperviousness_path is not None:
        impervious_output = destination / "fractional_imperviousness.tif"
        with rasterio.open(imperviousness_path) as source:
            with WarpedVRT(
                source,
                crs=grid.crs,
                transform=grid.transform,
                width=grid.width,
                height=grid.height,
                resampling=Resampling.bilinear,
                src_nodata=source.nodata if source.nodata is not None else IMPERvious_NODATA,
                nodata=IMPERvious_NODATA,
                dtype="float32",
            ) as warped:
                with rasterio.open(
                    impervious_output,
                    "w",
                    **_raster_profile(grid, "float32", IMPERvious_NODATA),
                ) as sink:
                    for window in iter_windows(grid.width, grid.height, window_size):
                        sink.write(warped.read((1,), window=window, out_dtype="float32")[0], 1, window=window)
    _write_json(destination / "statistics.json", statistics)
    return {
        "year": year,
        "land_cover": land_output,
        "valid_mask": valid_output,
        "imperviousness": impervious_output,
        "statistics_path": destination / "statistics.json",
        "statistics": statistics,
    }


def analyze_common_all_epoch_footprint(
    *,
    epoch_results: Sequence[dict[str, Any]],
    destination: Path,
    grid: TargetGrid,
    window_size: int,
    mapping: DevelopmentMapping = ANNUAL_NLCD_MAPPING,
) -> dict[str, Any]:
    """Publish the all-epoch valid intersection and comparable epoch metrics."""

    if len(epoch_results) < 2:
        raise HumanDevelopmentError("common all-epoch footprint requires at least two epochs")
    destination.mkdir(parents=True, exist_ok=True)
    mask_path = destination / "common_all_epoch_valid_mask.tif"
    valid_sources = [rasterio.open(Path(item["valid_mask"])) for item in epoch_results]
    land_sources = [rasterio.open(Path(item["land_cover"])) for item in epoch_results]
    developed_counts = [0 for _ in epoch_results]
    common_valid_count = 0
    try:
        with rasterio.open(mask_path, "w", **_raster_profile(grid, "uint8", 0)) as sink:
            for window in iter_windows(grid.width, grid.height, window_size):
                common = np.logical_and.reduce(
                    [source.read((1,), window=window)[0].astype(bool) for source in valid_sources]
                )
                sink.write(common.astype(np.uint8), 1, window=window)
                common_valid_count += int(np.count_nonzero(common))
                if not np.any(common):
                    continue
                for index, source in enumerate(land_sources):
                    values = source.read((1,), window=window)[0]
                    developed_counts[index] += int(
                        np.count_nonzero(common & (development_rank(values, mapping) > 0))
                    )
    finally:
        for source in [*valid_sources, *land_sources]:
            source.close()

    epoch_receipts = []
    for result, developed_count in zip(epoch_results, developed_counts):
        statistics = result["statistics"]
        per_epoch = {
            "valid_land_cover": statistics["valid_land_cover"],
            "developed_land": statistics["developed_land"],
            "non_developed_land": statistics["non_developed_land"],
        }
        common_valid = _metric(common_valid_count, grid.pixel_area_m2, common_valid_count)
        common_developed = _metric(developed_count, grid.pixel_area_m2, common_valid_count)
        common_non_developed = _metric(
            common_valid_count - developed_count,
            grid.pixel_area_m2,
            common_valid_count,
        )
        for metric in (common_valid, common_developed, common_non_developed):
            metric["percentage_of_common_footprint"] = metric["percentage_of_valid_comparison"]
        common = {
            "valid_footprint": common_valid,
            "developed_land": common_developed,
            "non_developed_land": common_non_developed,
        }
        statistics["schema_version"] = "fasterraster.human-development-epoch-statistics/v2"
        statistics["per_epoch_valid_footprint"] = per_epoch
        statistics["common_all_epoch_footprint"] = common
        _write_json(Path(result["statistics_path"]), statistics)
        epoch_receipts.append(
            {
                "year": int(result["year"]),
                "per_epoch_valid_footprint": per_epoch,
                "common_all_epoch_footprint": common,
            }
        )

    receipt = {
        "schema_version": "fasterraster.human-development-common-footprint/v1",
        "mask": mask_path.name,
        "mask_semantics": "logical AND of every epoch valid mask",
        "epoch_count": len(epoch_results),
        "epochs": [int(item["year"]) for item in epoch_results],
        "valid_footprint": _metric(common_valid_count, grid.pixel_area_m2, common_valid_count),
        "epoch_statistics": epoch_receipts,
        "mapping_id": mapping.mapping_id,
        "mapping_contract_sha256": mapping.sha256,
        "grid_fingerprint_sha256": grid.fingerprint,
    }
    statistics_path = destination / "common_all_epoch_statistics.json"
    _write_json(statistics_path, receipt)
    return {
        "mask": mask_path,
        "statistics_path": statistics_path,
        "statistics": receipt,
        "epoch_statistics": epoch_receipts,
    }


def _write_matrix(
    *,
    destination_json: Path,
    destination_csv: Path,
    labels: Sequence[str],
    codes: Sequence[int | str],
    matrix: np.ndarray,
    schema_version: str,
) -> None:
    rows = []
    for before_index, before_label in enumerate(labels):
        for after_index, after_label in enumerate(labels):
            rows.append(
                {
                    "before_code": codes[before_index],
                    "before_label": before_label,
                    "after_code": codes[after_index],
                    "after_label": after_label,
                    "pixels": int(matrix[before_index, after_index]),
                }
            )
    _write_json(
        destination_json,
        {
            "schema_version": schema_version,
            "labels": list(labels),
            "codes": list(codes),
            "matrix_pixels": matrix.astype(int).tolist(),
            "total_pixels": int(matrix.sum()),
            "rows": rows,
        },
    )
    destination_csv.parent.mkdir(parents=True, exist_ok=True)
    with destination_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_source_transitions(
    destination_json: Path,
    destination_csv: Path,
    transitions: Counter[tuple[int, int]],
    mapping: DevelopmentMapping,
    pixel_area_m2: float,
    valid_count: int,
) -> None:
    rows = []
    for (before_code, after_code), count in sorted(transitions.items()):
        area_m2 = int(count) * pixel_area_m2
        rows.append({
            "baseline_source_class": before_code,
            "baseline_class_label": mapping.class_labels[before_code],
            "comparison_source_class": after_code,
            "comparison_class_label": mapping.class_labels[after_code],
            "pixel_count": int(count),
            "square_metres": area_m2,
            "hectares": area_m2 / 10_000.0,
            "square_kilometres": area_m2 / 1_000_000.0,
            "percentage_of_valid_comparison": 100.0 * int(count) / valid_count if valid_count else None,
        })
    _write_json(destination_json, {
        "schema_version": "fasterraster.source-transition-table/v1",
        "mapping_id": mapping.mapping_id,
        "mapping_contract_sha256": mapping.sha256,
        "ordering": ["baseline_source_class", "comparison_source_class"],
        "total_pixels": sum(transitions.values()),
        "rows": rows,
    })
    destination_csv.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else [
        "baseline_source_class", "baseline_class_label", "comparison_source_class",
        "comparison_class_label", "pixel_count", "square_metres", "hectares",
        "square_kilometres", "percentage_of_valid_comparison",
    ]
    with destination_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def analyze_interval(
    *,
    before_year: int,
    after_year: int,
    before_land_cover: Path,
    after_land_cover: Path,
    before_imperviousness: Path | None,
    after_imperviousness: Path | None,
    destination: Path,
    grid: TargetGrid,
    window_size: int,
    mapping: DevelopmentMapping = ANNUAL_NLCD_MAPPING,
) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True)
    change_path = destination / "change_codes.tif"
    valid_path = destination / "valid_comparison_mask.tif"
    impervious_path = destination / "imperviousness_difference.tif"
    counts = np.zeros(8, dtype=np.int64)
    source_transitions: Counter[tuple[int, int]] = Counter()
    abstract_matrix = np.zeros((5, 5), dtype=np.int64)
    impervious_count = 0
    impervious_sum = 0.0
    impervious_min: float | None = None
    impervious_max: float | None = None
    has_imperviousness = before_imperviousness is not None and after_imperviousness is not None
    with rasterio.open(before_land_cover) as before_source:
        with rasterio.open(after_land_cover) as after_source:
            with rasterio.open(change_path, "w", **_raster_profile(grid, "uint8", 0)) as change_sink:
                with rasterio.open(valid_path, "w", **_raster_profile(grid, "uint8", 0)) as valid_sink:
                    before_imp_source = rasterio.open(before_imperviousness) if has_imperviousness else None
                    after_imp_source = rasterio.open(after_imperviousness) if has_imperviousness else None
                    imp_sink = (
                        rasterio.open(impervious_path, "w", **_raster_profile(grid, "float32", -9999.0))
                        if has_imperviousness
                        else None
                    )
                    try:
                        for window in iter_windows(grid.width, grid.height, window_size):
                            before = before_source.read((1,), window=window)[0]
                            after = after_source.read((1,), window=window)[0]
                            change = classify_change(before, after, mapping)
                            valid = change != 0
                            change_sink.write(change, 1, window=window)
                            valid_sink.write(valid.astype(np.uint8), 1, window=window)
                            counts += np.bincount(change.ravel(), minlength=8)
                            if np.any(valid):
                                pairs, pair_counts = np.unique(
                                    before[valid].astype(np.uint16) * 256 + after[valid].astype(np.uint16),
                                    return_counts=True,
                                )
                                source_transitions.update({
                                    (int(pair // 256), int(pair % 256)): int(count)
                                    for pair, count in zip(pairs, pair_counts)
                                })
                                before_state = development_rank(before[valid], mapping)
                                after_state = development_rank(after[valid], mapping)
                                abstract_matrix += np.bincount(
                                    before_state * 5 + after_state,
                                    minlength=25,
                                ).reshape(5, 5)
                            if has_imperviousness and before_imp_source and after_imp_source and imp_sink:
                                before_imp = before_imp_source.read((1,), window=window)[0].astype(np.float32)
                                after_imp = after_imp_source.read((1,), window=window)[0].astype(np.float32)
                                imp_valid = (
                                    valid
                                    & np.isfinite(before_imp)
                                    & np.isfinite(after_imp)
                                    & (before_imp >= 0)
                                    & (before_imp <= 100)
                                    & (after_imp >= 0)
                                    & (after_imp <= 100)
                                    & (before_imp != IMPERvious_NODATA)
                                    & (after_imp != IMPERvious_NODATA)
                                )
                                difference = np.full(before_imp.shape, -9999.0, dtype=np.float32)
                                difference[imp_valid] = after_imp[imp_valid] - before_imp[imp_valid]
                                imp_sink.write(difference, 1, window=window)
                                if np.any(imp_valid):
                                    selected = difference[imp_valid]
                                    impervious_count += int(selected.size)
                                    impervious_sum += float(selected.sum(dtype=np.float64))
                                    selected_min = float(selected.min())
                                    selected_max = float(selected.max())
                                    impervious_min = selected_min if impervious_min is None else min(impervious_min, selected_min)
                                    impervious_max = selected_max if impervious_max is None else max(impervious_max, selected_max)
                    finally:
                        if imp_sink:
                            imp_sink.close()
                        if before_imp_source:
                            before_imp_source.close()
                        if after_imp_source:
                            after_imp_source.close()
    elapsed_years = after_year - before_year
    valid_count = int(counts[1:].sum())
    metrics = {
        str(code): {
            "code": code,
            "name": CHANGE_CODE_INFO[code][0],
            "description": CHANGE_CODE_INFO[code][1],
            **_metric(int(counts[code]), grid.pixel_area_m2, valid_count),
        }
        for code in range(8)
    }
    gain = _metric(int(counts[3]), grid.pixel_area_m2, valid_count)
    loss = _metric(int(counts[4]), grid.pixel_area_m2, valid_count)
    net = _metric(int(counts[3] - counts[4]), grid.pixel_area_m2, valid_count)
    statistics = {
        "schema_version": "fasterraster.human-development-interval-statistics/v1",
        "before_year": before_year,
        "after_year": after_year,
        "elapsed_years": elapsed_years,
        "mapping_id": mapping.mapping_id,
        "mapping_contract_sha256": mapping.sha256,
        "change_codes": metrics,
        "valid_comparison": _metric(valid_count, grid.pixel_area_m2, valid_count),
        "invalid_comparison": _metric(int(counts[0]), grid.pixel_area_m2, valid_count),
        "gross_development_gain": gain,
        "apparent_development_loss": loss,
        "net_development_change": net,
        "development_intensity_increase": _metric(int(counts[5]), grid.pixel_area_m2, valid_count),
        "development_intensity_decrease": _metric(int(counts[6]), grid.pixel_area_m2, valid_count),
        "annualized": {
            "gross_gain_square_metres_per_year": gain["square_metres"] / elapsed_years,
            "apparent_loss_square_metres_per_year": loss["square_metres"] / elapsed_years,
            "net_change_square_metres_per_year": net["square_metres"] / elapsed_years,
        },
        "transition_reconciliation": {
            "valid_comparison_pixels": valid_count,
            "source_transition_pixels": int(sum(source_transitions.values())),
            "abstract_transition_pixels": int(abstract_matrix.sum()),
            "reconciles": int(sum(source_transitions.values())) == valid_count == int(abstract_matrix.sum()),
        },
        "imperviousness": {
            "status": "available" if has_imperviousness else "unavailable",
            "reason": None if has_imperviousness else "both interval endpoints must provide pinned fractional-imperviousness rasters",
            "valid_pixels": impervious_count,
            "mean_percentage_point_change": (impervious_sum / impervious_count) if impervious_count else None,
            "minimum_percentage_point_change": impervious_min,
            "maximum_percentage_point_change": impervious_max,
        },
    }
    _write_json(destination / "statistics.json", statistics)
    _write_json(
        destination / "imperviousness_evidence.json",
        {
            "schema_version": "fasterraster.imperviousness-evidence/v1",
            "status": statistics["imperviousness"]["status"],
            "reason": statistics["imperviousness"]["reason"],
            "difference_units": "percentage_points",
            "resampling": "bilinear",
            "valid_range": [0, 100],
            "nodata": IMPERvious_NODATA,
            "difference_raster": "imperviousness_difference.tif" if has_imperviousness else None,
        },
    )
    _write_source_transitions(
        destination / "source_transition_matrix.json",
        destination / "source_transition_matrix.csv",
        source_transitions,
        mapping,
        grid.pixel_area_m2,
        valid_count,
    )
    _write_matrix(
        destination_json=destination / "abstract_transition_matrix.json",
        destination_csv=destination / "abstract_transition_matrix.csv",
        labels=list(ABSTRACT_STATE_LABELS),
        codes=list(ABSTRACT_STATE_LABELS),
        matrix=abstract_matrix,
        schema_version="fasterraster.abstract-transition-matrix/v1",
    )
    return {
        "before_year": before_year,
        "after_year": after_year,
        "change_codes": change_path,
        "valid_comparison_mask": valid_path,
        "imperviousness_difference": impervious_path if has_imperviousness else None,
        "imperviousness_evidence": destination / "imperviousness_evidence.json",
        "statistics_path": destination / "statistics.json",
        "source_transition_json": destination / "source_transition_matrix.json",
        "source_transition_csv": destination / "source_transition_matrix.csv",
        "abstract_transition_json": destination / "abstract_transition_matrix.json",
        "abstract_transition_csv": destination / "abstract_transition_matrix.csv",
        "statistics": statistics,
    }


def methodology_receipt(grid: TargetGrid, mapping: DevelopmentMapping = ANNUAL_NLCD_MAPPING) -> dict[str, Any]:
    return {
        "schema_version": "fasterraster.human-development-methodology/v1",
        "workflow": "human_development_change",
        "mapping_id": mapping.mapping_id,
        "mapping_contract_sha256": mapping.sha256,
        "scientific_claim": mapping.scientific_claim,
        "unsupported_claims": [
            "population",
            "economic_activity",
            "construction_date",
            "causality",
            "occupancy",
            "cadastral_development_approval",
            "authoritative_non_agricultural_land_cover_change",
        ],
        "declared_source_classes": {str(code): mapping.class_labels[code] for code in mapping.valid_codes},
        "developed_classes": dict(mapping.developed_ranks),
        "invalid_codes": list(mapping.invalid_codes),
        "source_qualification": (
            "CDL is crop-focused; its non-agricultural classes are used only as a mapped development proxy. "
            "Apparent transitions may reflect real mapped development, ancillary non-agricultural classification "
            "changes, classification differences between CDL years, source-production differences, or a combination."
            if mapping.mapping_id == "usda_cdl_development_proxy_v1"
            else "Annual NLCD categorical land-cover mapping."
        ),
        "change_codes": {
            str(code): {"name": name, "description": description}
            for code, (name, description) in CHANGE_CODE_INFO.items()
        },
        "grid": grid.as_dict(),
        "harmonization": {
            "categorical_land_cover": "nearest",
            "fractional_imperviousness": "bilinear",
            "processing": "windowed",
        },
        "valid_mask": "both endpoint values must be members of the selected mapping's declared valid class contract",
        "imperviousness_valid_mask": "land-cover comparison valid and both fractional values finite in [0,100] and not nodata 250",
        "interval_policy": "all adjacent epochs; first-to-last endpoint added for multi-epoch preview",
        "statistics_units": ["pixels", "square_metres", "hectares", "square_kilometres", "percentage_of_valid_comparison"],
    }
