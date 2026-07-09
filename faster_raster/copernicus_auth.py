from __future__ import annotations

import os
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
    headers = {"User-Agent": "FasterRaster-CDSE-scaffold/0.5.8"}
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
