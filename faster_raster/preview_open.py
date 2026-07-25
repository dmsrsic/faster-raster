from __future__ import annotations

import csv
import json
import shlex
import shutil
import subprocess
from pathlib import Path, PurePosixPath
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


def _relative_artifact(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = PurePosixPath(value.replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    return candidate.as_posix()


def _classification_summary(
    handoff: Path,
    receipt: dict[str, Any],
) -> dict[str, Any] | None:
    analysis = handoff / "analysis" / "classification"
    receipt_classification = receipt.get("classification", {})
    if not isinstance(receipt_classification, dict):
        receipt_classification = {}
    classification_present = (
        analysis.is_dir()
        or receipt.get("recipe_id") == "naip_cdl_classification_audit"
        or bool(receipt_classification)
    )
    if not classification_present:
        return None
    model = _read_json(analysis / "model_receipt.json")
    training = _read_json(analysis / "training_receipt.json")
    metrics = _read_json(analysis / "weak_label_metrics.json")
    disagreement = _read_json(analysis / "disagreement_summary.json")
    publication = receipt_classification.get("publication", {})
    if not isinstance(publication, dict):
        publication = {}
    missing: list[str] = []
    for name, value in (
        ("model_receipt", model),
        ("training_receipt", training),
        ("weak_label_metrics", metrics),
        ("disagreement_summary", disagreement),
    ):
        if not value:
            missing.append(name)
    area_path = analysis / "class_area_inventory.csv"
    hectares: dict[str, float] = {}
    if area_path.is_file():
        try:
            with area_path.open("r", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    label = row.get("predicted_class_name")
                    value = row.get("hectares")
                    if label and value is not None:
                        hectares[label] = float(value)
        except (OSError, TypeError, ValueError):
            missing.append("class_area_inventory")
            hectares = {}
    else:
        missing.append("class_area_inventory")
    low_confidence = disagreement.get("low_confidence_fraction")
    classified_coverage = (
        1.0 - float(low_confidence)
        if isinstance(low_confidence, (int, float))
        else None
    )
    mapping_hash = model.get("mapping_sha256")
    return {
        "available": any((model, training, metrics, disagreement, hectares)),
        "classifier_backend": model.get("backend"),
        "mapping_id": model.get("mapping_id"),
        "mapping_sha256": mapping_hash,
        "mapping_sha256_abbreviated": (
            str(mapping_hash)[:12] if mapping_hash else None
        ),
        "train_samples": training.get("train_sample_total"),
        "holdout_samples": training.get("holdout_sample_total"),
        "weak_label_overall_agreement": metrics.get("overall_agreement"),
        "macro_f1": metrics.get("macro_f1"),
        "cohen_kappa": metrics.get("cohen_kappa"),
        "confidence_threshold": publication.get("confidence_threshold"),
        "classified_coverage": classified_coverage,
        "uncertain_fraction": low_confidence,
        "high_confidence_disagreement_fraction": disagreement.get(
            "high_confidence_disagreement_fraction"
        ),
        "predicted_hectares_by_class": hectares,
        "missing_fields": sorted(set(missing)),
    }


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
    asset_plan = _read_json(handoff / "asset_plan.json")
    status = (
        manifest.get("operation_status")
        or receipt.get("final_status")
        or receipt.get("status")
        or "unknown"
    )
    completed = status in {"completed", "PASS"}
    preview: str | None
    try:
        preview = str(finalized_preview(handoff))
    except PreviewOpenError:
        preview = None
    resolution_by_asset = {
        item.get("logical_asset"): item
        for item in resolution.get("decisions", [])
    }
    planned_assets = {
        item.get("asset_name"): item
        for item in asset_plan.get("assets", [])
        if item.get("asset_name")
    }
    executed_assets = {
        item.get("asset_name"): item
        for item in receipt.get("assets", [])
        if item.get("asset_name")
    }
    ordered_asset_names = [
        *planned_assets,
        *(
            name
            for name in executed_assets
            if name not in planned_assets
        ),
    ]
    asset_status = []
    for logical_asset in ordered_asset_names:
        planned = planned_assets.get(logical_asset, {})
        asset = executed_assets.get(logical_asset, {})
        source = resolution_by_asset.get(logical_asset, {})
        planned_action = planned.get("action", asset.get("action", "unknown"))
        action = asset.get("action", planned_action)
        relative_output = _relative_artifact(asset.get("output_path"))
        checksum = asset.get("sha256")
        output_exists = bool(
            relative_output
            and (handoff / PurePosixPath(relative_output)).is_file()
        )
        verification_recorded = asset.get("validation_result")
        verification_passed = bool(
            completed
            and verification_recorded == "PASS"
            and output_exists
            and isinstance(checksum, str)
            and len(checksum) == 64
        )
        if not asset:
            execution_action = "not_run"
        elif verification_passed and str(action).startswith("reuse_"):
            execution_action = "reused"
        elif verification_passed and action in {"acquire", "acquire_and_mosaic"}:
            execution_action = "acquired"
        elif verification_recorded and verification_recorded != "PASS":
            execution_action = "failed"
        else:
            execution_action = "not_verified"
        asset_status.append(
            {
                "logical_asset": logical_asset,
                "selected_source": source.get("selected_source"),
                "local_asset_readiness": LOCAL_READINESS_BY_ACTION.get(
                    planned_action,
                    "unknown",
                ),
                "remote_source_status": source.get("selected_capability_status") or "unknown",
                "action": action,
                "initial_local_asset_readiness": LOCAL_READINESS_BY_ACTION.get(
                    planned_action,
                    "unknown",
                ),
                "planned_action": planned_action,
                "execution_action": execution_action,
                "final_asset_verification": (
                    "PASS"
                    if verification_passed
                    else (
                        "FAIL"
                        if execution_action == "failed"
                        else "NOT_VERIFIED"
                    )
                ),
                "final_local_artifact": (
                    relative_output if verification_passed else None
                ),
                "network_bytes": int(asset.get("bytes_downloaded", 0) or 0),
                "checksum": checksum if verification_passed else None,
                "reused": execution_action == "reused",
                "acquired": execution_action == "acquired",
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
                "initial_local_asset_readiness": "unknown",
                "planned_action": "unknown",
                "execution_action": "not_run",
                "final_asset_verification": "NOT_VERIFIED",
                "final_local_artifact": None,
                "network_bytes": 0,
                "checksum": None,
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
                        "initial_local_asset_readiness": "ready_exact",
                        "planned_action": "reuse_direct",
                        "execution_action": "reused",
                        "final_asset_verification": "PASS",
                        "final_local_artifact": _relative_artifact(epoch.get(key)),
                        "network_bytes": 0,
                        "checksum": None,
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
        "status": status,
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
        "classification": _classification_summary(handoff, receipt),
    }
