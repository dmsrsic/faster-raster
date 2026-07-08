from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from faster_raster import cli_models as models
from faster_raster import cli_render as render
from faster_raster import cli_lingo

@dataclass
class ExploreResult:
    should_exit: bool
    output: str


def handle_slash_command(command: str, *, atlas_path: Path = models.DEFAULT_ATLAS, stack_path: Path = models.DEFAULT_STACK, unlocks_path: Path = models.DEFAULT_UNLOCKS, auth_path: Path = models.DEFAULT_AUTH) -> ExploreResult:
    command = command.strip()
    if not command:
        return ExploreResult(False, "")
    parts = shlex.split(command)
    cmd = parts[0]
    if cmd in {"/exit", "/quit"}:
        return ExploreResult(True, "bye\n")
    if cmd in {"/help", "/menu"}:
        return ExploreResult(False, "commands: /help /menu /help.style /menu.lingo /sources /pantry /sauces /source SOURCE_ID /sauce SOURCE_ID /stack /recipe /unlocks /batcher /auth /probe SOURCE_ID --dry-run /dip SOURCE_ID --dry-run /exit\n")
    if cmd in {"/help.style", "/menu.lingo"}:
        return ExploreResult(False, cli_lingo.glossary_text() + "\n" + render.help_style_plain())

    sources = models.load_sources(atlas_path)
    matrix = models.load_matrix(models.DEFAULT_MATRIX) if models.DEFAULT_MATRIX.exists() else []
    unlocks = models.load_unlocks(unlocks_path) if unlocks_path.exists() else []
    unlock_map = models.unlock_by_source(unlocks)
    matrix_map = models.matrix_by_source(matrix)

    def source_rows(mode: str | None = None):
        rows = [models.source_row(s, unlock_map.get(s["source_id"]), matrix_map.get(s["source_id"])) for s in sources]
        if mode in {"verified", "goods"}:
            rows = [r for r in rows if r["status"] == "verified_now"]
        elif mode in {"gated", "locks"}:
            rows = [r for r in rows if r["status"] == "credential_gated"]
        elif mode in {"adapter", "tool"}:
            rows = [r for r in rows if r["status"] == "adapter_needed"]
        elif mode == "mirror":
            rows = [r for r in rows if r["status"] == "mirror_candidate"]
        return rows

    if cmd in {"/sources", "/pantry", "/sauces"}:
        mode = parts[1] if len(parts) > 1 else None
        heading = "Pantry / sauces\n" if cmd in {"/pantry", "/sauces"} else ""
        return ExploreResult(False, heading + render.render_sources_plain(source_rows(mode)))
    if cmd == "/goods":
        return ExploreResult(False, "Goods / verified sauces\n" + render.render_sources_plain(source_rows("goods")))
    if cmd == "/bads":
        rows = [r for r in source_rows() if r["status"] in {"credential_gated", "adapter_needed", "blocked", "future_unverified", "failed_probe", "skipped_policy"}]
        return ExploreResult(False, "Bads / gated, blocked, waiting sauces\n" + render.render_sources_plain(rows))
    if cmd in {"/source", "/sauce"} and len(parts) > 1:
        source = models.source_by_id(sources, parts[1])
        return ExploreResult(False, "Sauce Card\n" + render.render_source_detail_plain(source, unlock_map.get(source["source_id"]), matrix_map.get(source["source_id"])))
    if cmd == "/flavors" and len(parts) > 1:
        source = models.source_by_id(sources, parts[1])
        formats = ", ".join(source.get("expected_formats", [])) or "unknown"
        return ExploreResult(False, f"flavors for {parts[1]}\nexpected_formats: {formats}\ntemporal_key_structure: {source.get('temporal_key_structure')}\nspatial_key_structure: {source.get('spatial_key_structure')}\n")
    if cmd in {"/reigns", "/buckets"}:
        groups: dict[str, list[dict]] = {}
        if cmd == "/reigns":
            for source in sources:
                groups.setdefault(source.get("source_family") or source.get("source_kind") or "unknown", []).append(source)
            return ExploreResult(False, "Reigns / grouped source families\n" + render.render_tree_plain(groups))
        for source in sources:
            groups.setdefault(models.source_status(source, matrix_map.get(source["source_id"])), []).append(source)
        return ExploreResult(False, "Buckets / readiness groups\n" + render.render_tree_plain(groups))
    if cmd in {"/stack", "/recipe"}:
        heading = "Recipe Board\n" if cmd == "/recipe" else ""
        return ExploreResult(False, heading + render.render_stack_summary_plain(models.stack_summary(models.load_stack(stack_path), models.load_atlas(atlas_path))))
    if cmd in {"/unlocks", "/batcher"}:
        heading = "Batcher / unlock planner\n" if cmd == "/batcher" else ""
        return ExploreResult(False, heading + render.render_unlocks_plain(unlocks, limit=10))
    if cmd == "/auth":
        return ExploreResult(False, render.render_auth_plain(models.auth_rows(models.load_auth(auth_path))))
    if cmd in {"/probe", "/dip"} and len(parts) > 2 and "--dry-run" in parts:
        from scripts.probe_atlas_source import load_atlas, run_atlas_probe
        report = run_atlas_probe(load_atlas(atlas_path), source_id=parts[1], allow_network=False, max_bytes=65_536)
        if parts[1] == "gridmet_daily":
            report["classification"] = models.gridmet_dry_run_classification(report)
        heading = "Dip Check\n" if cmd == "/dip" else ""
        return ExploreResult(False, heading + render.render_probe_plain(report))
    return ExploreResult(False, f"unknown command: {command}\n")


def run_explore() -> None:
    while True:
        try:
            command = input("fr> ")
        except EOFError:
            print()
            return
        result = handle_slash_command(command)
        if result.output:
            print(result.output, end="")
        if result.should_exit:
            return
