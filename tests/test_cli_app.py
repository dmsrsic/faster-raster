from __future__ import annotations

import json
import re
from pathlib import Path

from typer.testing import CliRunner

from faster_raster.cli import app

runner = CliRunner()
ANSI_RE = re.compile(r'\[[0-9;]*m')
ROOT = Path(__file__).resolve().parent.parent


def invoke(args):
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return result.output


def test_command_registration_and_version():
    assert 'FasterRaster' in invoke(['version', '--plain'])
    assert json.loads(invoke(['version', '--json']))['package_version']


def test_sources_list_plain_and_json():
    plain = invoke(['sources', 'list', '--plain'])
    assert 'gridmet_daily' in plain
    assert not ANSI_RE.search(plain)
    payload = json.loads(invoke(['sources', 'list', '--json']))
    assert any(row['source_id'] == 'gridmet_daily' for row in payload)


def test_sources_show_and_search():
    assert 'gridMET daily meteorology' in invoke(['sources', 'show', 'gridmet_daily', '--plain'])
    assert 'gridmet_daily' in invoke(['sources', 'search', 'gridmet', '--plain'])


def test_stack_summary_and_unlocks_next():
    summary = json.loads(invoke(['stack', 'summary', '--json']))
    assert summary['verified_live_source_count'] == 3
    assert 'gridmet_daily' in invoke(['unlocks', 'next', '--plain'])


def test_auth_profile_redaction():
    output = invoke(['auth', 'profiles', '--plain'])
    assert '<ENV_REF>' in output
    assert 'EARTHDATA_PASSWORD' not in output
    assert 'secret=' not in output.lower()


def test_probe_atlas_dry_run_gridmet():
    output = invoke(['probe', 'atlas', 'gridmet_daily', '--dry-run', '--plain'])
    assert 'endpoint_or_catalog_url is missing' in output
    payload = json.loads(invoke(['probe', 'atlas', 'gridmet_daily', '--dry-run', '--json']))
    assert payload['classification'] == 'blocked_by_endpoint_uncertainty'


def test_live_probe_refuses_without_allow_network():
    result = runner.invoke(app, ['probe', 'atlas', 'gridmet_daily'])
    assert result.exit_code != 0
    assert '--allow-network' in result.output or 'live atlas probe requires' in result.output


def test_help_style_and_tree_plain():
    text = invoke(['help', 'style', '--plain'])
    for label in ['verified_now', 'reused_existing_result', 'credential_gated', 'adapter_needed', 'mirror_candidate', 'future_unverified', 'blocked', 'failed_probe', 'skipped_policy']:
        assert label in text
    assert 'Verified now' in invoke(['sources', 'tree', '--plain'])


def test_old_cli_validate_still_works():
    output = invoke(["validate", str(ROOT / "tests" / "fixtures" / "ohio_cdl_edges" / "research_spec.json")])
    assert 'valid:' in output
