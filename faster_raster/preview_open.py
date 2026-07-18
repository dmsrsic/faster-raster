from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Sequence

from faster_raster.local_diagnostics import detect_wsl


class PreviewOpenError(ValueError):
    pass
LOCAL_READINESS_BY_ACTION = {
    "reuse_direct": "ready_exact",
    "reuse_crop": "ready_requires_crop",
    "reuse_reproject": "ready_requires_reprojection",
    "reuse_crop_reproject": "ready_requires_crop_reprojection",
    "acquire": "missing",
    "acquire_and_mosaic": "partial_only",
    "reject": "missing",
}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def is_finalized_handoff(path: Path) -> bool:
    if not path.is_dir() or path.name.startswith((".", "_")):
        return False
    lowered = path.name.lower()
    if any(part in lowered for part in ("staging", "incomplete", "failed", "tmp")):
        return False
    manifest = _read_json(path / "manifest.json")
    if manifest.get("operation_status") in {"completed", "PASS"}:
        return True
    for receipt_path in path.glob("**/recipe_receipt.json"):
        receipt = _read_json(receipt_path)
        if receipt.get("final_status") == "PASS" or receipt.get("status") == "PASS":
            return True
    return False


def latest_handoff(handoff_root: Path) -> Path:
    if not handoff_root.is_dir():
        raise PreviewOpenError(f"handoff directory does not exist: {handoff_root}")
    candidates = [path for path in handoff_root.iterdir() if is_finalized_handoff(path)]
    if not candidates:
        raise PreviewOpenError("no finalized FasterRaster handoff exists")
    return max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name))


def finalized_preview(handoff: Path) -> Path:
    if not is_finalized_handoff(handoff):
        raise PreviewOpenError(f"handoff is not finalized: {handoff}")
    candidates = [path for path in handoff.glob("**/*.png") if "_work" not in path.parts]
    if not candidates:
        raise PreviewOpenError(f"no preview exists in finalized handoff: {handoff}")
    candidates.sort(key=lambda path: ("4k" not in path.name.lower(), "preview" not in path.parts, str(path)))
    return candidates[0]


def resolve_handoff(value: str, handoff_root: Path) -> Path:
    if value == "latest":
        return latest_handoff(handoff_root)
    path = Path(value).expanduser().resolve()
    if not is_finalized_handoff(path):
        raise PreviewOpenError(f"not a finalized handoff: {path}")
    return path


def open_local_preview(
    preview: Path,
    *,
    configured_opener: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    converter: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    launcher: Callable[..., Any] = subprocess.Popen,
    is_wsl: bool | None = None,
) -> list[str]:
    target = preview.resolve()
    if not target.is_file():
        raise PreviewOpenError(f"preview does not exist: {target}")
    wsl = detect_wsl() if is_wsl is None else is_wsl
    if wsl and which("explorer.exe") and which("wslpath"):
        result = converter(["wslpath", "-w", str(target)], check=True, capture_output=True, text=True)
        command = ["explorer.exe", result.stdout.strip()]
    elif configured_opener:
        command = [*shlex.split(configured_opener), str(target)]
    elif which("xdg-open"):
        command = ["xdg-open", str(target)]
    else:
        raise PreviewOpenError("no local preview opener is available; configure preview.opener")
    launcher(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return command


def inspect_handoff(handoff: Path) -> dict[str, Any]:
    manifest = _read_json(handoff / "manifest.json")
    receipt_path = next(iter(sorted(handoff.glob("**/recipe_receipt.json"))), None)
    if receipt_path is None:
        workflow_receipt = handoff / "workflow_receipt.json"
        receipt_path = workflow_receipt if workflow_receipt.is_file() else None
    receipt = _read_json(receipt_path) if receipt_path else {}
    resolution = _read_json(handoff / "source_resolution.json")
    preview: str | None
    try:
        preview = str(finalized_preview(handoff))
    except PreviewOpenError:
        preview = None
    resolution_by_asset = {
        item.get("logical_asset"): item
        for item in resolution.get("decisions", [])
    }
    asset_status = []
    for asset in receipt.get("assets", []):
        logical_asset = asset.get("asset_name")
        source = resolution_by_asset.get(logical_asset, {})
        action = asset.get("action", "unknown")
        asset_status.append(
            {
                "logical_asset": logical_asset,
                "selected_source": source.get("selected_source"),
                "local_asset_readiness": LOCAL_READINESS_BY_ACTION.get(action, "unknown"),
                "remote_source_status": source.get("selected_capability_status") or "unknown",
                "action": action,
                "reused": str(action).startswith("reuse_"),
                "acquired": action in {"acquire", "acquire_and_mosaic"},
            }
        )
    if not asset_status:
        asset_status = [
            {
                "logical_asset": item.get("logical_asset"),
                "selected_source": item.get("selected_source"),
                "local_asset_readiness": "unknown",
                "remote_source_status": item.get("selected_capability_status") or "unknown",
                "action": "unknown",
                "reused": False,
                "acquired": False,
            }
            for item in resolution.get("decisions", [])
        ]
    if manifest.get("workflow") == "human_development_change":
        asset_plan = _read_json(handoff / "asset_plan.json")
        asset_status = []
        for epoch in asset_plan.get("epochs", []):
            for logical_asset, key in (
                ("land_cover", "land_cover_path"),
                ("fractional_imperviousness", "imperviousness_path"),
            ):
                if not epoch.get(key):
                    continue
                asset_status.append(
                    {
                        "logical_asset": f"{logical_asset}_{epoch.get('year')}",
                        "selected_source": "usgs_annual_nlcd_local_pinned",
                        "local_asset_readiness": "ready_exact",
                        "remote_source_status": "credential_missing",
                        "action": "reuse_direct",
                        "reused": True,
                        "acquired": False,
                    }
                )
    source_choices = [
        {
            "logical_asset": row["logical_asset"],
            "selected_source": row["selected_source"],
            "status": row["remote_source_status"],
        }
        for row in asset_status
    ]
    return {
        "schema_version": "fasterraster.handoff-inspection/v2",
        "handoff": str(handoff),
        "status": manifest.get("operation_status") or receipt.get("final_status") or receipt.get("status") or "unknown",
        "network_bytes": manifest.get("network_bytes", receipt.get("total_network_bytes", receipt.get("network_bytes", 0))),
        "reused_bytes": manifest.get("reused_bytes", receipt.get("total_reused_bytes", 0)),
        "source_choices": source_choices,
        "asset_status": asset_status,
        "preview": preview,
        "warnings": [
            item
            for item in (
                manifest.get("warnings", []) if isinstance(manifest.get("warnings", []), list) else []
            )
        ],
        "output_paths": receipt.get("generated_output_paths", []),
    }
