from __future__ import annotations

from pathlib import Path

import yaml

from faster_raster import cli_lingo


def test_lingo_yaml_parses():
    data = cli_lingo.load_lingo(Path("configs/cli_lingo.yaml"))
    assert data["terms"]["pantry"] == "source atlas"
    assert data["titles"]["sources_list"]["kitchen"].startswith("Pantry")


def test_every_lingo_term_has_canonical_meaning():
    rows = cli_lingo.glossary()
    assert rows
    assert all(row["term"] and row["meaning"] for row in rows)
    terms = {row["term"] for row in rows}
    assert {"pantry", "sauce", "dip", "recipe", "batcher", "crop-cookie", "goods", "bads", "locks"} <= terms


def test_lingo_modes_resolve(monkeypatch):
    assert cli_lingo.resolve_mode("standard") == "standard"
    assert cli_lingo.resolve_mode("kitchen") == "kitchen"
    monkeypatch.setenv("FASTERRASTER_LINGO", "kitchen")
    assert cli_lingo.resolve_mode(None) == "kitchen"
