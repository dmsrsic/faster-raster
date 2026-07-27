from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds
from rasterio.windows import Window, bounds as window_bounds, from_bounds


HARMONIZATION_PLAN_VERSION = 1
DEFAULT_MAX_OUTPUT_PIXELS = 100_000_000
_ALLOWED_RESAMPLING = {"nearest", "bilinear", "average"}
_FLOAT_TOLERANCE = 1e-9


class PrismHarmonizationError(ValueError):
    """Raised when a PRISM harmonization plan is unsafe or ambiguous."""


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contract_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _finite_float(value: Any, *, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PrismHarmonizationError(f"{name}_must_be_numeric") from exc
    if not math.isfinite(number):
        raise PrismHarmonizationError(f"{name}_must_be_finite")
    return number


def _normalize_bbox(value: Sequence[Any], *, name: str) -> tuple[float, float, float, float]:
    if len(value) != 4:
        raise PrismHarmonizationError(f"{name}_must_have_four_values")
    left, bottom, right, top = (
        _finite_float(item, name=f"{name}_{index}") for index, item in enumerate(value)
    )
    if not left < right or not bottom < top:
        raise PrismHarmonizationError(f"{name}_must_have_positive_area")
    return left, bottom, right, top


def _normalize_crs(value: Any, *, name: str) -> CRS:
    try:
        crs = CRS.from_user_input(value)
    except Exception as exc:  # Rasterio wraps several PROJ exception types.
        raise PrismHarmonizationError(f"{name}_invalid") from exc
    if not crs:
        raise PrismHarmonizationError(f"{name}_invalid")
    return crs


def _crs_contract(crs: CRS) -> dict[str, Any]:
    return {
        "authority": crs.to_authority(),
        "string": crs.to_string(),
        "wkt": crs.to_wkt(version="WKT2_2019"),
    }


def _normalize_resolution(value: float | Sequence[float]) -> tuple[float, float]:
    if isinstance(value, (int, float)):
        x = y = _finite_float(value, name="target_resolution")
    else:
        if len(value) != 2:
            raise PrismHarmonizationError("target_resolution_must_have_one_or_two_values")
        x = _finite_float(value[0], name="target_resolution_x")
        y = _finite_float(value[1], name="target_resolution_y")
    if x <= 0 or y <= 0:
        raise PrismHarmonizationError("target_resolution_must_be_positive")
    return x, y


def _intersection(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    result = (
        max(first[0], second[0]),
        max(first[1], second[1]),
        min(first[2], second[2]),
        min(first[3], second[3]),
    )
    if not result[0] < result[2] or not result[1] < result[3]:
        raise PrismHarmonizationError("requested_aoi_does_not_intersect_prism_raster")
    return result


def _north_up_resolution(transform: Affine) -> tuple[float, float]:
    if transform.a <= 0 or transform.e >= 0:
        raise PrismHarmonizationError("source_grid_must_be_north_up")
    if abs(transform.b) > _FLOAT_TOLERANCE or abs(transform.d) > _FLOAT_TOLERANCE:
        raise PrismHarmonizationError("rotated_or_sheared_source_grid_not_supported")
    return abs(float(transform.a)), abs(float(transform.e))


def _covering_source_window(
    bounds: tuple[float, float, float, float],
    *,
    transform: Affine,
    width: int,
    height: int,
) -> Window:
    raw = from_bounds(*bounds, transform=transform)
    col_start = max(0, math.floor(raw.col_off + _FLOAT_TOLERANCE))
    row_start = max(0, math.floor(raw.row_off + _FLOAT_TOLERANCE))
    col_end = min(width, math.ceil(raw.col_off + raw.width - _FLOAT_TOLERANCE))
    row_end = min(height, math.ceil(raw.row_off + raw.height - _FLOAT_TOLERANCE))
    if col_end <= col_start or row_end <= row_start:
        raise PrismHarmonizationError("requested_aoi_resolves_to_empty_source_window")
    return Window(col_start, row_start, col_end - col_start, row_end - row_start)


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _snap_down(value: float, *, origin: float, step: float) -> float:
    ratio = (_decimal(value) - _decimal(origin)) / _decimal(step)
    snapped = _decimal(origin) + ratio.to_integral_value(rounding=ROUND_FLOOR) * _decimal(step)
    return float(snapped)


def _snap_up(value: float, *, origin: float, step: float) -> float:
    ratio = (_decimal(value) - _decimal(origin)) / _decimal(step)
    snapped = _decimal(origin) + ratio.to_integral_value(rounding=ROUND_CEILING) * _decimal(step)
    return float(snapped)


def _snapped_target_grid(
    bounds: tuple[float, float, float, float],
    *,
    resolution: tuple[float, float],
    origin: tuple[float, float],
    max_output_pixels: int,
) -> dict[str, Any]:
    if max_output_pixels <= 0:
        raise PrismHarmonizationError("max_output_pixels_must_be_positive")
    xres, yres = resolution
    origin_x, origin_y = origin
    left = _snap_down(bounds[0], origin=origin_x, step=xres)
    bottom = _snap_down(bounds[1], origin=origin_y, step=yres)
    right = _snap_up(bounds[2], origin=origin_x, step=xres)
    top = _snap_up(bounds[3], origin=origin_y, step=yres)
    width_decimal = (_decimal(right) - _decimal(left)) / _decimal(xres)
    height_decimal = (_decimal(top) - _decimal(bottom)) / _decimal(yres)
    width = int(width_decimal.to_integral_value(rounding=ROUND_CEILING))
    height = int(height_decimal.to_integral_value(rounding=ROUND_CEILING))
    if width <= 0 or height <= 0:
        raise PrismHarmonizationError("target_grid_is_empty")
    pixel_count = width * height
    if pixel_count > max_output_pixels:
        raise PrismHarmonizationError("target_grid_pixel_limit_exceeded")
    transform = Affine(xres, 0.0, left, 0.0, -yres, top)
    return {
        "bounds": [left, bottom, right, top],
        "width": width,
        "height": height,
        "pixel_count": pixel_count,
        "resolution": [xres, yres],
        "origin": [origin_x, origin_y],
        "transform": list(transform)[:6],
        "snap_policy": "outward_from_explicit_origin",
    }


def _approximately_equal(first: float, second: float, *, tolerance: float = 1e-8) -> bool:
    return math.isclose(first, second, rel_tol=tolerance, abs_tol=tolerance)


def _same_crs_scale_class(
    source_resolution: tuple[float, float],
    target_resolution: tuple[float, float],
) -> str:
    sx, sy = source_resolution
    tx, ty = target_resolution
    same = _approximately_equal(sx, tx) and _approximately_equal(sy, ty)
    coarser = tx >= sx and ty >= sy and (tx > sx or ty > sy)
    finer = tx <= sx and ty <= sy and (tx < sx or ty < sy)
    if same:
        return "same_resolution"
    if coarser:
        return "coarser"
    if finer:
        return "finer"
    return "mixed_axis_resolution"


def _grid_aligned_with_source(
    target_grid: Mapping[str, Any],
    *,
    source_transform: Affine,
    source_resolution: tuple[float, float],
) -> bool:
    target_transform = Affine(*target_grid["transform"])
    target_resolution = tuple(float(item) for item in target_grid["resolution"])
    if not all(
        _approximately_equal(first, second)
        for first, second in zip(source_resolution, target_resolution, strict=True)
    ):
        return False
    col_offset = (target_transform.c - source_transform.c) / source_resolution[0]
    row_offset = (source_transform.f - target_transform.f) / source_resolution[1]
    return _approximately_equal(col_offset, round(col_offset)) and _approximately_equal(
        row_offset, round(row_offset)
    )


def _resampling_contract(
    method: str,
    *,
    same_crs: bool,
    scale_class: str,
    grid_aligned_with_source: bool,
) -> dict[str, Any]:
    normalized = str(method).strip().lower()
    if normalized not in _ALLOWED_RESAMPLING:
        raise PrismHarmonizationError("unsupported_prism_resampling_method")

    warnings: list[str] = []
    if normalized == "nearest":
        if not (same_crs and scale_class == "same_resolution" and grid_aligned_with_source):
            raise PrismHarmonizationError(
                "nearest_is_only_allowed_for_same_crs_same_resolution_aligned_subset"
            )
        interpretation = "value_preserving_aligned_subset"
    elif normalized == "average":
        if not same_crs:
            raise PrismHarmonizationError(
                "average_cross_crs_reprojection_policy_not_implemented"
            )
        if scale_class not in {"coarser", "same_resolution"}:
            raise PrismHarmonizationError(
                "average_requires_same_crs_equal_or_coarser_target_cells"
            )
        interpretation = "mean_precipitation_depth_over_contributing_source_pixels"
        if scale_class == "same_resolution":
            warnings.append("average_selected_without_coarsening")
    else:
        interpretation = "explicit_interpolation_of_continuous_precipitation_depth"
        warnings.append("bilinear_interpolation_is_not_mass_conservative")
        if same_crs and scale_class == "coarser":
            warnings.append("average_is_preferred_for_deliberate_same_crs_coarsening")

    return {
        "method": normalized,
        "explicit": True,
        "interpretation": interpretation,
        "warnings": warnings,
    }


def _plan_contract(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in plan.items()
        if key not in {"harmonization_plan_sha256", "source_raster_path"}
    }


def compute_harmonization_plan_sha256(plan: Mapping[str, Any]) -> str:
    return _contract_sha256(_plan_contract(plan))


def verify_harmonization_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    expected = compute_harmonization_plan_sha256(plan)
    if plan.get("harmonization_plan_sha256") != expected:
        failures.append("harmonization_plan_hash_mismatch")
    target = plan.get("target_grid") or {}
    try:
        width = int(target.get("width"))
        height = int(target.get("height"))
        pixel_count = int(target.get("pixel_count"))
    except (TypeError, ValueError):
        failures.append("target_grid_dimensions_invalid")
    else:
        if width <= 0 or height <= 0 or width * height != pixel_count:
            failures.append("target_grid_pixel_count_mismatch")
    if plan.get("planning_status") != "PASS":
        failures.append("harmonization_plan_not_pass")
    if (plan.get("resampling") or {}).get("method") not in _ALLOWED_RESAMPLING:
        failures.append("resampling_contract_invalid")
    return {
        "verification_status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "computed_harmonization_plan_sha256": expected,
    }


def plan_prism_harmonization(
    source_raster_path: Path,
    *,
    aoi_bbox: Sequence[float],
    aoi_crs: Any,
    target_crs: Any,
    target_resolution: float | Sequence[float],
    resampling_method: str,
    target_origin: Sequence[float] = (0.0, 0.0),
    max_output_pixels: int = DEFAULT_MAX_OUTPUT_PIXELS,
    source_raster_sha256: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic, planning-only PRISM harmonization contract.

    The planner transforms and clips the requested AOI, computes the minimal
    source-pixel window that covers it, snaps an output grid outward from an
    explicit origin, and validates an explicit precipitation resampling policy.
    It does not read pixel values, reproject, write output, or claim an exact
    footprint beyond the recorded requested AOI and pixel/grid envelopes.
    """

    source_raster_path = Path(source_raster_path)
    if not source_raster_path.is_file() or source_raster_path.is_symlink():
        raise PrismHarmonizationError("source_raster_missing_or_invalid")
    requested_aoi = _normalize_bbox(aoi_bbox, name="aoi_bbox")
    requested_aoi_crs = _normalize_crs(aoi_crs, name="aoi_crs")
    normalized_target_crs = _normalize_crs(target_crs, name="target_crs")
    target_resolution_pair = _normalize_resolution(target_resolution)
    if len(target_origin) != 2:
        raise PrismHarmonizationError("target_origin_must_have_two_values")
    normalized_origin = (
        _finite_float(target_origin[0], name="target_origin_x"),
        _finite_float(target_origin[1], name="target_origin_y"),
    )

    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_PAM_ENABLED="NO"):
        with rasterio.open(source_raster_path) as dataset:
            if dataset.crs is None:
                raise PrismHarmonizationError("source_raster_crs_missing")
            if dataset.count != 1:
                raise PrismHarmonizationError("prism_source_must_have_one_band")
            source_crs = dataset.crs
            source_transform = dataset.transform
            source_resolution = _north_up_resolution(source_transform)
            source_bounds = tuple(float(item) for item in dataset.bounds)
            source_width = int(dataset.width)
            source_height = int(dataset.height)
            source_dtype = str(dataset.dtypes[0])
            source_nodata = dataset.nodata

    try:
        aoi_in_source_crs = transform_bounds(
            requested_aoi_crs,
            source_crs,
            *requested_aoi,
            densify_pts=21,
        )
    except Exception as exc:
        raise PrismHarmonizationError("aoi_transform_to_source_crs_failed") from exc
    aoi_in_source_crs = _normalize_bbox(aoi_in_source_crs, name="aoi_in_source_crs")
    clipped_source_bounds = _intersection(aoi_in_source_crs, source_bounds)
    source_window = _covering_source_window(
        clipped_source_bounds,
        transform=source_transform,
        width=source_width,
        height=source_height,
    )
    source_window_envelope = tuple(
        float(item) for item in window_bounds(source_window, source_transform)
    )

    try:
        target_request_bounds = transform_bounds(
            source_crs,
            normalized_target_crs,
            *clipped_source_bounds,
            densify_pts=21,
        )
    except Exception as exc:
        raise PrismHarmonizationError("source_aoi_transform_to_target_crs_failed") from exc
    target_request_bounds = _normalize_bbox(target_request_bounds, name="target_request_bounds")
    target_grid = _snapped_target_grid(
        target_request_bounds,
        resolution=target_resolution_pair,
        origin=normalized_origin,
        max_output_pixels=max_output_pixels,
    )

    same_crs = source_crs == normalized_target_crs
    scale_class = (
        _same_crs_scale_class(source_resolution, target_resolution_pair)
        if same_crs
        else "cross_crs_explicit_resolution"
    )
    aligned = same_crs and _grid_aligned_with_source(
        target_grid,
        source_transform=source_transform,
        source_resolution=source_resolution,
    )
    resampling = _resampling_contract(
        resampling_method,
        same_crs=same_crs,
        scale_class=scale_class,
        grid_aligned_with_source=aligned,
    )

    source_sha256 = source_raster_sha256 or _sha256_file(source_raster_path)
    warnings = list(resampling["warnings"])
    if tuple(clipped_source_bounds) != tuple(aoi_in_source_crs):
        warnings.append("requested_aoi_was_clipped_to_prism_source_coverage")
    if any(
        not _approximately_equal(requested, envelope)
        for requested, envelope in zip(clipped_source_bounds, source_window_envelope, strict=True)
    ):
        warnings.append("source_window_is_a_pixel_envelope_around_the_requested_aoi")
    if any(
        not _approximately_equal(requested, snapped)
        for requested, snapped in zip(target_request_bounds, target_grid["bounds"], strict=True)
    ):
        warnings.append("target_grid_is_an_outward_snapped_envelope_around_the_requested_aoi")

    plan: dict[str, Any] = {
        "harmonization_plan_version": HARMONIZATION_PLAN_VERSION,
        "planning_status": "PASS",
        "source_id": "prism_daily_ppt_static_zip",
        "source_product": "PRISM daily precipitation",
        "variable": "ppt",
        "units": "millimeters",
        "source_raster_path": str(source_raster_path.resolve()),
        "source_raster_sha256": source_sha256,
        "source_grid": {
            "crs": _crs_contract(source_crs),
            "bounds": list(source_bounds),
            "width": source_width,
            "height": source_height,
            "resolution": list(source_resolution),
            "transform": list(source_transform)[:6],
            "dtype": source_dtype,
            "nodata": source_nodata,
        },
        "requested_aoi": {
            "crs": _crs_contract(requested_aoi_crs),
            "bounds": list(requested_aoi),
        },
        "source_aoi": {
            "transformed_requested_bounds": list(aoi_in_source_crs),
            "clipped_bounds": list(clipped_source_bounds),
            "source_window": {
                "col_off": int(source_window.col_off),
                "row_off": int(source_window.row_off),
                "width": int(source_window.width),
                "height": int(source_window.height),
            },
            "source_window_bounds": list(source_window_envelope),
            "window_policy": "minimal_covering_integer_source_window",
            "exact_requested_footprint_preserved_as_metadata": True,
        },
        "target_grid": {
            **target_grid,
            "crs": _crs_contract(normalized_target_crs),
            "requested_bounds_before_snap": list(target_request_bounds),
            "dtype": "float32",
            "nodata": -9999.0,
        },
        "resampling": resampling,
        "grid_relationship": {
            "same_crs": same_crs,
            "scale_class": scale_class,
            "aligned_with_source_grid": aligned,
        },
        "max_output_pixels": int(max_output_pixels),
        "warnings": sorted(set(warnings)),
        "execution_performed": False,
        "output_written": False,
        "next_stage": "nodata_preserving_reprojection_and_harmonized_cog_write",
    }
    plan["harmonization_plan_sha256"] = compute_harmonization_plan_sha256(plan)
    verification = verify_harmonization_plan(plan)
    if verification["verification_status"] != "PASS":
        raise PrismHarmonizationError("internal_harmonization_plan_verification_failed")
    return plan


def run_self_check() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="fr-prism-harmonization-") as temporary:
        raster_path = Path(temporary) / "synthetic_prism.tif"
        data = np.arange(100, dtype=np.float32).reshape(10, 10)
        with rasterio.open(
            raster_path,
            "w",
            driver="GTiff",
            width=10,
            height=10,
            count=1,
            dtype="float32",
            crs="EPSG:4326",
            transform=from_origin(-100.0, 50.0, 0.1, 0.1),
            nodata=-9999.0,
            tiled=True,
            blockxsize=16,
            blockysize=16,
        ) as dataset:
            dataset.write(data, 1)

        aligned = plan_prism_harmonization(
            raster_path,
            aoi_bbox=(-99.8, 49.2, -99.2, 49.8),
            aoi_crs="EPSG:4326",
            target_crs="EPSG:4326",
            target_resolution=0.1,
            target_origin=(0.0, 0.0),
            resampling_method="nearest",
            max_output_pixels=10_000,
        )
        projected = plan_prism_harmonization(
            raster_path,
            aoi_bbox=(-99.8, 49.2, -99.2, 49.8),
            aoi_crs="EPSG:4326",
            target_crs="EPSG:5070",
            target_resolution=4000,
            target_origin=(0.0, 0.0),
            resampling_method="bilinear",
            max_output_pixels=10_000,
        )
        unsafe_nearest_rejected = False
        try:
            plan_prism_harmonization(
                raster_path,
                aoi_bbox=(-99.8, 49.2, -99.2, 49.8),
                aoi_crs="EPSG:4326",
                target_crs="EPSG:5070",
                target_resolution=4000,
                resampling_method="nearest",
                max_output_pixels=10_000,
            )
        except PrismHarmonizationError as exc:
            unsafe_nearest_rejected = (
                str(exc)
                == "nearest_is_only_allowed_for_same_crs_same_resolution_aligned_subset"
            )

        checks = {
            "aligned_plan_verifies": verify_harmonization_plan(aligned)["verification_status"]
            == "PASS",
            "aligned_nearest_is_grid_safe": aligned["grid_relationship"][
                "aligned_with_source_grid"
            ],
            "projected_plan_verifies": verify_harmonization_plan(projected)[
                "verification_status"
            ]
            == "PASS",
            "projected_bilinear_is_explicit": projected["resampling"]["method"]
            == "bilinear",
            "unsafe_cross_crs_nearest_rejected": unsafe_nearest_rejected,
        }
        return {
            "self_check_status": "PASS" if all(checks.values()) else "FAIL",
            "checks": checks,
            "aligned_plan_sha256": aligned["harmonization_plan_sha256"],
            "projected_plan_sha256": projected["harmonization_plan_sha256"],
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m faster_raster.prism_harmonization",
        description="Build a planning-only PRISM AOI and target-grid harmonization contract.",
    )
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--raster", type=Path)
    parser.add_argument("--aoi", nargs=4, type=float, metavar=("LEFT", "BOTTOM", "RIGHT", "TOP"))
    parser.add_argument("--aoi-crs", default="EPSG:4326")
    parser.add_argument("--target-crs")
    parser.add_argument("--resolution", nargs="+", type=float)
    parser.add_argument("--resampling", choices=sorted(_ALLOWED_RESAMPLING))
    parser.add_argument("--origin", nargs=2, type=float, default=(0.0, 0.0))
    parser.add_argument("--max-output-pixels", type=int, default=DEFAULT_MAX_OUTPUT_PIXELS)
    parser.add_argument("--out", type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.self_check:
        payload = run_self_check()
    else:
        missing = [
            name
            for name, value in {
                "--raster": args.raster,
                "--aoi": args.aoi,
                "--target-crs": args.target_crs,
                "--resolution": args.resolution,
                "--resampling": args.resampling,
            }.items()
            if value is None
        ]
        if missing:
            raise SystemExit("missing required arguments: " + ", ".join(missing))
        resolution: float | Sequence[float]
        if len(args.resolution) == 1:
            resolution = args.resolution[0]
        elif len(args.resolution) == 2:
            resolution = args.resolution
        else:
            raise SystemExit("--resolution accepts one or two values")
        payload = plan_prism_harmonization(
            args.raster,
            aoi_bbox=args.aoi,
            aoi_crs=args.aoi_crs,
            target_crs=args.target_crs,
            target_resolution=resolution,
            resampling_method=args.resampling,
            target_origin=args.origin,
            max_output_pixels=args.max_output_pixels,
        )
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    if payload.get("self_check_status") == "FAIL":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
