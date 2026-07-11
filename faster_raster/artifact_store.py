from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path


ARTIFACT_ROOT = Path("cache/artifacts/sha256")
STAGING_ROOT = Path("cache/staging/materialization")


class ArtifactStoreError(ValueError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()




def _repo_root() -> Path:
    return Path.cwd().resolve()


def find_existing_storage_ancestor(path: Path, *, boundary: Path | None = None) -> Path:
    resolved_boundary = boundary.resolve() if boundary is not None else None
    current = path.resolve(strict=False)
    if current.exists() and current.is_file():
        current = current.parent
    while not current.exists():
        if current == current.parent:
            raise ArtifactStoreError("artifact_store_error")
        if resolved_boundary is not None and not current.is_relative_to(resolved_boundary):
            raise ArtifactStoreError("artifact_store_error")
        current = current.parent
    if resolved_boundary is not None and not current.is_relative_to(resolved_boundary):
        raise ArtifactStoreError("artifact_store_error")
    if not current.is_dir() or current.is_symlink():
        raise ArtifactStoreError("artifact_store_error")
    return current


def _validate_root_policy(path: Path, *, boundary: Path | None = None) -> Path:
    resolved_boundary = boundary.resolve() if boundary is not None else None
    resolved = path.resolve(strict=False)
    if resolved_boundary is not None and not resolved.is_relative_to(resolved_boundary):
        raise ArtifactStoreError("artifact_store_error")
    if path.exists():
        if path.is_symlink() or not path.is_dir():
            raise ArtifactStoreError("artifact_store_error")
    parent = find_existing_storage_ancestor(path, boundary=resolved_boundary)
    if parent.is_symlink():
        raise ArtifactStoreError("artifact_store_error")
    return resolved


def validate_artifact_root_policy(path: Path, *, boundary: Path | None = None) -> Path:
    return _validate_root_policy(path, boundary=boundary)


def validate_staging_root_policy(path: Path, *, boundary: Path | None = None) -> Path:
    return _validate_root_policy(path, boundary=boundary)


def inspect_available_disk_space(path: Path, *, boundary: Path | None = None) -> int:
    ancestor = find_existing_storage_ancestor(path, boundary=boundary)
    return shutil.disk_usage(ancestor).free


def prepare_artifact_store(path: Path = ARTIFACT_ROOT, *, reports_root: Path = Path("reports/artifacts"), boundary: Path | None = None) -> None:
    validate_artifact_root_policy(path, boundary=boundary)
    try:
        path.mkdir(parents=True, exist_ok=True)
        reports_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ArtifactStoreError("artifact_store_error") from exc


def prepare_staging_root(path: Path = STAGING_ROOT, *, boundary: Path | None = None) -> None:
    validate_staging_root_policy(path, boundary=boundary)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ArtifactStoreError("artifact_store_error") from exc


def trusted_extension(container: str | None, expected_format: str | None = None) -> str:
    if container == "gzip" or expected_format == "geotiff.gz":
        return ".tif.gz"
    if container in {"hdf5_netcdf4", "netcdf", "hdf5"} or expected_format == "netcdf":
        return ".nc"
    if container == "zip" or expected_format == "zip":
        return ".zip"
    return ".bin"


def content_addressed_path(sha256: str, extension: str, *, artifact_root: Path = ARTIFACT_ROOT) -> Path:
    if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
        raise ArtifactStoreError("invalid SHA256 for artifact path")
    if not extension.startswith(".") or "/" in extension or "\\" in extension:
        raise ArtifactStoreError("invalid artifact extension")
    root = artifact_root.resolve()
    destination = (artifact_root / sha256[:2] / sha256[2:4] / f"{sha256}{extension}").resolve()
    if not destination.is_relative_to(root):
        raise ArtifactStoreError("artifact path escapes artifact root")
    return destination


def staging_path(task_id: str, source_id: str, url_sha256: str, *, staging_root: Path = STAGING_ROOT) -> Path:
    destination = (staging_root / task_id / source_id / f"{url_sha256}.part").resolve()
    if not destination.is_relative_to(staging_root.resolve()):
        raise ArtifactStoreError("staging path escapes staging root")
    return destination


def ensure_disk_space(path: Path, required_bytes: int, safety_margin_bytes: int, *, boundary: Path | None = None) -> int:
    free = inspect_available_disk_space(path, boundary=boundary)
    required = required_bytes + safety_margin_bytes
    if free < required:
        raise ArtifactStoreError("insufficient_disk_space")
    return free


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    try:
        directory = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        pass


def promote_complete_artifact(staging_file: Path, sha256: str, extension: str, *, artifact_root: Path = ARTIFACT_ROOT) -> tuple[Path, bool]:
    destination = content_addressed_path(sha256, extension, artifact_root=artifact_root)
    if destination.exists():
        if destination.is_symlink():
            raise ArtifactStoreError("artifact destination is symlink")
        if sha256_file(destination) != sha256:
            raise ArtifactStoreError("existing content-addressed artifact is corrupt")
        if staging_file.exists():
            staging_file.unlink()
        return destination, True
    if not staging_file.is_file() or staging_file.is_symlink():
        raise ArtifactStoreError("staging artifact missing or invalid")
    if sha256_file(staging_file) != sha256:
        raise ArtifactStoreError("staging checksum mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging_file, destination)
    try:
        directory = os.open(str(destination.parent), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        pass
    return destination, False
