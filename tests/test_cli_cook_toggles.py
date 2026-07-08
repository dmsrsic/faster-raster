from __future__ import annotations

import json

from typer.testing import CliRunner

from faster_raster.cli import app

runner = CliRunner()


def ok(args):
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return result.output


def test_toggles_and_knobs_commands():
    assert "network_mode: off" in ok(["toggles", "show", "--plain"])
    assert "Knobs explained" in ok(["knobs", "explain", "--plain"])
    payload = json.loads(ok(["toggles", "show", "--json"]))
    assert payload["effective_toggles"]["network_mode"] == "off"


def test_cook_queue_and_aliases():
    assert "Cook plan" in ok(["cook", "plan", "--plain"])
    assert "gridmet_daily" in ok(["cook", "queue", "--plain"])
    assert "gridmet_daily" in ok(["queue", "--plain"])
    payload = json.loads(ok(["cook", "queue", "--json"]))
    assert payload["cook_queue"]


def test_cook_dip_gridmet_dry_run_and_json():
    text = ok(["cook", "dip", "gridmet_daily", "--dry-run", "--plain"])
    assert "blocked_by_endpoint_uncertainty" in text
    assert "endpoint_or_catalog_url is missing or unknown" in text
    payload = json.loads(ok(["cookdip", "gridmet_daily", "--dry-run", "--json"]))
    assert payload["classification"] == "blocked_by_endpoint_uncertainty"


def test_cook_live_refuses_when_network_mode_off():
    result = runner.invoke(app, ["cook", "dip", "prism_daily_ppt_static_zip", "--allow-network"])
    assert result.exit_code != 0
    assert "network_mode is off" in result.output


def test_cook_proposal_gridmet():
    text = ok(["cook", "propose", "gridmet_daily", "--plain"])
    assert "promotion_decision: not_ready" in text
    payload = json.loads(ok(["cookproposal", "gridmet_daily", "--json"]))
    assert payload["proposal_only"] is True
    assert payload["promotion_decision"] == "not_ready"
