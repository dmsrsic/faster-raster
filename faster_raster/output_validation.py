from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from faster_raster.manifest import read_manifest
from faster_raster.validation import CATEGORICAL_ALLOWED_RESAMPLING, CONTINUOUS_ALLOWED_RESAMPLING

MANIFEST_REQUIRED_FIELDS = {
    "request_id",
    "source_id",
    "registry_key",
    "adapter",
    "provider",
    "product",
    "year",
    "thematic_layer",
    "tile_id",
    "url",
    "semantic_type",
    "resampling",
    "source_aoi_bbox",
    "source_aoi_crs",
    "bbox",
    "bbox_crs",
    "export_image_crs",
    "target_grid_crs",
    "target_resolution_m",
    "status",
}

MANIFEST_CRITICAL_FIELDS = MANIFEST_REQUIRED_FIELDS | {
    "tile_width_pixels",
    "tile_height_pixels",
    "tile_planning_crs",
}

HARMONIZATION_REQUIRED_FIELDS = {"project_id", "target_grid", "inputs", "validation_checks"}
HARMONIZATION_INPUT_REQUIRED_FIELDS = {
    "request_id",
    "source_bbox",
    "bbox_crs",
    "export_image_crs",
    "target_grid_crs",
    "semantic_type",
    "resampling",
    "planned_output",
}


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_crs(value: Any) -> bool:
    return _is_non_empty_string(value) and value.upper().startswith("EPSG:") and value.split(":", 1)[1].isdigit()


def _is_bbox(value: Any) -> bool:
    if not isinstance(value, list) or len(value) != 4:
        return False
    if not all(isinstance(item, (int, float)) for item in value):
        return False
    min_x, min_y, max_x, max_y = value
    return min_x < max_x and min_y < max_y


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and value > 0


def _url_is_https(value: Any) -> bool:
    if not _is_non_empty_string(value):
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def _validate_semantic_resampling(container: dict, label: str) -> list[str]:
    errors: list[str] = []
    semantic_type = container.get("semantic_type")
    resampling = container.get("resampling")
    if semantic_type not in {"categorical", "continuous"}:
        errors.append(f"{label} semantic_type must be categorical or continuous")
        return errors
    if not _is_non_empty_string(resampling):
        errors.append(f"{label} resampling is required")
        return errors
    if semantic_type == "categorical" and resampling not in CATEGORICAL_ALLOWED_RESAMPLING:
        errors.append(f"{label} categorical raster cannot use {resampling} resampling")
    if semantic_type == "continuous" and resampling not in CONTINUOUS_ALLOWED_RESAMPLING:
        errors.append(f"{label} continuous raster cannot use {resampling} resampling")
    return errors


def _parse_manifest_jsonl(path: Path) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    errors: list[str] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    errors.append(f"line {line_number}: malformed JSONL: {exc.msg}")
                    continue
                if not isinstance(row, dict):
                    errors.append(f"line {line_number}: manifest row must be an object")
                    continue
                rows.append(row)
    except OSError as exc:
        errors.append(f"could not read manifest: {exc}")
    return rows, errors


def validate_manifest_rows(rows: list[dict]) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    duplicate_ids: list[str] = []

    for index, row in enumerate(rows, start=1):
        label = f"row {index}"
        missing = sorted(field for field in MANIFEST_REQUIRED_FIELDS if field not in row)
        for field in missing:
            errors.append(f"{label}: missing required field {field}")
        for field in sorted(MANIFEST_CRITICAL_FIELDS & row.keys()):
            if row.get(field) is None:
                errors.append(f"{label}: critical field {field} must not be null")
        request_id = row.get("request_id")
        if not _is_non_empty_string(request_id):
            errors.append(f"{label}: request_id must be a non-empty string")
        elif request_id in seen:
            duplicate_ids.append(request_id)
        else:
            seen.add(request_id)
        if "url" in row and not _url_is_https(row.get("url")):
            errors.append(f"{label}: url must be a non-empty HTTPS URL")
        for field in ["adapter", "source_id", "registry_key", "thematic_layer", "tile_id", "status"]:
            if field in row and not _is_non_empty_string(row.get(field)):
                errors.append(f"{label}: {field} must be a non-empty string")
        if "year" in row and not isinstance(row.get("year"), int):
            errors.append(f"{label}: year must be an integer")
        for field in ["bbox_crs", "source_aoi_crs", "export_image_crs", "target_grid_crs"]:
            if field in row and not _is_crs(row.get(field)):
                errors.append(f"{label}: {field} must be a non-empty EPSG code")
        for field in ["bbox", "source_aoi_bbox"]:
            if field in row and not _is_bbox(row.get(field)):
                errors.append(f"{label}: {field} must be [min_x, min_y, max_x, max_y]")
        for field in ["tile_width_pixels", "tile_height_pixels"]:
            if field in row and not _is_positive_int(row.get(field)):
                errors.append(f"{label}: {field} must be a positive integer")
        if "target_resolution_m" in row and not isinstance(row.get("target_resolution_m"), (int, float)):
            errors.append(f"{label}: target_resolution_m must be numeric")
        if "checksum" in row and not _is_non_empty_string(row.get("checksum")):
            errors.append(f"{label}: checksum must be a non-empty string when present")
        if "sha256" in row and not _is_non_empty_string(row.get("sha256")):
            errors.append(f"{label}: sha256 must be a non-empty string when present")
        errors.extend(_validate_semantic_resampling(row, label))

    for request_id in sorted(set(duplicate_ids)):
        errors.append(f"duplicate request_id: {request_id}")

    return {
        "status": "PASS" if not errors else "FAIL",
        "row_count": len(rows),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
    }


def validate_manifest(path: Path) -> dict:
    rows, parse_errors = _parse_manifest_jsonl(path)
    report = validate_manifest_rows(rows)
    if parse_errors:
        report["errors"] = parse_errors + report["errors"]
        report["error_count"] = len(report["errors"])
        report["status"] = "FAIL"
    report["path"] = str(path)
    return report


def validate_harmonization_plan(plan: dict, manifest_rows: list[dict] | None = None) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(plan, dict):
        return {
            "status": "FAIL",
            "input_count": 0,
            "error_count": 1,
            "warning_count": 0,
            "errors": ["harmonization plan must be a JSON object"],
            "warnings": [],
        }

    for field in sorted(HARMONIZATION_REQUIRED_FIELDS):
        if field not in plan:
            errors.append(f"missing required field {field}")
    target_grid = plan.get("target_grid")
    if not isinstance(target_grid, dict):
        errors.append("target_grid must be an object")
        target_grid = {}
    if not _is_crs(target_grid.get("crs")):
        errors.append("target_grid.crs must be a non-empty EPSG code")
    if not isinstance(target_grid.get("resolution_m"), (int, float)):
        errors.append("target_grid.resolution_m must be numeric")
    if "nodata" not in target_grid:
        errors.append("target_grid.nodata must be represented")

    inputs = plan.get("inputs")
    if not isinstance(inputs, list):
        errors.append("inputs must be a list")
        inputs = []

    seen: set[str] = set()
    duplicate_ids: list[str] = []
    for index, item in enumerate(inputs, start=1):
        label = f"input {index}"
        if not isinstance(item, dict):
            errors.append(f"{label}: input must be an object")
            continue
        for field in sorted(HARMONIZATION_INPUT_REQUIRED_FIELDS):
            if field not in item:
                errors.append(f"{label}: missing required field {field}")
        request_id = item.get("request_id")
        if not _is_non_empty_string(request_id):
            errors.append(f"{label}: request_id must be a non-empty string")
        elif request_id in seen:
            duplicate_ids.append(request_id)
        else:
            seen.add(request_id)
        for field in ["bbox_crs", "export_image_crs", "target_grid_crs"]:
            if field in item and not _is_crs(item.get(field)):
                errors.append(f"{label}: {field} must be a non-empty EPSG code")
        if "source_bbox" in item and not _is_bbox(item.get("source_bbox")):
            errors.append(f"{label}: source_bbox must be [min_x, min_y, max_x, max_y]")
        for field in ["tile_width_pixels", "tile_height_pixels"]:
            if field in item and not _is_positive_int(item.get(field)):
                errors.append(f"{label}: {field} must be a positive integer")
        if not _is_non_empty_string(item.get("planned_output")):
            errors.append(f"{label}: planned_output must be a non-empty string")
        errors.extend(_validate_semantic_resampling(item, label))
        if item.get("semantic_type") == "continuous" and item.get("resampling") == "mode":
            errors.append(f"{label}: continuous raster cannot use categorical-only mode resampling")

    for request_id in sorted(set(duplicate_ids)):
        errors.append(f"duplicate harmonization request_id: {request_id}")

    if manifest_rows is not None:
        manifest_ids = {row.get("request_id") for row in manifest_rows if _is_non_empty_string(row.get("request_id"))}
        plan_ids = {item.get("request_id") for item in inputs if isinstance(item, dict) and _is_non_empty_string(item.get("request_id"))}
        missing_from_plan = sorted(manifest_ids - plan_ids)
        missing_from_manifest = sorted(plan_ids - manifest_ids)
        for request_id in missing_from_plan:
            errors.append(f"manifest request_id missing from harmonization plan: {request_id}")
        for request_id in missing_from_manifest:
            errors.append(f"harmonization request_id missing from manifest: {request_id}")

    return {
        "status": "PASS" if not errors else "FAIL",
        "input_count": len(inputs),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
    }


def validate_harmonization(path: Path, manifest_path: Path | None = None) -> dict:
    errors: list[str] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            plan = json.load(handle)
    except json.JSONDecodeError as exc:
        return {
            "status": "FAIL",
            "path": str(path),
            "input_count": 0,
            "error_count": 1,
            "warning_count": 0,
            "errors": [f"malformed JSON: {exc.msg}"],
            "warnings": [],
        }
    except OSError as exc:
        return {
            "status": "FAIL",
            "path": str(path),
            "input_count": 0,
            "error_count": 1,
            "warning_count": 0,
            "errors": [f"could not read harmonization plan: {exc}"],
            "warnings": [],
        }

    manifest_rows = None
    if manifest_path is not None:
        manifest_rows, parse_errors = _parse_manifest_jsonl(manifest_path)
        if parse_errors:
            errors.extend(f"manifest {error}" for error in parse_errors)
    report = validate_harmonization_plan(plan, manifest_rows)
    if errors:
        report["errors"] = errors + report["errors"]
        report["error_count"] = len(report["errors"])
        report["status"] = "FAIL"
    report["path"] = str(path)
    if manifest_path is not None:
        report["manifest_path"] = str(manifest_path)
        report["manifest_row_count"] = len(manifest_rows or [])
    return report
