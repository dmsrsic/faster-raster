from __future__ import annotations

import json
import re
from typing import Any

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

STATUS_STYLE = {
    "verified_now": ("?", "green"),
    "reused_existing_result": ("i", "blue"),
    "credential_gated": ("lock", "yellow"),
    "adapter_needed": ("tool", "cyan"),
    "mirror_candidate": ("archive", "magenta"),
    "future_unverified": ("?", "dim"),
    "blocked": ("x", "red"),
    "failed_probe": ("x", "red"),
    "skipped_policy": ("-", "dim"),
}


def stable_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def strip_ansi(value: str) -> str:
    return ANSI_RE.sub("", value)


def status_label(status: str, *, plain: bool = False) -> str:
    symbol, _ = STATUS_STYLE.get(status, ("?", "dim"))
    return status if plain else f"{symbol} {status}"



SHORT_REPLACEMENTS = {
    "complete credential/session scaffold before live probe": "auth scaffold",
    "write metadata-only adapter/probe design": "metadata adapter",
    "verify official docs and endpoint semantics": "verify endpoint",
    "preserve proof and add contract fixture": "preserve proof",
    "run or preserve bounded opt-in probe": "preserve opt-in proof",
}


def shorten_text(value: Any, *, max_len: int = 28) -> str:
    text = str(value) if value is not None else ""
    for old, new in SHORT_REPLACEMENTS.items():
        text = text.replace(old, new)
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    return text[: max(0, max_len - 1)] + "?"


def table_plain(headers: list[str], rows: list[list[Any]], *, max_widths: list[int] | None = None) -> str:
    values = [[str(cell) if cell is not None else "" for cell in row] for row in rows]
    if max_widths:
        values = [[shorten_text(cell, max_len=max_widths[idx]) for idx, cell in enumerate(row)] for row in values]
    widths = [len(str(header)) for header in headers]
    for row in values:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))
    if max_widths:
        widths = [min(widths[idx], max_widths[idx]) for idx in range(len(widths))]
    lines = ["  ".join(str(header).ljust(widths[idx]) for idx, header in enumerate(headers))]
    lines.append("  ".join("-" * width for width in widths))
    for row in values:
        lines.append("  ".join(row[idx].ljust(widths[idx]) for idx in range(len(widths))))
    return "\n".join(lines) + "\n"


def render_sources_plain(rows: list[dict[str, Any]], *, columns: str = "standard") -> str:
    if columns == "essential":
        return table_plain(
            ["source_id", "provider", "bucket", "trust", "credential", "promotion", "next_unlock_short"],
            [[r["source_id"], r["provider"], r["access_pattern_category"], r["trust_level"], r["credential_requirement"], r["promotion_status"], r.get("next_unlock_step") or ""] for r in rows],
            max_widths=[44, 18, 18, 18, 14, 24, 22],
        )
    if columns == "full":
        return table_plain(
            ["source_id", "display_name", "provider", "category", "provenance", "trust", "credential", "promotion", "next_unlock"],
            [[r["source_id"], r.get("display_name", ""), r["provider"], r["access_pattern_category"], r["provenance_class"], r["trust_level"], r["credential_requirement"], r["promotion_status"], r.get("next_unlock_step") or ""] for r in rows],
        )
    return table_plain(
        ["source_id", "provider", "bucket", "trust", "credential", "promotion", "next_unlock_short"],
        [[r["source_id"], r["provider"], r["access_pattern_category"], r["trust_level"], r["credential_requirement"], r["promotion_status"], r.get("next_unlock_step") or ""] for r in rows],
        max_widths=[44, 24, 22, 20, 14, 26, 26],
    )


def render_goods_plain(rows: list[dict[str, Any]], *, columns: str = "standard") -> str:
    return table_plain(
        ["source_id", "provider", "access", "trust", "promotion", "next_unlock_short"],
        [[r["source_id"], r["provider"], r["access_pattern_category"], r["trust_level"], r["promotion_status"], r.get("next_unlock_step") or ""] for r in rows],
        max_widths=[44, 24, 22, 20, 26, 26] if columns != "full" else None,
    )


def render_bads_plain(rows: list[dict[str, Any]], *, columns: str = "standard") -> str:
    return table_plain(
        ["source_id", "provider", "blocker", "credential", "next_unlock_short"],
        [[r["source_id"], r["provider"], r.get("status") or r["access_pattern_category"], r["credential_requirement"], r.get("next_unlock_step") or ""] for r in rows],
        max_widths=[44, 24, 24, 14, 30] if columns != "full" else None,
    )

def render_source_detail_plain(source: dict[str, Any], unlock: dict[str, Any] | None = None, matrix_row: dict[str, Any] | None = None) -> str:
    lines = [
        f"source_id: {source['source_id']}",
        f"display_name: {source['display_name']}",
        f"provider: {source['provider']}",
        f"access_mode: {source['access_mode']}",
        f"access_pattern_category: {source['access_pattern_category']}",
        f"provenance_class: {source['provenance_class']}",
        f"trust_level: {source['trust_level']}",
        f"endpoint_or_catalog_url: {source.get('endpoint_or_catalog_url')}",
        f"temporal_key_structure: {source.get('temporal_key_structure')}",
        f"spatial_key_structure: {source.get('spatial_key_structure')}",
        f"expected_formats: {', '.join(source.get('expected_formats', []))}",
        f"native_crs_or_grid: {source.get('native_crs_or_grid')}",
        f"target_crs_assumption: {source.get('target_crs_assumption')}",
        f"credential_requirement: {source.get('credential_requirement')}",
        f"credential_profile_id: {source.get('credential_profile_id')}",
        f"bounded_probe_appropriate: {source.get('bounded_probe_appropriate')}",
        f"promotion_status: {source.get('promotion_status')}",
        f"last_probe_report: {source.get('last_probe_report')}",
        f"next_unlock_step: {(unlock or {}).get('recommended_action') or (matrix_row or {}).get('next_unlock_step')}",
        "official_docs:",
    ]
    lines.extend(f"  - {url}" for url in source.get("official_docs", []))
    lines.append(f"notes: {source.get('notes')}")
    return "\n".join(lines) + "\n"


def render_tree_plain(groups: dict[str, list[dict[str, Any]]]) -> str:
    lines: list[str] = []
    for name, rows in groups.items():
        lines.append(name)
        for row in rows:
            lines.append(f"  - {row['source_id']} ({row['provider']})")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_stack_summary_plain(summary: dict[str, Any]) -> str:
    return "\n".join(f"{key}: {value}" for key, value in summary.items()) + "\n"


def render_unlocks_plain(rows: list[dict[str, Any]], *, limit: int | None = None) -> str:
    selected = rows[:limit] if limit else rows
    return table_plain(
        ["rank", "source_id", "class", "credential", "bounded", "score", "reason"],
        [[idx, row["source_id"], row["class"], row["credential_requirement"], row["bounded_probe_appropriate"], row.get("score"), row.get("recommended_action")] for idx, row in enumerate(selected, 1)],
    )


def render_auth_plain(rows: list[dict[str, Any]]) -> str:
    return table_plain(
        ["auth_profile_id", "provider", "auth_type", "required_env_vars", "implementation_status", "enabled_default"],
        [[row["auth_profile_id"], row["provider"], row["auth_type"], ",".join(row.get("required_env_vars", [])), row["implementation_status"], row["enabled_default"]] for row in rows],
    )


def help_style_plain() -> str:
    labels = [
        "verified_now", "reused_existing_result", "credential_gated", "adapter_needed", "mirror_candidate", "future_unverified", "blocked", "failed_probe", "skipped_policy",
    ]
    lines = ["FasterRaster status legend", ""]
    lines.extend(f"- {label}" for label in labels)
    lines.extend([
        "",
        "Dry-run probes never call the network; live probes require --allow-network.",
        "Credential outputs show environment variable names only and never secret values.",
        "Blocked sources are planning states, not global failures.",
        "Examples:",
        "  faster-raster sources list",
        "  faster-raster probe atlas gridmet_daily --dry-run",
    ])
    return "\n".join(lines) + "\n"


def render_probe_plain(report: dict[str, Any]) -> str:
    probe = report["probe"]
    lines = [
        f"source_id: {report['source_id']}",
        f"classification: {report.get('classification') or probe['result_class']}",
        f"result_class: {probe['result_class']}",
        f"http_status: {probe['http_status']}",
        f"bytes_read: {probe['bytes_read']}",
        f"credential_requirement: {report.get('credential_requirement')}",
        f"bounded_probe_appropriate: {report.get('bounded_probe_appropriate')}",
        f"endpoint: {probe.get('url')}",
        f"error: {probe.get('error')}",
    ]
    return "\n".join(lines) + "\n"


def rich_console(no_color: bool = False):
    try:
        from rich.console import Console
        return Console(color_system=None if no_color else "auto")
    except Exception:
        return None


def rich_table(title: str, headers: list[str], rows: list[list[Any]], *, no_color: bool = False) -> bool:
    console = rich_console(no_color)
    if console is None:
        return False
    from rich.table import Table
    table = Table(title=title)
    for header in headers:
        table.add_column(header)
    for row in rows:
        table.add_row(*(str(cell) if cell is not None else "" for cell in row))
    console.print(table)
    return True


def render_lingo_glossary_plain() -> str:
    from faster_raster.cli_lingo import glossary_text
    return glossary_text()

def kitchen_heading(name: str, standard: str) -> str:
    from faster_raster.cli_lingo import title
    return title(name) or standard
