from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from faster_raster.adapter_contract import stable_json
from faster_raster.local_paths import resolve_local_paths
from packaging.version import InvalidVersion, Version

from .install_state import inspect_installation
from .models import InstallationState, ReleaseManifest, UpdateCheckResult, UpdateChannel
from .release_client import ReleaseClientError, discover


def _recommendation(state: InstallationState, candidate: ReleaseManifest | None) -> dict[str, Any]:
    if state.git_context == "dirty_checkout":
        return {"action": "blocked", "reason": "dirty_checkout", "guidance": ["commit or stash changes before updating"]}
    if candidate is None:
        return {"action": "none", "reason": "no_valid_candidate"}
    if state.git_context == "clean_checkout":
        return {"action": "manual_git_fast_forward", "argv": ["git", "pull", "--ff-only"], "guidance": "Review the diff, then reinstall the editable package if needed."}
    if state.distribution_origin == "editable":
        return {"action": "manual_editable_reinstall", "argv": ["python", "-m", "pip", "install", "-e", "."], "guidance": "Run from the reviewed checkout."}
    if state.distribution_origin not in {"local_wheel", "local_sdist"}:
        return {"action": "unsupported", "reason": "ambiguous_installation_origin", "guidance": "Reinstall from a verified FasterRaster release wheel."}
    wheel = next((asset for asset in candidate.assets if asset.kind == "wheel"), None)
    if wheel:
        return {"action": "manual_wheel_install", "url": wheel.url, "sha256": wheel.sha256, "guidance": "Download the exact wheel, verify SHA-256, and install manually."}
    return {"action": "unsupported", "reason": "no_wheel_asset"}


def _receipt(result: UpdateCheckResult) -> tuple[str, bytes]:
    """Hash the canonical receipt payload without its external digest field."""
    payload = result.as_dict()
    payload.pop("receipt_sha256", None)
    encoded = stable_json(payload).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return digest, encoded


def status(*, root: Path | None = None) -> UpdateCheckResult:
    state = inspect_installation(root)
    channel = _default_channel(state.active_version)
    result = UpdateCheckResult("offline", channel.value, state, recommendation=_recommendation(state, None))
    digest, _ = _receipt(result)
    return UpdateCheckResult(result.status, result.channel, result.installation, result.candidate, result.recommendation, result.error, digest)


def _default_channel(active_version: str) -> UpdateChannel:
    try:
        version = Version(active_version)
    except InvalidVersion:
        return UpdateChannel.STABLE
    return UpdateChannel.BETA if version.is_prerelease else UpdateChannel.STABLE


def check(*, channel: UpdateChannel | None, allow_network: bool, root: Path | None = None) -> UpdateCheckResult:
    state = inspect_installation(root)
    selected_channel = channel or _default_channel(state.active_version)
    if not allow_network:
        result = UpdateCheckResult("blocked", selected_channel.value, state, recommendation={"action": "blocked", "reason": "network_not_authorized"}, error="network access requires --allow-network")
    else:
        try:
            candidates = discover(selected_channel)
            candidate = candidates[0] if candidates else None
            if candidate is not None:
                try:
                    current = Version(state.active_version)
                except InvalidVersion as exc:
                    raise ReleaseClientError("active package version is invalid") from exc
                if Version(candidate.package_version) <= current:
                    candidate = None
            result = UpdateCheckResult("checked", selected_channel.value, state, candidate, _recommendation(state, candidate))
        except (ReleaseClientError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            result = UpdateCheckResult("error", selected_channel.value, state, recommendation={"action": "none", "reason": "metadata_error"}, error=str(exc))
    digest, encoded = _receipt(result)
    paths = resolve_local_paths(None)
    receipt_path = paths.state_home / "update" / f"{digest}.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_bytes(encoded)
    return UpdateCheckResult(result.status, result.channel, result.installation, result.candidate, result.recommendation, result.error, digest)
