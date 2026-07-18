from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


SOURCE_PREFIXES = (
    ".github/",
    "configs/",
    "docs/",
    "examples/",
    "faster_raster/",
    "recipes/",
    "schemas/",
    "scripts/",
    "tests/",
)
SOURCE_FILES = {".gitignore", "README.md", "pyproject.toml"}


def classify_release_path(path: str) -> tuple[str, bool, str]:
    normalized = path.replace("\\", "/")
    if normalized.startswith("outputs/beta_gate_1/"):
        return "baseline_or_validation_evidence", False, "generated gate evidence"
    if normalized.startswith("outputs/"):
        return "generated_output", False, "runtime output is not shipped as source"
    if normalized.startswith("reports/"):
        return "generated_report", False, "tracked and local reports are evidence, not wheel source"
    if normalized.startswith("recipes/ag/orders/"):
        return "operator_order", False, "operator-specific order"
    if normalized.startswith("scripts/") and ".backup." in Path(normalized).name:
        return "backup", False, "local backup"
    if normalized.startswith((".beta-tools/", ".fasterraster/", ".local-state/", "cache/")):
        return "local_runtime_state", False, "machine-local tool, cache, or state"
    if normalized.startswith(("build/", "dist/")) or normalized.endswith((".whl", ".tar.gz")):
        return "build_artifact", False, "reproducible package output"
    if normalized in SOURCE_FILES or normalized.startswith(SOURCE_PREFIXES):
        return "release_source", True, "source, test, schema, documentation, or CI input"
    return "repository_support", True, "tracked repository support file"


def _git_lines(root: Path, *arguments: str) -> list[str]:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def build_release_inventory(root: Path) -> dict[str, Any]:
    tracked = set(_git_lines(root, "ls-files"))
    status_lines = _git_lines(root, "status", "--porcelain=v1", "--untracked-files=all")
    status: dict[str, str] = {}
    for line in status_lines:
        code = line[:2]
        raw = line[3:]
        path = raw.split(" -> ", 1)[-1]
        status[path] = code
    paths = sorted(tracked | set(status))
    entries = []
    for path in paths:
        category, included, reason = classify_release_path(path)
        entries.append(
            {
                "path": path,
                "tracked": path in tracked,
                "worktree_status": status.get(path),
                "category": category,
                "included_in_source_release": included,
                "reason": reason,
            }
        )
    included = [item for item in entries if item["included_in_source_release"]]
    excluded = [item for item in entries if not item["included_in_source_release"]]
    return {
        "schema_version": "fasterraster.beta-release-inventory/v1",
        "policy": "path classification is deterministic and independent of timestamps",
        "summary": {
            "entries": len(entries),
            "included": len(included),
            "excluded": len(excluded),
            "tracked": sum(1 for item in entries if item["tracked"]),
            "untracked": sum(1 for item in entries if not item["tracked"]),
            "dirty": sum(1 for item in entries if item["worktree_status"]),
        },
        "entries": entries,
    }


def inventory_markdown(inventory: dict[str, Any]) -> str:
    summary = inventory["summary"]
    lines = [
        "# Beta release inventory",
        "",
        "This inventory is generated deterministically from Git tracking state and explicit path rules.",
        "",
        f"- Included source entries: {summary['included']}",
        f"- Excluded evidence/runtime entries: {summary['excluded']}",
        f"- Dirty entries classified: {summary['dirty']}",
        "",
        "| Path | Status | Category | Ship | Reason |",
        "|---|---:|---|:---:|---|",
    ]
    for item in inventory["entries"]:
        status = item["worktree_status"] or "clean"
        ship = "yes" if item["included_in_source_release"] else "no"
        lines.append(
            f"| `{item['path']}` | `{status}` | {item['category']} | {ship} | {item['reason']} |"
        )
    return "\n".join(lines) + "\n"
