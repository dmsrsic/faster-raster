from __future__ import annotations

import hashlib

import pytest

from faster_raster import artifact_store


def test_content_addressed_path_uses_full_sha_and_trusted_extension(tmp_path):
    digest = "a" * 64
    path = artifact_store.content_addressed_path(digest, ".tif.gz", artifact_root=tmp_path)
    assert path == tmp_path / "aa" / "aa" / f"{digest}.tif.gz"


def test_content_addressed_path_rejects_unsafe_extension(tmp_path):
    with pytest.raises(artifact_store.ArtifactStoreError):
        artifact_store.content_addressed_path("a" * 64, "../x", artifact_root=tmp_path)


def test_promote_complete_artifact_reuses_existing_valid_artifact(tmp_path):
    payload = b"complete-object"
    digest = hashlib.sha256(payload).hexdigest()
    staging = tmp_path / "stage.part"
    staging.write_bytes(payload)
    destination, reused = artifact_store.promote_complete_artifact(staging, digest, ".nc", artifact_root=tmp_path / "artifacts")
    assert reused is False
    assert destination.read_bytes() == payload
    staging2 = tmp_path / "stage2.part"
    staging2.write_bytes(payload)
    destination2, reused2 = artifact_store.promote_complete_artifact(staging2, digest, ".nc", artifact_root=tmp_path / "artifacts")
    assert destination2 == destination
    assert reused2 is True
    assert not staging2.exists()


def test_nonexistent_artifact_root_uses_existing_ancestor(tmp_path):
    root = tmp_path / "cache" / "artifacts" / "sha256"
    free = artifact_store.inspect_available_disk_space(root, boundary=tmp_path)
    assert free > 0
    artifact_store.prepare_artifact_store(root, reports_root=tmp_path / "reports" / "artifacts", boundary=tmp_path)
    assert root.is_dir()
    assert (tmp_path / "reports" / "artifacts").is_dir()


def test_symlink_artifact_root_rejected(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "cache" / "artifacts" / "sha256"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable")
        raise
    try:
        artifact_store.validate_artifact_root_policy(link, boundary=tmp_path)
    except artifact_store.ArtifactStoreError as exc:
        assert str(exc) == "artifact_store_error"
    else:
        raise AssertionError("symlink artifact root was accepted")


def test_artifact_root_outside_boundary_rejected(tmp_path):
    outside = tmp_path.parent / "outside-artifacts"
    try:
        artifact_store.validate_artifact_root_policy(outside, boundary=tmp_path)
    except artifact_store.ArtifactStoreError as exc:
        assert str(exc) == "artifact_store_error"
    else:
        raise AssertionError("outside artifact root was accepted")
