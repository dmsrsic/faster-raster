from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def classify(entry: dict[str, Any]) -> str:
    if entry["promotion_status"] in {"runtime_supported", "experimental_probe_supported"}:
        return "probe_next" if entry["bounded_probe_appropriate"] else "docs_research_next"
    if entry["credential_requirement"] != "none":
        return "auth_scaffold_next"
    if entry["promotion_status"] == "blocked_by_adapter":
        return "adapter_next"
    if entry["access_pattern_category"] in {"future_unverified", "mirror_candidate"}:
        return "docs_research_next"
    return "blocked"


def score(entry: dict[str, Any]) -> int:
    value = 0
    if entry["credential_requirement"] == "none": value += 30
    if entry["bounded_probe_appropriate"]: value += 25
    if entry["provenance_class"] in {"official_primary", "official_cloud_mirror"}: value += 20
    if entry["promotion_status"] == "blocked_by_adapter": value += 10
    if entry["access_pattern_category"] == "credential_gated": value -= 15
    if entry["provenance_class"] in {"community_mirror", "rescued_archive_unverified"}: value -= 20
    return value


def plan_unlocks(atlas: dict[str, Any], stack: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    plans = []
    verified = {r["source_id"] for r in (stack or {}).get("source_results", []) if r.get("result_class") == "pass_verified"}
    for entry in atlas["sources"]:
        if entry["source_id"] in verified:
            continue
        cls = classify(entry)
        plans.append({
            "source_id": entry["source_id"],
            "display_name": entry["display_name"],
            "class": cls,
            "score": score(entry),
            "credential_requirement": entry["credential_requirement"],
            "bounded_probe_appropriate": entry["bounded_probe_appropriate"],
            "recommended_action": recommendation(entry, cls),
        })
    return sorted(plans, key=lambda row: (-row["score"], row["source_id"]))


def recommendation(entry: dict[str, Any], cls: str) -> str:
    if cls == "probe_next":
        return "run or preserve bounded opt-in probe"
    if cls == "adapter_next":
        return "write metadata-only adapter/probe design"
    if cls == "auth_scaffold_next":
        return "complete credential/session scaffold before live probe"
    if cls == "docs_research_next":
        return "verify official docs and endpoint semantics"
    return "blocked until source-specific endpoint is selected"


def write_reports(plan: list[dict[str, Any]], out: Path, markdown: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    markdown.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"unlock_plan": plan}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = ["# Source Unlock Plan", "", "| Rank | Source | Class | Score | Action |", "| ---: | --- | --- | ---: | --- |"]
    for idx, row in enumerate(plan, 1):
        lines.append(f"| {idx} | `{row['source_id']}` | `{row['class']}` | {row['score']} | {row['recommended_action']} |")
    markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--atlas", required=True)
    parser.add_argument("--stack")
    parser.add_argument("--out", required=True)
    parser.add_argument("--markdown", required=True)
    args = parser.parse_args()
    stack = json.loads(Path(args.stack).read_text()) if args.stack else None
    plan = plan_unlocks(load_yaml(Path(args.atlas)), stack)
    write_reports(plan, Path(args.out), Path(args.markdown))
    print(json.dumps({"planned": len(plan), "top": plan[:5]}, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
