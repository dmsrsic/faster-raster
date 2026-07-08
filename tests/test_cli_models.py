from __future__ import annotations

from pathlib import Path

from faster_raster import cli_models as models


def test_load_sources_and_summary():
    sources = models.load_sources()
    assert len(sources) >= 26
    stack = models.stack_summary(models.load_stack(), models.load_atlas())
    assert stack['verified_live_source_count'] == 3


def test_filter_and_search_sources():
    sources = models.load_sources()
    assert models.filter_sources(sources, gated_only=True)
    assert any(s['source_id'] == 'gridmet_daily' for s in models.search_sources(sources, 'gridmet'))


def test_auth_rows_are_redacted():
    rows = models.auth_rows(models.load_auth())
    assert '<ENV_REF>' in str(rows)
    assert 'EARTHDATA_PASSWORD' not in str(rows)


def test_gridmet_dry_run_classification():
    report = {'probe': {'error': 'endpoint_or_catalog_url is missing or unknown'}}
    assert models.gridmet_dry_run_classification(report) == 'blocked_by_endpoint_uncertainty'
