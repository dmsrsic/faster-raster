from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import yaml

GROUPS = [
    ("Verified now", {"pass_verified"}),
    ("Reused existing result", {"existing_result_reused"}),
    ("Credential gated", {"credential_gated"}),
    ("Adapter needed", {"adapter_needed"}),
    ("Mirror candidates", set()),
    ("Future unverified", set()),
]


def load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def next_unlock(entry: dict[str, Any], result_class: str | None) -> str:
    if result_class == "pass_verified":
        return "preserve proof and add contract fixture"
    if entry["credential_requirement"] != "none":
        return "complete auth scaffold before probe"
    if entry["promotion_status"] == "blocked_by_adapter":
        return "design adapter and metadata probe"
    if entry["access_pattern_category"] == "mirror_candidate":
        return "verify provenance and bounded probe"
    return "docs verification or bounded probe design"


def build_matrix(stack: dict[str, Any], atlas: dict[str, Any]) -> list[dict[str, Any]]:
    stack_by_id = {r["source_id"]: r for r in stack.get("source_results", [])}
    rows = []
    for entry in atlas["sources"]:
        result = stack_by_id.get(entry["source_id"])
        result_class = result.get("result_class") if result else None
        rows.append({
            "source_id": entry["source_id"],
            "display_name": entry["display_name"],
            "provider": entry["provider"],
            "access_mode": entry["access_mode"],
            "access_pattern_category": entry["access_pattern_category"],
            "provenance_class": entry["provenance_class"],
            "trust_level": entry["trust_level"],
            "credential_requirement": entry["credential_requirement"],
            "promotion_status": entry["promotion_status"],
            "probe_result_class": result_class or "not_in_stack_probe",
            "last_http_status": result.get("http_status") if result else None,
            "last_bytes_read": result.get("bytes_read") if result else 0,
            "last_sha256_present": bool(result and result.get("sha256")),
            "bounded_probe_appropriate": entry["bounded_probe_appropriate"],
            "deterministic_url_generation_direct": entry["deterministic_url_generation_direct"],
            "deterministic_asset_resolution_after_discovery": entry["deterministic_asset_resolution_after_discovery"],
            "next_unlock_step": next_unlock(entry, result_class),
        })
    return rows


def group_name(row: dict[str, Any]) -> str:
    if row["probe_result_class"] == "pass_verified":
        return "Verified now"
    if row["probe_result_class"] == "existing_result_reused":
        return "Reused existing result"
    if row["credential_requirement"] != "none" or row["probe_result_class"] == "credential_gated":
        return "Credential gated"
    if row["promotion_status"] == "blocked_by_adapter" or row["probe_result_class"] == "adapter_needed":
        return "Adapter needed"
    if row["access_pattern_category"] == "mirror_candidate":
        return "Mirror candidates"
    return "Future unverified"


def write_outputs(rows: list[dict[str, Any]], out_json: Path, out_csv: Path, markdown: Path) -> None:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    markdown.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps({"rows": rows}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    fieldnames = list(rows[0]) if rows else []
    with out_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    lines = ["# Source Stack Matrix", ""]
    for group in ["Verified now", "Reused existing result", "Credential gated", "Adapter needed", "Mirror candidates", "Future unverified"]:
        subset = [row for row in rows if group_name(row) == group]
        if not subset:
            continue
        lines.extend([f"## {group}", "", "| Source | Provider | Access | Probe | Bytes | Next unlock |", "| --- | --- | --- | --- | ---: | --- |"])
        for row in subset:
            lines.append(f"| `{row['source_id']}` | {row['provider']} | `{row['access_mode']}` | `{row['probe_result_class']}` | {row['last_bytes_read']} | {row['next_unlock_step']} |")
        lines.append("")
    markdown.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stack", required=True)
    parser.add_argument("--atlas", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-csv", required=True)
    parser.add_argument("--markdown", required=True)
    args = parser.parse_args()
    rows = build_matrix(json.loads(Path(args.stack).read_text()), load_yaml(Path(args.atlas)))
    write_outputs(rows, Path(args.out_json), Path(args.out_csv), Path(args.markdown))
    print(json.dumps({"rows": len(rows)}, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
