from __future__ import annotations

import hashlib
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

TOKEN_ENV_KEYS = ["CDSE_ACCESS_TOKEN", "CDSE_REFRESH_TOKEN"]
LOGIN_ENV_KEYS = ["CDSE_USERNAME", "CDSE_PASSWORD", "CDSE_CLIENT_ID"]


@dataclass(frozen=True)
class CdseAuth:
    access_token: str | None = None
    refresh_token: str | None = None
    username: str | None = None
    password: str | None = None
    client_id: str | None = None

    @property
    def auth_present(self) -> bool:
        return bool(self.access_token or (self.username and self.password))

    @property
    def auth_method(self) -> str:
        if self.access_token:
            return "access_token"
        if self.username and self.password:
            return "username_password"
        return "none"


def load_cdse_auth_from_env(env: dict[str, str] | None = None) -> CdseAuth:
    values = env if env is not None else os.environ
    return CdseAuth(
        access_token=values.get("CDSE_ACCESS_TOKEN"),
        refresh_token=values.get("CDSE_REFRESH_TOKEN"),
        username=values.get("CDSE_USERNAME"),
        password=values.get("CDSE_PASSWORD"),
        client_id=values.get("CDSE_CLIENT_ID"),
    )


def redact_token(value: str | None) -> str | None:
    if not value:
        return value
    if len(value) <= 8:
        return "<REDACTED>"
    return f"{value[:4]}...{value[-4:]}"


def build_cdse_headers(auth: CdseAuth | None = None) -> dict[str, str]:
    auth = auth or load_cdse_auth_from_env()
    headers = {"User-Agent": "FasterRaster-CDSE-scaffold/0.5.9"}
    if auth.access_token:
        headers["Authorization"] = f"Bearer {auth.access_token}"
    return headers


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    redacted = dict(headers)
    if "Authorization" in redacted:
        redacted["Authorization"] = "Bearer <REDACTED>"
    return redacted


def validate_cdse_auth_presence(auth: CdseAuth | None = None) -> dict[str, Any]:
    auth = auth or load_cdse_auth_from_env()
    return {
        "auth_present": auth.auth_present,
        "auth_method": auth.auth_method,
        "token_present": bool(auth.access_token),
        "refresh_token_present": bool(auth.refresh_token),
        "username_present": bool(auth.username),
        "password_present": bool(auth.password),
        "client_id_present": bool(auth.client_id),
    }


def request_cdse_token(*_args: Any, **_kwargs: Any) -> str:
    raise RuntimeError("CDSE token request is scaffolded only; live token requests must be explicitly implemented and mocked in tests")


def live_auth_readiness_check(*, endpoint: str = "https://stac.dataspace.copernicus.eu/v1/", timeout_seconds: int = 25, max_bytes: int = 65536) -> dict[str, Any]:
    auth = load_cdse_auth_from_env()
    headers = build_cdse_headers(auth)
    request = urllib.request.Request(endpoint, headers=headers, method="GET")
    status = None
    bytes_read = 0
    response_sha256 = None
    warnings: list[str] = []
    errors: list[str] = []
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            data = response.read(max_bytes + 1)
            status = getattr(response, "status", None) or getattr(response, "code", None)
    except urllib.error.HTTPError as exc:
        data = exc.read(max_bytes + 1)
        status = exc.code
        if status in {401, 403}:
            warnings.append("CDSE endpoint rejected the request; credentials may be required or insufficient.")
    except Exception as exc:
        data = b""
        errors.append(str(exc))
    if len(data) > max_bytes:
        errors.append(f"byte cap exceeded: read more than {max_bytes} bytes")
        data = data[:max_bytes]
    bytes_read = len(data)
    response_sha256 = hashlib.sha256(data).hexdigest() if data else None
    return {
        **validate_cdse_auth_presence(auth),
        "network_run": True,
        "endpoint": endpoint,
        "http_status": status,
        "bytes_read": bytes_read,
        "response_sha256": response_sha256,
        "authorization_header_redacted": "Bearer <REDACTED>" if auth.access_token else None,
        "warnings": warnings,
        "errors": errors,
    }
