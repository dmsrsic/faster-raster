from __future__ import annotations

import csv
import json
import shlex
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Sequence

from faster_raster.contract_repair import intervention_reference
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


def _hybrid_summary(
    handoff: Path,
    receipt: dict[str, Any],
) -> dict[str, Any] | None:
    hybrid_receipt = receipt.get("index_guided_hybrid")
    analysis = handoff / "analysis" / "indices"
    if not isinstance(hybrid_receipt, dict) and not analysis.is_dir():
        return None
    registry = _read_json(analysis / "index_registry.json")
    capability = _read_json(analysis / "index_capability_report.json")
    plan = _read_json(analysis / "index_plan.json")
    statistics = _read_json(analysis / "index_statistics.json")
    ranking = _read_json(analysis / "index_candidate_ranking.json")
    rules = _read_json(analysis / "specialist_class_rules.json")
    overlap = _read_json(analysis / "specialist_overlap_matrix.json")
    inventory = _read_json(analysis / "hybrid_class_inventory.json")
    validation = _read_json(analysis / "index_validation_metrics.json")
    selection = _read_json(
        handoff / "receipts" / "index_selection_receipt.json"
    )
    hybrid = _read_json(
        handoff / "receipts" / "hybrid_classification_receipt.json"
    )
    definitions = {
        item.get("index_id"): item
        for item in registry.get("indices", [])
        if isinstance(item, dict) and item.get("index_id")
    }
    calculated = []
    for index_id in sorted(statistics):
        definition = definitions.get(index_id, {})
        index_statistics = statistics.get(index_id, {})
        calculated.append(
            {
                "index_id": index_id,
                "formula": definition.get("formula"),
                "formula_sha256": definition.get("content_sha256"),
                "required_bands": definition.get("required_bands", []),
                "valid_pixel_count": index_statistics.get(
                    "valid_pixel_count"
                ),
                "minimum": index_statistics.get("minimum"),
                "maximum": index_statistics.get("maximum"),
                "mean": index_statistics.get("mean"),
                "standard_deviation": index_statistics.get(
                    "standard_deviation"
                ),
                "quantiles": index_statistics.get("quantiles"),
            }
        )
    specialist_classes = []
    for item in rules.get("classes", []):
        if not isinstance(item, dict):
            continue
        strategy = item.get("strategy_contract", {})
        if not isinstance(strategy, dict):
            strategy = {}
        specialist_classes.append(
            {
                "class_id": item.get("class_id"),
                "label": item.get("label"),
                "output_code": item.get("output_code"),
                "eligible_parent_general_classes": item.get(
                    "eligible_parent_general_classes",
                    [],
                ),
                "priority": item.get("priority"),
                "enabled": item.get("enabled"),
                "candidate_pixels": item.get("candidate_pixels"),
                "score_semantics": strategy.get("score_semantics"),
                "strategy": strategy.get("strategy"),
                "calibration_source": (
                    item.get("calibration") or {}
                ).get("source"),
            }
        )
    selected = selection.get("selected")
    selected = selected if isinstance(selected, dict) else {}
    outer = selection.get("outer_holdout")
    outer = outer if isinstance(outer, dict) else {}
    actual_imagery = receipt.get("actual_imagery")
    actual_imagery = (
        actual_imagery if isinstance(actual_imagery, dict) else {}
    )
    resolved_location = receipt.get("resolved_location")
    resolved_location = (
        resolved_location if isinstance(resolved_location, dict) else {}
    )
    return {
        "available": bool(
            registry
            or capability
            or plan
            or statistics
            or rules
            or hybrid
        ),
        "registry_version": registry.get("schema_version"),
        "registry_sha256": registry.get("registry_sha256"),
        "source_compatibility_status": capability.get("status"),
        "source_bands": (
            (capability.get("source") or {}).get("actual_band_order")
        ),
        "calculated_indices": calculated,
        "selection_mode": plan.get("selection_mode"),
        "selection_status": selection.get("status"),
        "candidate_count": selection.get(
            "candidate_count",
            ranking.get("candidate_count"),
        ),
        "selected_candidate": selected.get("candidate_id"),
        "selected_indices": selected.get("index_ids"),
        "selected_threshold": selected.get("threshold"),
        "selected_direction": selected.get("direction"),
        "selected_weights": selected.get("weights"),
        "inner_selection": selection.get("inner_selection"),
        "untouched_holdout_metrics": outer.get("metrics"),
        "specialist_classes": specialist_classes,
        "overlaps": overlap.get("overlaps", []),
        "unresolved_pixels": hybrid.get("unresolved_pixels"),
        "arbitration": hybrid.get("arbitration"),
        "final_class_inventory": inventory.get("classes", []),
        "validation": validation,
        "imagery_year": actual_imagery.get("year"),
        "cdl_year": receipt.get("requested_cdl_year"),
        "temporal_mismatch": (
            actual_imagery.get("year") is not None
            and receipt.get("requested_cdl_year") is not None
            and actual_imagery.get("year")
            != receipt.get("requested_cdl_year")
        ),
        "analysis_aoi_recorded": (
            resolved_location.get("analysis_aoi_epsg_4326") is not None
        ),
        "machine_readable_evidence": {
            "registry": "analysis/indices/index_registry.json",
            "capability": "analysis/indices/index_capability_report.json",
            "plan": "analysis/indices/index_plan.json",
            "selection": "receipts/index_selection_receipt.json",
            "specialist_rules": "analysis/indices/specialist_class_rules.json",
            "hybrid": "receipts/hybrid_classification_receipt.json",
        },
    }



def _environmental_correlation_summary(
    handoff: Path,
    receipt: dict[str, Any],
) -> dict[str, Any] | None:
    path = handoff / "analysis" / "correlation_summary.json"
    summary = _read_json(path)
    workflow = receipt.get("workflow")
    if not summary and workflow != "prism_dem_ndvi_correlation_audit":
        return None
    methods = summary.get("methods") if isinstance(summary, dict) else {}
    methods = methods if isinstance(methods, dict) else {}
    return {
        "available": bool(summary),
        "common_valid_cell_count": summary.get("common_valid_cell_count"),
        "precipitation_period": summary.get("precipitation_period"),
        "naip_acquisition_dates": summary.get("naip_acquisition_dates", []),
        "temporal_alignment": summary.get("temporal_alignment"),
        "target_crs": summary.get("target_crs"),
        "target_resolution_m": summary.get("target_resolution_m"),
        "pearson": methods.get("pearson"),
        "spearman_rank": methods.get("spearman_rank"),
        "partial_correlation": methods.get("partial_correlation"),
        "standardized_linear_model": methods.get("standardized_linear_model"),
        "scientific_claim": summary.get("scientific_claim"),
        "unsupported_claims": summary.get("unsupported_claims", []),
        "evidence": "analysis/correlation_summary.json",
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
    raw_intervention = receipt.get(
        "contract_repair",
        manifest.get("contract_repair"),
    )
    raw_intervention = (
        raw_intervention
        if isinstance(raw_intervention, dict)
        else None
    )
    repair = intervention_reference(raw_intervention)
    resolved_location = receipt.get(
        "resolved_location",
        manifest.get("resolved_location"),
    )
    resolved_location = (
        resolved_location
        if isinstance(resolved_location, dict)
        else {}
    )
    repair["actual_imagery"] = receipt.get(
        "actual_imagery",
        manifest.get("actual_imagery"),
    )
    repair["resolved_location"] = {
        "request_bbox_epsg_4326": resolved_location.get(
            "request_bbox_epsg_4326"
        ),
        "analysis_aoi_recorded": (
            resolved_location.get("analysis_aoi_epsg_4326") is not None
        ),
        "acquisition_uses_request_envelope": resolved_location.get(
            "acquisition_uses_request_envelope",
            False,
        ),
    }
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
        "index_guided_hybrid": _hybrid_summary(handoff, receipt),
        "environmental_correlation": _environmental_correlation_summary(handoff, receipt),
        "contract_repair": repair,
    }
