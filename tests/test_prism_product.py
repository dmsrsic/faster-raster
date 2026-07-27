from __future__ import annotations

import hashlib
import shutil
import zipfile
from pathlib import Path

import pytest

from faster_raster import artifact_receipts, materialization, task_compiler
from faster_raster.prism_canary import run_canary
from faster_raster.prism_product import (
    PRISM_SOURCE_ID,
    PrismProductError,
    inspect_prism_archive,
)


DATE = "20230101"
STEM = f"prism_ppt_us_25m_{DATE}"


def _write_prism_zip(path: Path, *, members: dict[str, bytes] | None = None) -> Path:
    payloads = members or {
        f"{STEM}.tif": b"II*\x00synthetic-cog",
        f"{STEM}.prj": b"GEOGCS[...]",
        f"{STEM}.stx": b"0 100",
        f"{STEM}.xml": b"<metadata/>",
        f"{STEM}.tif.aux.xml": b"<PAMDataset/>",
        f"{STEM}.info.txt": b"grid count: 1",
        f"{STEM}.stn.csv": b"id,name\n1,test\n",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in payloads.items():
            archive.writestr(name, payload)
    return path


def _artifact_receipt(tmp_path: Path, archive: Path) -> dict:
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    artifact = tmp_path / "artifacts" / digest[:2] / digest[2:4] / f"{digest}.zip"
    artifact.parent.mkdir(parents=True)
    shutil.copy2(archive, artifact)
    profile = inspect_prism_archive(artifact, temporal_key=DATE)
    receipt = {
        "artifact_receipt_version": 1,
        "artifact_id": f"sha256:{digest}",
        "task_id": "task",
        "materialization_run_id": "fr_mat_test",
        "request_id": f"task__{PRISM_SOURCE_ID}__{DATE}",
        "source_id": PRISM_SOURCE_ID,
        "temporal_key": DATE,
        "object_status": "committed",
        "complete_object": True,
        "bounded_probe_only": False,
        "whole_object_sha256": digest,
        "object_size_bytes": artifact.stat().st_size,
        "artifact_path": str(artifact),
        "content_addressed": True,
        "prefix_match": True,
        "container_validation_status": "PASS",
        "container_metadata": {"product_profile": profile},
        "credentials_used": False,
        "authorization_headers_present": False,
        "artifact_receipt_contract_sha256": "",
    }
    receipt["artifact_receipt_contract_sha256"] = artifact_receipts.compute_artifact_receipt_sha256(receipt, tmp_path)
    return receipt


def test_prism_archive_inventory_is_deterministic_and_identifies_cog(tmp_path):
    archive = _write_prism_zip(tmp_path / f"{STEM}.zip")
    first = inspect_prism_archive(archive, temporal_key="2023-01-01")
    second = inspect_prism_archive(archive, temporal_key=DATE)

    assert first == second
    assert first["product_validation_status"] == "PASS"
    assert first["primary_raster_member"] == f"{STEM}.tif"
    assert first["primary_raster_format"] == "GeoTIFF"
    assert first["expected_cloud_optimized_geotiff"] is True
    assert first["cog_structure_validated"] is False
    assert first["extraction_performed"] is False
    assert first["profile_completeness"] == "complete"
    assert len(first["inventory_sha256"]) == 64
    assert {item["role"] for item in first["inventory"]} >= {
        "primary_cog_raster",
        "projection",
        "statistics",
        "fgdc_metadata",
        "esri_aux_metadata",
        "processing_info",
        "station_inventory",
    }


def test_prism_archive_rejects_path_traversal(tmp_path):
    archive = _write_prism_zip(
        tmp_path / f"{STEM}.zip",
        members={f"{STEM}.tif": b"II*\x00x", "../escape.txt": b"bad"},
    )
    with pytest.raises(PrismProductError, match="unsafe_zip_member_path"):
        inspect_prism_archive(archive, temporal_key=DATE)


def test_prism_archive_rejects_ambiguous_raster(tmp_path):
    archive = _write_prism_zip(
        tmp_path / f"{STEM}.zip",
        members={
            f"{STEM}.tif": b"II*\x00x",
            "unexpected_second.tif": b"II*\x00y",
        },
    )
    with pytest.raises(PrismProductError, match="unexpected_additional_prism_raster"):
        inspect_prism_archive(archive, temporal_key=DATE)


def test_prism_archive_rejects_expansion_ratio_over_limit(tmp_path):
    archive = _write_prism_zip(
        tmp_path / f"{STEM}.zip",
        members={f"{STEM}.tif": b"0" * 100_000},
    )
    with pytest.raises(PrismProductError, match="prism_member_expansion_ratio_exceeded"):
        inspect_prism_archive(archive, temporal_key=DATE, max_expansion_ratio=2.0)


def test_materialization_container_validation_embeds_product_profile(tmp_path):
    archive = _write_prism_zip(tmp_path / f"{STEM}.zip")
    magic, status, metadata, error = materialization._basic_container_validation(
        archive,
        "zip",
        source_id=PRISM_SOURCE_ID,
        temporal_key=DATE,
    )

    assert (magic, status, error) == ("zip", "PASS", None)
    assert metadata["validation_level"] == "product_profile_structural"
    assert metadata["product_profile"]["inventory_sha256"]


def test_prism_artifact_receipt_reinspects_archive(tmp_path):
    archive = _write_prism_zip(tmp_path / f"{STEM}.zip")
    receipt = _artifact_receipt(tmp_path, archive)
    verification = artifact_receipts.verify_artifact_receipt(receipt, repo_root=tmp_path)
    assert verification["verification_status"] == "PASS"
    assert next(check for check in verification["checks"] if check["name"] == "prism_product_profile")["status"] == "PASS"

    receipt["container_metadata"]["product_profile"]["inventory_sha256"] = "0" * 64
    receipt["artifact_receipt_contract_sha256"] = artifact_receipts.compute_artifact_receipt_sha256(receipt, tmp_path)
    tampered = artifact_receipts.verify_artifact_receipt(receipt, repo_root=tmp_path)
    assert tampered["verification_status"] == "FAIL"
    assert "PRISM product profile receipt mismatch" in tampered["failures"]


def test_task_compiler_reports_prism_product_readiness():
    assert task_compiler._harmonization_readiness(PRISM_SOURCE_ID) == "archive_profile_supported_cog_raster_decode_pending"


def test_canary_execution_requires_explicit_permissions(tmp_path):
    with pytest.raises(ValueError, match="requires --allow-network and --allow-materialization"):
        run_canary(
            repo_root=tmp_path,
            workspace=tmp_path / "workspace",
            execute=True,
            allow_network=True,
        )
