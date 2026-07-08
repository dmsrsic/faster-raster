from __future__ import annotations

import json
import re

from typer.testing import CliRunner

from faster_raster.cli import app
from faster_raster import cli_explore

runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def invoke(*args):
    return runner.invoke(app, list(args))


def test_kitchen_aliases_return_success():
    commands = [
        ["pantry", "--plain"],
        ["sauces", "--plain"],
        ["sauce", "gridmet_daily", "--plain"],
        ["reigns", "--plain"],
        ["buckets", "--plain"],
        ["goods", "--plain"],
        ["bads", "--plain"],
        ["recipe", "--plain"],
        ["batcher", "--plain"],
        ["dips", "gridmet_daily", "--dry-run", "--plain"],
        ["menu", "lingo", "--plain"],
    ]
    for command in commands:
        result = runner.invoke(app, command)
        assert result.exit_code == 0, (command, result.output, result.exception)


def test_standard_commands_still_work():
    for command in [["sources", "list", "--plain"], ["stack", "summary", "--plain"], ["probe", "atlas", "gridmet_daily", "--dry-run", "--plain"]]:
        result = runner.invoke(app, command)
        assert result.exit_code == 0, result.output


def test_json_output_is_canonical_and_lingo_not_used_as_keys():
    result = invoke("sources", "list", "--json")
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert isinstance(payload, list)
    forbidden = {"pantry", "sauce", "dip", "recipe", "batcher"}
    assert not (forbidden & set(payload[0]))
    assert "source_id" in payload[0]


def test_plain_output_is_ansi_free():
    result = invoke("pantry", "--plain")
    assert result.exit_code == 0
    assert not ANSI_RE.search(result.output)


def test_lingo_kitchen_changes_titles_and_standard_preserves_professional_terms():
    kitchen = invoke("sources", "list", "--plain", "--lingo", "kitchen")
    standard = invoke("sources", "list", "--plain", "--lingo", "standard")
    assert kitchen.exit_code == 0
    assert standard.exit_code == 0
    assert "Pantry" in kitchen.output
    assert "Source Atlas" in standard.output


def test_menu_lingo_plain_includes_core_terms():
    result = invoke("menu", "lingo", "--plain")
    assert result.exit_code == 0
    for term in ["pantry", "sauce", "dip", "recipe", "batcher"]:
        assert term in result.output


def test_dips_gridmet_dry_run_reports_endpoint_uncertainty():
    result = invoke("dips", "gridmet_daily", "--dry-run", "--plain")
    assert result.exit_code == 0
    assert "blocked_by_endpoint_uncertainty" in result.output
    assert "endpoint_or_catalog_url is missing or unknown" in result.output


def test_explore_kitchen_slash_parser():
    for command in ["/help", "/menu", "/menu.lingo", "/pantry", "/sauce gridmet_daily", "/recipe", "/batcher", "/dip gridmet_daily --dry-run"]:
        result = cli_explore.handle_slash_command(command)
        assert not result.should_exit
        assert result.output
    assert cli_explore.handle_slash_command("/exit").should_exit
