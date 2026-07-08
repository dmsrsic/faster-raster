from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


def write_manifest(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")))
            handle.write("\n")


def read_manifest(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def summarize_manifest(rows: list[dict]) -> dict:
    by_source = Counter(row["source_id"] for row in rows)
    by_year = Counter(str(row["year"]) for row in rows)
    by_layer = Counter(row["thematic_layer"] for row in rows)
    return {
        "records": len(rows),
        "by_source": dict(sorted(by_source.items())),
        "by_year": dict(sorted(by_year.items())),
        "by_thematic_layer": dict(sorted(by_layer.items())),
        "statuses": dict(sorted(Counter(row["status"] for row in rows).items())),
    }

