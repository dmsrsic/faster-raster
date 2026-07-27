from __future__ import annotations

import binascii
import hashlib
import json
import math
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from xml.etree import ElementTree

import numpy as np
import rasterio
from rasterio.crs import CRS

from faster_raster import artifact_store
from faster_raster.prism_product import (
    PRISM_SOURCE_ID,
    PrismProductError,
    inspect_prism_archive,
)


RASTER_PROFILE_VERSION = 1
RASTER_RECEIPT_VERSION = 1
DEFAULT_MAX_EXTRACTED_RASTER_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_METADATA_MEMBER_BYTES = 2 * 1024 * 1024
EXPECTED_WIDTH = 1405
EXPECTED_HEIGHT = 621
EXPECTED_EPSG = 4269
EXPECTED_NODATA = -9999.0
EXPECTED_RESOLUTION_DEGREES = 0.041666666667
EXPECTED_TRANSFORM = (
    0.041666666667,
    0.0,
    -125.0208333333335,
    0.0,
    -0.041666666667,
    49.9375000000005,
)
EXPECTED_BOUNDS = (
    -125.0208333333335,
    24.062499999793495,
    -66.4791666661985,
    49.9375000000005,
)
_ALLOWED_COG_COMPRESSION = {"LZW", "DEFLATE", "ZSTD"}
_REQUIRED_PRISM_TAGS = {
    "PRISM_CODE_VERSION",
    "PRISM_DATASET_CREATE_DATE",
    "PRISM_DATASET_FILENAME",
    "PRISM_DATASET_REMARKS",
    "PRISM_DATASET_TYPE",
    "PRISM_DATASET_VERSION",
}


class PrismRasterError(ValueError):
    """Raised when a decoded PRISM raster violates the product contract."""


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _close(left: float, right: float, tolerance: float) -> bool:
    return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=tolerance)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise PrismRasterError(code)


def _profile_member(profile: Mapping[str, Any], role: str) -> dict[str, Any]:
    matches = [item for item in profile.get("inventory", []) if item.get("role") == role]
    if len(matches) != 1:
        raise PrismRasterError(f"prism_{role}_member_missing_or_ambiguous")
    return dict(matches[0])


def _read_zip_member(
    archive: zipfile.ZipFile,
    member_name: str,
    *,
    max_bytes: int = DEFAULT_MAX_METADATA_MEMBER_BYTES,
) -> bytes:
    try:
        info = archive.getinfo(member_name)
    except KeyError as exc:
        raise PrismRasterError("prism_metadata_member_missing") from exc
    if info.file_size > max_bytes:
        raise PrismRasterError("prism_metadata_member_limit_exceeded")
    with archive.open(info, "r") as source:
        payload = source.read(max_bytes + 1)
    if len(payload) > max_bytes or len(payload) != info.file_size:
        raise PrismRasterError("prism_metadata_member_size_mismatch")
    return payload


def _decode_text(payload: bytes, code: str) -> str:
    if b"\x00" in payload:
        raise PrismRasterError(code)
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return payload.decode("latin-1")
        except UnicodeDecodeError as exc:
            raise PrismRasterError(code) from exc


def _parse_info(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            raise PrismRasterError("invalid_prism_processing_info")
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip()
    return result


def _parse_stx(text: str) -> dict[str, float | int]:
    rows = [line.split() for line in text.splitlines() if line.strip()]
    if len(rows) != 1 or len(rows[0]) != 5:
        raise PrismRasterError("invalid_prism_statistics_sidecar")
    try:
        band = int(rows[0][0])
        minimum, maximum, mean, stddev = (float(value) for value in rows[0][1:])
    except ValueError as exc:
        raise PrismRasterError("invalid_prism_statistics_sidecar") from exc
    if band != 1:
        raise PrismRasterError("unexpected_prism_statistics_band")
    return {
        "band": band,
        "minimum": minimum,
        "maximum": maximum,
        "mean": mean,
        "stddev": stddev,
    }


def _xml_root(payload: bytes, code: str) -> ElementTree.Element:
    upper = payload[:4096].upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise PrismRasterError("unsafe_prism_xml")
    try:
        return ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise PrismRasterError(code) from exc


def _find_text(root: ElementTree.Element, path: str, code: str) -> str:
    value = root.findtext(path)
    if value is None or not value.strip():
        raise PrismRasterError(code)
    return value.strip()


def _parse_fgdc(payload: bytes) -> dict[str, Any]:
    root = _xml_root(payload, "invalid_prism_fgdc_metadata")
    try:
        return {
            "title": _find_text(root, "./idinfo/citation/citeinfo/title", "prism_metadata_title_missing"),
            "begin_date": _find_text(root, "./idinfo/timeperd/timeinfo/rngdates/begdate", "prism_metadata_date_missing"),
            "end_date": _find_text(root, "./idinfo/timeperd/timeinfo/rngdates/enddate", "prism_metadata_date_missing"),
            "west": float(_find_text(root, "./idinfo/spdom/bounding/westbc", "prism_metadata_bounds_missing")),
            "east": float(_find_text(root, "./idinfo/spdom/bounding/eastbc", "prism_metadata_bounds_missing")),
            "north": float(_find_text(root, "./idinfo/spdom/bounding/northbc", "prism_metadata_bounds_missing")),
            "south": float(_find_text(root, "./idinfo/spdom/bounding/southbc", "prism_metadata_bounds_missing")),
            "latitude_resolution": float(_find_text(root, "./spref/horizsys/geograph/latres", "prism_metadata_resolution_missing")),
            "longitude_resolution": float(_find_text(root, "./spref/horizsys/geograph/longres", "prism_metadata_resolution_missing")),
            "horizontal_datum": _find_text(root, "./spref/horizsys/geodetic/horizdn", "prism_metadata_datum_missing"),
            "units": _find_text(root, "./eainfo/detailed/attr/attrdomv/rdom/attrunit", "prism_metadata_units_missing"),
            "domain_minimum": float(_find_text(root, "./eainfo/detailed/attr/attrdomv/rdom/rdommin", "prism_metadata_domain_missing")),
            "domain_maximum": float(_find_text(root, "./eainfo/detailed/attr/attrdomv/rdom/rdommax", "prism_metadata_domain_missing")),
        }
    except ValueError as exc:
        raise PrismRasterError("invalid_prism_fgdc_metadata") from exc


def _parse_aux_xml(payload: bytes) -> dict[str, Any]:
    root = _xml_root(payload, "invalid_prism_aux_metadata")
    nodata = _find_text(root, "./PAMRasterBand/NoDataValue", "prism_aux_nodata_missing")
    metadata = {
        item.attrib.get("key", ""): (item.text or "").strip()
        for item in root.findall("./PAMRasterBand/Metadata/MDI")
    }
    try:
        nodata_value = float(nodata)
    except ValueError as exc:
        raise PrismRasterError("invalid_prism_aux_metadata") from exc
    return {"nodata": nodata_value, "metadata": dict(sorted(metadata.items()))}


def _stream_statistics(dataset: rasterio.io.DatasetReader) -> dict[str, Any]:
    nodata = dataset.nodata
    total_count = dataset.width * dataset.height
    valid_count = 0
    nodata_count = 0
    nonfinite_count = 0
    zero_count = 0
    negative_count = 0
    minimum = math.inf
    maximum = -math.inf
    total = 0.0
    total_squares = 0.0

    for _, window in dataset.block_windows(1):
        values = dataset.read(indexes=(1,), window=window, masked=False)[0]
        if nodata is None:
            nodata_mask = np.zeros(values.shape, dtype=bool)
        else:
            nodata_mask = values == nodata
        nodata_count += int(np.count_nonzero(nodata_mask))
        candidates = values[~nodata_mask]
        finite_mask = np.isfinite(candidates)
        nonfinite_count += int(candidates.size - np.count_nonzero(finite_mask))
        valid = candidates[finite_mask].astype(np.float64, copy=False)
        if not valid.size:
            continue
        valid_count += int(valid.size)
        zero_count += int(np.count_nonzero(valid == 0.0))
        negative_count += int(np.count_nonzero(valid < 0.0))
        minimum = min(minimum, float(valid.min()))
        maximum = max(maximum, float(valid.max()))
        total += float(valid.sum(dtype=np.float64))
        total_squares += float(np.square(valid, dtype=np.float64).sum(dtype=np.float64))

    _require(valid_count > 0, "prism_raster_has_no_valid_pixels")
    _require(valid_count + nodata_count + nonfinite_count == total_count, "prism_raster_pixel_accounting_mismatch")
    mean = total / valid_count
    variance = max(total_squares / valid_count - mean * mean, 0.0)
    return {
        "total_pixel_count": total_count,
        "valid_pixel_count": valid_count,
        "nodata_pixel_count": nodata_count,
        "nonfinite_pixel_count": nonfinite_count,
        "zero_pixel_count": zero_count,
        "negative_pixel_count": negative_count,
        "minimum": round(minimum, 6),
        "maximum": round(maximum, 6),
        "mean": round(mean, 6),
        "stddev_population": round(math.sqrt(variance), 6),
        "gdal_checksum": int(dataset.checksum(1)),
    }


def inspect_prism_raster(
    raster_path: Path,
    *,
    archive_path: Path,
    product_profile: Mapping[str, Any] | None = None,
    temporal_key: str | None = None,
) -> dict[str, Any]:
    """Decode and validate the selected PRISM COG and its provider sidecars."""

    raster_path = Path(raster_path)
    archive_path = Path(archive_path)
    if not raster_path.is_file() or raster_path.is_symlink():
        raise PrismRasterError("prism_raster_missing_or_invalid")
    if not archive_path.is_file() or archive_path.is_symlink():
        raise PrismRasterError("prism_archive_missing_or_invalid")

    try:
        profile = dict(product_profile or inspect_prism_archive(archive_path, temporal_key=temporal_key))
    except PrismProductError as exc:
        raise PrismRasterError(str(exc)) from exc
    _require(profile.get("product_validation_status") == "PASS", "prism_archive_profile_not_validated")
    effective_date = str(temporal_key or profile.get("temporal_key") or "").replace("-", "")
    _require(bool(re.fullmatch(r"\d{8}", effective_date)), "invalid_prism_temporal_key")

    primary = _profile_member(profile, "primary_cog_raster")
    projection = _profile_member(profile, "projection")
    statistics_member = _profile_member(profile, "statistics")
    metadata_member = _profile_member(profile, "fgdc_metadata")
    aux_member = _profile_member(profile, "esri_aux_metadata")
    info_member = _profile_member(profile, "processing_info")

    try:
        with zipfile.ZipFile(archive_path) as archive:
            prj_text = _decode_text(_read_zip_member(archive, projection["member_name"]), "invalid_prism_projection_sidecar")
            stx = _parse_stx(_decode_text(_read_zip_member(archive, statistics_member["member_name"]), "invalid_prism_statistics_sidecar"))
            fgdc = _parse_fgdc(_read_zip_member(archive, metadata_member["member_name"]))
            aux = _parse_aux_xml(_read_zip_member(archive, aux_member["member_name"]))
            processing_info = _parse_info(_decode_text(_read_zip_member(archive, info_member["member_name"]), "invalid_prism_processing_info"))
    except zipfile.BadZipFile as exc:
        raise PrismRasterError("corrupt_prism_archive") from exc

    try:
        projection_crs = CRS.from_wkt(prj_text)
    except Exception as exc:
        raise PrismRasterError("invalid_prism_projection_sidecar") from exc

    try:
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
            with rasterio.open(raster_path) as dataset:
                transform = tuple(float(value) for value in tuple(dataset.transform)[:6])
                bounds = tuple(float(value) for value in dataset.bounds)
                image_structure = dict(sorted(dataset.tags(ns="IMAGE_STRUCTURE").items()))
                dataset_tags = dict(sorted(dataset.tags().items()))
                band_tags = dict(sorted(dataset.tags(1).items()))
                overviews = list(dataset.overviews(1))
                block_shapes = [list(shape) for shape in dataset.block_shapes]
                statistics = _stream_statistics(dataset)

                _require(dataset.driver == "GTiff", "prism_primary_raster_not_geotiff")
                _require(dataset.count == 1, "unexpected_prism_raster_band_count")
                _require(tuple(dataset.dtypes) == ("float32",), "unexpected_prism_raster_dtype")
                _require(dataset.nodata is not None and _close(dataset.nodata, EXPECTED_NODATA, 1e-6), "unexpected_prism_raster_nodata")
                _require(dataset.crs is not None and dataset.crs.to_epsg() == EXPECTED_EPSG, "unexpected_prism_raster_crs")
                _require(projection_crs == dataset.crs or projection_crs.to_epsg() == dataset.crs.to_epsg(), "prism_projection_sidecar_mismatch")
                _require(dataset.width == EXPECTED_WIDTH and dataset.height == EXPECTED_HEIGHT, "unexpected_prism_raster_dimensions")
                _require(all(_close(actual, expected, 1e-10) for actual, expected in zip(transform, EXPECTED_TRANSFORM)), "unexpected_prism_raster_transform")
                _require(all(_close(actual, expected, 2e-6) for actual, expected in zip(bounds, EXPECTED_BOUNDS)), "unexpected_prism_raster_bounds")
                _require(_close(abs(dataset.res[0]), EXPECTED_RESOLUTION_DEGREES, 1e-10), "unexpected_prism_raster_resolution")
                _require(_close(abs(dataset.res[1]), EXPECTED_RESOLUTION_DEGREES, 1e-10), "unexpected_prism_raster_resolution")
                _require(bool(dataset.profile.get("tiled")), "prism_raster_not_tiled")
                _require(len(block_shapes) == 1, "unexpected_prism_raster_block_layout")
                block_height, block_width = block_shapes[0]
                _require(block_width > 0 and block_height > 0 and block_width <= 512 and block_height <= 512, "unexpected_prism_raster_block_layout")
                _require((block_width & (block_width - 1)) == 0 and (block_height & (block_height - 1)) == 0, "unexpected_prism_raster_block_layout")
                _require(image_structure.get("LAYOUT") == "COG", "prism_cog_layout_not_declared")
                compression = image_structure.get("COMPRESSION") or str(dataset.compression or "").split(".")[-1].upper()
                _require(compression.upper() in _ALLOWED_COG_COMPRESSION, "unexpected_prism_raster_compression")
                _require(overviews == sorted(set(overviews)) and all(value > 1 for value in overviews), "invalid_prism_raster_overviews")
                _require(overviews or max(dataset.width, dataset.height) <= 512, "prism_cog_overviews_missing")
                _require(_REQUIRED_PRISM_TAGS.issubset(dataset_tags), "prism_raster_provider_tags_missing")
                _require(effective_date in dataset_tags["PRISM_DATASET_FILENAME"], "prism_raster_provider_date_mismatch")
                _require(statistics["nonfinite_pixel_count"] == 0, "prism_raster_nonfinite_values")
                _require(statistics["negative_pixel_count"] == 0, "prism_raster_negative_precipitation")

                embedded_statistics = {
                    "minimum": float(band_tags["STATISTICS_MINIMUM"]),
                    "maximum": float(band_tags["STATISTICS_MAXIMUM"]),
                    "mean": float(band_tags["STATISTICS_MEAN"]),
                    "stddev": float(band_tags["STATISTICS_STDDEV"]),
                    # STATISTICS_NNULL is not a standard GDAL statistics tag.
                    # The provider .stx sidecar and streamed pixel accounting
                    # remain mandatory and authoritative when it is absent.
                    "nnull": (
                        int(band_tags["STATISTICS_NNULL"])
                        if "STATISTICS_NNULL" in band_tags
                        else None
                    ),
                }
    except rasterio.errors.RasterioIOError as exc:
        raise PrismRasterError("prism_raster_decode_failed") from exc
    except PrismRasterError:
        raise
    except (KeyError, ValueError) as exc:
        raise PrismRasterError("invalid_prism_raster_statistics_tags") from exc

    _require(fgdc["begin_date"] == effective_date and fgdc["end_date"] == effective_date, "prism_metadata_date_mismatch")
    _require("precipitation" in fgdc["title"].casefold(), "prism_metadata_variable_mismatch")
    _require("cog" in fgdc["title"].casefold(), "prism_metadata_cog_claim_missing")
    _require(fgdc["units"].casefold() in {"millimeter", "millimeters", "millimetre", "millimetres"}, "prism_metadata_units_mismatch")
    _require("north american datum of 1983" in fgdc["horizontal_datum"].casefold(), "prism_metadata_datum_mismatch")
    fgdc_bounds = (fgdc["west"], fgdc["south"], fgdc["east"], fgdc["north"])
    _require(all(_close(actual, expected, 2e-6) for actual, expected in zip(bounds, fgdc_bounds)), "prism_metadata_bounds_mismatch")
    _require(_close(fgdc["longitude_resolution"], abs(transform[0]), 1e-8), "prism_metadata_resolution_mismatch")
    _require(_close(fgdc["latitude_resolution"], abs(transform[4]), 1e-8), "prism_metadata_resolution_mismatch")
    _require(_close(aux["nodata"], EXPECTED_NODATA, 1e-6), "prism_aux_nodata_mismatch")
    _require(statistics["maximum"] <= fgdc["domain_maximum"] + 1e-6, "prism_raster_above_metadata_domain")
    _require(statistics["minimum"] >= fgdc["domain_minimum"] - 1e-6, "prism_raster_below_metadata_domain")

    for key in _REQUIRED_PRISM_TAGS:
        _require(processing_info.get(key) == dataset_tags.get(key), "prism_processing_info_tag_mismatch")

    if embedded_statistics["nnull"] is not None:
        _require(
            embedded_statistics["nnull"] == statistics["nodata_pixel_count"],
            "prism_embedded_nodata_count_mismatch",
        )
    for key in ("minimum", "maximum", "mean", "stddev"):
        computed_key = "stddev_population" if key == "stddev" else key
        _require(_close(stx[key], embedded_statistics[key], 1e-4), "prism_statistics_sidecar_mismatch")
        _require(_close(stx[key], statistics[computed_key], 1e-3), "prism_computed_statistics_mismatch")

    profile_contract = {
        "raster_profile_version": RASTER_PROFILE_VERSION,
        "source_id": PRISM_SOURCE_ID,
        "temporal_key": effective_date,
        "archive_name": profile.get("archive_name"),
        "archive_inventory_sha256": profile.get("inventory_sha256"),
        "primary_raster_member": primary["member_name"],
        "raster_sha256": _sha256_file(raster_path),
        "raster_size_bytes": raster_path.stat().st_size,
        "driver": "GTiff",
        "width": EXPECTED_WIDTH,
        "height": EXPECTED_HEIGHT,
        "band_count": 1,
        "dtype": "float32",
        "nodata": EXPECTED_NODATA,
        "epsg": EXPECTED_EPSG,
        "transform": list(transform),
        "bounds": list(bounds),
        "resolution": [abs(transform[0]), abs(transform[4])],
        "block_shapes": block_shapes,
        "overviews": overviews,
        "image_structure": image_structure,
        "dataset_tags": dataset_tags,
        "band_tags": band_tags,
        "computed_statistics": statistics,
        "provider_statistics": stx,
        "fgdc_metadata": fgdc,
        "aux_metadata": aux,
        "processing_info": processing_info,
    }
    profile_sha256 = hashlib.sha256(_stable_json(profile_contract).encode("utf-8")).hexdigest()
    return {
        **profile_contract,
        "raster_profile_sha256": profile_sha256,
        "raster_decode_status": "PASS",
        "cog_structure_validation_status": "PASS",
        "sidecar_consistency_status": "PASS",
        "scientific_value_domain_status": "PASS",
        "extraction_fidelity_status": "PASS",
        "units": "millimeters",
        "variable": "ppt",
        "region": "CONUS",
        "harmonization_readiness": "decoded_cog_profile_verified_aoi_harmonization_pending",
        "next_validation_stage": "aoi_subset_and_target_grid_harmonization",
    }


def _receipt_contract(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in receipt.items()
        if key not in {
            "generated_at_utc",
            "raster_receipt_contract_sha256",
            "archive_artifact_path",
            "raster_artifact_path",
        }
    }


def compute_prism_raster_receipt_sha256(receipt: Mapping[str, Any]) -> str:
    return hashlib.sha256(_stable_json(_receipt_contract(receipt)).encode("utf-8")).hexdigest()


def materialize_prism_primary_raster(
    archive_path: Path,
    *,
    temporal_key: str,
    product_profile: Mapping[str, Any] | None = None,
    artifact_root: Path = Path("cache/derived/prism/sha256"),
    staging_root: Path = Path("cache/staging/prism-raster"),
    receipt_path: Path | None = None,
    max_extracted_raster_bytes: int = DEFAULT_MAX_EXTRACTED_RASTER_BYTES,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    """Extract only the validated primary GeoTIFF and promote it content-addressably."""

    archive_path = Path(archive_path)
    if max_extracted_raster_bytes <= 0:
        raise PrismRasterError("invalid_prism_raster_byte_limit")
    try:
        profile = dict(product_profile or inspect_prism_archive(archive_path, temporal_key=temporal_key))
    except PrismProductError as exc:
        raise PrismRasterError(str(exc)) from exc
    _require(profile.get("product_validation_status") == "PASS", "prism_archive_profile_not_validated")
    primary = _profile_member(profile, "primary_cog_raster")
    declared_size = int(primary["uncompressed_bytes"])
    _require(0 < declared_size <= max_extracted_raster_bytes, "prism_primary_raster_byte_limit_exceeded")

    artifact_store.validate_artifact_root_policy(artifact_root)
    artifact_store.validate_staging_root_policy(staging_root)
    artifact_root.mkdir(parents=True, exist_ok=True)
    staging_root.mkdir(parents=True, exist_ok=True)

    archive_sha256 = _sha256_file(archive_path)
    staging_candidate = (staging_root / f"{archive_sha256[:16]}-{Path(primary['member_name']).name}.part").resolve()
    _require(staging_candidate.is_relative_to(staging_root.resolve()), "prism_raster_staging_path_escape")
    if staging_candidate.exists():
        if staging_candidate.is_symlink() or not staging_candidate.is_file():
            raise PrismRasterError("prism_raster_staging_path_invalid")
        staging_candidate.unlink()

    digest = hashlib.sha256()
    crc = 0
    bytes_written = 0
    try:
        with zipfile.ZipFile(archive_path) as archive:
            info = archive.getinfo(primary["member_name"])
            _require(info.file_size == declared_size, "prism_primary_raster_declared_size_mismatch")
            with archive.open(info, "r") as source, staging_candidate.open("xb") as destination:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    bytes_written += len(chunk)
                    if bytes_written > max_extracted_raster_bytes or bytes_written > declared_size:
                        raise PrismRasterError("prism_primary_raster_byte_limit_exceeded")
                    digest.update(chunk)
                    crc = binascii.crc32(chunk, crc)
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            _require(bytes_written == declared_size, "prism_primary_raster_extracted_size_mismatch")
            _require((crc & 0xFFFFFFFF) == info.CRC, "prism_primary_raster_crc_mismatch")
    except (zipfile.BadZipFile, KeyError) as exc:
        staging_candidate.unlink(missing_ok=True)
        raise PrismRasterError("prism_primary_raster_extraction_failed") from exc
    except Exception:
        staging_candidate.unlink(missing_ok=True)
        raise

    raster_sha256 = digest.hexdigest()
    try:
        raster_profile = inspect_prism_raster(
            staging_candidate,
            archive_path=archive_path,
            product_profile=profile,
            temporal_key=temporal_key,
        )
    except Exception:
        staging_candidate.unlink(missing_ok=True)
        raise
    raster_path, reused = artifact_store.promote_complete_artifact(
        staging_candidate,
        raster_sha256,
        ".tif",
        artifact_root=artifact_root,
    )

    receipt: dict[str, Any] = {
        "raster_receipt_version": RASTER_RECEIPT_VERSION,
        "source_id": PRISM_SOURCE_ID,
        "temporal_key": str(temporal_key).replace("-", ""),
        "generated_at_utc": generated_at_utc or _utc_now(),
        "archive_artifact_id": f"sha256:{archive_sha256}",
        "archive_artifact_path": str(archive_path.resolve()),
        "archive_whole_object_sha256": archive_sha256,
        "archive_object_size_bytes": archive_path.stat().st_size,
        "archive_inventory_sha256": profile.get("inventory_sha256"),
        "selected_member": primary["member_name"],
        "selected_member_crc32": primary["crc32"],
        "selected_member_uncompressed_bytes": declared_size,
        "streamed_extraction": True,
        "extracted_size_match": True,
        "extracted_crc_match": True,
        "raster_artifact_id": f"sha256:{raster_sha256}",
        "raster_artifact_path": str(raster_path.resolve()),
        "raster_sha256": raster_sha256,
        "raster_size_bytes": raster_path.stat().st_size,
        "content_addressed": True,
        "reused_existing_raster_artifact": reused,
        "raster_profile": raster_profile,
        "raster_profile_sha256": raster_profile["raster_profile_sha256"],
        "validation_status": "PASS",
    }
    receipt["raster_receipt_contract_sha256"] = compute_prism_raster_receipt_sha256(receipt)
    if receipt_path is not None:
        receipt_path = Path(receipt_path)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = receipt_path.with_name(receipt_path.name + ".tmp")
        temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, receipt_path)
    return receipt


def verify_prism_raster_receipt(receipt_or_path: Mapping[str, Any] | Path) -> dict[str, Any]:
    if isinstance(receipt_or_path, Path):
        receipt = json.loads(receipt_or_path.read_text(encoding="utf-8"))
    else:
        receipt = dict(receipt_or_path)
    failures: list[str] = []
    checks: list[dict[str, str]] = []

    def check(name: str, condition: bool, failure: str) -> None:
        checks.append({"name": name, "status": "PASS" if condition else "FAIL"})
        if not condition:
            failures.append(failure)

    check(
        "receipt_contract",
        compute_prism_raster_receipt_sha256(receipt) == receipt.get("raster_receipt_contract_sha256"),
        "PRISM raster receipt hash mismatch",
    )
    archive_path = Path(receipt.get("archive_artifact_path") or "")
    raster_path = Path(receipt.get("raster_artifact_path") or "")
    check("archive_exists", archive_path.is_file() and not archive_path.is_symlink(), "PRISM archive artifact missing")
    check("raster_exists", raster_path.is_file() and not raster_path.is_symlink(), "PRISM raster artifact missing")

    if archive_path.is_file() and not archive_path.is_symlink():
        check("archive_checksum", _sha256_file(archive_path) == receipt.get("archive_whole_object_sha256"), "PRISM archive checksum mismatch")
        check("archive_size", archive_path.stat().st_size == receipt.get("archive_object_size_bytes"), "PRISM archive size mismatch")
    if raster_path.is_file() and not raster_path.is_symlink():
        raster_sha = _sha256_file(raster_path)
        check("raster_checksum", raster_sha == receipt.get("raster_sha256"), "PRISM raster checksum mismatch")
        check("raster_size", raster_path.stat().st_size == receipt.get("raster_size_bytes"), "PRISM raster size mismatch")
        check("content_addressed_path", raster_sha in raster_path.name, "PRISM raster path is not content-addressed")

    if archive_path.is_file() and raster_path.is_file() and not archive_path.is_symlink() and not raster_path.is_symlink():
        try:
            archive_profile = inspect_prism_archive(
                archive_path,
                temporal_key=receipt.get("temporal_key"),
                logical_archive_name=(receipt.get("raster_profile") or {}).get("archive_name"),
            )
            recomputed = inspect_prism_raster(
                raster_path,
                archive_path=archive_path,
                product_profile=archive_profile,
                temporal_key=receipt.get("temporal_key"),
            )
        except (PrismProductError, PrismRasterError) as exc:
            failures.append(f"PRISM raster profile verification failed: {exc}")
            checks.append({"name": "raster_profile", "status": "FAIL"})
        else:
            check(
                "archive_inventory",
                archive_profile.get("inventory_sha256") == receipt.get("archive_inventory_sha256"),
                "PRISM archive inventory mismatch",
            )
            check(
                "raster_profile",
                recomputed.get("raster_profile_sha256") == receipt.get("raster_profile_sha256"),
                "PRISM raster profile mismatch",
            )
            primary = _profile_member(archive_profile, "primary_cog_raster")
            crc = 0
            with raster_path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    crc = binascii.crc32(chunk, crc)
            check(
                "extraction_crc",
                f"{crc & 0xFFFFFFFF:08x}" == primary.get("crc32") == receipt.get("selected_member_crc32"),
                "PRISM extracted raster CRC mismatch",
            )

    return {
        "verification_status": "PASS" if not failures else "FAIL",
        "check_count": len(checks),
        "passed_check_count": sum(1 for item in checks if item["status"] == "PASS"),
        "failed_check_count": sum(1 for item in checks if item["status"] == "FAIL"),
        "failures": sorted(set(failures)),
        "checks": checks,
    }
