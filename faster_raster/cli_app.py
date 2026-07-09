from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from faster_raster import __version__
from faster_raster import cli_explore
from faster_raster import cli_models as models
from faster_raster import cli_render as render
from faster_raster import cli_lingo
from faster_raster import user_toggles
from faster_raster import task_builder
from faster_raster import real_preview
from faster_raster import copernicus_auth
from faster_raster.adapters import copernicus_cdse

sources_app = typer.Typer(no_args_is_help=True)
stack_app = typer.Typer(no_args_is_help=True)
unlocks_app = typer.Typer(no_args_is_help=True)
auth_app = typer.Typer(no_args_is_help=True)
probe_app = typer.Typer(no_args_is_help=True)
help_app = typer.Typer(no_args_is_help=True)
toggles_app = typer.Typer(no_args_is_help=True)
cook_app = typer.Typer(no_args_is_help=True)
knobs_app = typer.Typer(no_args_is_help=False, invoke_without_command=True)
task_app = typer.Typer(no_args_is_help=True)
copernicus_app = typer.Typer(no_args_is_help=True)
copernicus_sentinel_app = typer.Typer(no_args_is_help=True)


def emit(value, *, json_output: bool = False, plain: bool = False, no_color: bool = False, plain_text: str | None = None, table: tuple[str, list[str], list[list]] | None = None) -> None:
    if json_output:
        typer.echo(render.stable_json(value), nl=False)
        return
    if plain or table is None:
        typer.echo(plain_text if plain_text is not None else str(value))
        return
    title, headers, rows = table
    if not render.rich_table(title, headers, rows, no_color=no_color):
        typer.echo(plain_text if plain_text is not None else str(value))


def load_context(atlas: Path, stack: Path, unlocks: Path):
    sources = models.load_sources(atlas)
    matrix = models.load_matrix(models.DEFAULT_MATRIX) if models.DEFAULT_MATRIX.exists() else []
    unlock_rows = models.load_unlocks(unlocks) if unlocks.exists() else []
    return sources, matrix, unlock_rows


def version_command(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain")) -> None:
    payload = {"package_version": __version__}
    emit(payload, json_output=json_output, plain=True, plain_text=f"FasterRaster {__version__}\n")


def doctor_command(
    json_output: bool = typer.Option(False, "--json"),
    plain: bool = typer.Option(False, "--plain"),
    atlas: Path = typer.Option(models.DEFAULT_ATLAS, "--atlas"),
    stack: Path = typer.Option(models.DEFAULT_STACK, "--stack"),
    unlocks: Path = typer.Option(models.DEFAULT_UNLOCKS, "--unlocks"),
) -> None:
    report = models.doctor_report(models.CliPaths(atlas=atlas, stack=stack, unlocks=unlocks))
    text = "\n".join([f"doctor: {report['status']}"] + [f"{c['name']}: {'OK' if c['exists'] else 'MISSING'} {c['path']}" for c in report['checks']]) + "\n"
    emit(report, json_output=json_output, plain=True, plain_text=text)
    if report["status"] != "PASS":
        raise typer.Exit(code=1)


@sources_app.command("list")
def sources_list(
    json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain"), no_color: bool = typer.Option(False, "--no-color"), lingo: str = typer.Option(None, "--lingo"),
    compact: bool = typer.Option(False, "--compact"), wide: bool = typer.Option(False, "--wide"), full: bool = typer.Option(False, "--full"), columns: str = typer.Option("standard", "--columns"),
    atlas: Path = typer.Option(models.DEFAULT_ATLAS, "--atlas"), category: Optional[str] = typer.Option(None, "--category"), provider: Optional[str] = typer.Option(None, "--provider"), credential: Optional[str] = typer.Option(None, "--credential"), promotion_status: Optional[str] = typer.Option(None, "--promotion-status"), verified_only: bool = typer.Option(False, "--verified-only"), gated_only: bool = typer.Option(False, "--gated-only"), adapter_needed_only: bool = typer.Option(False, "--adapter-needed-only"), mirror_only: bool = typer.Option(False, "--mirror-only"),
) -> None:
    matrix = models.load_matrix(models.DEFAULT_MATRIX) if models.DEFAULT_MATRIX.exists() else []
    unlocks = models.load_unlocks(models.DEFAULT_UNLOCKS) if models.DEFAULT_UNLOCKS.exists() else []
    sources = models.filter_sources(models.load_sources(atlas), category=category, provider=provider, credential=credential, promotion_status=promotion_status, verified_only=verified_only, gated_only=gated_only, adapter_needed_only=adapter_needed_only, mirror_only=mirror_only, matrix=matrix)
    umap = models.unlock_by_source(unlocks); mmap = models.matrix_by_source(matrix)
    rows = [models.source_row(s, umap.get(s['source_id']), mmap.get(s['source_id'])) for s in sources]
    selected_columns = "full" if (wide or full or columns == "full") else ("essential" if (compact or columns == "essential") else "standard")
    plain_text = render.render_sources_plain(rows, columns=selected_columns)
    if lingo is not None:
        plain_text = cli_lingo.title("sources_list", lingo) + "\n" + plain_text
    emit(rows, json_output=json_output, plain=plain, no_color=no_color, plain_text=plain_text, table=(cli_lingo.title("sources_list", lingo), ["source_id", "provider", "status", "credential", "promotion"], [[r['source_id'], r['provider'], r['status'], r['credential_requirement'], r['promotion_status']] for r in rows]))



@sources_app.command("tree")
def sources_tree(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain"), atlas: Path = typer.Option(models.DEFAULT_ATLAS, "--atlas")) -> None:
    matrix = models.load_matrix(models.DEFAULT_MATRIX) if models.DEFAULT_MATRIX.exists() else []
    mmap = models.matrix_by_source(matrix)
    groups = {"Verified now": [], "Credential gated": [], "Adapter needed": [], "Mirror candidates": [], "Future/unverified": [], "Blocked/research only": []}
    for source in models.load_sources(atlas):
        status = models.source_status(source, mmap.get(source['source_id']))
        if status == "verified_now": groups["Verified now"].append(source)
        elif status == "credential_gated": groups["Credential gated"].append(source)
        elif status == "adapter_needed": groups["Adapter needed"].append(source)
        elif status == "mirror_candidate": groups["Mirror candidates"].append(source)
        elif source.get('promotion_status') in {'research_only','blocked_by_auth','blocked_by_adapter'}: groups["Blocked/research only"].append(source)
        else: groups["Future/unverified"].append(source)
    emit(groups, json_output=json_output, plain=True, plain_text=render.render_tree_plain(groups))


@sources_app.command("show")
def sources_show(source_id: str, json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain"), atlas: Path = typer.Option(models.DEFAULT_ATLAS, "--atlas")) -> None:
    source = models.source_by_id(models.load_sources(atlas), source_id)
    unlock = models.unlock_by_source(models.load_unlocks(models.DEFAULT_UNLOCKS)).get(source_id) if models.DEFAULT_UNLOCKS.exists() else None
    matrix = models.matrix_by_source(models.load_matrix(models.DEFAULT_MATRIX)).get(source_id) if models.DEFAULT_MATRIX.exists() else None
    payload = {**source, "next_unlock_step": (unlock or {}).get("recommended_action") or (matrix or {}).get("next_unlock_step")}
    emit(payload, json_output=json_output, plain=True, plain_text=render.render_source_detail_plain(source, unlock, matrix))


@sources_app.command("search")
def sources_search(query: str, json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain"), atlas: Path = typer.Option(models.DEFAULT_ATLAS, "--atlas")) -> None:
    found = models.search_sources(models.load_sources(atlas), query)
    rows = [models.source_row(s) for s in found]
    emit(rows, json_output=json_output, plain=True, plain_text=render.render_sources_plain(rows))


@stack_app.command("summary")
def stack_summary(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain"), lingo: str = typer.Option(None, "--lingo"), stack: Path = typer.Option(models.DEFAULT_STACK, "--stack"), atlas: Path = typer.Option(models.DEFAULT_ATLAS, "--atlas")) -> None:
    summary = models.stack_summary(models.load_stack(stack), models.load_atlas(atlas))
    text = render.render_stack_summary_plain(summary)
    if lingo is not None:
        text = cli_lingo.title("stack_summary", lingo) + "\n" + text
    emit(summary, json_output=json_output, plain=True, plain_text=text)


@stack_app.command("matrix")
def stack_matrix(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain"), group_by: str = typer.Option("status", "--group-by"), stack: Path = typer.Option(models.DEFAULT_MATRIX, "--stack")) -> None:
    rows = models.load_matrix(stack)
    emit(rows, json_output=json_output, plain=True, plain_text=render.render_sources_plain(rows))


@unlocks_app.command("list")
def unlocks_list(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain"), unlocks: Path = typer.Option(models.DEFAULT_UNLOCKS, "--unlocks")) -> None:
    rows = models.load_unlocks(unlocks)
    emit(rows, json_output=json_output, plain=True, plain_text=render.render_unlocks_plain(rows))


@unlocks_app.command("next")
def unlocks_next(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain"), unlocks: Path = typer.Option(models.DEFAULT_UNLOCKS, "--unlocks")) -> None:
    rows = models.load_unlocks(unlocks)
    top = rows[0] if rows else {}
    text = "\n".join(f"{k}: {v}" for k, v in top.items()) + "\n"
    emit(top, json_output=json_output, plain=True, plain_text=text)


@auth_app.command("profiles")
def auth_profiles(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain"), auth: Path = typer.Option(models.DEFAULT_AUTH, "--auth")) -> None:
    rows = models.auth_rows(models.load_auth(auth))
    emit(rows, json_output=json_output, plain=True, plain_text=render.render_auth_plain(rows))


@auth_app.command("show")
def auth_show(profile_id: str, json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain"), auth: Path = typer.Option(models.DEFAULT_AUTH, "--auth")) -> None:
    rows = models.auth_rows(models.load_auth(auth))
    row = next((item for item in rows if item["auth_profile_id"] == profile_id), None)
    if row is None:
        raise typer.BadParameter(f"unknown auth profile: {profile_id}")
    text = "\n".join(f"{k}: {v}" for k, v in row.items()) + "\n"
    emit(row, json_output=json_output, plain=True, plain_text=text)


@probe_app.command("atlas")
def probe_atlas(source_id: str, dry_run: bool = typer.Option(False, "--dry-run"), allow_network: bool = typer.Option(False, "--allow-network"), max_bytes: int = typer.Option(65_536, "--max-bytes"), json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain"), atlas: Path = typer.Option(models.DEFAULT_ATLAS, "--atlas")) -> None:
    from scripts.probe_atlas_source import load_atlas, run_atlas_probe, write_reports
    if not dry_run and not allow_network:
        raise typer.BadParameter("live atlas probe requires --allow-network or use --dry-run")
    report = run_atlas_probe(load_atlas(atlas), source_id=source_id, allow_network=allow_network and not dry_run, max_bytes=max_bytes)
    if source_id == "gridmet_daily":
        report["classification"] = models.gridmet_dry_run_classification(report)
    if not dry_run and allow_network:
        write_reports(report, Path(f"reports/atlas_probe_{source_id}.json"), Path(f"reports/atlas_probe_{source_id}.md"))
    emit(report, json_output=json_output, plain=True, plain_text=render.render_probe_plain(report))


@help_app.command("style")
def help_style(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain")) -> None:
    payload = {"status_labels": list(render.STATUS_STYLE)}
    emit(payload, json_output=json_output, plain=True, plain_text=render.help_style_plain())


def explore_command() -> None:
    cli_explore.run_explore()




def pantry(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain"), wide: bool = typer.Option(False, "--wide"), full: bool = typer.Option(False, "--full"), atlas: Path = typer.Option(models.DEFAULT_ATLAS, "--atlas")) -> None:
    sources_list(json_output=json_output, plain=plain, no_color=False, lingo="kitchen", atlas=atlas, category=None, provider=None, credential=None, promotion_status=None, verified_only=False, gated_only=False, adapter_needed_only=False, mirror_only=False, compact=False, wide=wide, full=full, columns="standard")


def sauces(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain"), wide: bool = typer.Option(False, "--wide"), full: bool = typer.Option(False, "--full"), atlas: Path = typer.Option(models.DEFAULT_ATLAS, "--atlas")) -> None:
    sources_list(json_output=json_output, plain=plain, no_color=False, lingo="kitchen", atlas=atlas, category=None, provider=None, credential=None, promotion_status=None, verified_only=False, gated_only=False, adapter_needed_only=False, mirror_only=False, compact=False, wide=wide, full=full, columns="standard")


def sauce(source_id: str, json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain"), atlas: Path = typer.Option(models.DEFAULT_ATLAS, "--atlas")) -> None:
    source = models.source_by_id(models.load_sources(atlas), source_id)
    unlock = models.unlock_by_source(models.load_unlocks(models.DEFAULT_UNLOCKS)).get(source_id) if models.DEFAULT_UNLOCKS.exists() else None
    matrix = models.matrix_by_source(models.load_matrix(models.DEFAULT_MATRIX)).get(source_id) if models.DEFAULT_MATRIX.exists() else None
    payload = {**source, "next_unlock_step": (unlock or {}).get("recommended_action") or (matrix or {}).get("next_unlock_step")}
    text = cli_lingo.title("source_detail", "kitchen") + "\n" + render.render_source_detail_plain(source, unlock, matrix)
    emit(payload, json_output=json_output, plain=True, plain_text=text)


def reigns(plain: bool = typer.Option(False, "--plain"), atlas: Path = typer.Option(models.DEFAULT_ATLAS, "--atlas")) -> None:
    sources = models.load_sources(atlas)
    groups = {}
    for source in sources:
        groups.setdefault(source.get("source_family") or source.get("source_kind") or "unknown", []).append(source)
    typer.echo("Reigns / grouped source families")
    typer.echo(render.render_tree_plain(groups))


def buckets(plain: bool = typer.Option(False, "--plain"), atlas: Path = typer.Option(models.DEFAULT_ATLAS, "--atlas")) -> None:
    typer.echo("Buckets / status groups")
    sources_tree(json_output=False, plain=True, atlas=atlas)


def goods(plain: bool = typer.Option(False, "--plain"), include_guards: bool = typer.Option(False, "--include-guards"), wide: bool = typer.Option(False, "--wide"), full: bool = typer.Option(False, "--full"), atlas: Path = typer.Option(models.DEFAULT_ATLAS, "--atlas")) -> None:
    matrix = models.load_matrix(models.DEFAULT_MATRIX) if models.DEFAULT_MATRIX.exists() else []
    matrix_map = models.matrix_by_source(matrix)
    unlock_map = models.unlock_by_source(models.load_unlocks(models.DEFAULT_UNLOCKS)) if models.DEFAULT_UNLOCKS.exists() else {}
    selected = []
    for source in models.load_sources(atlas):
        if not include_guards and "duplicate_guard" in source["source_id"]:
            continue
        status = models.source_status(source, matrix_map.get(source["source_id"]))
        if status == "verified_now":
            selected.append(models.source_row(source, unlock_map.get(source["source_id"]), matrix_map.get(source["source_id"])))
    columns = "full" if (wide or full) else "standard"
    typer.echo(f"Goods / verified sauces: {len(selected)}")
    typer.echo(render.render_goods_plain(selected, columns=columns))


def bads(plain: bool = typer.Option(False, "--plain"), wide: bool = typer.Option(False, "--wide"), full: bool = typer.Option(False, "--full"), atlas: Path = typer.Option(models.DEFAULT_ATLAS, "--atlas")) -> None:
    matrix = models.load_matrix(models.DEFAULT_MATRIX) if models.DEFAULT_MATRIX.exists() else []
    matrix_map = models.matrix_by_source(matrix)
    unlock_map = models.unlock_by_source(models.load_unlocks(models.DEFAULT_UNLOCKS)) if models.DEFAULT_UNLOCKS.exists() else {}
    selected = []
    for source in models.load_sources(atlas):
        status = models.source_status(source, matrix_map.get(source["source_id"]))
        if status in {"credential_gated", "adapter_needed", "blocked", "future_unverified", "failed_probe", "skipped_policy"}:
            selected.append(models.source_row(source, unlock_map.get(source["source_id"]), matrix_map.get(source["source_id"])))
    columns = "full" if (wide or full) else "standard"
    typer.echo("Bads / blocked, gated, and waiting sauces")
    typer.echo(render.render_bads_plain(selected, columns=columns))


def recipe(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain")) -> None:
    stack_summary(json_output=json_output, plain=plain, lingo="kitchen", stack=models.DEFAULT_STACK, atlas=models.DEFAULT_ATLAS)


def batcher(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain")) -> None:
    rows = models.load_unlocks(models.DEFAULT_UNLOCKS)
    top = rows[0] if rows else {}
    summary = models.stack_summary(models.load_stack(models.DEFAULT_STACK), models.load_atlas(models.DEFAULT_ATLAS))
    payload = {"top_unlock": top, "stack_summary": summary}
    text = "Batcher / unlock planner\n" + "\n".join(f"{k}: {v}" for k, v in top.items()) + "\n" + f"goods_verified: {summary['verified_live_source_count']}\nlocks: {summary['credential_gated_count']}\nadapter_needed: {summary['adapter_needed_count']}\n"
    emit(payload, json_output=json_output, plain=True, plain_text=text)


def dips(source_id: str, dry_run: bool = typer.Option(False, "--dry-run"), allow_network: bool = typer.Option(False, "--allow-network"), plain: bool = typer.Option(False, "--plain"), json_output: bool = typer.Option(False, "--json"), atlas: Path = typer.Option(models.DEFAULT_ATLAS, "--atlas")) -> None:
    probe_atlas(source_id, dry_run=dry_run, allow_network=allow_network, max_bytes=65_536, json_output=json_output, plain=plain, atlas=atlas)


menu_app = typer.Typer(no_args_is_help=True)
@menu_app.command("lingo")
def menu_lingo(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain")) -> None:
    payload={'terms': cli_lingo.glossary()}
    emit(payload, json_output=json_output, plain=True, plain_text=cli_lingo.glossary_text())



@toggles_app.command("show")
def toggles_show(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain"), toggles: Path = typer.Option(user_toggles.DEFAULT_TOGGLES, "--toggles")) -> None:
    data = user_toggles.load_user_toggles(toggles)
    report = user_toggles.write_effective_reports(data, Path("reports/user_toggles_effective.json"), Path("reports/user_toggles_effective.md"))
    text = "Knobs / effective toggles\n" + render.render_stack_summary_plain({
        "lingo_mode": report["effective_toggles"]["lingo_mode"],
        "network_mode": report["effective_toggles"]["network_mode"],
        "no_auth_only": report["effective_toggles"]["source_scope"]["no_auth_only"],
        "max_bytes_per_source": report["effective_toggles"]["dip_limits"]["max_bytes_per_source"],
        "promotion_policy": report["effective_toggles"]["promotion_policy"]["mode"],
    })
    emit(report, json_output=json_output, plain=True, plain_text=text)


@toggles_app.command("explain")
def toggles_explain(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain")) -> None:
    payload = {
        "network_mode": "off by default; live dips require explicit --allow-network",
        "source_scope": "no-auth official/institutional sources first; use source-scope or scope, not source_scope",
        "promotion_policy": "proposal_only; runtime registry edits forbidden",
        "safety": "fail closed on unknown endpoints, no extraction, no secrets",
    }
    text = "Knobs explained\n" + "\n".join(f"{k}: {v}" for k, v in payload.items()) + "\n"
    emit(payload, json_output=json_output, plain=True, plain_text=text)


def _cook_queue_rows(atlas: Path = models.DEFAULT_ATLAS, unlocks: Path = models.DEFAULT_UNLOCKS, toggles: Path = user_toggles.DEFAULT_TOGGLES):
    from scripts.plan_no_auth_cook_queue import build_queue, load_yaml
    return build_queue(load_yaml(atlas), models.read_json(unlocks), user_toggles.load_user_toggles(toggles))


@cook_app.command("plan")
def cook_plan(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain"), atlas: Path = typer.Option(models.DEFAULT_ATLAS, "--atlas"), unlocks: Path = typer.Option(models.DEFAULT_UNLOCKS, "--unlocks"), toggles: Path = typer.Option(user_toggles.DEFAULT_TOGGLES, "--toggles")) -> None:
    rows = _cook_queue_rows(atlas, unlocks, toggles)
    payload = {"cook_queue_count": len(rows), "top": rows[:5]}
    text = "Cook plan / no-auth unlock plan\n" + render.render_unlocks_plain([
        {"source_id": r["source_id"], "class": r["cook_status"], "credential_requirement": r["credential_requirement"], "bounded_probe_appropriate": r["bounded_probe_appropriate"], "score": r["score"], "recommended_action": r["recommended_action"]} for r in rows
    ])
    emit(payload, json_output=json_output, plain=True, plain_text=text)


@cook_app.command("queue")
def cook_queue(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain"), atlas: Path = typer.Option(models.DEFAULT_ATLAS, "--atlas"), unlocks: Path = typer.Option(models.DEFAULT_UNLOCKS, "--unlocks"), toggles: Path = typer.Option(user_toggles.DEFAULT_TOGGLES, "--toggles")) -> None:
    rows = _cook_queue_rows(atlas, unlocks, toggles)
    payload = {"cook_queue": rows}
    text = "Cook queue / sauces ready for review\n" + render.render_unlocks_plain([
        {"source_id": r["source_id"], "class": r["cook_status"], "credential_requirement": r["credential_requirement"], "bounded_probe_appropriate": r["bounded_probe_appropriate"], "score": r["score"], "recommended_action": r["recommended_action"]} for r in rows
    ])
    emit(payload, json_output=json_output, plain=True, plain_text=text)


@cook_app.command("dip")
def cook_dip(source_id: str, dry_run: bool = typer.Option(False, "--dry-run"), allow_network: bool = typer.Option(False, "--allow-network"), json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain"), atlas: Path = typer.Option(models.DEFAULT_ATLAS, "--atlas"), toggles: Path = typer.Option(user_toggles.DEFAULT_TOGGLES, "--toggles")) -> None:
    data = user_toggles.effective_toggles(user_toggles.load_user_toggles(toggles))
    if allow_network and data["network_mode"] == "off":
        raise typer.BadParameter("network_mode is off; live cook dips are disabled by toggles")
    from scripts.probe_atlas_source import load_atlas, run_atlas_probe
    max_bytes = int(data["dip_limits"]["max_bytes_per_source"])
    report = run_atlas_probe(load_atlas(atlas), source_id=source_id, allow_network=allow_network and not dry_run, max_bytes=max_bytes)
    if source_id == "gridmet_daily":
        report["classification"] = models.gridmet_dry_run_classification(report)
    report["toggles"] = {"network_mode": data["network_mode"], "max_bytes_per_source": max_bytes, "safe_mode": data["network_mode"] == "off"}
    emit(report, json_output=json_output, plain=True, plain_text="Cook dip / bounded source probe\n" + render.render_probe_plain(report))


@cook_app.command("propose")
def cook_propose(source_id: str, json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain"), atlas: Path = typer.Option(models.DEFAULT_ATLAS, "--atlas"), toggles: Path = typer.Option(user_toggles.DEFAULT_TOGGLES, "--toggles")) -> None:
    from scripts.propose_adapter_promotion import build_proposal, load_yaml, write_reports
    proposal = build_proposal(source_id, load_yaml(atlas), user_toggles.load_user_toggles(toggles), Path("reports"))
    write_reports(proposal, Path("reports/adapter_promotion_proposals"))
    text = "Cook proposal / adapter promotion proposal\n" + "\n".join(f"{k}: {proposal[k]}" for k in ["source_id", "promotion_decision", "endpoint_status", "credential_status", "expected_adapter_type", "proposal_only"] if k in proposal) + "\n"
    emit(proposal, json_output=json_output, plain=True, plain_text=text)


@knobs_app.callback(invoke_without_command=True)
def knobs_callback(ctx: typer.Context, json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain")) -> None:
    if ctx.invoked_subcommand is None:
        toggles_show(json_output=json_output, plain=plain, toggles=user_toggles.DEFAULT_TOGGLES)


@knobs_app.command("show")
def knobs_show(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain")) -> None:
    toggles_show(json_output=json_output, plain=plain, toggles=user_toggles.DEFAULT_TOGGLES)


@knobs_app.command("explain")
def knobs_explain_command(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain")) -> None:
    toggles_explain(json_output=json_output, plain=plain)


def knobs(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain")) -> None:
    toggles_show(json_output=json_output, plain=plain, toggles=user_toggles.DEFAULT_TOGGLES)


def knobs_explain(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain")) -> None:
    toggles_explain(json_output=json_output, plain=plain)


def cookplan(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain")) -> None:
    cook_plan(json_output=json_output, plain=plain, atlas=models.DEFAULT_ATLAS, unlocks=models.DEFAULT_UNLOCKS, toggles=user_toggles.DEFAULT_TOGGLES)


def queue(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain")) -> None:
    cook_queue(json_output=json_output, plain=plain, atlas=models.DEFAULT_ATLAS, unlocks=models.DEFAULT_UNLOCKS, toggles=user_toggles.DEFAULT_TOGGLES)


def cookdip(source_id: str, dry_run: bool = typer.Option(False, "--dry-run"), allow_network: bool = typer.Option(False, "--allow-network"), json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain")) -> None:
    cook_dip(source_id, dry_run=dry_run, allow_network=allow_network, json_output=json_output, plain=plain, atlas=models.DEFAULT_ATLAS, toggles=user_toggles.DEFAULT_TOGGLES)


def cookproposal(source_id: str, json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain")) -> None:
    cook_propose(source_id, json_output=json_output, plain=plain, atlas=models.DEFAULT_ATLAS, toggles=user_toggles.DEFAULT_TOGGLES)


def source_scope(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain"), toggles: Path = typer.Option(user_toggles.DEFAULT_TOGGLES, "--toggles")) -> None:
    data = user_toggles.effective_toggles(user_toggles.load_user_toggles(toggles))['source_scope']
    text = "Source scope / cook safety scope\n" + render.render_stack_summary_plain(data)
    emit(data, json_output=json_output, plain=True, plain_text=text)


def _load_endpoint_pack(path: Path = Path("reports/endpoint_readiness_pack_v0_5_3.json")):
    return models.read_json(path)


@cook_app.command("endpoints")
def cook_endpoints(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain"), wide: bool = typer.Option(False, "--wide"), ready_only: bool = typer.Option(False, "--ready-only"), pack: Path = typer.Option(Path("reports/endpoint_readiness_pack_v0_5_3.json"), "--pack")) -> None:
    payload = _load_endpoint_pack(pack)
    rows = payload['endpoint_readiness']
    if ready_only:
        rows = [row for row in rows if row['live_test_safety'] in {'safe_for_next_bounded_metadata_test', 'safe_for_next_bounded_range_test'}]
    if wide:
        text = render.table_plain(
            ["source_id", "provider", "status", "probe", "content", "endpoint", "risk_notes"],
            [[r['source_id'], r['provider'], r['endpoint_status'], r['recommended_probe_type'], r['expected_content_type'], r.get('known_endpoint_or_catalog_url') or '', r.get('risk_notes') or ''] for r in rows],
            max_widths=[34, 22, 28, 24, 24, 32, 40],
        )
    else:
        text = render.table_plain(
            ["source_id", "status", "probe", "max_bytes", "score", "next_action"],
            [[r['source_id'], r['endpoint_status'], r['recommended_probe_type'], r['max_bytes_recommended'], r['quality_candidate_score'], r['next_exact_action']] for r in rows],
            max_widths=[34, 30, 22, 9, 5, 32],
        )
    emit({'endpoint_readiness': rows}, json_output=json_output, plain=True, plain_text="Endpoint readiness pack\n" + text)


def endpoints(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain"), wide: bool = typer.Option(False, "--wide"), ready_only: bool = typer.Option(False, "--ready-only")) -> None:
    cook_endpoints(json_output=json_output, plain=plain, wide=wide, ready_only=ready_only, pack=Path("reports/endpoint_readiness_pack_v0_5_3.json"))


@task_app.command("new")
def task_new(
    task_id: str = typer.Option(..., "--id"),
    name: str = typer.Option(..., "--name"),
    bbox: str = typer.Option(..., "--bbox"),
    bbox_crs: str = typer.Option(..., "--bbox-crs"),
    target_crs: str = typer.Option(..., "--target-crs"),
    resolution_m: float = typer.Option(30, "--resolution-m"),
    years: str = typer.Option("", "--years"),
    dates: str = typer.Option("", "--dates"),
    theme: list[str] = typer.Option([], "--theme"),
    source: list[str] = typer.Option([], "--source"),
    description: str | None = typer.Option(None, "--description"),
    json_output: bool = typer.Option(False, "--json"),
    plain: bool = typer.Option(False, "--plain"),
) -> None:
    task = task_builder.default_task(task_id, name, task_builder.parse_bbox(bbox), bbox_crs, target_crs, task_builder.parse_years(years), list(theme), list(source), description, resolution_m=resolution_m, dates=task_builder.parse_dates(dates))
    errors = task_builder.validate_task(task)
    if errors:
        raise typer.BadParameter("; ".join(errors))
    path = task_builder.save_task(task)
    summary = task_builder.write_task_reports(task)
    payload = {"task_path": str(path), "summary": summary}
    text = f"created task: {path}\njson: {summary['output_artifacts']['task_json']}\nmarkdown: {summary['output_artifacts']['task_md']}\n"
    emit(payload, json_output=json_output, plain=True, plain_text=text)


@task_app.command("list")
def task_list(json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain")) -> None:
    rows = task_builder.list_tasks()
    text = render.table_plain(["task_id", "name", "sources", "themes", "path"], [[r["task_id"], r["name"], r["sources"], r["themes"], r["path"]] for r in rows], max_widths=[36, 34, 7, 7, 46])
    emit({"tasks": rows}, json_output=json_output, plain=True, plain_text=text)


@task_app.command("show")
def task_show(task_id: str, json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain")) -> None:
    task = task_builder.load_task(task_id)
    summary = task_builder.task_summary(task)
    emit(summary, json_output=json_output, plain=True, plain_text=task_builder.render_task_plain(summary))


@task_app.command("validate")
def task_validate(task_id: str, json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain")) -> None:
    task = task_builder.load_task(task_id)
    errors = task_builder.validate_task(task)
    payload = {"task_id": task_id, "status": "PASS" if not errors else "FAIL", "errors": errors, "network_needed": False}
    text = f"task_id: {task_id}\nstatus: {payload['status']}\nnetwork_needed: False\n" + ("" if not errors else "errors:\n" + "\n".join(f"  - {e}" for e in errors) + "\n")
    emit(payload, json_output=json_output, plain=True, plain_text=text)
    if errors:
        raise typer.Exit(code=1)


@task_app.command("preview")
def task_preview(task_id: str, open_after_create: bool = typer.Option(False, "--open"), json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain")) -> None:
    report = task_builder.create_preview(task_builder.load_task(task_id), open_after_create=open_after_create)
    text = f"preview_png: {report['preview_png']}\npreview_json: {report['preview_json']}\npreview_md: {report['preview_md']}\nnetwork_run: False\n"
    if open_after_create:
        text += task_builder.open_preview(Path(report['preview_png'])) + "\n"
    emit(report, json_output=json_output, plain=True, plain_text=text)


@task_app.command("preview-real")
def task_preview_real(
    task_id: str,
    allow_network: bool = typer.Option(False, "--allow-network"),
    max_bytes_per_source: int = typer.Option(real_preview.DEFAULT_MAX_BYTES_PER_SOURCE, "--max-bytes-per-source"),
    max_pixels: int = typer.Option(real_preview.DEFAULT_MAX_PIXELS, "--max-pixels"),
    timeout_seconds: int = typer.Option(real_preview.DEFAULT_TIMEOUT_SECONDS, "--timeout-seconds"),
    preview_size: int = typer.Option(real_preview.DEFAULT_PREVIEW_SIZE, "--preview-size"),
    cdl_verify_samples: bool = typer.Option(True, "--cdl-verify-samples/--no-cdl-verify-samples", help="--cdl-verify-samples / --no-cdl-verify-samples"),
    sample_grid_size: int = typer.Option(real_preview.DEFAULT_SAMPLE_GRID_SIZE, "--sample-grid-size", min=1, max=7, help="--sample-grid-size"),
    grid_size: Optional[int] = typer.Option(None, "--grid-size", min=1, max=7, help="--grid-size alias for --sample-grid-size"),
    preview_expand_factor: float = typer.Option(real_preview.DEFAULT_PREVIEW_EXPAND_FACTOR, "--preview-expand-factor", min=1.0, max=25.0, help="--preview-expand-factor"),
    cdl_render_mode: str = typer.Option("auto", "--cdl-render-mode", help="--cdl-render-mode: auto, service_png, manual_samples, service_tiff"),
    layout: str = typer.Option(real_preview.DEFAULT_PREVIEW_LAYOUT, "--layout", help="--layout: clean, cockpit, report"),
    visibility_mode: str = typer.Option("typed-log", "--visibility-mode", help="--visibility-mode: typed-log, equal, base-dominant"),
    overlay_strength: float = typer.Option(1.0, "--overlay-strength", min=0.25, max=2.0),
    debug_artifacts: bool = typer.Option(False, "--debug-artifacts"),
    no_cache_raw: bool = typer.Option(False, "--no-cache-raw"),
    include_archives: bool = typer.Option(False, "--include-archives"),
    open_after_create: bool = typer.Option(False, "--open"),
    json_output: bool = typer.Option(False, "--json"),
    plain: bool = typer.Option(False, "--plain"),
) -> None:
    """Create a bounded real-data preview. CDL options: --cdl-verify-samples --sample-grid-size --grid-size --preview-expand-factor --cdl-render-mode."""
    report = real_preview.create_real_preview(
        task_builder.load_task(task_id),
        allow_network=allow_network,
        max_bytes_per_source=max_bytes_per_source,
        max_pixels=max_pixels,
        timeout_seconds=timeout_seconds,
        include_archives=include_archives,
        open_after_create=open_after_create,
        preview_size=preview_size,
        debug_artifacts=debug_artifacts,
        cache_raw=not no_cache_raw,
        cdl_verify_samples=cdl_verify_samples,
        sample_grid_size=grid_size if grid_size is not None else sample_grid_size,
        preview_expand_factor=preview_expand_factor,
        cdl_render_mode=cdl_render_mode,
        preview_layout=layout,
        visibility_mode=visibility_mode,
        overlay_strength=overlay_strength,
    )
    if report["network_run"]:
        text = (
            f"real_preview_png: {report['png_path']}\n"
            f"real_preview_json: {report['preview_json']}\n"
            f"real_preview_md: {report['md_path']}\n"
            f"network_run: {report['network_run']}\n"
            f"real_fetch_attempted: {report['real_fetch_attempted']}\n"
            f"real_raster_data_rendered: {report['real_raster_data_rendered']}\n"
        )
    else:
        text = (
            f"real_preview_plan_json: {report['json_path']}\n"
            f"real_preview_plan_md: {report['md_path']}\n"
            f"network_run: {report['network_run']}\n"
            f"real_fetch_attempted: {report['real_fetch_attempted']}\n"
            "dry_run: True\n"
        )
    emit(report, json_output=json_output, plain=True, plain_text=text)


@copernicus_app.command("auth-check")
def copernicus_auth_check(
    live: bool = typer.Option(False, "--live"),
    allow_network: bool = typer.Option(False, "--allow-network"),
    json_output: bool = typer.Option(False, "--json"),
    plain: bool = typer.Option(False, "--plain"),
) -> None:
    if live and not allow_network:
        raise typer.BadParameter("--live requires --allow-network")
    if live:
        report = copernicus_auth.live_auth_readiness_check()
    else:
        report = copernicus_auth.validate_cdse_auth_presence()
        report.update({
            "network_run": False,
            "live_probe_attempted": False,
            "endpoint": None,
            "http_status": None,
            "bytes_read": 0,
            "token_redacted": bool(report.get("token_present")),
            "no_downloads": True,
        })
    text = (
        f"auth_present: {report['auth_present']}\n"
        f"auth_method: {report['auth_method']}\n"
        f"network_run: {report['network_run']}\n"
        f"live_probe_attempted: {report['live_probe_attempted']}\n"
        f"endpoint: {report['endpoint']}\n"
        f"http_status: {report['http_status']}\n"
        f"bytes_read: {report['bytes_read']}\n"
        f"token_redacted: {report['token_redacted']}\n"
        "no_downloads: True\n"
    )
    emit(report, json_output=json_output, plain=True, plain_text=text)


@copernicus_sentinel_app.command("search-plan")
def copernicus_sentinel_search_plan(
    task_id: str,
    cloud_cover_max: int = typer.Option(30, "--cloud-cover-max"),
    json_output: bool = typer.Option(False, "--json"),
    plain: bool = typer.Option(False, "--plain"),
) -> None:
    plan = copernicus_cdse.create_search_plan(task_id, cloud_cover_max=cloud_cover_max)
    text = (
        f"search_plan_json: {plan['json_path']}\n"
        f"search_plan_md: {plan['md_path']}\n"
        f"source_id: {plan['source_id']}\n"
        f"collection: {plan['collection']}\n"
        f"auth_present: {plan['auth_present']}\n"
        "network_run: False\n"
    )
    emit(plan, json_output=json_output, plain=True, plain_text=text)


@copernicus_sentinel_app.command("search-live")
def copernicus_sentinel_search_live(
    task_id: str,
    allow_network: bool = typer.Option(False, "--allow-network"),
    collection: str = typer.Option(copernicus_cdse.SENTINEL2_L2A_COLLECTION, "--collection"),
    cloud_cover_max: int = typer.Option(30, "--cloud-cover-max"),
    max_items: int = typer.Option(5, "--max-items"),
    timeout_seconds: int = typer.Option(25, "--timeout-seconds"),
    max_bytes: int = typer.Option(1_000_000, "--max-bytes"),
    fields_minimal: bool = typer.Option(False, "--fields-minimal"),
    json_output: bool = typer.Option(False, "--json"),
    plain: bool = typer.Option(False, "--plain"),
) -> None:
    if not allow_network:
        raise typer.BadParameter("search-live requires --allow-network")
    report = copernicus_cdse.create_search_live(
        task_id,
        collection=collection,
        cloud_cover_max=cloud_cover_max,
        max_items=max_items,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
        fields_minimal=fields_minimal,
    )
    text = (
        f"search_live_json: {report['json_path']}\n"
        f"search_live_md: {report['md_path']}\n"
        f"source_id: {report['source_id']}\n"
        f"collection: {report['collection']}\n"
        f"http_status: {report['http_status']}\n"
        f"item_count: {report['item_count']}\n"
        "no_downloads: True\n"
    )
    emit(report, json_output=json_output, plain=True, plain_text=text)


@stack_app.command("preview")
def stack_preview(task_id: str, open_after_create: bool = typer.Option(False, "--open"), json_output: bool = typer.Option(False, "--json"), plain: bool = typer.Option(False, "--plain")) -> None:
    task_preview(task_id, open_after_create=open_after_create, json_output=json_output, plain=plain)


@stack_app.command("preview-real")
def stack_preview_real(
    task_id: str,
    allow_network: bool = typer.Option(False, "--allow-network"),
    max_bytes_per_source: int = typer.Option(real_preview.DEFAULT_MAX_BYTES_PER_SOURCE, "--max-bytes-per-source"),
    max_pixels: int = typer.Option(real_preview.DEFAULT_MAX_PIXELS, "--max-pixels"),
    timeout_seconds: int = typer.Option(real_preview.DEFAULT_TIMEOUT_SECONDS, "--timeout-seconds"),
    preview_size: int = typer.Option(real_preview.DEFAULT_PREVIEW_SIZE, "--preview-size"),
    cdl_verify_samples: bool = typer.Option(True, "--cdl-verify-samples/--no-cdl-verify-samples", help="--cdl-verify-samples / --no-cdl-verify-samples"),
    sample_grid_size: int = typer.Option(real_preview.DEFAULT_SAMPLE_GRID_SIZE, "--sample-grid-size", min=1, max=7, help="--sample-grid-size"),
    grid_size: Optional[int] = typer.Option(None, "--grid-size", min=1, max=7, help="--grid-size alias for --sample-grid-size"),
    preview_expand_factor: float = typer.Option(real_preview.DEFAULT_PREVIEW_EXPAND_FACTOR, "--preview-expand-factor", min=1.0, max=25.0, help="--preview-expand-factor"),
    cdl_render_mode: str = typer.Option("auto", "--cdl-render-mode", help="--cdl-render-mode: auto, service_png, manual_samples, service_tiff"),
    layout: str = typer.Option(real_preview.DEFAULT_PREVIEW_LAYOUT, "--layout", help="--layout: clean, cockpit, report"),
    visibility_mode: str = typer.Option("typed-log", "--visibility-mode", help="--visibility-mode: typed-log, equal, base-dominant"),
    overlay_strength: float = typer.Option(1.0, "--overlay-strength", min=0.25, max=2.0),
    debug_artifacts: bool = typer.Option(False, "--debug-artifacts"),
    no_cache_raw: bool = typer.Option(False, "--no-cache-raw"),
    include_archives: bool = typer.Option(False, "--include-archives"),
    open_after_create: bool = typer.Option(False, "--open"),
    json_output: bool = typer.Option(False, "--json"),
    plain: bool = typer.Option(False, "--plain"),
) -> None:
    """Alias for task preview-real. CDL options: --cdl-verify-samples --sample-grid-size --grid-size --preview-expand-factor --cdl-render-mode."""
    task_preview_real(
        task_id,
        allow_network=allow_network,
        max_bytes_per_source=max_bytes_per_source,
        max_pixels=max_pixels,
        timeout_seconds=timeout_seconds,
        include_archives=include_archives,
        open_after_create=open_after_create,
        preview_size=preview_size,
        debug_artifacts=debug_artifacts,
        no_cache_raw=no_cache_raw,
        cdl_verify_samples=cdl_verify_samples,
        sample_grid_size=sample_grid_size,
        grid_size=grid_size,
        preview_expand_factor=preview_expand_factor,
        cdl_render_mode=cdl_render_mode,
        layout=layout,
        visibility_mode=visibility_mode,
        overlay_strength=overlay_strength,
        json_output=json_output,
        plain=plain,
    )

def register_product_commands(app: typer.Typer) -> None:
    app.command("version")(version_command)
    app.command("doctor")(doctor_command)
    app.add_typer(sources_app, name="sources")
    app.add_typer(stack_app, name="stack")
    app.add_typer(task_app, name="task")
    copernicus_app.add_typer(copernicus_sentinel_app, name="sentinel")
    app.add_typer(copernicus_app, name="copernicus")
    app.add_typer(unlocks_app, name="unlocks")
    app.add_typer(auth_app, name="auth")
    app.add_typer(probe_app, name="probe")
    app.add_typer(help_app, name="help")
    app.command("explore")(explore_command)
    app.command("pantry")(pantry)
    app.command("sauces")(sauces)
    app.command("sauce")(sauce)
    app.command("reigns")(reigns)
    app.command("buckets")(buckets)
    app.command("goods")(goods)
    app.command("bads")(bads)
    app.command("recipe")(recipe)
    app.command("batcher")(batcher)
    app.command("dips")(dips)
    app.add_typer(menu_app, name="menu")
    app.add_typer(toggles_app, name="toggles")
    app.add_typer(cook_app, name="cook")
    app.add_typer(knobs_app, name="knobs")
    app.command("cookplan")(cookplan)
    app.command("queue")(queue)
    app.command("cookdip")(cookdip)
    app.command("cookproposal")(cookproposal)
    app.command("source-scope")(source_scope)
    app.command("scope")(source_scope)
    app.command("endpoints")(endpoints)
