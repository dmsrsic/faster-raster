from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "probe_atlas_source.py"
ATLAS = ROOT / "research" / "source_atlas_v0_4.yaml"


def load_module():
    spec = importlib.util.spec_from_file_location('probe_atlas_source', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeHeaders(dict):
    def items(self):
        return super().items()


class FakeResponse:
    status = 200
    headers = FakeHeaders({'Content-Type': 'text/plain', 'Content-Length': '3'})

    def __init__(self):
        self.body = b'ok\n'
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size: int) -> bytes:
        chunk = self.body[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk


def test_gridmet_is_blocked_by_endpoint_uncertainty():
    module = load_module()
    report = module.run_atlas_probe(module.load_atlas(ATLAS), source_id='gridmet_daily', allow_network=True, max_bytes=64)
    assert report['probe']['result_class'] == 'skipped_policy'
    assert 'endpoint_or_catalog_url is missing' in report['probe']['error']


def test_refuses_credentialed_source():
    module = load_module()
    report = module.run_atlas_probe(module.load_atlas(ATLAS), source_id='nlcd_annual_landcover', allow_network=True, max_bytes=64)
    assert report['probe']['result_class'] == 'skipped_policy'
    assert 'promotion_status is not probe-safe' in report['probe']['error'] or 'credential_requirement' in report['probe']['error']


def test_mocked_safe_source_probe_passes():
    module = load_module()
    atlas = {'sources': [{
        'source_id': 'safe_test',
        'display_name': 'Safe Test',
        'provider': 'Test',
        'access_mode': 'static_https',
        'access_pattern_category': 'static_verified',
        'promotion_status': 'experimental_probe_supported',
        'credential_requirement': 'none',
        'bounded_probe_appropriate': True,
        'endpoint_or_catalog_url': 'https://example.invalid/data.txt',
    }]}
    def opener(request, timeout):
        return FakeResponse()
    report = module.run_atlas_probe(atlas, source_id='safe_test', allow_network=True, max_bytes=64, opener=opener)
    assert report['probe']['result_class'] == 'pass_verified'
    assert report['probe']['bytes_read'] > 0


def test_report_writers(tmp_path):
    module = load_module()
    report = module.run_atlas_probe(module.load_atlas(ATLAS), source_id='gridmet_daily', allow_network=True, max_bytes=64)
    out = tmp_path / 'atlas.json'
    md = tmp_path / 'atlas.md'
    module.write_reports(report, out, md)
    assert json.loads(out.read_text())['source_id'] == 'gridmet_daily'
    assert 'Atlas Source Probe' in md.read_text()
