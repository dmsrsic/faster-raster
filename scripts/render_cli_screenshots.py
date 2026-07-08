from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from faster_raster import cli_models as models


def write_scene(out_dir: Path, name: str, render_fn) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    console = Console(record=True, width=120, color_system="truecolor")
    render_fn(console)
    text = console.export_text(clear=False)
    svg = console.export_svg(title=name)
    (out_dir / f"{name}.txt").write_text(text, encoding="utf-8")
    (out_dir / f"{name}.svg").write_text(svg, encoding="utf-8")


def table(title: str, columns: list[str]) -> Table:
    tbl = Table(title=title)
    for column in columns:
        tbl.add_column(column)
    return tbl


def scene_pantry(console: Console, sources: list[dict[str, Any]], matrix: list[dict[str, Any]], unlocks: list[dict[str, Any]]) -> None:
    mmap = models.matrix_by_source(matrix)
    umap = models.unlock_by_source(unlocks)
    rows = [models.source_row(s, umap.get(s["source_id"]), mmap.get(s["source_id"])) for s in sources]
    tbl = table(f"Pantry: {len(sources)} sauces ready for stacking", ["sauce", "provider", "bucket", "locks", "next"])
    for row in rows[:12]:
        tbl.add_row(row["source_id"], row["provider"], row["status"], row["credential_requirement"], str(row.get("next_unlock_step") or ""))
    console.print(Panel(f"Pantry / Source Atlas\n{len(sources)} sauces, buckets, locks, goods, and bads", title="Kitchen Mode"))
    console.print(tbl)


def scene_sauce_prism(console: Console, sources: list[dict[str, Any]], matrix: list[dict[str, Any]]) -> None:
    source = models.source_by_id(sources, "prism_daily_ppt_static_zip")
    row = models.matrix_by_source(matrix).get(source["source_id"], {})
    body = "\n".join([
        f"sauce: {source['source_id']}",
        f"flavor: {', '.join(source.get('expected_formats', []))}",
        f"crop-cookie: {source.get('spatial_key_structure')}",
        "goods status: verified_now",
        "bt: bounded probe budgeted by policy",
        f"at: last bytes {row.get('last_bytes_read')}",
        f"crumbs: sha256_present={row.get('last_sha256_present')}",
        f"provider: {source['provider']}",
    ])
    console.print(Panel(body, title="Sauce Card: PRISM goods"))


def scene_recipe(console: Console, stack_summary: dict[str, Any]) -> None:
    body = "\n".join([
        f"goods: {stack_summary['verified_live_source_count']}",
        f"locks: {stack_summary['credential_gated_count']}",
        f"adapter-needed bads: {stack_summary['adapter_needed_count']}",
        f"highest verified stack: {stack_summary['highest_verified_stack_count']}",
        f"live bytes: {stack_summary['live_bytes_read']}",
        f"drift: {stack_summary['runtime_drift_status']}",
    ])
    console.print(Panel(body, title="Recipe Board / Stack Summary"))


def scene_gridmet(console: Console, sources: list[dict[str, Any]], unlocks: list[dict[str, Any]]) -> None:
    source = models.source_by_id(sources, "gridmet_daily")
    unlock = models.unlock_by_source(unlocks).get("gridmet_daily", {})
    body = "\n".join([
        "dip check: gridmet_daily",
        "classification: blocked_by_endpoint_uncertainty",
        "bad/blocker: endpoint_or_catalog_url is missing or unknown",
        "network: no network, dry-run only",
        f"next unlock step: {unlock.get('recommended_action')}",
        f"provider: {source['provider']}",
    ])
    console.print(Panel(body, title="Dip Check: gridMET blocked"))


def scene_batcher(console: Console, unlocks: list[dict[str, Any]]) -> None:
    tbl = table("Batcher / unlock planner", ["rank", "sauce", "action class", "locks/adapters", "next"])
    for idx, row in enumerate(unlocks[:8], 1):
        lock = row.get("credential_requirement") or row.get("class")
        tbl.add_row(str(idx), row["source_id"], row.get("class", ""), str(lock), str(row.get("recommended_action") or ""))
    console.print(tbl)


def scene_goods_bads(console: Console, sources: list[dict[str, Any]], matrix: list[dict[str, Any]], unlocks: list[dict[str, Any]]) -> None:
    mmap = models.matrix_by_source(matrix)
    umap = models.unlock_by_source(unlocks)
    goods = []
    bads = []
    for source in sources:
        if "duplicate_guard" in source["source_id"]:
            continue
        row = models.source_row(source, umap.get(source["source_id"]), mmap.get(source["source_id"]))
        if row["status"] == "verified_now":
            goods.append(row)
        elif row["status"] in {"credential_gated", "adapter_needed", "blocked", "future_unverified", "failed_probe", "skipped_policy"}:
            bads.append(row)
    tbl = table("Goods and Bads", ["bucket", "sauce", "provider", "next"])
    for row in goods[:5]:
        tbl.add_row("goods", row["source_id"], row["provider"], str(row.get("next_unlock_step") or ""))
    for row in bads[:8]:
        tbl.add_row("bads", row["source_id"], row["provider"], str(row.get("next_unlock_step") or ""))
    console.print(Panel(f"goods: {len(goods)}\nbads: {len(bads)}\nduplicate guards hidden by default", title="Buckets"))
    console.print(tbl)


def scene_endpoint_readiness(console: Console, pack_path: Path) -> None:
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    tbl = table("Endpoint readiness pack", ["sauce", "status", "probe", "score", "next"])
    for row in pack["endpoint_readiness"]:
        tbl.add_row(row["source_id"], row["endpoint_status"], row["recommended_probe_type"], str(row["quality_candidate_score"]), row["next_exact_action"])
    console.print(Panel("No live dips were run. Exact endpoints are required before metadata/range tests.", title="Endpoint Readiness"))
    console.print(tbl)


def scene_lingo(console: Console) -> None:
    lines = [
        "pantry = source atlas",
        "sauces = sources / datasets",
        "reigns = grouped source families",
        "buckets = status groups",
        "recipes = stack plans",
        "dips = bounded probes",
        "crop-cookie = AOI bounds",
        "bt / at = expected / actual time",
        "goods / bads = ready / blocked sources",
        "locks / flips / crumbs = auth gates / transforms / provenance",
    ]
    console.print(Panel("\n".join(lines), title="Menu Lingo"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--atlas", type=Path, default=models.DEFAULT_ATLAS)
    parser.add_argument("--stack", type=Path, default=models.DEFAULT_MATRIX)
    parser.add_argument("--unlocks", type=Path, default=models.DEFAULT_UNLOCKS)
    parser.add_argument("--out-dir", type=Path, default=Path("reports/cli_screenshots"))
    args = parser.parse_args()

    sources = models.load_sources(args.atlas)
    matrix = models.load_matrix(args.stack)
    unlocks = models.load_unlocks(args.unlocks)
    summary = models.stack_summary(models.load_stack(models.DEFAULT_STACK), models.load_atlas(args.atlas))

    write_scene(args.out_dir, "01_pantry_sauces", lambda c: scene_pantry(c, sources, matrix, unlocks))
    write_scene(args.out_dir, "02_sauce_card_prism", lambda c: scene_sauce_prism(c, sources, matrix))
    write_scene(args.out_dir, "03_recipe_stack_summary", lambda c: scene_recipe(c, summary))
    write_scene(args.out_dir, "04_gridmet_dip_blocked", lambda c: scene_gridmet(c, sources, unlocks))
    write_scene(args.out_dir, "05_batcher_unlocks", lambda c: scene_batcher(c, unlocks))
    write_scene(args.out_dir, "06_menu_lingo", scene_lingo)
    write_scene(args.out_dir, "07_goods_bads", lambda c: scene_goods_bads(c, sources, matrix, unlocks))
    write_scene(args.out_dir, "08_endpoint_readiness", lambda c: scene_endpoint_readiness(c, Path("reports/endpoint_readiness_pack_v0_5_3.json")))
    print(f"wrote screenshots: {args.out_dir}")


if __name__ == "__main__":
    main()
