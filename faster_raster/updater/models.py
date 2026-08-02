from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class UpdateChannel(StrEnum):
    STABLE = "stable"
    BETA = "beta"


@dataclass(frozen=True)
class InstallationState:
    active_version: str
    python_version: str
    git_context: str
    distribution_origin: str
    compatible: bool = True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReleaseAsset:
    kind: str
    name: str
    url: str
    sha256: str
    size_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReleaseManifest:
    schema_version: str
    tag: str
    package_version: str
    channel: str
    requires_python: str
    updater_manifest_major: int
    release_notes_url: str
    assets: tuple[ReleaseAsset, ...]

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["assets"] = [asset.as_dict() for asset in self.assets]
        return value


@dataclass(frozen=True)
class UpdateCheckResult:
    status: str
    channel: str
    installation: InstallationState
    candidate: ReleaseManifest | None = None
    recommendation: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    receipt_sha256: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "fasterraster.update-check-receipt/v1",
            "status": self.status,
            "channel": self.channel,
            "installation": self.installation.as_dict(),
            "candidate": self.candidate.as_dict() if self.candidate else None,
            "recommendation": self.recommendation,
            "error": self.error,
            "receipt_sha256": self.receipt_sha256,
        }
