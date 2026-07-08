from __future__ import annotations

from faster_raster.cli_explore import handle_slash_command


def test_explore_help_and_exit():
    assert '/sources' in handle_slash_command('/help').output
    result = handle_slash_command('/exit')
    assert result.should_exit is True


def test_explore_sources_source_stack_unlocks():
    assert 'source_id' in handle_slash_command('/sources').output
    assert 'gridmet_daily' in handle_slash_command('/source gridmet_daily').output
    assert 'verified_live_source_count' in handle_slash_command('/stack').output
    assert 'gridmet_daily' in handle_slash_command('/unlocks').output


def test_explore_probe_dry_run():
    output = handle_slash_command('/probe gridmet_daily --dry-run').output
    assert 'endpoint_or_catalog_url is missing' in output
