from __future__ import annotations

import json
from pathlib import Path

from faster_raster.capability_registry import (
    load_capability_registry,
    markdown_table,
    public_json,
    registry_markdown,
)
from faster_raster.grounding_bundle import (
    _grounding_file_record,
    build_grounding_bundle,
)


ROOT = Path(__file__).resolve().parent.parent


def test_grounding_file_hashes_and_sizes_are_newline_independent(tmp_path):
    variants = {
        "lf": b"first line\nsecond line\n",
        "crlf": b"first line\r\nsecond line\r\n",
        "cr": b"first line\rsecond line\r",
    }
    records = []
    for name, content in variants.items():
        path = tmp_path / f"{name}.txt"
        path.write_bytes(content)
        records.append(
            _grounding_file_record(
                path,
                relative_path="same.txt",
                role="fixture",
            )
        )

    assert records[0] == records[1] == records[2]
    assert records[0]["bytes"] == len(variants["lf"])


def test_capability_registry_is_valid_deterministic_and_status_complete():
    first = load_capability_registry()
    second = load_capability_registry()
    assert first["capability_registry_sha256"] == second["capability_registry_sha256"]
    assert set(first["status_definitions"]) == {
        "released",
        "experimental",
        "private",
        "planned",
        "unsupported",
    }
    assert {row["capability_id"] for row in first["capabilities"]} >= {
        "sauce_pack",
        "sauce_time",
        "preview_templates",
        "credential_reference",
    }


def test_checked_in_capability_surfaces_do_not_drift():
    registry = load_capability_registry()
    markdown = (ROOT / "docs" / "generated" / "capabilities.md").read_text(
        encoding="utf-8"
    )
    grounding = json.loads(
        (
            ROOT
            / "prompts"
            / "flavortown_sauce_wizard"
            / "capabilities.json"
        ).read_text(encoding="utf-8")
    )
    assert markdown == registry_markdown(registry)
    assert grounding == public_json(registry)


def _between(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0].strip()


def test_readme_and_supported_source_tables_match_registry():
    registry = load_capability_registry()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    supported = (ROOT / "docs" / "supported-sources.md").read_text(
        encoding="utf-8"
    )
    assert _between(
        readme,
        "<!-- BEGIN GENERATED CAPABILITY MATRIX -->",
        "<!-- END GENERATED CAPABILITY MATRIX -->",
    ) == markdown_table(registry["capabilities"])
    assert _between(
        supported,
        "<!-- BEGIN GENERATED SOURCE CAPABILITY MATRIX -->",
        "<!-- END GENERATED SOURCE CAPABILITY MATRIX -->",
    ) == markdown_table(registry["sources"])


def test_release_status_and_grounding_bundle_do_not_drift():
    registry = load_capability_registry()
    release = registry["release"]
    release_notes = (ROOT / "docs" / "release-notes.md").read_text(
        encoding="utf-8"
    )
    assert release["public_release"] in release_notes
    assert release["package_version"] in release_notes
    assert release["development_label"] in release_notes
    checked_in = json.loads(
        (
            ROOT
            / "prompts"
            / "flavortown_sauce_wizard"
            / "grounding_bundle.json"
        ).read_text(encoding="utf-8")
    )
    assert checked_in == build_grounding_bundle(ROOT)
