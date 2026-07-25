from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from faster_raster.ag_classification_contracts import (
    CLASSIFICATION_SCIENTIFIC_CLAIM,
    CLASSIFICATION_UNSUPPORTED_CLAIMS,
    CDL_SURFACE_SUPERCLASSES,
)
from faster_raster.ag_assets import asset_plan_document, compile_asset_plan, discover_cached_assets
from faster_raster.ag_geography import (
    asset_safety_profile,
    estimate_uncompressed_asset_bytes,
)
from faster_raster.ag_recipes import load_named_recipe
from faster_raster.local_config import (
    ConfigDocument,
    load_config_file,
    normalized_config_paths,
    resolved_config_document,
)
from faster_raster.local_paths import LocalPaths
from faster_raster.source_capabilities import (
    SourceDefinition,
    load_capability_profile,
    shipped_source_definitions,
    source_evidence_state,
    write_profile_atomic,
)
from faster_raster.workfiles import HumanDevelopmentWorkfileSpec, Workfile


ASSET_LABELS = {
    "natural": "Natural imagery",
    "naip_multispectral": "Raw four-band NAIP imagery",
    "ndvi": "Vegetation index imagery",
    "cdl_classes": "Crop classes",
    "cdl_color": "Crop class colors",
    "hillshade": "Terrain",
}

PIN_GROUPS = {
    "natural": "natural_imagery",
    "naip_multispectral": "natural_imagery",
    "ndvi": "natural_imagery",
    "cdl_classes": "crop_classes",
    "cdl_color": "crop_classes",
    "hillshade": "terrain",
}

FATAL_SOURCE_STATUSES = {
    "credential_missing",
    "authentication_failed",
    "unreachable",
    "timeout",
    "rate_limited",
    "service_error",
    "unsupported_local_format",
    "unsupported_local_driver",
    "invalid_response",
    "disabled_by_user",
    "future_unverified",
}

LOCAL_READINESS_BY_ACTION = {
    "reuse_direct": "ready_exact",
    "reuse_crop": "ready_requires_crop",
    "reuse_reproject": "ready_requires_reprojection",
    "reuse_crop_reproject": "ready_requires_crop_reprojection",
    "acquire": "missing",
    "acquire_and_mosaic": "partial_only",
    "reject": "missing",
}


def _layer_values(document: ConfigDocument, explicit: Mapping[str, Any]) -> dict[str, tuple[Any, str]]:
    result: dict[str, tuple[Any, str]] = {}
    execution = explicit.get("execution", {})
    if "reuse_mode" in execution:
        result["reuse_mode"] = (document.execution.reuse_mode, "execution.reuse_mode")
    if "default_byte_ceiling" in execution:
        result["maximum_download_mb"] = (
            document.execution.default_byte_ceiling / 1_000_000,
            "execution.default_byte_ceiling",
        )
    if "service_tile_size" in execution:
        result["service_tile_size"] = (document.execution.service_tile_size, "execution.service_tile_size")
    if "maximum_parallel_tasks" in execution:
        result["maximum_parallel_tasks"] = (
            document.execution.maximum_parallel_tasks,
            "execution.maximum_parallel_tasks",
        )
    preview = explicit.get("preview", {})
    if "open_when_complete" in preview:
        result["open_when_complete"] = (document.preview.open_when_complete, "preview.open_when_complete")
    sources = explicit.get("sources", {})
    if "offline" in sources:
        result["offline"] = (document.sources.offline, "sources.offline")
    paths = explicit.get("paths", {})
    for key in ("cache_root", "state_root", "temporary_root"):
        if key in paths:
            result[key] = (getattr(document.paths, key), f"paths.{key}")
    return result


def _set_value(
    resolved: dict[str, dict[str, Any]],
    key: str,
    value: Any,
    *,
    origin: str,
    original_key: str,
    source_file: Path | None = None,
    default: bool = False,
    recommended: bool = False,
    overridden: bool = False,
) -> None:
    resolved[key] = {
        "value": value,
        "origin": origin,
        "key": original_key,
        "source_file": str(source_file) if source_file else None,
        "default": default,
        "recommended": recommended,
        "explicitly_overridden": overridden,
    }


def compile_resolved_configuration(
    repository_root: Path,
    workfile: Workfile,
    paths: LocalPaths,
    *,
    cli_overrides: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], ConfigDocument]:
    recipe = load_named_recipe(repository_root, workfile.spec.workflow_id)
    effective_config, config_files = resolved_config_document(paths)
    normalized = normalized_config_paths(effective_config, paths)
    values: dict[str, dict[str, Any]] = {}
    source_defaults = {
        "reuse_mode": "auto",
        "maximum_download_mb": 250.0,
        "service_tile_size": 1800,
        "maximum_parallel_tasks": 1,
        "resolution_m": None,
        "preview": True,
        "open_when_complete": False,
        "offline": False,
        "cache_root": str(paths.cache_home),
        "state_root": str(paths.state_home),
        "temporary_root": str(paths.temporary_root),
    }
    for key, value in source_defaults.items():
        _set_value(values, key, value, origin="source_defaults", original_key=key, default=True)

    workflow_defaults = {
        "maximum_download_mb": recipe.defaults.max_total_bytes / 1_000_000,
        "service_tile_size": recipe.defaults.service_tile_size,
        "resolution_m": recipe.defaults.naip_resolution_meters,
    }
    for key, value in workflow_defaults.items():
        _set_value(
            values,
            key,
            value,
            origin="workflow_defaults",
            original_key=f"recipe.defaults.{key}",
            source_file=repository_root / "recipes" / "ag" / f"{recipe.recipe_id}.json",
            default=True,
        )

    for layer, config_path in (("user_configuration", paths.user_config), ("project_configuration", paths.project_config)):
        if config_path is None or not config_path.is_file():
            continue
        document = load_config_file(config_path)
        explicit = document.model_dump(mode="json", exclude_unset=True)
        for key, (value, original_key) in _layer_values(document, explicit).items():
            _set_value(values, key, value, origin=layer, original_key=original_key, source_file=config_path)

    work_values: dict[str, tuple[Any, str]] = {}
    if "data" in workfile.front_matter and "reuse" in workfile.front_matter["data"]:
        work_values["reuse_mode"] = (workfile.spec.data.reuse, "data.reuse")
    if "limits" in workfile.front_matter and "maximum_download_mb" in workfile.front_matter["limits"]:
        work_values["maximum_download_mb"] = (
            workfile.spec.limits.maximum_download_mb,
            "limits.maximum_download_mb",
        )
    if "outputs" in workfile.front_matter and "preview" in workfile.front_matter["outputs"]:
        work_values["preview"] = (workfile.spec.outputs.preview, "outputs.preview")
    if "outputs" in workfile.front_matter and "open_when_complete" in workfile.front_matter["outputs"]:
        work_values["open_when_complete"] = (
            workfile.spec.outputs.open_when_complete,
            "outputs.open_when_complete",
        )
    if workfile.spec.processing.resolution_m is not None:
        work_values["resolution_m"] = (workfile.spec.processing.resolution_m, "processing.resolution_m")
    if workfile.spec.processing.service_tile_size is not None:
        work_values["service_tile_size"] = (
            workfile.spec.processing.service_tile_size,
            "processing.service_tile_size",
        )
    if workfile.spec.processing.maximum_parallel_tasks is not None:
        work_values["maximum_parallel_tasks"] = (
            workfile.spec.processing.maximum_parallel_tasks,
            "processing.maximum_parallel_tasks",
        )
    for key, (value, original_key) in work_values.items():
        _set_value(values, key, value, origin="workfile", original_key=original_key, source_file=workfile.path)

    for key, value in (cli_overrides or {}).items():
        if value is not None:
            _set_value(values, key, value, origin="cli_override", original_key=key, overridden=True)

    # Ensure dynamic defaults reflect the fully merged local configuration even when a path was not explicit.
    for key, path in normalized.items():
        if values[key]["origin"] in {"source_defaults", "workflow_defaults"}:
            values[key]["value"] = str(path)
    return {
        "schema_version": "fasterraster.resolved-config/v1",
        "workfile": str(workfile.path),
        "workflow": recipe.recipe_id,
        "configuration_files": [str(path) for path in config_files],
        "precedence": [
            "cli_override",
            "workfile",
            "project_configuration",
            "user_configuration",
            "workflow_defaults",
            "source_defaults",
        ],
        "values": values,
    }, effective_config


def _resolve_alias(value: str, definitions: Mapping[str, SourceDefinition]) -> str:
    for source_id, definition in definitions.items():
        if value == source_id or value in definition.aliases:
            return source_id
    return value


def _pin_for_asset(workfile: Workfile, asset: str) -> str | None:
    sources = workfile.spec.sources
    direct = getattr(sources, asset, None)
    if direct:
        return direct
    return getattr(sources, PIN_GROUPS.get(asset, ""), None) if PIN_GROUPS.get(asset) else None


def resolve_study_sources(
    workfile: Workfile,
    recipe_assets: list[str],
    config: ConfigDocument,
    profile: Mapping[str, Any] | None,
    *,
    definitions: Mapping[str, SourceDefinition] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    catalog = dict(definitions or shipped_source_definitions())
    profile_sources = dict((profile or {}).get("sources", {}))
    current = now or datetime.now(timezone.utc)
    preference = [
        _resolve_alias(value, catalog)
        for value in [*workfile.spec.sources.prefer, *config.sources.preference_order]
    ]
    deny = {
        _resolve_alias(value, catalog)
        for value in [*workfile.spec.sources.deny, *config.sources.denylist]
    }
    allow = {_resolve_alias(value, catalog) for value in config.sources.allowlist}
    decisions: list[dict[str, Any]] = []
    for asset in recipe_assets:
        candidates: list[dict[str, Any]] = []
        pinned = _pin_for_asset(workfile, asset) if workfile.spec.sources.policy == "pinned" else None
        pinned_id = _resolve_alias(pinned, catalog) if pinned else None
        definitions_for_asset = [item for item in catalog.values() if asset in item.logical_assets]
        for definition in sorted(definitions_for_asset, key=lambda item: item.source_id):
            record = profile_sources.get(definition.source_id)
            status = str(record.get("status", "unknown")) if record else "unknown"
            evidence = source_evidence_state(record, config, now=current) if record else {
                "stale": True,
                "age_seconds": None,
                "ttl_hours": None,
            }
            reasons: list[str] = []
            if pinned_id and definition.source_id != pinned_id:
                reasons.append(f"workfile pinned {asset} to {pinned_id}")
            if allow and definition.source_id not in allow:
                reasons.append("not in user source allowlist")
            if definition.source_id in deny:
                reasons.append("denied by user or workfile")
            if not definition.selectable or definition.access_category == "future_unverified":
                reasons.append("future-unverified sources are not selectable")
            if status in FATAL_SOURCE_STATUSES:
                reasons.append(f"capability status is {status}")
            compatibility = (record or {}).get("format_compatibility", {})
            if compatibility.get("compatible") is False:
                reasons.append("required local raster driver is unavailable")
            candidates.append(
                {
                    "source_id": definition.source_id,
                    "provider": definition.provider,
                    "product": definition.product,
                    "access_category": definition.access_category,
                    "capability_status": status,
                    "capability_timestamp": (record or {}).get("probe_timestamp"),
                    "evidence_age_seconds": evidence["age_seconds"],
                    "evidence_stale": evidence["stale"],
                    "credentials_required": bool(definition.credential_env),
                    "credentials_present": (record or {}).get("credential_state") == "present",
                    "local_driver_compatible": compatibility.get("compatible"),
                    "rejected": bool(reasons),
                    "rejection_reasons": reasons,
                }
            )
        usable = [candidate for candidate in candidates if not candidate["rejected"]]

        def rank(candidate: Mapping[str, Any]) -> tuple[int, int, str]:
            preferred = preference.index(candidate["source_id"]) if candidate["source_id"] in preference else len(preference)
            availability = 0 if candidate["capability_status"] in {"available", "available_unverified_auth"} else 1
            return preferred, availability, str(candidate["source_id"])

        usable.sort(key=rank)
        selected = usable[0] if usable else None
        provisional = bool(
            selected
            and (
                selected["evidence_stale"]
                or selected["capability_status"] not in {"available", "available_unverified_auth"}
            )
        )
        decisions.append(
            {
                "logical_asset": asset,
                "display_name": ASSET_LABELS.get(asset, asset.replace("_", " ").title()),
                "candidates_considered": candidates,
                "candidates_rejected": [item for item in candidates if item["rejected"]],
                "selected_source": selected["source_id"] if selected else None,
                "selected_capability_status": selected["capability_status"] if selected else None,
                "selected_fallback": bool(selected and preference and selected["source_id"] != preference[0]),
                "provisional": provisional,
                "live_execution_must_revalidate": bool(selected),
                "blocking_reason": None if selected else "no compatible selectable source remains",
            }
        )
    return {
        "schema_version": "fasterraster.source-resolution/v1",
        "workfile": str(workfile.path),
        "profile_path": None,
        "profile_last_refresh": (profile or {}).get("last_refresh_at"),
        "network_requests": 0,
        "decisions": decisions,
        "blocking": any(item["selected_source"] is None for item in decisions),
    }


def compile_study_plan(
    repository_root: Path,
    workfile: Workfile,
    paths: LocalPaths,
    *,
    cli_overrides: Mapping[str, Any] | None = None,
    output_dir: Path | None = None,
    inventory_root: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if isinstance(workfile.spec, HumanDevelopmentWorkfileSpec):
        try:
            from faster_raster.human_development_workflow import compile_human_development_plan
        except (ImportError, ModuleNotFoundError) as exc:
            missing = getattr(exc, "name", None) or type(exc).__name__
            raise RuntimeError(
                "human_development_change requires the installed NumPy and Rasterio dependencies "
                f"(missing or unloadable: {missing}); install FasterRaster through its package metadata"
            ) from exc
        return compile_human_development_plan(
            repository_root,
            workfile,
            paths,
            cli_overrides=cli_overrides,
            output_dir=output_dir,
        )
    resolved, config = compile_resolved_configuration(
        repository_root,
        workfile,
        paths,
        cli_overrides=cli_overrides,
    )
    recipe = load_named_recipe(repository_root, workfile.spec.workflow_id)
    profile = load_capability_profile(paths.capability_profile)
    resolution = resolve_study_sources(
        workfile,
        list(recipe.required_assets),
        config,
        profile,
        now=now,
    )
    resolution["profile_path"] = str(paths.capability_profile) if profile else None
    cache = inventory_root or Path(
        os.environ.get("FASTERRASTER_AG_CACHE_ROOT", str(repository_root / "outputs" / "handoffs"))
    )
    inventory = [] if resolved["values"]["reuse_mode"]["value"] == "never" else discover_cached_assets(cache)
    asset_decisions = compile_asset_plan(
        recipe,
        inventory,
        tuple(workfile.spec.area.bbox),
        workfile.spec.time.crop_year,
        resolved["values"]["reuse_mode"]["value"],
    )
    asset_plan = asset_plan_document(
        recipe,
        asset_decisions,
        bbox=tuple(workfile.spec.area.bbox),
        start=workfile.spec.time.start.isoformat(),
        end=workfile.spec.time.end.isoformat(),
        year=workfile.spec.time.crop_year,
        reuse_mode=resolved["values"]["reuse_mode"]["value"],
        requested_resolution_m=float(resolved["values"]["resolution_m"]["value"]),
    )
    source_by_asset = {item["logical_asset"]: item for item in resolution["decisions"]}
    rows = []
    for decision in asset_decisions:
        source = source_by_asset[decision.asset_name]
        is_local_reuse = decision.action.startswith("reuse_")
        remote_status = source["selected_capability_status"] or (
            source["candidates_considered"][0]["capability_status"]
            if source["candidates_considered"]
            else "unknown"
        )
        remote_required = decision.action in {"acquire", "acquire_and_mosaic"}
        remote_blocking = remote_required and source["selected_source"] is None
        rows.append(
            {
                "data": ASSET_LABELS.get(decision.asset_name, decision.asset_name),
                "logical_asset": decision.asset_name,
                "source": source["selected_source"],
                "local_asset_readiness": LOCAL_READINESS_BY_ACTION[decision.action],
                "remote_source_status": remote_status,
                "remote_source_required": remote_required,
                "remote_source_blocking": remote_blocking,
                "action": decision.action,
                "reason": decision.reason,
                "provisional": bool(remote_required and source["provisional"]),
                "reused": is_local_reuse,
                "acquired": remote_required,
            }
        )
    plan = {
        "schema_version": "fasterraster.study-plan/v1",
        "created_at": (now or datetime.now(timezone.utc)).isoformat(),
        "workfile": str(workfile.path),
        "study_name": workfile.spec.name,
        "workflow": recipe.recipe_id,
        "offline_planning": True,
        "network_requests": 0,
        "blocking": any(row["remote_source_blocking"] for row in rows)
        or any(item.action == "reject" for item in asset_decisions),
        "rows": rows,
        "asset_plan": asset_plan,
        "maximum_download_bytes": int(resolved["values"]["maximum_download_mb"]["value"] * 1_000_000),
    }
    if recipe.schema_version == 3:
        from faster_raster.ag_classification import classification_dependency_status

        requested_resolution = float(
            resolved["values"]["resolution_m"]["value"]
        )
        dependency = classification_dependency_status()
        estimated_transfer = estimate_uncompressed_asset_bytes(
            tuple(workfile.spec.area.bbox),
            asset_safety_profile(
                recipe.required_assets,
                requested_resolution,
            ),
        )
        plan["classification"] = {
            "raw_four_band_naip": {
                "asset": "naip_multispectral",
                "band_ids": [0, 1, 2, 3],
                "requested_resolution_m": requested_resolution,
            },
            "weak_supervision": "same-year USDA CDL superclasses",
            "mapping_id": CDL_SURFACE_SUPERCLASSES.mapping_id,
            "mapping_sha256": CDL_SURFACE_SUPERCLASSES.sha256,
            "estimated_uncompressed_transfer_bytes": estimated_transfer,
            "dependency_readiness": dependency,
            "scientific_claim": CLASSIFICATION_SCIENTIFIC_CLAIM,
            "unsupported_claims": list(CLASSIFICATION_UNSUPPORTED_CLAIMS),
        }
        if not dependency["available"]:
            plan["blocking"] = True
            plan["classification"]["blocking_reason"] = (
                "classification extra is unavailable before raster transfer"
            )
    destination = output_dir or Path(resolved["values"]["state_root"]["value"]) / "plans" / workfile.spec.name
    destination.mkdir(parents=True, exist_ok=True)
    write_profile_atomic(destination / "resolved_config.json", resolved)
    write_profile_atomic(destination / "source_resolution.json", resolution)
    write_profile_atomic(destination / "plan.json", plan)
    plan["artifacts"] = {
        "directory": str(destination),
        "resolved_config": str(destination / "resolved_config.json"),
        "source_resolution": str(destination / "source_resolution.json"),
        "plan": str(destination / "plan.json"),
    }
    plan["resolved_config"] = resolved
    plan["source_resolution"] = resolution
    return plan
