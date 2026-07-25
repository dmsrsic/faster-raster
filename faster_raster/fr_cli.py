from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from faster_raster import __version__
from faster_raster.ag_execution import (
    RecipeExecutionError,
    _regenerate_checksums,
    configured_handoff_root,
    execute_recipe,
)
from faster_raster.ag_recipes import RecipeLoadError, load_named_recipe
from faster_raster.local_config import (
    ConfigError,
    apply_config_updates,
    default_config,
    load_config_file,
    resolved_config_document,
    write_config_atomic,
)
from faster_raster.local_diagnostics import run_doctor
from faster_raster.local_paths import LocalPaths, resolve_local_paths
from faster_raster.preview_open import (
    PreviewOpenError,
    finalized_preview,
    inspect_handoff,
    open_local_preview,
    resolve_handoff,
)
from faster_raster.source_capabilities import (
    DEFAULT_GLOBAL_BYTE_CEILING,
    SOURCE_STATUSES,
    evaluate_sources,
    load_capability_profile,
    shipped_source_definitions,
    source_evidence_state,
    write_profile_atomic,
)
from faster_raster.study_planning import compile_study_plan
from faster_raster.study_templates import (
    get_study_template,
    list_study_templates,
    render_study_template,
    show_study_template,
)
from faster_raster.workfiles import (
    HumanDevelopmentWorkfileSpec,
    WorkfileError,
    load_workfile,
    workfile_template,
)


class CommandError(ValueError):
    pass


def _human_execute() -> Any:
    try:
        from faster_raster.human_development_workflow import execute_human_development
    except (ImportError, ModuleNotFoundError) as exc:
        missing = getattr(exc, "name", None) or type(exc).__name__
        raise CommandError(
            "human_development_change requires the installed NumPy and Rasterio dependencies "
            f"(missing or unloadable: {missing}); install FasterRaster through its package metadata"
        ) from exc
    return execute_human_development


def _human_publisher() -> tuple[Any, Any]:
    try:
        from faster_raster.human_development_publication import (
            PublicationOptions,
            publish_human_development_hybrid,
        )
    except (ImportError, ModuleNotFoundError) as exc:
        missing = getattr(exc, "name", None) or type(exc).__name__
        raise CommandError(
            "human-development publication requires NumPy, Rasterio, and "
            f"Pillow (missing or unloadable: {missing})"
        ) from exc
    return PublicationOptions, publish_human_development_hybrid


FRIENDLY_REMOTE_STATUS = {
    "available": "Available",
    "available_unverified_auth": "Available (authentication not exercised)",
    "unknown": "Not checked on this machine",
    "stale": "Last check is stale",
    "credential_missing": "Sign-in or credentials required",
    "authentication_failed": "Authentication failed",
    "unreachable": "Currently unreachable",
    "timeout": "Timed out during the last check",
    "rate_limited": "Temporarily rate limited",
    "service_error": "Service reported an error",
    "disabled_by_user": "Disabled in local configuration",
    "future_unverified": "Not yet supported for selection",
}

FRIENDLY_READINESS = {
    "ready_exact": "Ready locally",
    "ready_requires_crop": "Ready locally; crop required",
    "ready_requires_reprojection": "Ready locally; reprojection required",
    "ready_requires_crop_reprojection": "Ready locally; crop and reprojection required",
    "partial_only": "Only partial local coverage is available",
    "missing": "Not available locally",
}

FRIENDLY_ACTION = {
    "reuse_direct": "Reuse as-is",
    "reuse_crop": "Reuse and crop",
    "reuse_reproject": "Reuse and reproject",
    "reuse_crop_reproject": "Reuse, crop, and reproject",
    "acquire": "Download",
    "acquire_and_mosaic": "Download and mosaic",
    "reject": "Cannot continue under reuse-only policy",
}


def _friendly(labels: Mapping[str, str], value: Any, *, verbose: bool) -> str:
    exact = str(value or "unknown")
    label = labels.get(exact, exact.replace("_", " ").title())
    return f"{label} [{exact}]" if verbose else label



def repository_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def _csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _paths(project_root: Path | None = None) -> LocalPaths:
    return resolve_local_paths(project_root or Path.cwd())


def _doctor_text(report: Mapping[str, Any]) -> None:
    machine = report["machine"]
    print(f"FasterRaster doctor: {report['status']}")
    print(f"System: {machine['operating_system']} {machine['architecture']} (WSL: {machine['is_wsl']})")
    print(f"Python: {machine['python_version']}")
    print(f"GDAL: {report['gdal'].get('version') or 'missing'}")
    print(f"Raster drivers: {len(report['gdal'].get('drivers', []))}")
    print(f"Temporary fixtures removed: {report['temporary_artifacts_removed']}")
    for failure in report["failures"]:
        print(f"BLOCKING: {failure}")
    for warning in report["warnings"]:
        print(f"WARNING: {warning}")
    recommendations = report["recommendations"]
    facts = recommendations["observed_facts"]
    print(f"Heuristic: {recommendations['heuristic_version']} (applied: {recommendations['applied']})")
    print("Observed facts:")
    for key, value in facts.items():
        print(f"  {key}: {value}")
    print("Intermediate candidates and limiting factors:")
    for key, candidates in recommendations["intermediate_candidates"].items():
        limit = recommendations["limiting_factor"][key]
        print(f"  {key}: {candidates}; limiting factor: {limit}")
    print(f"Safety note: {recommendations['safety_note']}")
    print("Recommendations (not applied):")
    for key in ("maximum_parallel_tasks", "service_tile_size", "default_byte_ceiling", "workflow_hint"):
        item = recommendations[key]
        print(f"  {key}: {item['value']} — {item['reason']}")


def _source_rows(paths: LocalPaths, config: Any) -> list[dict[str, Any]]:
    catalog = shipped_source_definitions()
    profile = load_capability_profile(paths.capability_profile)
    records = (profile or {}).get("sources", {})
    rows: list[dict[str, Any]] = []
    for source_id, definition in catalog.items():
        record = records.get(source_id)
        status = str(record.get("status", "unknown")) if record else (
            "future_unverified" if definition.access_category == "future_unverified" else "unknown"
        )
        age = source_evidence_state(record, config) if record else {"stale": False, "age_seconds": None, "ttl_hours": None}
        rows.append(
            {
                "source_id": source_id,
                "provider": definition.provider,
                "product": definition.product,
                "access_category": definition.access_category,
                "status": "stale" if age["stale"] and status == "available" else status,
                "last_status": status,
                "stale": age["stale"],
                "evidence_age_seconds": age["age_seconds"],
                "credential_state": (record or {}).get("credential_state", "unknown"),
                "profile_exists": profile is not None,
            }
        )
    return rows


def _print_sources(
    rows: Sequence[Mapping[str, Any]],
    profile_path: Path,
    *,
    verbose: bool = False,
) -> None:
    print(f"Capability profile: {profile_path if profile_path.is_file() else 'not evaluated on this machine'}")
    print(f"{'PRODUCT':34} {'AVAILABILITY':42} SOURCE")
    for row in rows:
        status = _friendly(FRIENDLY_REMOTE_STATUS, row["status"], verbose=verbose)
        source = str(row["source_id"])
        if not verbose:
            source = str(row["provider"])
        print(f"{str(row['product'])[:34]:34} {status[:42]:42} {source}")
        if verbose:
            category = str(row["access_category"])
            print(f"  category: {category}; evidence age seconds: {row['evidence_age_seconds']}")
    if not profile_path.is_file():
        print("Source availability has not been evaluated on this machine.")
        print("Run fr doctor and fr sources evaluate.")


def _plan_text(plan: Mapping[str, Any], *, verbose: bool = False) -> None:
    print(f"Study: {plan['study_name']}")
    if plan.get("explanation"):
        request_count = int(plan.get("network_requests", 0))
        if request_count:
            print(
                f"Planning made {request_count} bounded metadata/catalog requests; "
                "no raster pixels were transferred."
            )
        else:
            print("Planning used complete verified cache evidence. No network requests were made.")
        asset_plan = plan["asset_plan"]
        print(
            f"Source: {asset_plan['source_id']}; mapping: {asset_plan['mapping_id']}; "
            f"ceiling: {asset_plan['maximum_download_bytes']:,} bytes"
        )
        for epoch in asset_plan["epochs"]:
            print(
                f"{epoch['year']}: coverage={epoch['exact_coverage_status']} "
                f"records={epoch['catalog_record_ids']} action={epoch['planned_action']} "
                f"cache={epoch['cache_state']}; semantic={epoch['source_semantic_type']}; "
                f"{epoch['source_crs']} -> {epoch['expected_export_crs']} "
                f"{epoch['expected_width']}x{epoch['expected_height']} @ "
                f"{epoch['expected_resolution_m']} m, {epoch['expected_resampling']}; "
                f"max={epoch['estimated_maximum_bytes']:,} bytes, "
                f"cumulative={epoch['cumulative_estimated_raster_bytes']:,} bytes"
            )
        context = asset_plan["context_imagery"]
        print(
            f"Context: year={context['year']} source={context['source_id']} "
            f"role={context['role']} action={context['planned_action']}"
        )
        if plan["blocking"]:
            print("BLOCKING: " + "; ".join(plan.get("blocking_reasons", [])))
        print(f"Plan artifacts: {plan['artifacts']['directory']}")
        return
    print("Planning used saved local evidence. No network requests were made.")
    print(f"{'DATA':24} {'LOCAL READINESS':34} {'REMOTE SOURCE':34} NEXT STEP")
    for row in plan["rows"]:
        local = _friendly(FRIENDLY_READINESS, row["local_asset_readiness"], verbose=verbose)
        remote = _friendly(FRIENDLY_REMOTE_STATUS, row["remote_source_status"], verbose=verbose)
        action = _friendly(FRIENDLY_ACTION, row["action"], verbose=verbose)
        print(f"{row['data'][:24]:24} {local[:34]:34} {remote[:34]:34} {action}")
        if verbose:
            print(f"  source: {row['source'] or 'none selected'}; reason: {row['reason']}")
    if any(row["provisional"] for row in plan["rows"]):
        print("A required download has provisional source evidence; cook must revalidate it.")
    if plan.get("classification"):
        classification = plan["classification"]
        raw = classification["raw_four_band_naip"]
        dependency = classification["dependency_readiness"]
        print(
            "Classification: raw NAIP bands "
            f"{raw['band_ids']} at {raw['requested_resolution_m']:g} m; "
            f"CDL weak supervision; mapping {classification['mapping_id']} "
            f"{classification['mapping_sha256'][:12]}…"
        )
        print(
            "Estimated uncompressed transfer: "
            f"{classification['estimated_uncompressed_transfer_bytes']:,} bytes; "
            f"classifier dependency ready: {dependency['available']}"
        )
        print("Scientific claim: " + classification["scientific_claim"])
        print("Unsupported claims: " + "; ".join(classification["unsupported_claims"]))
    if plan["blocking"]:
        print("BLOCKING: a required local asset or required download source is unavailable.")
    print(f"Plan artifacts: {plan['artifacts']['directory']}")


def _cli_overrides(args: argparse.Namespace) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if getattr(args, "maximum_download_mb", None) is not None:
        result["maximum_download_mb"] = args.maximum_download_mb
    if getattr(args, "reuse", None) is not None:
        result["reuse_mode"] = args.reuse
    if getattr(args, "service_tile_size", None) is not None:
        result["service_tile_size"] = args.service_tile_size
    if getattr(args, "maximum_parallel_tasks", None) is not None:
        result["maximum_parallel_tasks"] = args.maximum_parallel_tasks
    if getattr(args, "offline", None):
        result["offline"] = True
    if getattr(args, "open_when_complete", None) is not None:
        result["open_when_complete"] = args.open_when_complete
    return result


def _refresh_if_requested(args: argparse.Namespace, paths: LocalPaths, config: Any) -> None:
    if not getattr(args, "refresh_sources", False):
        return
    evaluate_sources(
        paths,
        config,
        offline=bool(getattr(args, "offline", False)),
        global_byte_ceiling=DEFAULT_GLOBAL_BYTE_CEILING,
    )


def command_templates(args: argparse.Namespace) -> int:
    if args.templates_command == "list":
        templates = list_study_templates()
        if args.json:
            _json({"templates": templates})
        else:
            for item in templates:
                print(f"{item['template_id']:<32} {item['summary']}")
        return 0
    template = get_study_template(args.template_id)
    if args.json:
        _json({"template": template.as_dict(), "workfile": show_study_template(args.template_id)})
    else:
        print(show_study_template(args.template_id), end="")
    return 0


def command_init(args: argparse.Namespace) -> int:
    destination = args.workfile.resolve()
    if destination.exists() and not args.force:
        raise CommandError(f"refusing to overwrite existing workfile: {destination}; use --force")
    destination.parent.mkdir(parents=True, exist_ok=True)
    name = destination.name
    for suffix in (".fr.md", ".md"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    if args.template:
        content = render_study_template(
            args.template,
            name=args.name or name,
            bbox=args.bbox,
            years=args.years,
        )
    else:
        if args.name is not None or args.bbox is not None or args.years is not None:
            raise CommandError("--name, --bbox, and --years require --template")
        content = workfile_template(name)
    destination.write_text(content, encoding="utf-8")
    print(f"Created study workfile: {destination}")
    if args.template:
        print(f"Template: {args.template}")
    if args.project_config:
        paths = _paths(destination.parent)
        assert paths.project_config is not None
        if paths.project_config.exists() and not args.force:
            raise CommandError(f"refusing to overwrite existing project configuration: {paths.project_config}")
        write_config_atomic(paths.project_config, default_config(paths))
        print(f"Created project configuration: {paths.project_config}")
    print("No network requests were made.")
    return 0


def command_configure(args: argparse.Namespace) -> int:
    paths = _paths(Path.cwd())
    target = paths.project_config if args.project else paths.user_config
    assert target is not None
    if args.path:
        print(target)
        return 0
    if args.validate:
        load_config_file(paths.user_config)
        load_config_file(paths.project_config)
        print("Configuration is valid.")
        return 0
    existing = load_config_file(target) if target.is_file() else default_config(paths)
    updates: dict[str, Any] = {}
    execution: dict[str, Any] = {}
    sources: dict[str, Any] = {}
    preview: dict[str, Any] = {}
    path_values: dict[str, Any] = {}
    for argument, key in (
        (args.maximum_download_mb, "default_byte_ceiling"),
        (args.service_tile_size, "service_tile_size"),
        (args.maximum_parallel_tasks, "maximum_parallel_tasks"),
    ):
        if argument is not None:
            execution[key] = int(argument * 1_000_000) if key == "default_byte_ceiling" else argument
    if args.reuse is not None:
        execution["reuse_mode"] = args.reuse
    if args.offline is not None:
        sources["offline"] = args.offline
    if args.source_preference is not None:
        sources["preference_order"] = _csv(args.source_preference)
    if args.source_allowlist is not None:
        sources["allowlist"] = _csv(args.source_allowlist)
    if args.source_denylist is not None:
        sources["denylist"] = _csv(args.source_denylist)
    if args.preview_open is not None:
        preview["open_when_complete"] = args.preview_open
    for value, key in ((args.cache_root, "cache_root"), (args.state_root, "state_root"), (args.temporary_root, "temporary_root")):
        if value is not None:
            path_values[key] = str(value.expanduser())
    if args.apply_recommendations:
        profile = load_capability_profile(paths.capability_profile)
        if not profile:
            raise CommandError("no capability profile exists; run fr doctor and fr sources evaluate first")
        recommendations = profile.get("recommended_execution_settings", {})
        for key in ("maximum_parallel_tasks", "service_tile_size", "default_byte_ceiling"):
            if key in recommendations:
                execution[key] = recommendations[key]["value"]
    if execution:
        updates["execution"] = execution
    if sources:
        updates["sources"] = sources
    if preview:
        updates["preview"] = preview
    if path_values:
        updates["paths"] = path_values
    if updates:
        updated = apply_config_updates(existing, updates)
        write_config_atomic(target, updated)
        print(f"Updated configuration: {target}")
    resolved, files = resolved_config_document(paths)
    if args.json:
        _json({"paths": {"user": str(paths.user_config), "project": str(paths.project_config)}, "files": files, "config": resolved.model_dump(mode="json")})
    elif args.show or not updates:
        print(f"User configuration: {paths.user_config}")
        print(f"Project configuration: {paths.project_config}")
        print(json.dumps(resolved.model_dump(mode="json"), indent=2, sort_keys=True))
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    paths = _paths(Path.cwd())
    report = run_doctor(paths, offline=args.offline)
    _json(report) if args.json else _doctor_text(report)
    return 1 if report["status"] == "FAIL" else 0


def command_sources(args: argparse.Namespace) -> int:
    paths = _paths(Path.cwd())
    config, _ = resolved_config_document(paths)
    if args.sources_command == "evaluate":
        selected: list[str] = []
        for value in args.source or []:
            selected.extend(_csv(value))
        report = evaluate_sources(
            paths,
            config,
            source_ids=selected or None,
            offline=args.offline,
            keep_probe_artifacts=args.keep_probe_artifacts,
            global_byte_ceiling=args.maximum_bytes,
        )
        if args.json:
            _json(report)
        else:
            print(f"Capability profile: {paths.capability_profile}")
            print(f"Network requests: {report['evaluation']['requests_made']}")
            print(f"Network bytes: {report['evaluation']['bytes_transferred']}")
            print(f"Temporary artifacts removed: {report['evaluation']['temporary_artifacts_removed']}")
            rows = _source_rows(paths, config)
            evaluated_ids = set(report["sources"])
            _print_sources([row for row in rows if row["source_id"] in evaluated_ids], paths.capability_profile, verbose=args.verbose)
        return 0
    rows = _source_rows(paths, config)
    if args.status:
        rows = [row for row in rows if row["status"] == args.status]
    if args.category:
        rows = [row for row in rows if row["access_category"] == args.category]
    if args.stale:
        rows = [row for row in rows if row["stale"]]
    _json({"profile": str(paths.capability_profile) if paths.capability_profile.is_file() else None, "sources": rows}) if args.json else _print_sources(rows, paths.capability_profile, verbose=args.verbose)
    return 0


def command_validate(args: argparse.Namespace) -> int:
    workfile = load_workfile(args.workfile, repository_root=repository_root())
    paths = _paths(workfile.path.parent)
    resolved_config_document(paths)
    result = {
        "status": "PASS",
        "workfile": str(workfile.path),
        "workflow": workfile.spec.workflow_id,
        "prose_bytes_ignored": len(workfile.prose.encode("utf-8")),
        "network_requests": 0,
    }
    _json(result) if args.json else print(f"Valid study workfile: {workfile.path}\nNo network requests were made.")
    return 0


def _load_and_plan(args: argparse.Namespace) -> tuple[Any, Any, LocalPaths, dict[str, Any]]:
    root = repository_root()
    workfile = load_workfile(args.workfile, repository_root=root)
    paths = _paths(workfile.path.parent)
    config, _ = resolved_config_document(paths)
    _refresh_if_requested(args, paths, config)
    plan = compile_study_plan(
        root,
        workfile,
        paths,
        cli_overrides=_cli_overrides(args),
        output_dir=args.out.resolve() if getattr(args, "out", None) else None,
    )
    return root, workfile, paths, plan


def command_plan(args: argparse.Namespace) -> int:
    _, _, _, plan = _load_and_plan(args)
    _json(plan) if args.json else _plan_text(plan, verbose=args.verbose)
    return 2 if plan["blocking"] else 0


def command_explain(args: argparse.Namespace) -> int:
    _, _, _, plan = _load_and_plan(args)
    if args.json:
        _json(
            {
                "resolved_config": plan["resolved_config"],
                "source_resolution": plan["source_resolution"],
                "asset_plan": plan["asset_plan"],
                "classification": plan.get("classification"),
                "explanation": plan.get("explanation"),
                "source_discovery": plan.get("source_discovery"),
            }
        )
        return 2 if plan["blocking"] else 0
    if plan.get("explanation"):
        explanation = plan["explanation"]
        print(explanation["scientific_claim"])
        for key in ("source_selection", "mapping", "invalid_values", "year_acceptance", "resampling", "grid", "context", "outputs"):
            print(f"{key.replace('_', ' ').title()}: {explanation[key]}")
        print("Unsupported statements: " + "; ".join(explanation["unsupported"]))
        print(f"Metadata/catalog requests: {plan['network_requests']}; no raster pixels were transferred.")
        for epoch in plan["asset_plan"]["epochs"]:
            print(
                f"{epoch['year']}: coverage={epoch['exact_coverage_status']} "
                f"records={epoch['catalog_record_ids']} action={epoch['planned_action']}"
            )
        return 2 if plan["blocking"] else 0
    print("FasterRaster resolved the study without making network requests.")
    if args.verbose:
        print("Configuration precedence (highest first):")
        print("  CLI override → workfile → project configuration → user configuration → workflow defaults → source defaults")
        for key, item in plan["resolved_config"]["values"].items():
            print(f"  {key}: {item['value']} ({item['origin']}, {item['key']})")
    decisions = {
        item["logical_asset"]: item
        for item in plan["source_resolution"]["decisions"]
    }
    for row in plan["rows"]:
        local = _friendly(FRIENDLY_READINESS, row["local_asset_readiness"], verbose=args.verbose)
        remote = _friendly(FRIENDLY_REMOTE_STATUS, row["remote_source_status"], verbose=args.verbose)
        action = _friendly(FRIENDLY_ACTION, row["action"], verbose=args.verbose)
        print(f"{row['data']}:")
        print(f"  Local readiness: {local}")
        print(f"  Remote source: {remote}")
        print(f"  Next step: {action}")
        if args.verbose:
            print(f"  Selected source: {row['source'] or 'none'}")
            print(f"  Decision reason: {row['reason']}")
            for rejected in decisions[row["logical_asset"]].get("candidates_rejected", []):
                print(f"  Rejected {rejected['source_id']}: {'; '.join(rejected['rejection_reasons'])}")
    if plan.get("classification"):
        classification = plan["classification"]
        print("Scientific claim: " + classification["scientific_claim"])
        print(
            "Acquisition: raw four-band NAIP "
            f"{classification['raw_four_band_naip']['band_ids']} at "
            f"{classification['raw_four_band_naip']['requested_resolution_m']:g} m"
        )
        print(
            f"Weak supervision: {classification['weak_supervision']}; mapping "
            f"{classification['mapping_id']} ({classification['mapping_sha256']})"
        )
        print(
            "Estimated uncompressed transfer: "
            f"{classification['estimated_uncompressed_transfer_bytes']:,} bytes"
        )
        dependency = classification["dependency_readiness"]
        print(
            "Classifier dependency: "
            + ("ready" if dependency["available"] else "missing")
            + f" ({dependency['install_command']})"
        )
        print("Unsupported claims: " + "; ".join(classification["unsupported_claims"]))
    return 2 if plan["blocking"] else 0


def _recipe_renderer() -> Any:
    library = repository_root() / "scripts" / "lib"
    if str(library) not in sys.path:
        sys.path.insert(0, str(library))
    from fr_ag_recipe_runtime import render_recipe

    return render_recipe


def _handoff_from_preview(preview: Path, handoff_root: Path) -> Path:
    root = handoff_root.resolve()
    for parent in preview.resolve().parents:
        if parent.parent == root:
            return parent
    raise CommandError(f"execution returned a preview outside the handoff root: {preview}")


def command_cook(args: argparse.Namespace) -> int:
    root, workfile, _, plan = _load_and_plan(args)
    if plan["blocking"]:
        raise CommandError("study plan is blocked; run fr explain for source and reuse details")
    if isinstance(workfile.spec, HumanDevelopmentWorkfileSpec):
        values = plan["resolved_config"]["values"]
        execute_human_development = _human_execute()
        preview = execute_human_development(
            root,
            workfile=workfile,
            plan=plan,
            open_preview=bool(values["open_when_complete"]["value"]),
        )
        final = _handoff_from_preview(preview, configured_handoff_root(root))
        result = {"status": "PASS", "handoff": str(final), "preview": str(preview), "network_plan": plan["rows"]}
        _json(result) if args.json else print(f"Cook complete: {final}\nPreview: {preview}")
        return 0
    recipe = load_named_recipe(root, workfile.spec.workflow_id)
    recipe_raw = json.loads((root / "recipes" / "ag" / f"{recipe.recipe_id}.json").read_text(encoding="utf-8"))
    values = plan["resolved_config"]["values"]
    preview = execute_recipe(
        root,
        recipe=recipe,
        recipe_raw=recipe_raw,
        name=workfile.spec.name,
        bbox=tuple(workfile.spec.area.bbox),
        start=workfile.spec.time.start.isoformat(),
        end=workfile.spec.time.end.isoformat(),
        year=workfile.spec.time.crop_year,
        reuse_mode=values["reuse_mode"]["value"],
        open_preview=bool(values["open_when_complete"]["value"]),
        max_total_bytes=int(values["maximum_download_mb"]["value"] * 1_000_000),
        service_tile_size=int(values["service_tile_size"]["value"]),
        renderer=_recipe_renderer(),
        naip_resolution_m=(
            float(values["resolution_m"]["value"])
            if "resolution_m" in values
            else None
        ),
    )
    final = _handoff_from_preview(preview, configured_handoff_root(root))
    write_profile_atomic(final / "resolved_config.json", plan["resolved_config"])
    write_profile_atomic(final / "source_resolution.json", plan["source_resolution"])
    _regenerate_checksums(final)
    result = {"status": "PASS", "handoff": str(final), "preview": str(preview), "network_plan": plan["rows"]}
    _json(result) if args.json else print(f"Cook complete: {final}\nPreview: {preview}")
    return 0


def command_inspect(args: argparse.Namespace) -> int:
    handoff = resolve_handoff(args.target, configured_handoff_root(repository_root()))
    report = inspect_handoff(handoff)
    if args.json:
        _json(report)
    else:
        print(f"Handoff: {report['handoff']}")
        print(f"Status: {report['status']}")
        print(f"Network bytes: {report['network_bytes']}")
        print(f"Reused bytes: {report['reused_bytes']}")
        print(f"Preview: {report['preview'] or 'none'}")
        for asset in report["asset_status"]:
            local = _friendly(
                FRIENDLY_READINESS,
                asset["local_asset_readiness"],
                verbose=args.verbose,
            )
            remote = _friendly(FRIENDLY_REMOTE_STATUS, asset["remote_source_status"], verbose=args.verbose)
            action = _friendly(FRIENDLY_ACTION, asset["action"], verbose=args.verbose)
            print(f"  {asset['logical_asset']}:")
            if asset.get("execution_action") in {"acquired", "reused", "failed"}:
                planned = _friendly(
                    FRIENDLY_ACTION,
                    asset["planned_action"],
                    verbose=args.verbose,
                )
                print(f"    Initial local readiness: {local}")
                print(f"    Planned action: {planned}")
                print(f"    Execution action: {asset['execution_action']}")
                print(
                    "    Final asset verification: "
                    f"{asset['final_asset_verification']}"
                )
                print(
                    "    Final local artifact: "
                    f"{asset['final_local_artifact'] or 'none'}"
                )
                if args.verbose:
                    print(f"    Source ID: {asset['selected_source'] or 'none'}")
                    print(f"    Network bytes: {asset['network_bytes']:,}")
                    print(f"    Checksum: {asset['checksum'] or 'not verified'}")
            else:
                print(f"    Local readiness: {local}")
                print(f"    Remote source: {remote}")
                print(f"    Action taken: {action}")
                if args.verbose:
                    print(
                        f"    Source ID: {asset['selected_source'] or 'none'}; "
                        f"reused={asset['reused']}; acquired={asset['acquired']}"
                    )
        classification = report.get("classification")
        if args.verbose and classification is not None:
            print("  Classification summary:")
            if not classification["available"]:
                print(
                    "    Finalized classification metrics are unavailable; "
                    "no dependency was imported."
                )
            else:
                mapping_hash = (
                    classification["mapping_sha256_abbreviated"]
                    or "unavailable"
                )
                print(
                    "    Classifier backend: "
                    f"{classification['classifier_backend'] or 'unavailable'}"
                )
                print(
                    "    Mapping: "
                    f"{classification['mapping_id'] or 'unavailable'} "
                    f"({mapping_hash})"
                )
                print(
                    "    Train / holdout samples: "
                    f"{classification['train_samples'] or 'unavailable'} / "
                    f"{classification['holdout_samples'] or 'unavailable'}"
                )
                for label, key in (
                    (
                        "Weak-label overall agreement",
                        "weak_label_overall_agreement",
                    ),
                    ("Macro F1", "macro_f1"),
                    ("Cohen's kappa", "cohen_kappa"),
                    ("Confidence threshold", "confidence_threshold"),
                    ("Classified coverage", "classified_coverage"),
                    ("Uncertain fraction", "uncertain_fraction"),
                    (
                        "High-confidence disagreement",
                        "high_confidence_disagreement_fraction",
                    ),
                ):
                    value = classification[key]
                    if value is None:
                        rendered = "unavailable in finalized artifacts"
                    elif key in {
                        "classified_coverage",
                        "uncertain_fraction",
                        "high_confidence_disagreement_fraction",
                    }:
                        rendered = f"{float(value):.1%}"
                    else:
                        rendered = f"{float(value):.3f}"
                    print(f"    {label}: {rendered}")
                print("    Predicted hectares by class:")
                hectares = classification["predicted_hectares_by_class"]
                if hectares:
                    for label, value in hectares.items():
                        print(f"      {label}: {value:,.3f}")
                else:
                    print("      unavailable")
                if classification["missing_fields"]:
                    print(
                        "    Missing optional artifacts: "
                        + ", ".join(classification["missing_fields"])
                    )
    return 0


def command_open(args: argparse.Namespace) -> int:
    handoff = resolve_handoff(args.target, configured_handoff_root(repository_root()))
    preview = finalized_preview(handoff)
    config, _ = resolved_config_document(_paths(Path.cwd()))
    command = open_local_preview(preview, configured_opener=config.preview.opener)
    print(f"Opened preview: {preview}")
    if args.json:
        _json({"status": "PASS", "preview": str(preview), "opener": command[0]})
    return 0


def command_publish(args: argparse.Namespace) -> int:
    options_type, publisher = _human_publisher()
    options = options_type(
        mode=args.mode,
        imagery_year=args.imagery_year,
        regional_resolution_m=args.regional_resolution_m,
        hotspot_resolution_m=args.hotspot_resolution_m,
        hotspot_size_m=args.hotspot_size_m,
        maximum_download_mb=args.maximum_download_mb,
        workers=args.workers,
        reuse=args.reuse,
        allow_network=args.allow_network,
        open_when_complete=args.open_when_complete,
    )
    preview = publisher(repository_root(), args.handoff, options)
    publication = preview.parent.parent
    if args.json:
        _json({
            "status": "PASS",
            "publication": str(publication),
            "preview": str(preview),
        })
    else:
        print(f"Published human-development hybrid: {publication}")
        print(f"Preview: {preview}")
    return 0


def _add_plan_options(parser: argparse.ArgumentParser, *, include_out: bool = True) -> None:
    parser.add_argument("workfile", type=Path)
    parser.add_argument("--json", action="store_true", help="emit structured JSON")
    parser.add_argument("--verbose", action="store_true", help="show exact identifiers and decision reasons")
    if include_out:
        parser.add_argument("--out", type=Path, help="write plan artifacts to this directory")
    parser.add_argument("--refresh-sources", action="store_true", help="explicitly refresh bounded source evidence first")
    parser.add_argument("--offline", action="store_true", help="prohibit source network requests, including refresh")
    parser.add_argument("--maximum-download-mb", type=float, help="override the workfile byte ceiling")
    parser.add_argument("--reuse", choices=("auto", "only", "never"), help="override data reuse policy")
    parser.add_argument("--service-tile-size", type=int)
    parser.add_argument("--maximum-parallel-tasks", type=int)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fr", description="FasterRaster local studies, capabilities, plans, and results")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--debug", action="store_true", help="show tracebacks for unexpected failures")
    commands = parser.add_subparsers(dest="command", required=True)

    templates = commands.add_parser("templates", help="discover and inspect built-in study templates")
    template_commands = templates.add_subparsers(dest="templates_command", required=True)
    templates_list = template_commands.add_parser("list", help="list built-in template IDs")
    templates_list.add_argument("--json", action="store_true")
    templates_list.set_defaults(handler=command_templates)
    templates_show = template_commands.add_parser("show", help="show a deterministic template workfile")
    templates_show.add_argument("template_id")
    templates_show.add_argument("--json", action="store_true")
    templates_show.set_defaults(handler=command_templates)

    init = commands.add_parser("init", help="create a valid Markdown study workfile")
    init.add_argument("workfile", type=Path)
    init.add_argument("--template", help="built-in template ID from 'fr templates list'")
    init.add_argument("--name", help="study name rendered into the selected template")
    init.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        metavar=("MINX", "MINY", "MAXX", "MAXY"),
        help="EPSG:4326 bounding box rendered into the selected template",
    )
    init.add_argument("--years", type=int, nargs="+", help="ordered epoch years rendered into the selected template")
    init.add_argument("--project-config", action="store_true", help="also initialize .fasterraster/config.toml")
    init.add_argument("--force", action="store_true", help="overwrite existing initialized files")
    init.set_defaults(handler=command_init)

    configure = commands.add_parser("configure", help="show, validate, or update local configuration")
    configure.add_argument("--show", action="store_true")
    configure.add_argument("--path", action="store_true")
    configure.add_argument("--validate", action="store_true")
    configure.add_argument("--json", action="store_true")
    configure.add_argument("--project", action="store_true", help="update project rather than user configuration")
    configure.add_argument("--apply-recommendations", action="store_true")
    configure.add_argument("--cache-root", type=Path)
    configure.add_argument("--state-root", type=Path)
    configure.add_argument("--temporary-root", type=Path)
    configure.add_argument("--maximum-download-mb", type=float)
    configure.add_argument("--service-tile-size", type=int)
    configure.add_argument("--maximum-parallel-tasks", type=int)
    configure.add_argument("--reuse", choices=("auto", "only", "never"))
    configure.add_argument("--source-preference", help="comma-separated source IDs or aliases")
    configure.add_argument("--source-allowlist", help="comma-separated source IDs or aliases")
    configure.add_argument("--source-denylist", help="comma-separated source IDs or aliases")
    configure.add_argument("--offline", dest="offline", action="store_true")
    configure.add_argument("--online", dest="offline", action="store_false")
    configure.add_argument("--preview-open", dest="preview_open", action="store_true")
    configure.add_argument("--no-preview-open", dest="preview_open", action="store_false")
    configure.set_defaults(handler=command_configure, offline=None, preview_open=None)

    doctor = commands.add_parser("doctor", help="inspect local GDAL, filesystem, resource, and preview capability")
    doctor.add_argument("--offline", action="store_true", help="record that network use is prohibited")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(handler=command_doctor)

    sources = commands.add_parser("sources", help="show shipped sources and local capability evidence")
    sources.add_argument("--json", action="store_true")
    sources.add_argument("--verbose", action="store_true", help="show exact source and status identifiers")
    sources.add_argument("--stale", action="store_true")
    sources.add_argument("--status", choices=SOURCE_STATUSES)
    sources.add_argument("--category", choices=("static_verified", "service_discovered", "api_discovered", "credential_gated", "future_unverified"))
    source_commands = sources.add_subparsers(dest="sources_command")
    evaluate = source_commands.add_parser("evaluate", help="explicitly run bounded, self-cleaning source probes")
    evaluate.add_argument("--source", action="append", help="source ID or comma-separated source IDs")
    evaluate.add_argument("--offline", action="store_true")
    evaluate.add_argument("--keep-probe-artifacts", action="store_true")
    evaluate.add_argument("--refresh", action="store_true", help="refresh selected evidence now")
    evaluate.add_argument("--maximum-bytes", type=int, default=DEFAULT_GLOBAL_BYTE_CEILING)
    evaluate.add_argument("--json", action="store_true")
    evaluate.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)
    sources.set_defaults(handler=command_sources)

    validate = commands.add_parser("validate", help="validate workfile front matter and configuration without network access")
    validate.add_argument("workfile", type=Path)
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(handler=command_validate)

    plan = commands.add_parser("plan", help="compile an offline asset and source plan")
    _add_plan_options(plan)
    plan.set_defaults(handler=command_plan)

    explain = commands.add_parser("explain", help="explain precedence, source choices, reuse, and acquisition")
    _add_plan_options(explain)
    explain.set_defaults(handler=command_explain)

    cook = commands.add_parser("cook", help="execute a validated workfile through its workflow-specific runtime")
    _add_plan_options(cook, include_out=False)
    cook.add_argument("--open", dest="open_when_complete", action="store_true")
    cook.add_argument("--no-open", dest="open_when_complete", action="store_false")
    cook.set_defaults(handler=command_cook, open_when_complete=None, out=None)

    inspect = commands.add_parser("inspect", help="summarize a finalized handoff")
    inspect.add_argument("target", help="latest or an explicit finalized handoff path")
    inspect.add_argument("--json", action="store_true")
    inspect.add_argument("--verbose", action="store_true", help="show exact asset status identifiers")
    inspect.set_defaults(handler=command_inspect)

    open_command = commands.add_parser("open", help="open a finalized preview with the local platform opener")
    open_command.add_argument("target", help="latest or an explicit finalized handoff path")
    open_command.add_argument("--json", action="store_true")
    open_command.set_defaults(handler=command_open)

    publish = commands.add_parser(
        "publish", help="create a finalized publication from a handoff"
    )
    publish_commands = publish.add_subparsers(
        dest="publish_command", required=True
    )
    hybrid = publish_commands.add_parser(
        "human-development-hybrid",
        help="create a classification-first NAIP hybrid publication",
    )
    hybrid.add_argument("handoff", type=Path)
    hybrid.add_argument(
        "--mode",
        choices=("regional-change", "developed-state", "hotspot", "combined"),
        default="combined",
    )
    hybrid.add_argument("--imagery-year", type=int, default=2021)
    hybrid.add_argument("--regional-resolution-m", type=float, default=4.2)
    hybrid.add_argument("--hotspot-resolution-m", type=float, default=1.0)
    hybrid.add_argument("--hotspot-size-m", type=float, default=1024.0)
    hybrid.add_argument("--maximum-download-mb", type=float, default=75.0)
    hybrid.add_argument("--workers", type=int, default=2)
    hybrid.add_argument(
        "--reuse", choices=("auto", "only", "never"), default="auto"
    )
    hybrid.add_argument("--allow-network", action="store_true")
    hybrid.add_argument(
        "--open", dest="open_when_complete", action="store_true"
    )
    hybrid.add_argument("--json", action="store_true")
    hybrid.set_defaults(handler=command_publish, open_when_complete=False)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (CommandError, ConfigError, WorkfileError, PreviewOpenError, RecipeLoadError, RecipeExecutionError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("ERROR: interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        if args.debug:
            raise
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
