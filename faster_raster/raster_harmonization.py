from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import rasterio
from affine import Affine
from rasterio.enums import Resampling
from rasterio.shutil import copy as raster_copy
from rasterio.warp import reproject
from rasterio.windows import Window

HARMONIZED_RASTER_VERSION = 1
HARMONIZATION_RECEIPT_VERSION = 1
DEFAULT_MAX_OUTPUT_BYTES = 512 * 1024 * 1024
DEFAULT_BLOCK_SIZE = 512
_ALLOWED_RESAMPLING = {
    "nearest": Resampling.nearest,
    "bilinear": Resampling.bilinear,
    "average": Resampling.average,
}
_PROFILE_FIELDS = {
    "width",
    "height",
    "count",
    "dtype",
    "nodata",
    "crs",
    "transform",
    "bounds",
    "block_shapes",
    "overviews",
    "compression",
    "cog_layout",
    "valid_pixel_count",
    "nodata_pixel_count",
    "minimum",
    "maximum",
    "mean",
    "nonfinite_pixel_count",
}


class RasterHarmonizationError(ValueError):
    """Raised when a harmonization plan or output violates the execution contract."""


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




def _plan_contract(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in plan.items()
        if key not in {"harmonization_plan_sha256", "source_raster_path"}
    }


def compute_harmonization_plan_sha256(plan: Mapping[str, Any]) -> str:
    return _contract_sha256(_plan_contract(plan))


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _ensure_safe_directory(path: Path) -> Path:
    resolved = path.resolve(strict=False)
    current = resolved
    while not current.exists():
        if current == current.parent:
            raise RasterHarmonizationError("harmonization_storage_root_invalid")
        current = current.parent
    if not current.is_dir() or current.is_symlink():
        raise RasterHarmonizationError("harmonization_storage_root_invalid")
    if path.exists() and (not path.is_dir() or path.is_symlink()):
        raise RasterHarmonizationError("harmonization_storage_root_invalid")
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RasterHarmonizationError("harmonization_storage_root_invalid")
    return resolved


def _content_addressed_path(root: Path, digest: str) -> Path:
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise RasterHarmonizationError("invalid_harmonized_raster_sha256")
    root_resolved = root.resolve()
    destination = (root / digest[:2] / digest[2:4] / f"{digest}.tif").resolve()
    if not destination.is_relative_to(root_resolved):
        raise RasterHarmonizationError("harmonized_raster_path_escapes_artifact_root")
    return destination


def _as_int(value: Any, *, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise RasterHarmonizationError(f"{name}_invalid") from exc
    return result


def _as_float(value: Any, *, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RasterHarmonizationError(f"{name}_invalid") from exc
    if not math.isfinite(result):
        raise RasterHarmonizationError(f"{name}_invalid")
    return result


def _coerce_affine(value: Sequence[Any], *, name: str) -> Affine:
    if len(value) != 6:
        raise RasterHarmonizationError(f"{name}_invalid")
    transform = Affine(*(_as_float(item, name=name) for item in value))
    if transform.a <= 0 or transform.e >= 0 or abs(transform.b) > 1e-12 or abs(transform.d) > 1e-12:
        raise RasterHarmonizationError(f"{name}_must_be_north_up")
    return transform


def _authority_to_string(contract: Mapping[str, Any]) -> str:
    authority = contract.get("authority")
    if isinstance(authority, (list, tuple)) and len(authority) == 2:
        return f"{authority[0]}:{authority[1]}"
    value = contract.get("string")
    if not value:
        raise RasterHarmonizationError("target_crs_contract_invalid")
    return str(value)


def _float_equal(first: Any, second: Any, *, tolerance: float = 1e-8) -> bool:
    try:
        return math.isclose(float(first), float(second), rel_tol=tolerance, abs_tol=tolerance)
    except (TypeError, ValueError):
        return False


def _validate_plan_and_source(plan: Mapping[str, Any], *, max_output_bytes: int) -> dict[str, Any]:
    expected_plan_sha256 = compute_harmonization_plan_sha256(plan)
    if plan.get("harmonization_plan_sha256") != expected_plan_sha256:
        raise RasterHarmonizationError("harmonization_plan_verification_failed")
    if plan.get("planning_status") != "PASS":
        raise RasterHarmonizationError("harmonization_plan_not_pass")
    if plan.get("execution_performed") is not False or plan.get("output_written") is not False:
        raise RasterHarmonizationError("harmonization_plan_is_not_execution_ready")
    if max_output_bytes <= 0:
        raise RasterHarmonizationError("max_output_bytes_must_be_positive")

    source_path = Path(str(plan.get("source_raster_path") or ""))
    if not source_path.is_file() or source_path.is_symlink():
        raise RasterHarmonizationError("source_raster_missing_or_invalid")
    source_sha256 = _sha256_file(source_path)
    if source_sha256 != plan.get("source_raster_sha256"):
        raise RasterHarmonizationError("source_raster_sha256_mismatch")

    source_grid = plan.get("source_grid") or {}
    source_window_contract = (plan.get("source_aoi") or {}).get("source_window") or {}
    target_grid = plan.get("target_grid") or {}
    width = _as_int(target_grid.get("width"), name="target_width")
    height = _as_int(target_grid.get("height"), name="target_height")
    pixel_count = _as_int(target_grid.get("pixel_count"), name="target_pixel_count")
    if width <= 0 or height <= 0 or width * height != pixel_count:
        raise RasterHarmonizationError("target_grid_dimensions_invalid")
    output_dtype = np.dtype(str(target_grid.get("dtype") or "float32"))
    if output_dtype.kind != "f" or output_dtype.itemsize != 4:
        raise RasterHarmonizationError("only_float32_harmonized_outputs_are_supported")
    estimated_uncompressed_bytes = width * height * output_dtype.itemsize
    if estimated_uncompressed_bytes > max_output_bytes:
        raise RasterHarmonizationError("harmonized_output_byte_limit_exceeded")

    target_transform = _coerce_affine(target_grid.get("transform") or [], name="target_transform")
    target_crs = _authority_to_string(target_grid.get("crs") or {})
    target_nodata = _as_float(target_grid.get("nodata"), name="target_nodata")
    method = str((plan.get("resampling") or {}).get("method") or "").lower()
    if method not in _ALLOWED_RESAMPLING:
        raise RasterHarmonizationError("harmonization_resampling_method_invalid")

    source_window = Window(
        _as_int(source_window_contract.get("col_off"), name="source_window_col_off"),
        _as_int(source_window_contract.get("row_off"), name="source_window_row_off"),
        _as_int(source_window_contract.get("width"), name="source_window_width"),
        _as_int(source_window_contract.get("height"), name="source_window_height"),
    )
    if source_window.width <= 0 or source_window.height <= 0:
        raise RasterHarmonizationError("source_window_invalid")

    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_PAM_ENABLED="NO"):
        with rasterio.open(source_path) as source:
            if source.count != 1:
                raise RasterHarmonizationError("source_raster_must_have_one_band")
            if source.crs is None:
                raise RasterHarmonizationError("source_raster_crs_missing")
            if source_window.col_off < 0 or source_window.row_off < 0:
                raise RasterHarmonizationError("source_window_outside_raster")
            if source_window.col_off + source_window.width > source.width:
                raise RasterHarmonizationError("source_window_outside_raster")
            if source_window.row_off + source_window.height > source.height:
                raise RasterHarmonizationError("source_window_outside_raster")
            if source.width != _as_int(source_grid.get("width"), name="source_width"):
                raise RasterHarmonizationError("source_grid_width_mismatch")
            if source.height != _as_int(source_grid.get("height"), name="source_height"):
                raise RasterHarmonizationError("source_grid_height_mismatch")
            if source.dtypes[0] != str(source_grid.get("dtype")):
                raise RasterHarmonizationError("source_grid_dtype_mismatch")
            expected_source_transform = _coerce_affine(
                source_grid.get("transform") or [], name="source_transform"
            )
            if any(
                not _float_equal(actual, expected)
                for actual, expected in zip(list(source.transform)[:6], list(expected_source_transform)[:6], strict=True)
            ):
                raise RasterHarmonizationError("source_grid_transform_mismatch")
            expected_source_crs = _authority_to_string(source_grid.get("crs") or {})
            if source.crs != rasterio.crs.CRS.from_user_input(expected_source_crs):
                raise RasterHarmonizationError("source_grid_crs_mismatch")
            source_nodata = source.nodata
            expected_source_nodata = source_grid.get("nodata")
            if expected_source_nodata is not None and not _float_equal(
                source_nodata, expected_source_nodata
            ):
                raise RasterHarmonizationError("source_grid_nodata_mismatch")

    requested_bounds = target_grid.get("requested_bounds_before_snap") or []
    if len(requested_bounds) != 4:
        raise RasterHarmonizationError("target_requested_bounds_invalid")
    requested_bounds = tuple(
        _as_float(item, name="target_requested_bounds") for item in requested_bounds
    )
    if not requested_bounds[0] < requested_bounds[2] or not requested_bounds[1] < requested_bounds[3]:
        raise RasterHarmonizationError("target_requested_bounds_invalid")

    return {
        "source_path": source_path,
        "source_sha256": source_sha256,
        "source_window": source_window,
        "target_width": width,
        "target_height": height,
        "target_transform": target_transform,
        "target_crs": target_crs,
        "target_nodata": target_nodata,
        "target_dtype": output_dtype,
        "requested_bounds": requested_bounds,
        "resampling_method": method,
        "estimated_uncompressed_bytes": estimated_uncompressed_bytes,
    }


def _mask_outside_requested_bbox(
    values: np.ndarray,
    *,
    transform: Affine,
    requested_bounds: tuple[float, float, float, float],
    nodata: float,
) -> int:
    left, bottom, right, top = requested_bounds
    x_centers = transform.c + (np.arange(values.shape[1], dtype=np.float64) + 0.5) * transform.a
    y_centers = transform.f + (np.arange(values.shape[0], dtype=np.float64) + 0.5) * transform.e
    allowed_columns = (x_centers >= left) & (x_centers <= right)
    allowed_rows = (y_centers >= bottom) & (y_centers <= top)
    outside = ~(allowed_rows[:, None] & allowed_columns[None, :])
    masked_count = int(np.count_nonzero(outside & (values != nodata)))
    values[outside] = nodata
    return masked_count


def _profile_contract(path: Path) -> dict[str, Any]:
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_PAM_ENABLED="NO"):
        with rasterio.open(path) as dataset:
            nodata = dataset.nodata
            valid_count = 0
            nodata_count = 0
            nonfinite_count = 0
            minimum: float | None = None
            maximum: float | None = None
            total = 0.0
            compensation = 0.0
            for _, window in dataset.block_windows(1):
                values = dataset.read(indexes=(1,), window=window, masked=False)[0].astype(np.float64, copy=False)
                finite = np.isfinite(values)
                nonfinite_count += int(values.size - np.count_nonzero(finite))
                if nodata is None:
                    valid_mask = finite
                else:
                    valid_mask = finite & (values != float(nodata))
                    nodata_count += int(np.count_nonzero(values == float(nodata)))
                valid = values[valid_mask]
                if not valid.size:
                    continue
                valid_count += int(valid.size)
                block_minimum = float(valid.min())
                block_maximum = float(valid.max())
                minimum = block_minimum if minimum is None else min(minimum, block_minimum)
                maximum = block_maximum if maximum is None else max(maximum, block_maximum)
                block_total = float(valid.sum(dtype=np.float64))
                corrected = block_total - compensation
                updated = total + corrected
                compensation = (updated - total) - corrected
                total = updated
            mean = total / valid_count if valid_count else None
            image_structure = dataset.tags(ns="IMAGE_STRUCTURE")
            profile = {
                "width": int(dataset.width),
                "height": int(dataset.height),
                "count": int(dataset.count),
                "dtype": str(dataset.dtypes[0]),
                "nodata": None if nodata is None else float(nodata),
                "crs": dataset.crs.to_string() if dataset.crs else None,
                "transform": list(dataset.transform)[:6],
                "bounds": [float(item) for item in dataset.bounds],
                "block_shapes": [list(item) for item in dataset.block_shapes],
                "overviews": list(dataset.overviews(1)),
                "compression": image_structure.get("COMPRESSION"),
                "cog_layout": image_structure.get("LAYOUT"),
                "valid_pixel_count": valid_count,
                "nodata_pixel_count": nodata_count,
                "nonfinite_pixel_count": nonfinite_count,
                "minimum": minimum,
                "maximum": maximum,
                "mean": mean,
                "tags": dataset.tags(),
            }
    profile["profile_sha256"] = _contract_sha256(
        {key: profile[key] for key in sorted(_PROFILE_FIELDS)}
    )
    return profile


def _validate_output_profile(profile: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
    target = plan.get("target_grid") or {}
    if profile.get("width") != target.get("width") or profile.get("height") != target.get("height"):
        raise RasterHarmonizationError("harmonized_output_dimensions_mismatch")
    if profile.get("count") != 1 or profile.get("dtype") != str(target.get("dtype")):
        raise RasterHarmonizationError("harmonized_output_band_or_dtype_mismatch")
    if not _float_equal(profile.get("nodata"), target.get("nodata")):
        raise RasterHarmonizationError("harmonized_output_nodata_mismatch")
    expected_crs = rasterio.crs.CRS.from_user_input(_authority_to_string(target.get("crs") or {}))
    if rasterio.crs.CRS.from_user_input(profile.get("crs")) != expected_crs:
        raise RasterHarmonizationError("harmonized_output_crs_mismatch")
    expected_transform = _coerce_affine(target.get("transform") or [], name="target_transform")
    if any(
        not _float_equal(actual, expected)
        for actual, expected in zip(profile.get("transform") or [], list(expected_transform)[:6], strict=True)
    ):
        raise RasterHarmonizationError("harmonized_output_transform_mismatch")
    if profile.get("nonfinite_pixel_count") != 0:
        raise RasterHarmonizationError("harmonized_output_contains_nonfinite_pixels")
    if profile.get("valid_pixel_count", 0) + profile.get("nodata_pixel_count", 0) != (
        int(profile.get("width", 0)) * int(profile.get("height", 0))
    ):
        raise RasterHarmonizationError("harmonized_output_pixel_accounting_mismatch")
    if profile.get("cog_layout") != "COG":
        raise RasterHarmonizationError("harmonized_output_cog_layout_not_declared")
    if not profile.get("block_shapes"):
        raise RasterHarmonizationError("harmonized_output_not_tiled")


def _receipt_contract(receipt: Mapping[str, Any]) -> dict[str, Any]:
    excluded = {
        "generated_at_utc",
        "receipt_path",
        "output_artifact_path",
        "reused_existing_output",
        "harmonization_receipt_sha256",
    }
    return {key: value for key, value in receipt.items() if key not in excluded}


def compute_harmonization_receipt_sha256(receipt: Mapping[str, Any]) -> str:
    return _contract_sha256(_receipt_contract(receipt))


def execute_raster_harmonization(
    plan: Mapping[str, Any],
    *,
    artifact_root: Path,
    staging_root: Path,
    receipt_path: Path | None = None,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    mask_to_requested_bounds: bool = True,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Execute one verified single-band raster harmonization plan.

    The executor is source-neutral: it consumes the existing harmonization-plan
    shape, verifies the bound source raster, reads only the declared source
    window, performs nodata-aware reprojection, writes a deterministic COG, and
    emits a content-bound receipt. It does not acquire source data or expand dates.
    """

    context = _validate_plan_and_source(plan, max_output_bytes=max_output_bytes)
    artifact_root = Path(artifact_root)
    staging_root = Path(staging_root)
    _ensure_safe_directory(artifact_root)
    _ensure_safe_directory(staging_root)

    plan_sha256 = str(plan["harmonization_plan_sha256"])
    stage_dir = staging_root / plan_sha256[:16]
    if stage_dir.exists() and (not stage_dir.is_dir() or stage_dir.is_symlink()):
        raise RasterHarmonizationError("harmonization_staging_path_invalid")
    stage_dir.mkdir(parents=True, exist_ok=True)
    raw_path = stage_dir / "harmonized-working.tif"
    cog_path = stage_dir / "harmonized-cog.tmp.tif"
    for temporary in (raw_path, cog_path):
        if temporary.is_symlink():
            raise RasterHarmonizationError("harmonization_staging_path_invalid")
        temporary.unlink(missing_ok=True)

    target_shape = (context["target_height"], context["target_width"])
    destination = np.full(target_shape, context["target_nodata"], dtype=np.float32)
    masked_valid_pixel_count = 0
    try:
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_PAM_ENABLED="NO"):
            with rasterio.open(context["source_path"]) as source:
                source_values = source.read(indexes=(1,), window=context["source_window"], masked=False)[0]
                source_transform = source.window_transform(context["source_window"])
                reproject(
                    source=source_values,
                    destination=destination,
                    src_transform=source_transform,
                    src_crs=source.crs,
                    src_nodata=source.nodata,
                    dst_transform=context["target_transform"],
                    dst_crs=context["target_crs"],
                    dst_nodata=context["target_nodata"],
                    resampling=_ALLOWED_RESAMPLING[context["resampling_method"]],
                    init_dest_nodata=True,
                    num_threads=1,
                )

            if mask_to_requested_bounds:
                masked_valid_pixel_count = _mask_outside_requested_bbox(
                    destination,
                    transform=context["target_transform"],
                    requested_bounds=context["requested_bounds"],
                    nodata=context["target_nodata"],
                )

            block_size = max(16, min(DEFAULT_BLOCK_SIZE, 4096))
            with rasterio.open(
                raw_path,
                "w",
                driver="GTiff",
                width=context["target_width"],
                height=context["target_height"],
                count=1,
                dtype="float32",
                crs=context["target_crs"],
                transform=context["target_transform"],
                nodata=context["target_nodata"],
                tiled=True,
                blockxsize=block_size,
                blockysize=block_size,
                compress="DEFLATE",
                predictor=3,
                BIGTIFF="IF_SAFER",
            ) as output:
                output.write(destination, 1)
                output.update_tags(
                    AREA_OR_POINT="Area",
                    FASTERRASTER_HARMONIZATION_PLAN_SHA256=plan_sha256,
                    FASTERRASTER_SOURCE_RASTER_SHA256=context["source_sha256"],
                    FASTERRASTER_HARMONIZED_RASTER_VERSION=str(HARMONIZED_RASTER_VERSION),
                    FASTERRASTER_VARIABLE=str(plan.get("variable") or ""),
                    FASTERRASTER_UNITS=str(plan.get("units") or ""),
                )

            raster_copy(
                raw_path,
                cog_path,
                driver="COG",
                compress="DEFLATE",
                blocksize=DEFAULT_BLOCK_SIZE,
                overview_resampling="average",
                predictor="FLOATING_POINT",
                BIGTIFF="IF_SAFER",
                NUM_THREADS="1",
            )
    except Exception:
        raw_path.unlink(missing_ok=True)
        cog_path.unlink(missing_ok=True)
        raise
    finally:
        raw_path.unlink(missing_ok=True)

    if not cog_path.is_file() or cog_path.is_symlink():
        raise RasterHarmonizationError("harmonized_cog_was_not_created")
    output_size = cog_path.stat().st_size
    if output_size > max_output_bytes:
        cog_path.unlink(missing_ok=True)
        raise RasterHarmonizationError("harmonized_output_byte_limit_exceeded")
    output_sha256 = _sha256_file(cog_path)
    output_profile = _profile_contract(cog_path)
    _validate_output_profile(output_profile, plan)

    destination_path = _content_addressed_path(artifact_root, output_sha256)
    reused = False
    if destination_path.exists():
        if destination_path.is_symlink() or not destination_path.is_file():
            raise RasterHarmonizationError("harmonized_output_destination_invalid")
        if _sha256_file(destination_path) != output_sha256:
            raise RasterHarmonizationError("existing_harmonized_output_is_corrupt")
        cog_path.unlink(missing_ok=True)
        reused = True
    else:
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(cog_path, destination_path)

    generated = generated_at_utc or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    receipt: dict[str, Any] = {
        "harmonization_receipt_version": HARMONIZATION_RECEIPT_VERSION,
        "validation_status": "PASS",
        "generated_at_utc": generated,
        "source_id": plan.get("source_id"),
        "source_product": plan.get("source_product"),
        "variable": plan.get("variable"),
        "units": plan.get("units"),
        "source_raster_sha256": context["source_sha256"],
        "harmonization_plan_sha256": plan_sha256,
        "resampling_method": context["resampling_method"],
        "requested_bounds_mask_applied": bool(mask_to_requested_bounds),
        "requested_bounds_mask_policy": "pixel_center_within_target_requested_bounds"
        if mask_to_requested_bounds
        else "not_applied",
        "masked_valid_pixel_count": masked_valid_pixel_count,
        "estimated_uncompressed_output_bytes": context["estimated_uncompressed_bytes"],
        "max_output_bytes": int(max_output_bytes),
        "output_artifact_id": f"sha256:{output_sha256}",
        "output_artifact_path": str(destination_path),
        "output_sha256": output_sha256,
        "output_size_bytes": output_size,
        "reused_existing_output": reused,
        "output_profile": output_profile,
        "execution_performed": True,
        "output_written": True,
    }
    if receipt_path is not None:
        receipt["receipt_path"] = str(Path(receipt_path))
    receipt["harmonization_receipt_sha256"] = compute_harmonization_receipt_sha256(receipt)
    if receipt_path is not None:
        _atomic_write_json(Path(receipt_path), receipt)
    return receipt


def verify_harmonization_receipt(receipt_or_path: Mapping[str, Any] | Path) -> dict[str, Any]:
    if isinstance(receipt_or_path, Mapping):
        receipt = dict(receipt_or_path)
    else:
        receipt = json.loads(Path(receipt_or_path).read_text(encoding="utf-8"))
    failures: list[str] = []
    computed_receipt_sha256 = compute_harmonization_receipt_sha256(receipt)
    if computed_receipt_sha256 != receipt.get("harmonization_receipt_sha256"):
        failures.append("harmonization_receipt_hash_mismatch")
    output_path = Path(str(receipt.get("output_artifact_path") or ""))
    if not output_path.is_file() or output_path.is_symlink():
        failures.append("harmonized_output_missing_or_invalid")
    else:
        output_sha256 = _sha256_file(output_path)
        if output_sha256 != receipt.get("output_sha256"):
            failures.append("harmonized_output_sha256_mismatch")
        if output_path.stat().st_size != receipt.get("output_size_bytes"):
            failures.append("harmonized_output_size_mismatch")
        if output_sha256 not in output_path.name:
            failures.append("harmonized_output_path_is_not_content_addressed")
        try:
            profile = _profile_contract(output_path)
        except Exception as exc:
            failures.append(f"harmonized_output_profile_failed:{type(exc).__name__}")
        else:
            stored_profile = receipt.get("output_profile") or {}
            comparable = _PROFILE_FIELDS | {"profile_sha256"}
            if any(stored_profile.get(field) != profile.get(field) for field in comparable):
                failures.append("harmonized_output_profile_mismatch")
            tags = profile.get("tags") or {}
            if tags.get("FASTERRASTER_HARMONIZATION_PLAN_SHA256") != receipt.get(
                "harmonization_plan_sha256"
            ):
                failures.append("harmonization_plan_binding_missing_from_output")
            if tags.get("FASTERRASTER_SOURCE_RASTER_SHA256") != receipt.get(
                "source_raster_sha256"
            ):
                failures.append("source_raster_binding_missing_from_output")
    if receipt.get("validation_status") != "PASS":
        failures.append("harmonization_receipt_not_pass")
    if receipt.get("execution_performed") is not True or receipt.get("output_written") is not True:
        failures.append("harmonization_receipt_does_not_describe_execution")
    return {
        "verification_status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "computed_harmonization_receipt_sha256": computed_receipt_sha256,
    }


def run_self_check() -> dict[str, Any]:
    from rasterio.transform import from_origin
    from faster_raster.prism_harmonization import plan_prism_harmonization

    with tempfile.TemporaryDirectory(prefix="fr-raster-harmonization-") as temporary:
        root = Path(temporary)
        source = root / "source.tif"
        values = np.arange(100, dtype=np.float32).reshape(10, 10)
        values[0, :] = -9999.0
        with rasterio.open(
            source,
            "w",
            driver="GTiff",
            width=10,
            height=10,
            count=1,
            dtype="float32",
            crs="EPSG:4326",
            transform=from_origin(-100.0, 50.0, 0.1, 0.1),
            nodata=-9999.0,
        ) as dataset:
            dataset.write(values, 1)

        aligned_plan = plan_prism_harmonization(
            source,
            aoi_bbox=(-99.8, 49.2, -99.2, 49.8),
            aoi_crs="EPSG:4326",
            target_crs="EPSG:4326",
            target_resolution=0.1,
            target_origin=(0.0, 0.0),
            resampling_method="nearest",
            max_output_pixels=10_000,
        )
        receipt_path = root / "receipt.json"
        first = execute_raster_harmonization(
            aligned_plan,
            artifact_root=root / "artifacts",
            staging_root=root / "staging",
            receipt_path=receipt_path,
            max_output_bytes=16 * 1024 * 1024,
            generated_at_utc="2026-01-01T00:00:00Z",
        )
        second = execute_raster_harmonization(
            aligned_plan,
            artifact_root=root / "replay-artifacts",
            staging_root=root / "replay-staging",
            max_output_bytes=16 * 1024 * 1024,
            generated_at_utc="2026-01-02T00:00:00Z",
        )
        verification = verify_harmonization_receipt(receipt_path)
        tampered = dict(first)
        tampered["output_size_bytes"] = int(tampered["output_size_bytes"]) + 1
        tampered_verification = verify_harmonization_receipt(tampered)

        projected_plan = plan_prism_harmonization(
            source,
            aoi_bbox=(-99.8, 49.2, -99.2, 49.8),
            aoi_crs="EPSG:4326",
            target_crs="EPSG:5070",
            target_resolution=4000,
            target_origin=(0.0, 0.0),
            resampling_method="bilinear",
            max_output_pixels=10_000,
        )
        projected = execute_raster_harmonization(
            projected_plan,
            artifact_root=root / "projected-artifacts",
            staging_root=root / "projected-staging",
            max_output_bytes=16 * 1024 * 1024,
            generated_at_utc="2026-01-01T00:00:00Z",
        )

        checks = {
            "aligned_receipt_verifies": verification["verification_status"] == "PASS",
            "aligned_output_is_cog": first["output_profile"]["cog_layout"] == "COG",
            "aligned_output_matches_plan_grid": first["output_profile"]["width"]
            == aligned_plan["target_grid"]["width"]
            and first["output_profile"]["height"] == aligned_plan["target_grid"]["height"],
            "cross_workspace_output_is_deterministic": second["output_sha256"]
            == first["output_sha256"],
            "cross_workspace_receipt_contract_is_deterministic": second[
                "harmonization_receipt_sha256"
            ]
            == first["harmonization_receipt_sha256"],
            "tampered_receipt_is_rejected": tampered_verification["verification_status"] == "FAIL",
            "projected_output_is_cog": projected["output_profile"]["cog_layout"] == "COG",
            "projected_output_uses_requested_crs": projected["output_profile"]["crs"]
            == "EPSG:5070",
        }
        return {
            "self_check_status": "PASS" if all(checks.values()) else "FAIL",
            "checks": checks,
            "aligned_plan_sha256": aligned_plan["harmonization_plan_sha256"],
            "aligned_output_sha256": first["output_sha256"],
            "projected_plan_sha256": projected_plan["harmonization_plan_sha256"],
            "projected_output_sha256": projected["output_sha256"],
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m faster_raster.raster_harmonization",
        description="Execute a verified single-band raster harmonization plan into a content-addressed COG.",
    )
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--staging-root", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--max-output-bytes", type=int, default=DEFAULT_MAX_OUTPUT_BYTES)
    parser.add_argument("--no-requested-bounds-mask", action="store_true")
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.self_check:
        payload = run_self_check()
    else:
        missing = [
            name
            for name, value in {
                "--plan": args.plan,
                "--artifact-root": args.artifact_root,
                "--staging-root": args.staging_root,
            }.items()
            if value is None
        ]
        if missing:
            raise SystemExit("missing required arguments: " + ", ".join(missing))
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        payload = execute_raster_harmonization(
            plan,
            artifact_root=args.artifact_root,
            staging_root=args.staging_root,
            receipt_path=args.receipt,
            max_output_bytes=args.max_output_bytes,
            mask_to_requested_bounds=not args.no_requested_bounds_mask,
        )
    print(json.dumps(payload, indent=2, sort_keys=True))
    if payload.get("self_check_status") == "FAIL":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
