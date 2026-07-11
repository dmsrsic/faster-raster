from __future__ import annotations

from faster_raster import copernicus_auth


def test_auth_redaction():
    assert copernicus_auth.redact_token("fake-token") == "fake...oken"
    headers = copernicus_auth.redact_headers({"Authorization": "Bearer fake-token"})
    assert headers["Authorization"] == "Bearer <REDACTED>"
    assert "fake-token" not in str(headers)


def test_env_auth_presence_detection():
    auth = copernicus_auth.load_cdse_auth_from_env({"CDSE_ACCESS_TOKEN": "fake-token"})
    report = copernicus_auth.validate_cdse_auth_presence(auth)
    assert report["auth_present"] is True
    assert report["auth_method"] == "access_token"
    assert "fake-token" not in str(report)


def test_headers_use_fake_token_but_reports_can_redact():
    auth = copernicus_auth.load_cdse_auth_from_env({"CDSE_ACCESS_TOKEN": "fake-token"})
    headers = copernicus_auth.build_cdse_headers(auth)
    assert headers["Authorization"] == "Bearer fake-token"
    assert "fake-token" not in str(copernicus_auth.redact_headers(headers))



class FakeHeaders(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class FakeResponse:
    def __init__(self, data: bytes = b'{"type":"Catalog"}', status: int = 200):
        self.data = data
        self.headers = FakeHeaders({"Content-Type": "application/json"})
        self.status = status
        self.code = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size: int = -1):
        return self.data if size < 0 else self.data[:size]


def test_live_auth_readiness_redacts_token(monkeypatch):
    monkeypatch.setenv("CDSE_ACCESS_TOKEN", "fake-token")
    seen = {}
    def fake_urlopen(request, timeout=0):
        seen["authorization"] = request.headers.get("Authorization")
        return FakeResponse()
    monkeypatch.setattr(copernicus_auth.urllib.request, "urlopen", fake_urlopen)
    report = copernicus_auth.live_auth_readiness_check()
    assert seen["authorization"] == "Bearer fake-token"
    assert report["network_run"] is True
    assert report["authorization_header_redacted"] == "Bearer <REDACTED>"
    assert "fake-token" not in str(report)
