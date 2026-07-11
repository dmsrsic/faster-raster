from __future__ import annotations

import hashlib

from faster_raster import artifact_receipts


def _artifact_receipt(tmp_path):
    payload = b"\x1f\x8b" + b"x" * 32 + b"trailer"
    digest = hashlib.sha256(payload).hexdigest()
    artifact = tmp_path / "cache" / "artifacts" / "sha256" / digest[:2] / digest[2:4] / f"{digest}.tif.gz"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(payload)
    receipt = {
        "artifact_receipt_version": 1,
        "artifact_id": f"sha256:{digest}",
        "task_id": "task",
        "materialization_run_id": "fr_mat_test",
        "request_id": "req",
        "source_id": "chirps_daily_precipitation",
        "object_status": "committed",
        "complete_object": True,
        "bounded_probe_only": False,
        "whole_object_sha256": digest,
        "object_size_bytes": len(payload),
        "artifact_path": str(artifact),
        "content_addressed": True,
        "prefix_match": True,
        "container_validation_status": "PASS",
        "credentials_used": False,
        "authorization_headers_present": False,
        "artifact_receipt_contract_sha256": "",
    }
    receipt["artifact_receipt_contract_sha256"] = artifact_receipts.compute_artifact_receipt_sha256(receipt, tmp_path)
    return receipt


def test_artifact_receipt_hash_verifies(tmp_path):
    receipt = _artifact_receipt(tmp_path)
    verification = artifact_receipts.verify_artifact_receipt(receipt, repo_root=tmp_path)
    assert verification["verification_status"] == "PASS"


def test_tampered_artifact_receipt_fails(tmp_path):
    receipt = _artifact_receipt(tmp_path)
    receipt["object_size_bytes"] += 1
    verification = artifact_receipts.verify_artifact_receipt(receipt, repo_root=tmp_path)
    assert verification["verification_status"] == "FAIL"
