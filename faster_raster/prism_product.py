from __future__ import annotations

import hashlib
import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


PRISM_SOURCE_ID = "prism_daily_ppt_static_zip"
PRODUCT_PROFILE_VERSION = 1
DEFAULT_MAX_MEMBERS = 64
DEFAULT_MAX_MEMBER_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_TOTAL_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_EXPANSION_RATIO = 200.0
_ALLOWED_COMPRESSION_METHODS = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
_DATE_RE = re.compile(r"^(?:\d{4}-\d{2}-\d{2}|\d{8})$")
_MEMBER_STEM_RE = re.compile(r"^prism_ppt_us_25m_(\d{8})$")


class PrismProductError(ValueError):
    """Raised when a PRISM archive violates the public product contract."""


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _normalize_date(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not _DATE_RE.fullmatch(text):
        raise PrismProductError("invalid_prism_temporal_key")
    return text.replace("-", "")


def expected_archive_stem(temporal_key: str) -> str:
    return f"prism_ppt_us_25m_{_normalize_date(temporal_key)}"


def _compression_name(method: int) -> str:
    return {
        zipfile.ZIP_STORED: "stored",
        zipfile.ZIP_DEFLATED: "deflated",
        zipfile.ZIP_BZIP2: "bzip2",
        zipfile.ZIP_LZMA: "lzma",
    }.get(method, f"unknown_{method}")


def _member_role(name: str, stem: str) -> str:
    base = PurePosixPath(name).name
    candidates = {
        f"{stem}.tif": "primary_cog_raster",
        f"{stem}.prj": "projection",
        f"{stem}.stx": "statistics",
        f"{stem}.xml": "fgdc_metadata",
        f"{stem}.tif.xml": "fgdc_metadata",
        f"{stem}.aux.xml": "esri_aux_metadata",
        f"{stem}.tif.aux.xml": "esri_aux_metadata",
        f"{stem}.info.txt": "processing_info",
        f"{stem}.stn.csv": "station_inventory",
    }
    return {key.casefold(): value for key, value in candidates.items()}.get(
        base.casefold(),
        "ancillary_unknown",
    )


def _validate_member_path(name: str) -> PurePosixPath:
    if not name or "\\" in name or "\x00" in name:
        raise PrismProductError("unsafe_zip_member_path")
    if any(ord(char) < 32 for char in name):
        raise PrismProductError("unsafe_zip_member_path")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise PrismProductError("unsafe_zip_member_path")
    if path.parts and (":" in path.parts[0] or path.parts[0] in {"", "."}):
        raise PrismProductError("unsafe_zip_member_path")
    return path


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def inspect_prism_archive(
    archive_path: Path,
    *,
    temporal_key: str | None = None,
    logical_archive_name: str | None = None,
    max_members: int = DEFAULT_MAX_MEMBERS,
    max_member_uncompressed_bytes: int = DEFAULT_MAX_MEMBER_UNCOMPRESSED_BYTES,
    max_total_uncompressed_bytes: int = DEFAULT_MAX_TOTAL_UNCOMPRESSED_BYTES,
    max_expansion_ratio: float = DEFAULT_MAX_EXPANSION_RATIO,
    verify_crc: bool = True,
) -> dict[str, Any]:
    """Inspect a complete PRISM daily precipitation ZIP without extracting it.

    The function verifies archive safety, bounded expansion, member CRCs, naming,
    and the presence of exactly one date-matched GeoTIFF. It intentionally does
    not claim raster decode, COG conformance, spatial metadata, or scientific
    validity; those are downstream stages.
    """

    archive_path = Path(archive_path)
    if not archive_path.is_file() or archive_path.is_symlink():
        raise PrismProductError("prism_archive_missing_or_invalid")
    if max_members <= 0:
        raise PrismProductError("invalid_member_limit")
    if max_member_uncompressed_bytes <= 0 or max_total_uncompressed_bytes <= 0:
        raise PrismProductError("invalid_uncompressed_byte_limit")
    if max_expansion_ratio <= 0:
        raise PrismProductError("invalid_expansion_ratio_limit")

    expected_date = _normalize_date(temporal_key)
    archive_name = logical_archive_name or archive_path.name
    archive_match = re.fullmatch(r"prism_ppt_us_25m_(\d{8})\.zip", archive_name)
    archive_date = archive_match.group(1) if archive_match else None
    effective_date = expected_date or archive_date
    if effective_date is None:
        raise PrismProductError("prism_archive_name_missing_date")
    if expected_date and archive_date and expected_date != archive_date:
        raise PrismProductError("prism_archive_date_mismatch")
    stem = expected_archive_stem(effective_date)

    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            if not infos:
                raise PrismProductError("empty_prism_archive")
            if len(infos) > max_members:
                raise PrismProductError("prism_archive_member_limit_exceeded")

            seen_names: set[str] = set()
            inventory: list[dict[str, Any]] = []
            total_compressed = 0
            total_uncompressed = 0
            maximum_ratio = 0.0

            for info in infos:
                path = _validate_member_path(info.filename)
                normalized_name = path.as_posix().casefold()
                if normalized_name in seen_names:
                    raise PrismProductError("duplicate_prism_archive_member")
                seen_names.add(normalized_name)
                if info.flag_bits & 0x1:
                    raise PrismProductError("encrypted_prism_archive_member")
                if _is_symlink(info):
                    raise PrismProductError("symlink_prism_archive_member")
                if info.compress_type not in _ALLOWED_COMPRESSION_METHODS:
                    raise PrismProductError("unsupported_prism_zip_compression")
                if info.is_dir() and info.file_size:
                    raise PrismProductError("invalid_prism_directory_member")
                if info.file_size > max_member_uncompressed_bytes:
                    raise PrismProductError("prism_member_uncompressed_limit_exceeded")

                ratio = info.file_size / max(info.compress_size, 1)
                if ratio > max_expansion_ratio:
                    raise PrismProductError("prism_member_expansion_ratio_exceeded")
                total_compressed += info.compress_size
                total_uncompressed += info.file_size
                maximum_ratio = max(maximum_ratio, ratio)
                if total_uncompressed > max_total_uncompressed_bytes:
                    raise PrismProductError("prism_archive_uncompressed_limit_exceeded")

                inventory.append(
                    {
                        "member_name": path.as_posix(),
                        "basename": path.name,
                        "role": "directory" if info.is_dir() else _member_role(path.as_posix(), stem),
                        "is_directory": info.is_dir(),
                        "compressed_bytes": info.compress_size,
                        "uncompressed_bytes": info.file_size,
                        "crc32": f"{info.CRC:08x}",
                        "compression_method": _compression_name(info.compress_type),
                    }
                )

            bad_member = archive.testzip() if verify_crc else None
            if bad_member:
                raise PrismProductError("prism_archive_crc_failure")
    except zipfile.BadZipFile as exc:
        raise PrismProductError("corrupt_prism_archive") from exc

    inventory = sorted(inventory, key=lambda item: item["member_name"])
    primary = [item for item in inventory if item["role"] == "primary_cog_raster"]
    any_tiffs = [
        item for item in inventory
        if not item["is_directory"] and item["basename"].lower().endswith((".tif", ".tiff"))
    ]
    if len(primary) != 1:
        raise PrismProductError("prism_primary_raster_missing_or_ambiguous")
    if len(any_tiffs) != 1:
        raise PrismProductError("unexpected_additional_prism_raster")

    roles = sorted({item["role"] for item in inventory if item["role"] != "directory"})
    expected_ancillary_roles = {
        "projection",
        "statistics",
        "fgdc_metadata",
        "esri_aux_metadata",
        "processing_info",
    }
    missing_ancillary = sorted(expected_ancillary_roles - set(roles))
    unknown_members = [item["member_name"] for item in inventory if item["role"] == "ancillary_unknown"]
    completeness = "complete" if not missing_ancillary and not unknown_members else "primary_raster_verified_with_ancillary_variance"

    inventory_contract = {
        "product_profile_version": PRODUCT_PROFILE_VERSION,
        "source_id": PRISM_SOURCE_ID,
        "variable": "ppt",
        "region": "us",
        "resolution": "25m",
        "temporal_key": effective_date,
        "archive_stem": stem,
        "members": inventory,
    }
    inventory_sha256 = hashlib.sha256(_stable_json(inventory_contract).encode("utf-8")).hexdigest()

    return {
        "product_profile_version": PRODUCT_PROFILE_VERSION,
        "product_validation_status": "PASS",
        "source_id": PRISM_SOURCE_ID,
        "provider": "PRISM Group at Oregon State University",
        "variable": "ppt",
        "units": "millimeters",
        "region": "CONUS",
        "resolution_label": "4km",
        "resolution_code": "25m",
        "temporal_granularity": "daily",
        "temporal_key": effective_date,
        "archive_name": archive_name,
        "archive_stem": stem,
        "archive_object_size_bytes": archive_path.stat().st_size,
        "archive_member_count": len(inventory),
        "compressed_member_total_bytes": total_compressed,
        "uncompressed_member_total_bytes": total_uncompressed,
        "maximum_member_expansion_ratio": round(maximum_ratio, 6),
        "crc_verification_status": "PASS" if verify_crc else "NOT_RUN",
        "safe_paths_status": "PASS",
        "encryption_status": "NONE",
        "symlink_status": "NONE",
        "extraction_performed": False,
        "inventory": inventory,
        "inventory_sha256": inventory_sha256,
        "primary_raster_member": primary[0]["member_name"],
        "primary_raster_format": "GeoTIFF",
        "expected_cloud_optimized_geotiff": True,
        "cog_structure_validated": False,
        "ancillary_roles_present": roles,
        "missing_expected_ancillary_roles": missing_ancillary,
        "unknown_members": unknown_members,
        "profile_completeness": completeness,
        "harmonization_readiness": "archive_profile_verified_raster_decode_pending",
        "next_validation_stage": "rasterio_open_and_cog_structure_validation",
    }
