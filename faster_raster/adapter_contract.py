from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


SUPPORTED_ADAPTERS = {
    "arcgis_imageserver",
    "generic_https_template",
    "static_http_range",
    "stac_metadata",
}


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def redact_headers(headers: dict[str, str] | None) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key, value in (headers or {}).items():
        if key.lower() == "authorization":
            continue
        redacted[key] = value
    return redacted


@dataclass(frozen=True)
class PlannedRequest:
    request_id: str
    task_id: str
    source_id: str
    adapter: str
    acquisition_mode: str
    source_classification: str
    execution_status: str
    deterministic_url: str | None
    request_method: str
    request_headers_redacted: dict[str, str]
    temporal_key: str
    spatial_key: str
    expected_content_family: str | list[str] | None
    expected_magic: str | list[str] | None
    expected_format: str | None
    max_bytes: int | None
    bounded_request: bool
    credential_required: bool
    auth_profile: str | None
    fixture_only: bool
    network_required: bool
    checksum_policy: str
    validation_steps: list[str]
    harmonization_readiness: str
    warnings: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        if self.adapter not in SUPPORTED_ADAPTERS:
            raise ValueError(f"unsupported adapter in planned request: {self.adapter}")
        headers = redact_headers(self.request_headers_redacted)
        url = self.deterministic_url
        row = {
            "request_id": self.request_id,
            "task_id": self.task_id,
            "source_id": self.source_id,
            "adapter": self.adapter,
            "acquisition_mode": self.acquisition_mode,
            "source_classification": self.source_classification,
            "execution_status": self.execution_status,
            "deterministic_url": url,
            "url_sha256": sha256_text(url or ""),
            "request_method": self.request_method,
            "request_headers_redacted": headers,
            "temporal_key": self.temporal_key,
            "spatial_key": self.spatial_key,
            "expected_content_family": self.expected_content_family,
            "expected_magic": self.expected_magic,
            "expected_format": self.expected_format,
            "max_bytes": self.max_bytes,
            "bounded_request": self.bounded_request,
            "credential_required": self.credential_required,
            "auth_profile": self.auth_profile,
            "fixture_only": self.fixture_only,
            "network_required": self.network_required,
            "checksum_policy": self.checksum_policy,
            "validation_steps": list(self.validation_steps),
            "harmonization_readiness": self.harmonization_readiness,
            "warnings": list(self.warnings),
            "provenance": dict(self.provenance),
        }
        if any(key.lower() == "authorization" for key in headers):
            raise ValueError("authorization header must not appear in planned request")
        return row


def row_contract_hash(row: dict[str, Any]) -> str:
    return sha256_text(stable_json(row))
