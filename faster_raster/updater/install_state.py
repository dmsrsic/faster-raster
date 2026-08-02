from __future__ import annotations

import importlib.metadata
import json
import platform
import subprocess
import urllib.parse
from pathlib import Path

from .models import InstallationState


def _git_context(root: Path) -> str:
    try:
        inside = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if inside.returncode != 0:
            if "not a git repository" in inside.stderr.lower():
                return "absent"
            return "dirty_checkout"
        top = Path(inside.stdout.strip()).resolve()
        pyproject = top / "pyproject.toml"
        if not pyproject.is_file() or 'name = "faster-raster"' not in pyproject.read_text(encoding="utf-8"):
            return "absent"
        dirty = subprocess.run(
            ["git", "-C", str(top), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if dirty.returncode != 0:
            return "dirty_checkout"
        return "dirty_checkout" if dirty.stdout else "clean_checkout"
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return "dirty_checkout"


def _distribution_origin() -> str:
    try:
        distribution = importlib.metadata.distribution("faster-raster")
    except importlib.metadata.PackageNotFoundError:
        return "ambiguous"
    direct_url = distribution.read_text("direct_url.json")
    if direct_url:
        try:
            payload = json.loads(direct_url)
        except json.JSONDecodeError:
            return "ambiguous"
        if not isinstance(payload, dict):
            return "ambiguous"
        dir_info = payload.get("dir_info", {})
        if not isinstance(dir_info, dict):
            return "ambiguous"
        if dir_info.get("editable"):
            return "editable"
        url = str(payload.get("url", ""))
        if url.startswith("file:"):
            parsed = urllib.parse.urlparse(url)
            lowered = parsed.path.lower()
            if lowered.endswith(".whl"):
                return "local_wheel"
            if lowered.endswith((".tar.gz", ".zip")):
                return "local_sdist"
            return "ambiguous"
    # PEP 610 is absent for many old installs, so never guess an index origin.
    return "package_index_or_unknown"


def inspect_installation(root: Path | None = None) -> InstallationState:
    root = (root or Path.cwd()).resolve()
    try:
        version = importlib.metadata.version("faster-raster")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
    return InstallationState(
        active_version=version,
        python_version=platform.python_version(),
        git_context=_git_context(root),
        distribution_origin=_distribution_origin(),
    )
