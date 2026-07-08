from __future__ import annotations

import json
import re

from typer.testing import CliRunner

from faster_raster.cli import app
from faster_raster import cli_render as render

runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def ok(args):
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return result.output


def test_shortener_rewrites_repeated_phrases():
    assert render.shorten_text("complete credential/session scaffold before live probe") == "auth scaffold"
    assert render.shorten_text("write metadata-only adapter/probe design") == "metadata adapter"


def test_pantry_compact_and_wide_modes_are_available():
    compact = ok(["pantry", "--plain"])
    wide = ok(["pantry", "--wide", "--plain"])
    assert "next_unlock_short" in compact
    assert "display_name" not in compact.splitlines()[1]
    assert "display_name" in wide
    assert not ANSI_RE.search(compact)


def test_goods_default_excludes_duplicate_guard_and_include_guards_adds_it():
    default = ok(["goods", "--plain"])
    guarded = ok(["goods", "--include-guards", "--plain"])
    assert "Goods / verified sauces: 3" in default
    assert "daymet_single_pixel_rest_duplicate_guard" not in default
    assert "Goods / verified sauces: 4" in guarded
    assert "daymet_single_pixel_rest_duplicate_guard" in guarded


def test_source_scope_and_scope_alias():
    assert "no_auth_only: True" in ok(["source-scope", "--plain"])
    assert "max_sources_per_run: 5" in ok(["scope", "--plain"])
    assert "source-scope or scope" in ok(["knobs", "explain", "--plain"])


def test_endpoint_readiness_cli_json_and_plain():
    text = ok(["cook", "endpoints", "--plain"])
    assert "gridmet_daily" in text
    assert "verified_docs_only" in text
    alias = ok(["endpoints", "--plain"])
    assert "terraclimate_monthly" in alias
    payload = json.loads(ok(["cook", "endpoints", "--json"]))
    assert len(payload["endpoint_readiness"]) == 9
