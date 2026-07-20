from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "multi_source_stack_probe.py"
SPEC_PATH = ROOT / "research" / "multi_source_stack_probe_spec.yaml"


def load_module():
    spec = importlib.util.spec_from_file_location('multi_source_stack_probe', SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeHeaders(dict):
    def items(self):
        return super().items()


class FakeResponse:
    status = 200
    headers = FakeHeaders({'Content-Type': 'application/octet-stream', 'Content-Length': '100', 'Accept-Ranges': 'bytes'})

    def __init__(self, body: bytes):
        self.body = body
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size: int) -> bytes:
        chunk = self.body[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk


def test_stack_spec_parses():
    spec = yaml.safe_load(SPEC_PATH.read_text())
    assert spec['target']['lat'] == 39.805
    assert len(spec['sources']) == 10


def test_classification_logic():
    module = load_module()
    source = {'source_id': 'x', 'probe_type': 'classify_without_probe', 'failure_gate_classification': 'adapter_needed'}
    result = module.classify_without_probe(source)
    assert result['result_class'] == 'adapter_needed'


def test_refuses_without_network_for_live_sources():
    module = load_module()
    spec = module.load_spec(SPEC_PATH)
    with pytest.raises(SystemExit, match='without --allow-network'):
        module.run_stack(spec, allow_network=False, timeout_seconds=1, root=SPEC_PATH.parent.parent)


def test_bounded_read_helper():
    module = load_module()
    response = FakeResponse(b'abcdefghijklmnopqrstuvwxyz')
    result = module.read_bounded_response(response, max_bytes=10, start=module.time.perf_counter())
    assert result['bytes_read'] == 10
    assert result['truncated'] is True
    assert result['sha256']


def test_report_shape(tmp_path):
    module = load_module()
    report = module.build_report(
        {'target': {'lat': 1, 'lon': 2}},
        [
            module.base_result({'source_id': 'a', 'probe_type': 'classify_without_probe'}, 'adapter_needed'),
            module.base_result({'source_id': 'b', 'probe_type': 'live_bounded_probe'}, 'pass_verified'),
        ],
    )
    out = tmp_path / 'stack.json'
    md = tmp_path / 'stack.md'
    module.write_reports(report, out, md)
    assert json.loads(out.read_text())['stack_status'] == 'COMPLETED'
    assert 'Multi-Source Stack Probe' in md.read_text()


def test_source_failure_does_not_crash_stack(tmp_path):
    module = load_module()
    spec = {
        'target': {'lat': 1, 'lon': 2},
        'sources': [
            {
                'source_id': 'bad_live',
                'probe_type': 'live_bounded_probe',
                'endpoint_or_url': 'https://example.invalid/test',
                'max_bytes': 64,
                'expected_success_status': [200],
            }
        ],
    }

    def opener(*args, **kwargs):
        raise RuntimeError('boom')

    report = module.run_stack(spec, allow_network=True, timeout_seconds=1, root=tmp_path, opener=opener)
    assert report['stack_status'] == 'COMPLETED'
    assert report['source_results'][0]['result_class'] == 'fail_endpoint'
    assert 'RuntimeError: boom' in report['source_results'][0]['error']
