from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image, TiffImagePlugin

from faster_raster import derived_artifacts, metadata_catalog, metadata_verification, raster_metadata


def _tiff_bytes() -> bytes:
    import io

    image = Image.new("F", (4, 3))
    tags = TiffImagePlugin.ImageFileDirectory_v2()
    tags[33550] = (0.05, 0.05, 0.0)
    tags[33922] = (0.0, 0.0, 0.0, -180.0, 90.0, 0.0)
    tags[34735] = (1, 1, 0, 3, 1024, 0, 1, 2, 2048, 0, 1, 4326, 2048, 0, 1, 4326)
    tags[42113] = "-9999"
    handle = io.BytesIO()
    image.save(handle, format="TIFF", tiffinfo=tags)
    return handle.getvalue()


def _source(root: Path, payload: bytes | None = None) -> tuple[str, Path]:
    raw = gzip.compress(payload if payload is not None else _tiff_bytes())
    sha = hashlib.sha256(raw).hexdigest()
    path = root / "cache" / "artifacts" / "sha256" / sha[:2] / sha[2:4] / f"{sha}.tif.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return sha, path


def _derive(root: Path):
    sha, _ = _source(root)
    plan = derived_artifacts.build_derivation_plan(sha, root=root)
    result = derived_artifacts.run_derivation(sha, allow_derivation=True, approve_plan_sha256=plan["derivation_plan_contract_sha256"], root=root)
    assert result["receipt"]["operation_status"] == "completed"
    return result


def test_derivation_plan_hash_is_deterministic_and_policy_sensitive(tmp_path):
    sha, _ = _source(tmp_path)
    first = derived_artifacts.build_derivation_plan(sha, root=tmp_path)
    second = derived_artifacts.build_derivation_plan(sha, root=tmp_path)
    assert first["derivation_plan_contract_sha256"] == second["derivation_plan_contract_sha256"]
    second["generated_at_utc"] = "2099-01-01T00:00:00Z"
    assert derived_artifacts.compute_derivation_plan_sha256(second, root=tmp_path) == first["derivation_plan_contract_sha256"]
    changed = derived_artifacts.build_derivation_plan(sha, max_expansion_ratio=10, root=tmp_path)
    assert changed["derivation_plan_contract_sha256"] != first["derivation_plan_contract_sha256"]


def test_approval_required_and_wrong_hash_blocked(tmp_path):
    sha, _ = _source(tmp_path)
    blocked = derived_artifacts.run_derivation(sha, root=tmp_path)
    assert blocked["receipt"]["failure_class"] == "approval_required"
    wrong = derived_artifacts.run_derivation(sha, allow_derivation=True, approve_plan_sha256="0" * 64, root=tmp_path)
    assert wrong["receipt"]["failure_class"] == "plan_hash_mismatch"


def test_valid_gzip_decompresses_and_source_unchanged(tmp_path):
    sha, src = _source(tmp_path)
    before = src.read_bytes()
    plan = derived_artifacts.build_derivation_plan(sha, root=tmp_path)
    result = derived_artifacts.run_derivation(sha, allow_derivation=True, approve_plan_sha256=plan["derivation_plan_contract_sha256"], root=tmp_path)
    receipt = result["receipt"]
    assert receipt["operation_status"] == "completed"
    assert receipt["complete_output"] is True
    assert receipt["atomic_commit_completed"] is True
    assert receipt["validation_status"] == "PASS"
    assert src.read_bytes() == before
    assert (tmp_path / receipt["output_logical_path"]).read_bytes() == _tiff_bytes()


def test_invalid_truncated_and_limits_fail_without_partial_output(tmp_path):
    sha, src = _source(tmp_path)
    src.write_bytes(b"not gzip")
    result = derived_artifacts.run_derivation(sha, allow_derivation=True, approve_plan_sha256=derived_artifacts.build_derivation_plan(sha, root=tmp_path)["derivation_plan_contract_sha256"], root=tmp_path)
    assert result["receipt"]["failure_class"] in {"invalid_gzip", "source_artifact_integrity_failed"}
    sha2, src2 = _source(tmp_path)
    src2.write_bytes(src2.read_bytes()[:10])
    result2 = derived_artifacts.run_derivation(sha2, allow_derivation=True, approve_plan_sha256=derived_artifacts.build_derivation_plan(sha2, root=tmp_path)["derivation_plan_contract_sha256"], root=tmp_path)
    assert result2["receipt"]["failure_class"] == "source_artifact_integrity_failed"
    sha3, _ = _source(tmp_path)
    plan3 = derived_artifacts.build_derivation_plan(sha3, max_output_bytes=2, root=tmp_path)
    result3 = derived_artifacts.run_derivation(sha3, allow_derivation=True, approve_plan_sha256=plan3["derivation_plan_contract_sha256"], max_output_bytes=2, root=tmp_path)
    assert result3["receipt"]["failure_class"] == "decompression_limit_exceeded"
    assert not any((tmp_path / "cache/staging/derivations").glob("**/*.part"))


def test_existing_identical_reused_and_conflict_rejected(tmp_path):
    result = _derive(tmp_path)
    receipt = result["receipt"]
    sha = receipt["source_artifact_sha256"]
    plan = derived_artifacts.build_derivation_plan(sha, root=tmp_path)
    reused = derived_artifacts.run_derivation(sha, allow_derivation=True, approve_plan_sha256=plan["derivation_plan_contract_sha256"], root=tmp_path)
    assert reused["receipt"]["reused_existing_artifact"] is True
    out = tmp_path / receipt["output_logical_path"]
    out.write_bytes(b"bad")
    conflict = derived_artifacts.run_derivation(sha, allow_derivation=True, approve_plan_sha256=plan["derivation_plan_contract_sha256"], root=tmp_path)
    assert conflict["receipt"]["failure_class"] == "derived_artifact_conflict"


def test_symlink_source_rejected(tmp_path):
    sha, src = _source(tmp_path)
    target = tmp_path / "real.gz"
    shutil.move(src, target)
    src.symlink_to(target)
    plan = derived_artifacts.build_derivation_plan(sha, root=tmp_path)
    result = derived_artifacts.run_derivation(sha, allow_derivation=True, approve_plan_sha256=plan["derivation_plan_contract_sha256"], root=tmp_path)
    assert result["receipt"]["failure_class"] == "source_artifact_not_regular"


def test_metadata_extract_verify_and_catalog(tmp_path):
    result = _derive(tmp_path)
    receipt = result["receipt"]
    metadata = raster_metadata.extract_raster_metadata(receipt, root=tmp_path)
    assert metadata["raster_shape"]["width"] == 4
    assert metadata["raster_shape"]["height"] == 3
    assert metadata["raster_shape"]["dtypes"] == ["float32"]
    assert metadata["band_metadata"][0]["nodata"] == -9999.0
    assert metadata["semantic_declarations"]["status"]["canonical_units"] == "declared_only"
    contract = metadata["metadata_contract_sha256"]
    metadata["metadata_contract_sha256"] = ""
    metadata["metadata_contract_sha256"] = raster_metadata.compute_metadata_contract_sha256(metadata, root=tmp_path)
    assert metadata["metadata_contract_sha256"] == contract
    verification = metadata_verification.verify_metadata(metadata, receipt, root=tmp_path)
    assert verification["verification_status"] == "PASS"
    raster_metadata.write_metadata_reports(metadata, verification, root=tmp_path)
    catalog = metadata_catalog.update_catalog(metadata, verification, root=tmp_path)
    assert catalog["artifact_count"] == 1
    same = metadata_catalog.update_catalog(metadata, verification, root=tmp_path)
    assert same == catalog
    tampered = dict(metadata)
    tampered["metadata_contract_sha256"] = "0" * 64
    with pytest.raises(metadata_catalog.MetadataCatalogError):
        metadata_catalog.update_catalog(tampered, verification, root=tmp_path)
    assert metadata_catalog.verify_catalog(root=tmp_path)["verification_status"] == "PASS"


def test_tampering_and_lineage_break_detected(tmp_path):
    result = _derive(tmp_path)
    receipt = result["receipt"]
    metadata = raster_metadata.extract_raster_metadata(receipt, root=tmp_path)
    verification = metadata_verification.verify_metadata(metadata, receipt, root=tmp_path)
    assert verification["verification_status"] == "PASS"
    bad_metadata = json.loads(json.dumps(metadata))
    bad_metadata["grid_geometry"]["bounds"][0] = 1
    assert metadata_verification.verify_metadata(bad_metadata, receipt, root=tmp_path)["verification_status"] == "FAIL"
    bad_receipt = dict(receipt)
    bad_receipt["source_artifact_sha256"] = "0" * 64
    assert metadata_verification.verify_metadata(metadata, bad_receipt, root=tmp_path)["lineage_verification_status"] == "FAIL"
    out = tmp_path / receipt["output_logical_path"]
    out.write_bytes(out.read_bytes() + b"tamper")
    assert derived_artifacts.verify_derivation_receipt(receipt, root=tmp_path)["verification_status"] == "FAIL"
