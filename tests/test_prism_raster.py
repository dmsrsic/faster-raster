from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.shutil import copy as raster_copy
from rasterio.transform import Affine

from faster_raster.prism_product import inspect_prism_archive
from faster_raster.prism_raster import (
    PrismRasterError,
    inspect_prism_raster,
    materialize_prism_primary_raster,
    verify_prism_raster_receipt,
)


DATE = "20230101"
STEM = f"prism_ppt_us_25m_{DATE}"
TRANSFORM = Affine(
    0.041666666667,
    0.0,
    -125.0208333333335,
    0.0,
    -0.041666666667,
    49.9375000000005,
)
TAGS = {
    "PRISM_CODE_VERSION": "test-version",
    "PRISM_DATASET_CREATE_DATE": "20230102",
    "PRISM_DATASET_FILENAME": f"adj_best_ppt_us_us_30s_{DATE}.bil",
    "PRISM_DATASET_REMARKS": "Synthetic offline PRISM product fixture.",
    "PRISM_DATASET_TYPE": "an91/r2112",
    "PRISM_DATASET_VERSION": "D2",
    "AREA_OR_POINT": "Area",
}


def _fixture_values() -> np.ndarray:
    values = np.zeros((621, 1405), dtype=np.float32)
    values[:20, :] = -9999.0
    values[100:200, 200:400] = 12.5
    values[300:320, 700:800] = 25.0
    return values


def _computed(values: np.ndarray) -> dict[str, float | int]:
    valid = values[values != -9999.0].astype(np.float64)
    return {
        "minimum": float(valid.min()),
        "maximum": float(valid.max()),
        "mean": float(valid.mean()),
        "stddev": float(valid.std()),
        "nnull": int(np.count_nonzero(values == -9999.0)),
    }


def _fgdc_xml(*, date: str = DATE) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<metadata>
  <idinfo>
    <citation><citeinfo><title>United States Daily Total Precipitation, January 1, 2023 (4km; COG)</title></citeinfo></citation>
    <timeperd><timeinfo><rngdates><begdate>{date}</begdate><enddate>{date}</enddate></rngdates></timeinfo></timeperd>
    <spdom><bounding><westbc>-125.0208333</westbc><eastbc>-66.4791667</eastbc><northbc>49.9375000</northbc><southbc>24.0625000</southbc></bounding></spdom>
  </idinfo>
  <spref><horizsys><geograph><latres>0.04166667</latres><longres>0.04166667</longres></geograph><geodetic><horizdn>North American Datum of 1983</horizdn></geodetic></horizsys></spref>
  <eainfo><detailed><attr><attrdomv><rdom><rdommin>0</rdommin><rdommax>15000</rdommax><attrunit>Millimeters</attrunit></rdom></attrdomv></attr></detailed></eainfo>
</metadata>
"""


def _create_archive(
    root: Path,
    *,
    cog: bool = True,
    metadata_date: str = DATE,
    projection_wkt: str | None = None,
    include_statistics_nnull: bool = True,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    source = root / "source.tif"
    primary = root / f"{STEM}.tif"
    values = _fixture_values()
    stats = _computed(values)
    with rasterio.open(
        source,
        "w",
        driver="GTiff",
        width=1405,
        height=621,
        count=1,
        dtype="float32",
        crs="EPSG:4269",
        transform=TRANSFORM,
        nodata=-9999.0,
    ) as dataset:
        dataset.write(values, 1)
        dataset.update_tags(**TAGS)
        embedded_statistics = {
            "STATISTICS_MINIMUM": f"{stats['minimum']:.4f}",
            "STATISTICS_MAXIMUM": f"{stats['maximum']:.4f}",
            "STATISTICS_MEAN": f"{stats['mean']:.4f}",
            "STATISTICS_STDDEV": f"{stats['stddev']:.4f}",
            "STATISTICS_VALID_PERCENT": (
                f"{100.0 * (values.size - int(stats['nnull'])) / values.size:.6f}"
            ),
        }
        if include_statistics_nnull:
            embedded_statistics["STATISTICS_NNULL"] = str(stats["nnull"])
        dataset.update_tags(1, **embedded_statistics)
    if cog:
        raster_copy(source, primary, driver="COG", compress="LZW", blocksize=512, overview_resampling="nearest")
    else:
        raster_copy(source, primary, driver="GTiff", compress="LZW", tiled=False)

    prj = projection_wkt or rasterio.crs.CRS.from_epsg(4269).to_wkt(version="WKT1_ESRI")
    info = "\n".join(f"{key}: {value}" for key, value in TAGS.items() if key != "AREA_OR_POINT") + "\n"
    stx = f"1 {stats['minimum']:.10f} {stats['maximum']:.10f} {stats['mean']:.10f} {stats['stddev']:.10f}\n"
    aux = "<PAMDataset><PAMRasterBand band=\"1\"><NoDataValue>-9.99900000000000E+03</NoDataValue><Metadata><MDI key=\"STATISTICS_APPROXIMATE\">YES</MDI></Metadata></PAMRasterBand></PAMDataset>\n"
    archive_path = root / f"{STEM}.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{STEM}.info.txt", info)
        archive.writestr(f"{STEM}.prj", prj)
        archive.writestr(f"{STEM}.stn.csv", "station_id,longitude,latitude\nTEST,-100,40\n")
        archive.writestr(f"{STEM}.stx", stx)
        archive.write(primary, arcname=f"{STEM}.tif")
        archive.writestr(f"{STEM}.tif.aux.xml", aux)
        archive.writestr(f"{STEM}.xml", _fgdc_xml(date=metadata_date))
    return archive_path


def test_materializes_decoded_cog_and_verifies_receipt(tmp_path):
    archive = _create_archive(tmp_path / "fixture")
    profile = inspect_prism_archive(archive, temporal_key=DATE)
    receipt_path = tmp_path / "evidence" / "prism_raster_receipt.json"
    receipt = materialize_prism_primary_raster(
        archive,
        temporal_key=DATE,
        product_profile=profile,
        artifact_root=tmp_path / "artifacts",
        staging_root=tmp_path / "staging",
        receipt_path=receipt_path,
        generated_at_utc="2026-01-01T00:00:00Z",
    )

    assert receipt["validation_status"] == "PASS"
    assert receipt["selected_member"] == f"{STEM}.tif"
    assert receipt["extracted_size_match"] is True
    assert receipt["extracted_crc_match"] is True
    assert Path(receipt["raster_artifact_path"]).is_file()
    assert receipt["raster_sha256"] in Path(receipt["raster_artifact_path"]).name
    raster_profile = receipt["raster_profile"]
    assert raster_profile["raster_decode_status"] == "PASS"
    assert raster_profile["cog_structure_validation_status"] == "PASS"
    assert raster_profile["sidecar_consistency_status"] == "PASS"
    assert raster_profile["width"] == 1405
    assert raster_profile["height"] == 621
    assert raster_profile["epsg"] == 4269
    assert raster_profile["computed_statistics"]["negative_pixel_count"] == 0
    assert raster_profile["harmonization_readiness"] == "decoded_cog_profile_verified_aoi_harmonization_pending"

    verification = verify_prism_raster_receipt(receipt_path)
    assert verification["verification_status"] == "PASS", verification



def test_accepts_standard_gdal_statistics_without_nonstandard_nnull(tmp_path):
    archive = _create_archive(
        tmp_path / "fixture-no-nnull",
        include_statistics_nnull=False,
    )
    profile = inspect_prism_archive(archive, temporal_key=DATE)
    receipt = materialize_prism_primary_raster(
        archive,
        temporal_key=DATE,
        product_profile=profile,
        artifact_root=tmp_path / "artifacts-no-nnull",
        staging_root=tmp_path / "staging-no-nnull",
    )

    raster_profile = receipt["raster_profile"]
    assert receipt["validation_status"] == "PASS"
    assert "STATISTICS_NNULL" not in raster_profile["band_tags"]
    assert raster_profile["computed_statistics"]["nodata_pixel_count"] > 0
    assert raster_profile["sidecar_consistency_status"] == "PASS"

def test_reuses_existing_content_addressed_raster(tmp_path):
    archive = _create_archive(tmp_path / "fixture")
    kwargs = {
        "temporal_key": DATE,
        "artifact_root": tmp_path / "artifacts",
        "staging_root": tmp_path / "staging",
        "generated_at_utc": "2026-01-01T00:00:00Z",
    }
    first = materialize_prism_primary_raster(archive, **kwargs)
    second = materialize_prism_primary_raster(archive, **kwargs)
    assert first["raster_sha256"] == second["raster_sha256"]
    assert second["reused_existing_raster_artifact"] is True



def test_receipt_contract_is_independent_of_workspace_paths(tmp_path):
    archive = _create_archive(tmp_path / "fixture")
    first = materialize_prism_primary_raster(
        archive,
        temporal_key=DATE,
        artifact_root=tmp_path / "workspace-a" / "artifacts",
        staging_root=tmp_path / "workspace-a" / "staging",
        generated_at_utc="2026-01-01T00:00:00Z",
    )
    second = materialize_prism_primary_raster(
        archive,
        temporal_key=DATE,
        artifact_root=tmp_path / "workspace-b" / "artifacts",
        staging_root=tmp_path / "workspace-b" / "staging",
        generated_at_utc="2026-01-02T00:00:00Z",
    )
    assert first["raster_receipt_contract_sha256"] == second["raster_receipt_contract_sha256"]
    assert first["raster_profile_sha256"] == second["raster_profile_sha256"]

def test_refuses_primary_raster_over_extraction_cap(tmp_path):
    archive = _create_archive(tmp_path / "fixture")
    with pytest.raises(PrismRasterError, match="prism_primary_raster_byte_limit_exceeded"):
        materialize_prism_primary_raster(
            archive,
            temporal_key=DATE,
            artifact_root=tmp_path / "artifacts",
            staging_root=tmp_path / "staging",
            max_extracted_raster_bytes=1024,
        )


def test_rejects_non_cog_primary_raster(tmp_path):
    archive = _create_archive(tmp_path / "fixture", cog=False)
    with pytest.raises(PrismRasterError, match="prism_raster_not_tiled|prism_cog_layout_not_declared"):
        materialize_prism_primary_raster(
            archive,
            temporal_key=DATE,
            artifact_root=tmp_path / "artifacts",
            staging_root=tmp_path / "staging",
        )


def test_rejects_metadata_date_mismatch(tmp_path):
    archive = _create_archive(tmp_path / "fixture", metadata_date="20230102")
    with pytest.raises(PrismRasterError, match="prism_metadata_date_mismatch"):
        materialize_prism_primary_raster(
            archive,
            temporal_key=DATE,
            artifact_root=tmp_path / "artifacts",
            staging_root=tmp_path / "staging",
        )


def test_rejects_projection_sidecar_mismatch(tmp_path):
    wrong_projection = rasterio.crs.CRS.from_epsg(4326).to_wkt(version="WKT1_ESRI")
    archive = _create_archive(tmp_path / "fixture", projection_wkt=wrong_projection)
    with pytest.raises(PrismRasterError, match="prism_projection_sidecar_mismatch"):
        materialize_prism_primary_raster(
            archive,
            temporal_key=DATE,
            artifact_root=tmp_path / "artifacts",
            staging_root=tmp_path / "staging",
        )


def test_receipt_verification_detects_raster_tampering(tmp_path):
    archive = _create_archive(tmp_path / "fixture")
    receipt = materialize_prism_primary_raster(
        archive,
        temporal_key=DATE,
        artifact_root=tmp_path / "artifacts",
        staging_root=tmp_path / "staging",
        generated_at_utc="2026-01-01T00:00:00Z",
    )
    raster_path = Path(receipt["raster_artifact_path"])
    raster_path.write_bytes(raster_path.read_bytes() + b"tamper")
    verification = verify_prism_raster_receipt(receipt)
    assert verification["verification_status"] == "FAIL"
    assert any("checksum" in failure for failure in verification["failures"])


def test_real_observed_archive_contract_is_representable(tmp_path):
    archive = _create_archive(tmp_path / "fixture")
    profile = inspect_prism_archive(archive, temporal_key=DATE)
    primary = next(item for item in profile["inventory"] if item["role"] == "primary_cog_raster")
    assert primary["member_name"] == f"{STEM}.tif"
    assert profile["profile_completeness"] == "complete"
    assert hashlib.sha256(json.dumps(profile["inventory"], sort_keys=True).encode()).hexdigest()
