from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from faster_raster import __version__
from faster_raster.adapter_contract import stable_json
from faster_raster.artifact_receipts import compute_artifact_receipt_sha256, normalize_artifact_contract
from faster_raster.artifact_store import sha256_file
from faster_raster.run_receipts import write_json, write_jsonl

SOURCE_ROOT = Path("cache/artifacts/sha256")
DERIVED_ROOT = Path("cache/derived/sha256")
STAGING_ROOT = Path("cache/staging/derivations")
REPORT_ROOT = Path("reports/derivations")
IMPLEMENTATION_VERSION = "derived-artifacts-v1"


class DerivationError(ValueError):
    def __init__(self, failure_class: str, message: str | None = None):
        super().__init__(message or failure_class)
        self.failure_class = failure_class


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def repo_root(path: Path | None = None) -> Path:
    return (path or Path.cwd()).resolve()


def logical(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return str(path)


def hash_contract(value: dict[str, Any], *, root: Path | None = None, exclude: set[str] | None = None) -> str:
    excluded = exclude or set()
    contract = {k: normalize_artifact_contract(v, root or Path.cwd()) for k, v in value.items() if k not in excluded}
    return hashlib.sha256(stable_json(contract).encode("utf-8")).hexdigest()


def derived_content_addressed_path(sha256: str, extension: str = ".tif", *, derived_root: Path = DERIVED_ROOT, root: Path | None = None) -> Path:
    root = repo_root(root)
    base = (root / derived_root).resolve() if not derived_root.is_absolute() else derived_root.resolve()
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise DerivationError("derived_store_error", "invalid SHA256")
    if not extension.startswith(".") or "/" in extension or "\\" in extension:
        raise DerivationError("derived_store_error", "invalid extension")
    path = (base / sha256[:2] / sha256[2:4] / f"{sha256}{extension}").resolve()
    if not path.is_relative_to(base):
        raise DerivationError("derived_store_error", "derived path escapes root")
    return path


def source_path(artifact_sha256: str, *, root: Path) -> Path:
    if len(artifact_sha256) != 64 or any(c not in "0123456789abcdef" for c in artifact_sha256):
        raise DerivationError("source_artifact_missing", "invalid source SHA256")
    base = (root / SOURCE_ROOT).resolve()
    path = base / artifact_sha256[:2] / artifact_sha256[2:4] / f"{artifact_sha256}.tif.gz"
    if not path.parent.resolve().is_relative_to(base):
        raise DerivationError("source_artifact_missing", "source path escapes root")
    return path


def source_catalog(root: Path) -> dict[str, Any]:
    path = root / "reports/artifacts/artifact_catalog.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def source_catalog_entry(artifact_sha256: str, root: Path) -> dict[str, Any]:
    for entry in source_catalog(root).get("entries", []):
        if entry.get("whole_object_sha256") == artifact_sha256:
            return entry
    return {}


def source_receipt_hash(entry: dict[str, Any], root: Path) -> str | None:
    for raw in entry.get("artifact_receipt_paths", []):
        path = root / raw
        if path.exists():
            receipt = json.loads(path.read_text(encoding="utf-8"))
            return receipt.get("artifact_receipt_contract_sha256") or compute_artifact_receipt_sha256(receipt, root)
    return None


def compute_derivation_plan_sha256(plan: dict[str, Any], *, root: Path | None = None) -> str:
    return hash_contract(plan, root=root, exclude={"derivation_plan_contract_sha256", "generated_at_utc"})


def build_derivation_plan(
    artifact_sha256: str,
    *,
    operation: str = "gzip-decompress",
    max_output_bytes: int = 1_073_741_824,
    max_expansion_ratio: float = 500,
    root: Path | None = None,
) -> dict[str, Any]:
    root = repo_root(root)
    if operation != "gzip-decompress":
        raise DerivationError("unsupported_operation")
    src = source_path(artifact_sha256, root=root)
    plan = {
        "schema_version": 1,
        "operation": "gzip_decompress",
        "source_artifact_sha256": artifact_sha256,
        "source_artifact_logical_path": logical(src, root),
        "source_size_bytes": src.stat().st_size if src.exists() and src.is_file() and not src.is_symlink() else None,
        "expected_source_container": "gzip",
        "expected_derived_format": "geotiff",
        "max_decompressed_bytes": int(max_output_bytes),
        "max_expansion_ratio": float(max_expansion_ratio),
        "staging_root_policy": {"root": "cache/staging/derivations", "absolute_paths_excluded_from_hash": True, "symlinks_rejected": True},
        "derived_artifact_root_policy": {"root": "cache/derived/sha256", "content_addressed": True, "symlinks_rejected": True, "no_overwrite": True},
        "implementation_version": IMPLEMENTATION_VERSION,
        "approval_requirement": "require --allow-derivation and --approve-plan-sha256",
        "generated_at_utc": utc_now(),
        "derivation_plan_contract_sha256": "",
    }
    plan["derivation_plan_contract_sha256"] = compute_derivation_plan_sha256(plan, root=root)
    return plan


def read_geotiff_info(path: Path) -> dict[str, Any]:
    try:
        import rasterio

        with rasterio.open(path, "r") as dataset:
            return {
                "reader": "rasterio",
                "driver": dataset.driver,
                "width": dataset.width,
                "height": dataset.height,
                "band_count": dataset.count,
                "dtypes": list(dataset.dtypes),
                "transform": list(dataset.transform)[:6],
                "crs_present": dataset.crs is not None,
                "crs": dataset.crs.to_string() if dataset.crs else None,
                "crs_authority": dataset.crs.to_authority()[0] if dataset.crs and dataset.crs.to_authority() else None,
                "crs_code": dataset.crs.to_authority()[1] if dataset.crs and dataset.crs.to_authority() else None,
                "crs_wkt": dataset.crs.to_wkt() if dataset.crs else None,
                "crs_projjson": dataset.crs.to_json_dict() if dataset.crs else None,
                "nodata": list(dataset.nodatavals),
                "bounds": list(dataset.bounds),
                "block_shapes": [list(item) for item in dataset.block_shapes],
                "compression": dataset.compression.value if dataset.compression else None,
                "tiled": bool(dataset.is_tiled),
                "interleave": dataset.profile.get("interleave"),
                "overview_levels": dataset.overviews(1) if dataset.count else [],
                "color_interpretations": [item.name for item in dataset.colorinterp],
                "bands": [
                    {
                        "band_index": index,
                        "dtype": dataset.dtypes[index - 1],
                        "nodata": dataset.nodatavals[index - 1],
                        "scale": dataset.scales[index - 1] if dataset.scales else None,
                        "offset": dataset.offsets[index - 1] if dataset.offsets else None,
                        "units": dataset.units[index - 1] if dataset.units else None,
                        "description": dataset.descriptions[index - 1] if dataset.descriptions else None,
                        "tags": dataset.tags(index),
                        "mask_flags": [flag.name for flag in dataset.mask_flag_enums[index - 1]],
                        "block_shape": list(dataset.block_shapes[index - 1]),
                    }
                    for index in range(1, dataset.count + 1)
                ],
            }
    except Exception:
        pass
    from PIL import Image

    with Image.open(path) as image:
        tags = {int(k): v for k, v in image.tag_v2.items()}
        width, height = image.size
        mode_dtype = {"F": "float32", "I": "int32", "I;16": "uint16", "I;16B": "uint16", "L": "uint8", "1": "bool"}
        dtype = mode_dtype.get(image.mode, image.mode)
        scale = tags.get(33550) or (1.0, 1.0, 0.0)
        tie = tags.get(33922) or (0.0, 0.0, 0.0, 0.0, float(height), 0.0)
        sx = float(scale[0])
        sy = float(scale[1])
        x0 = float(tie[3]) - float(tie[0]) * sx if len(tie) >= 6 else 0.0
        y0 = float(tie[4]) + float(tie[1]) * sy if len(tie) >= 6 else float(height)
        transform = [sx, 0.0, x0, 0.0, -sy, y0]
        bounds = [x0, y0 - height * sy, x0 + width * sx, y0]
        geokeys = tags.get(34735) or []
        epsg = None
        if geokeys:
            vals = list(geokeys)
            for i in range(4, len(vals), 4):
                key_id, tag_location, count, value = vals[i : i + 4]
                if key_id in {2048, 3072} and tag_location == 0:
                    epsg = str(value)
        nodata_raw = tags.get(42113)
        if isinstance(nodata_raw, bytes):
            nodata_raw = nodata_raw.decode("ascii", errors="ignore")
        try:
            nodata = float(str(nodata_raw).strip().split("\x00")[0]) if nodata_raw is not None else None
        except ValueError:
            nodata = str(nodata_raw) if nodata_raw is not None else None
        rows_per_strip = tags.get(278) or height
        block_shape = [int(rows_per_strip), int(width)]
        compression = tags.get(259)
        return {
            "reader": "Pillow",
            "driver": "GTiff",
            "width": width,
            "height": height,
            "band_count": len(image.getbands()),
            "dtypes": [dtype for _ in image.getbands()],
            "transform": transform,
            "crs_present": epsg is not None,
            "crs": f"EPSG:{epsg}" if epsg else None,
            "crs_authority": "EPSG" if epsg else None,
            "crs_code": epsg,
            "crs_wkt": None,
            "crs_projjson": None,
            "nodata": [nodata for _ in image.getbands()],
            "bounds": bounds,
            "block_shapes": [block_shape for _ in image.getbands()],
            "compression": compression,
            "tiled": 322 in tags and 323 in tags,
            "interleave": None,
            "overview_levels": [],
            "color_interpretations": list(image.getbands()),
            "bands": [
                {
                    "band_index": index + 1,
                    "dtype": dtype,
                    "nodata": nodata,
                    "scale": None,
                    "offset": None,
                    "units": None,
                    "description": None,
                    "tags": {},
                    "mask_flags": [],
                    "block_shape": block_shape,
                }
                for index, _ in enumerate(image.getbands())
            ],
        }


def validate_geotiff(path: Path) -> dict[str, Any]:
    failures: list[str] = []
    size = path.stat().st_size if path.exists() else 0
    header = path.read_bytes()[:16] if path.exists() else b""
    byte_order = None
    magic = None
    if size <= 0:
        failures.append("file size is zero")
    if header[:2] == b"II":
        byte_order = "little"
        magic = int.from_bytes(header[2:4], "little")
        ifd = int.from_bytes(header[4:8], "little")
    elif header[:2] == b"MM":
        byte_order = "big"
        magic = int.from_bytes(header[2:4], "big")
        ifd = int.from_bytes(header[4:8], "big")
    else:
        ifd = 0
        failures.append("invalid TIFF byte order")
    if byte_order and magic not in {42, 43}:
        failures.append("invalid TIFF magic")
    if byte_order and (ifd <= 0 or ifd >= size):
        failures.append("invalid first IFD offset")
    raster: dict[str, Any] = {"reader": "unavailable"}
    try:
        raster = read_geotiff_info(path)
        if raster["width"] <= 0 or raster["height"] <= 0 or raster["band_count"] <= 0:
            failures.append("invalid raster dimensions")
    except Exception as exc:
        failures.append(f"raster reader failed: {type(exc).__name__}")
    return {
        "validation_status": "PASS" if not failures else "FAIL",
        "file_size_bytes": size,
        "byte_order": byte_order,
        "tiff_magic": magic,
        "first_ifd_offset_valid": bool(byte_order and ifd > 0 and ifd < size),
        "raster": raster,
        "errors": failures,
    }


def decompress_stream(source: Path, staging_file: Path, *, max_output_bytes: int, max_expansion_ratio: float) -> dict[str, Any]:
    digest = hashlib.sha256()
    compressed_read = 0
    decompressed_written = 0
    gzip_members = 0
    ended_cleanly = False
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    with source.open("rb") as src, staging_file.open("wb") as dst:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            compressed_read += len(chunk)
            while chunk:
                try:
                    output = decompressor.decompress(chunk)
                except zlib.error as exc:
                    raise DerivationError("invalid_gzip", str(exc)) from exc
                if output:
                    decompressed_written += len(output)
                    if decompressed_written > max_output_bytes:
                        raise DerivationError("decompression_limit_exceeded")
                    if decompressed_written / max(compressed_read, 1) > max_expansion_ratio:
                        raise DerivationError("expansion_ratio_exceeded")
                    dst.write(output)
                    digest.update(output)
                if decompressor.eof:
                    gzip_members += 1
                    ended_cleanly = True
                    chunk = decompressor.unused_data
                    if chunk:
                        if len(chunk) >= 2 and chunk[:2] == b"\x1f\x8b":
                            ended_cleanly = False
                            decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
                            continue
                        raise DerivationError("invalid_gzip", "trailing malformed gzip data")
                    break
                chunk = b""
        if gzip_members == 0 or not ended_cleanly:
            raise DerivationError("truncated_gzip")
        dst.flush()
        os.fsync(dst.fileno())
    return {
        "output_sha256": digest.hexdigest(),
        "compressed_bytes_read": source.stat().st_size,
        "decompressed_bytes_written": decompressed_written,
        "gzip_member_count": gzip_members,
        "multiple_gzip_members": gzip_members > 1,
    }


def promote(staging_file: Path, sha256: str, *, root: Path) -> tuple[Path, bool]:
    destination = derived_content_addressed_path(sha256, ".tif", root=root)
    base = (root / DERIVED_ROOT).resolve()
    if destination.exists():
        if destination.is_symlink() or sha256_file(destination) != sha256:
            raise DerivationError("derived_artifact_conflict")
        staging_file.unlink(missing_ok=True)
        return destination, True
    if not destination.parent.resolve().is_relative_to(base):
        raise DerivationError("derived_store_error")
    if staging_file.is_symlink() or not staging_file.is_file():
        raise DerivationError("derived_store_error")
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging_file, destination)
    try:
        fd = os.open(str(destination.parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass
    return destination, False


def compute_derived_artifact_receipt_sha256(receipt: dict[str, Any], *, root: Path | None = None) -> str:
    return hash_contract(receipt, root=root, exclude={"derived_artifact_receipt_contract_sha256", "started_at_utc", "finished_at_utc", "duration_ms"})


def write_plan(plan: dict[str, Any], *, root: Path | None = None) -> Path:
    root = repo_root(root)
    run_dir = root / REPORT_ROOT / f"fr_deriv_plan_{plan['derivation_plan_contract_sha256'][:12]}"
    write_json(run_dir / "derivation_plan.json", plan)
    return run_dir / "derivation_plan.json"


def run_derivation(
    artifact_sha256: str,
    *,
    operation: str = "gzip-decompress",
    allow_derivation: bool = False,
    approve_plan_sha256: str | None = None,
    max_output_bytes: int = 1_073_741_824,
    max_expansion_ratio: float = 500,
    root: Path | None = None,
) -> dict[str, Any]:
    root = repo_root(root)
    started = utc_now()
    start = time.monotonic()
    run_id = f"fr_deriv_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:12]}"
    run_dir = root / REPORT_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    plan = build_derivation_plan(artifact_sha256, operation=operation, max_output_bytes=max_output_bytes, max_expansion_ratio=max_expansion_ratio, root=root)
    write_json(run_dir / "derivation_plan.json", plan)
    source = source_path(artifact_sha256, root=root)
    staging_file = root / STAGING_ROOT / run_id / f"{artifact_sha256}.tif.part"
    stats: dict[str, Any] = {}
    validation: dict[str, Any] = {"validation_status": "NOT_RUN"}
    output_path: Path | None = None
    reused = False
    failure_class = None
    errors: list[str] = []
    status = "failed"
    complete_output = False
    atomic_commit = False
    try:
        if not allow_derivation:
            raise DerivationError("approval_required")
        if approve_plan_sha256 != plan["derivation_plan_contract_sha256"]:
            raise DerivationError("plan_hash_mismatch")
        if not source.exists():
            raise DerivationError("source_artifact_missing")
        if source.is_symlink() or not source.is_file():
            raise DerivationError("source_artifact_not_regular")
        if sha256_file(source) != artifact_sha256:
            raise DerivationError("source_artifact_integrity_failed")
        staging_file.parent.mkdir(parents=True, exist_ok=True)
        if staging_file.exists() and staging_file.is_symlink():
            raise DerivationError("derived_store_error")
        stats = decompress_stream(source, staging_file, max_output_bytes=max_output_bytes, max_expansion_ratio=max_expansion_ratio)
        validation = validate_geotiff(staging_file)
        if validation["validation_status"] != "PASS":
            raise DerivationError("invalid_gzip", "decompressed output failed GeoTIFF validation")
        output_path, reused = promote(staging_file, stats["output_sha256"], root=root)
        status = "completed"
        complete_output = True
        atomic_commit = True
    except DerivationError as exc:
        failure_class = exc.failure_class
        errors.append(exc.failure_class)
        staging_file.unlink(missing_ok=True)
    finished = utc_now()
    entry = source_catalog_entry(artifact_sha256, root)
    receipt = {
        "schema_version": 1,
        "derivation_run_id": run_id,
        "operation": "gzip_decompress",
        "operation_status": status,
        "source_artifact_sha256": artifact_sha256,
        "source_artifact_size_bytes": source.stat().st_size if source.exists() and source.is_file() and not source.is_symlink() else None,
        "source_artifact_logical_path": logical(source, root),
        "derivation_plan_contract_sha256": plan["derivation_plan_contract_sha256"],
        "compressed_bytes_read": stats.get("compressed_bytes_read", 0),
        "decompressed_bytes_written": stats.get("decompressed_bytes_written", 0),
        "expansion_ratio": round(stats.get("decompressed_bytes_written", 0) / max(stats.get("compressed_bytes_read", 0), 1), 8),
        "gzip_member_count": stats.get("gzip_member_count", 0),
        "multiple_gzip_members": stats.get("multiple_gzip_members", False),
        "output_format": "geotiff",
        "output_extension": ".tif",
        "output_sha256": stats.get("output_sha256"),
        "output_sha256_short": (stats.get("output_sha256") or "")[:12],
        "output_size_bytes": stats.get("decompressed_bytes_written", 0),
        "output_logical_path": logical(output_path, root) if output_path else None,
        "reused_existing_artifact": reused,
        "complete_output": complete_output,
        "atomic_commit_completed": atomic_commit,
        "validation_status": validation.get("validation_status"),
        "validation": validation,
        "failure_class": failure_class,
        "warnings": [],
        "errors": errors,
        "started_at_utc": started,
        "finished_at_utc": finished,
        "duration_ms": int((time.monotonic() - start) * 1000),
        "derived_artifact_receipt_contract_sha256": "",
        "provenance": {
            "source_artifact_receipt_sha256": source_receipt_hash(entry, root),
            "source_artifact_catalog_sha256": source_catalog(root).get("catalog_contract_sha256"),
            "faster_raster_version": __version__,
            "derivation_implementation_version": IMPLEMENTATION_VERSION,
        },
    }
    receipt["derived_artifact_receipt_contract_sha256"] = compute_derived_artifact_receipt_sha256(receipt, root=root)
    write_json(run_dir / "derivation_run_receipt.json", receipt)
    write_json(run_dir / "derived_artifact_receipts.json", [receipt])
    verification = verify_derivation_receipt(receipt, root=root)
    write_json(run_dir / "derivation_verification.json", verification)
    write_jsonl(run_dir / "execution_log.jsonl", [{"event_type": "derivation_completed", "derivation_run_id": run_id, "status": status, "timestamp_utc": finished}])
    write_json(run_dir / "safety_events.json", {"events": [] if status == "completed" else [{"failure_class": failure_class}]})
    pointer = {"derivation_run_id": run_id, "receipt_path": logical(run_dir / "derivation_run_receipt.json", root), "operation_status": status, "updated_at_utc": finished}
    write_json(root / REPORT_ROOT / "latest_derivation.json", pointer)
    if status == "completed":
        write_json(root / REPORT_ROOT / "latest_successful_derivation.json", pointer)
    return {"plan": plan, "receipt": receipt, "verification": verification, "run_dir": str(run_dir)}


def latest_successful_receipt_path(root: Path | None = None) -> Path:
    root = repo_root(root)
    pointer = json.loads((root / REPORT_ROOT / "latest_successful_derivation.json").read_text(encoding="utf-8"))
    return root / pointer["receipt_path"]


def latest_receipt_path(root: Path | None = None) -> Path:
    root = repo_root(root)
    pointer = json.loads((root / REPORT_ROOT / "latest_derivation.json").read_text(encoding="utf-8"))
    return root / pointer["receipt_path"]


def verify_derivation_receipt(receipt: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    root = repo_root(root)
    failures: list[str] = []
    checks: list[dict[str, Any]] = []
    computed = compute_derived_artifact_receipt_sha256(receipt, root=root)
    if computed != receipt.get("derived_artifact_receipt_contract_sha256"):
        failures.append("receipt tampering detected")
    out = root / (receipt.get("output_logical_path") or "")
    if not out.is_file():
        failures.append("derived artifact missing")
    elif out.is_symlink():
        failures.append("derived artifact symlink")
    else:
        if sha256_file(out) != receipt.get("output_sha256"):
            failures.append("derived artifact checksum mismatch")
        if out.stat().st_size != receipt.get("output_size_bytes"):
            failures.append("derived artifact size mismatch")
        if not out.resolve().is_relative_to((root / DERIVED_ROOT).resolve()):
            failures.append("derived artifact outside derived store")
    src = root / receipt.get("source_artifact_logical_path", "")
    if not src.is_file() or sha256_file(src) != receipt.get("source_artifact_sha256"):
        failures.append("source lineage failed")
    checks.append({"name": "receipt_hash", "status": "PASS" if "receipt tampering detected" not in failures else "FAIL"})
    checks.append({"name": "derived_artifact", "status": "PASS" if not any("derived artifact" in item for item in failures) else "FAIL"})
    checks.append({"name": "source_lineage", "status": "PASS" if not any("source lineage" in item for item in failures) else "FAIL"})
    return {
        "verification_status": "PASS" if not failures else "FAIL",
        "identity_verification_status": "PASS" if "receipt tampering detected" not in failures else "FAIL",
        "lineage_verification_status": "PASS" if not any("source lineage" in item for item in failures) else "FAIL",
        "derived_store_verification_status": "PASS" if not any("derived artifact" in item for item in failures) else "FAIL",
        "failures": failures,
        "warnings": [],
        "checks": checks,
        "check_count": len(checks),
        "passed_check_count": sum(1 for item in checks if item["status"] == "PASS"),
        "failed_check_count": sum(1 for item in checks if item["status"] == "FAIL"),
    }
