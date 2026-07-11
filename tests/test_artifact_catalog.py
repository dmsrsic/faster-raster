from __future__ import annotations

import hashlib

from faster_raster import artifact_catalog
from faster_raster.run_receipts import write_json


def _receipt(tmp_path, run_id="fr_mat_test"):
    payload = b"catalog-object"
    digest = hashlib.sha256(payload).hexdigest()
    artifact = tmp_path / "cache" / "artifacts" / "sha256" / digest[:2] / digest[2:4] / f"{digest}.nc"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(payload)
    return {
        "artifact_id": f"sha256:{digest}",
        "whole_object_sha256": digest,
        "object_size_bytes": len(payload),
        "artifact_path": str(artifact),
        "artifact_extension": ".nc",
        "source_id": "gridmet_daily",
        "request_id": "req",
        "task_id": "task",
        "temporal_key": "20230101",
        "detected_content_family": "netcdf",
        "detected_magic": "netcdf",
        "container_validation_status": "PASS",
        "materialization_run_id": run_id,
    }


def test_catalog_update_and_verify(tmp_path):
    receipt = _receipt(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    write_json(receipt_path, receipt)
    snapshot = artifact_catalog.update_catalog([receipt], [receipt_path], catalog_root=tmp_path / "reports" / "artifacts", now="2026-01-01T00:00:00Z")
    assert snapshot["artifact_count"] == 1
    verification = artifact_catalog.verify_artifact_catalog(snapshot, catalog_root=tmp_path / "reports" / "artifacts")
    assert verification["verification_status"] == "PASS"


def test_catalog_detects_tampered_artifact(tmp_path):
    receipt = _receipt(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    write_json(receipt_path, receipt)
    snapshot = artifact_catalog.update_catalog([receipt], [receipt_path], catalog_root=tmp_path / "reports" / "artifacts", now="2026-01-01T00:00:00Z")
    artifact = snapshot["entries"][0]["artifact_path"]
    from pathlib import Path

    Path(artifact).write_bytes(b"tampered")
    verification = artifact_catalog.verify_artifact_catalog(snapshot, catalog_root=tmp_path / "reports" / "artifacts")
    assert verification["verification_status"] == "FAIL"


def test_absent_catalog_is_not_applicable(tmp_path):
    verification = artifact_catalog.verify_artifact_catalog(catalog_root=tmp_path / "catalog")
    assert verification["verification_status"] == "NOT_APPLICABLE"
    assert verification["catalog_status"] == "not_initialized"
    assert verification["blocking"] is False


def test_absent_catalog_after_claimed_update_fails(tmp_path):
    verification = artifact_catalog.verify_artifact_catalog(catalog_root=tmp_path / "catalog", claimed_update=True)
    assert verification["verification_status"] == "FAIL"
    assert "catalog_missing_after_claimed_update" in verification["failures"]
