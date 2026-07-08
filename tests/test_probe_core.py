from __future__ import annotations

import pytest

from faster_raster import probe_core


class FakeHeaders(dict):
    def items(self):
        return super().items()


class FakeResponse:
    def __init__(self, body: bytes, *, status: int = 200, content_type: str = 'text/plain'):
        self.body = body
        self.offset = 0
        self.status = status
        self.headers = FakeHeaders({'Content-Type': content_type, 'Content-Length': str(len(body)), 'Accept-Ranges': 'bytes'})

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size: int) -> bytes:
        chunk = self.body[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk


def test_requires_network_opt_in():
    with pytest.raises(SystemExit, match='--allow-network'):
        probe_core.probe_http(url='https://example.invalid', allow_network=False)


def test_bounded_probe_text_preview_and_sha():
    def opener(request, timeout):
        return FakeResponse(b'alpha\nbeta\n')

    result = probe_core.probe_http(url='https://example.invalid/data', allow_network=True, max_bytes=64, opener=opener)
    assert result['result_class'] == 'pass_verified'
    assert result['bytes_read'] == 11
    assert result['sha256']
    assert result['text_preview'] == ['alpha', 'beta']


def test_partial_content_classification():
    def opener(request, timeout):
        return FakeResponse(b'abcdef', status=206, content_type='application/octet-stream')

    result = probe_core.probe_http(url='https://example.invalid/bin', allow_network=True, max_bytes=64, opener=opener)
    assert result['result_class'] == 'pass_partial_content_verified'
    assert result['text_preview'] == []


def test_classification_rules():
    assert probe_core.classify_probe_result(status_code=401, bytes_read=0, truncated=False, error=None) == 'credential_gated'
    assert probe_core.classify_probe_result(status_code=403, bytes_read=0, truncated=False, error=None) == 'credential_gated'
    assert probe_core.classify_probe_result(status_code=400, bytes_read=10, truncated=False, error=None, metadata_probe=True) == 'malformed_request_expected'
    assert probe_core.classify_probe_result(status_code=404, bytes_read=0, truncated=False, error=None) == 'fail_endpoint'


def test_url_redaction():
    redacted = probe_core.redact_url('https://example.invalid?a=1&token=secretvalue123&b=2')
    assert 'secretvalue123' not in redacted
    assert 'token=<REDACTED>' in redacted


def test_stable_json_is_deterministic():
    assert probe_core.stable_json({'b': 1, 'a': 2}).splitlines()[1].strip().startswith('"a"')
