from __future__ import annotations

from faster_raster import copernicus_auth


def test_auth_redaction():
    assert copernicus_auth.redact_token("fake-token-value") == "fake...alue"
    headers = copernicus_auth.redact_headers({"Authorization": "Bearer fake-token-value"})
    assert headers["Authorization"] == "Bearer <REDACTED>"
    assert "fake-token-value" not in str(headers)


def test_env_auth_presence_detection():
    auth = copernicus_auth.load_cdse_auth_from_env({"CDSE_ACCESS_TOKEN": "fake-token-value"})
    report = copernicus_auth.validate_cdse_auth_presence(auth)
    assert report["auth_present"] is True
    assert report["auth_method"] == "access_token"
    assert "fake-token-value" not in str(report)


def test_headers_use_fake_token_but_reports_can_redact():
    auth = copernicus_auth.load_cdse_auth_from_env({"CDSE_ACCESS_TOKEN": "fake-token-value"})
    headers = copernicus_auth.build_cdse_headers(auth)
    assert headers["Authorization"] == "Bearer fake-token-value"
    assert "fake-token-value" not in str(copernicus_auth.redact_headers(headers))
