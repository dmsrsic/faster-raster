from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from faster_raster import __version__
from faster_raster.adapter_contract import stable_json


SCHEMA_VERSION = "fasterraster.capability-registry/v1"
DEFAULT_REGISTRY_PATH = (
    Path(__file__).resolve().parent.parent / "configs" / "public_capabilities.yaml"
)
STATUSES = {"released", "experimental", "private", "planned", "unsupported"}
BOOLEAN_FIELDS = {"planning", "preview", "materialization", "analysis"}


def load_capability_registry(path: Path | None = None) -> dict[str, Any]:
    registry_path = path or DEFAULT_REGISTRY_PATH
    try:
        raw = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"unable to read capability registry {registry_path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"capability registry must use {SCHEMA_VERSION}")
    release = raw.get("release")
    if not isinstance(release, dict):
        raise ValueError("capability registry requires release metadata")
    if str(release.get("package_version")) != __version__:
        raise ValueError(
            "capability registry package version does not match faster_raster.__version__"
        )
    definitions = raw.get("status_definitions")
    if not isinstance(definitions, dict) or set(definitions) != STATUSES:
        raise ValueError("capability registry must define every public status")
    identifiers: set[str] = set()
    for section in ("capabilities", "sources"):
        rows = raw.get(section)
        if not isinstance(rows, list):
            raise ValueError(f"capability registry {section} must be an array")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"capability registry {section} rows must be objects")
            identifier = str(row.get("capability_id") or "")
            if not identifier or identifier in identifiers:
                raise ValueError(f"missing or duplicate capability_id {identifier!r}")
            identifiers.add(identifier)
            if row.get("status") not in STATUSES:
                raise ValueError(f"{identifier} has an invalid status")
            for field in BOOLEAN_FIELDS:
                if not isinstance(row.get(field), bool):
                    raise ValueError(f"{identifier}.{field} must be boolean")
            if not row.get("credential_requirement") or not row.get("public_execution"):
                raise ValueError(f"{identifier} is missing access or execution scope")
    result = dict(raw)
    result["capability_registry_sha256"] = hashlib.sha256(
        stable_json(raw).encode("utf-8")
    ).hexdigest()
    return result


def capability_rows(
    registry: Mapping[str, Any],
    *,
    sections: Iterable[str] = ("capabilities", "sources"),
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for section in sections
        for row in registry.get(section, [])
    ]


def markdown_table(rows: Iterable[Mapping[str, Any]]) -> str:
    lines = [
        "| Capability | Status | Plan | Preview | Materialize | Analyze | Public execution |",
        "|---|---:|:---:|:---:|:---:|:---:|---|",
    ]
    for row in rows:
        lines.append(
            "| {label} | `{status}` | {planning} | {preview} | {materialization} | "
            "{analysis} | `{execution}` |".format(
                label=str(row["label"]).replace("|", "\\|"),
                status=row["status"],
                planning="yes" if row["planning"] else "no",
                preview="yes" if row["preview"] else "no",
                materialization="yes" if row["materialization"] else "no",
                analysis="yes" if row["analysis"] else "no",
                execution=str(row["public_execution"]).replace("|", "\\|"),
            )
        )
    return "\n".join(lines)


def registry_markdown(registry: Mapping[str, Any]) -> str:
    release = registry["release"]
    return (
        "# Public capability matrix\n\n"
        f"Published release: `{release['public_release']}` "
        f"(`{release['package_version']}`). Development tranche: "
        f"`{release['development_label']}` / `{release['contract_status']}`.\n\n"
        "A catalog entry never implies that every product, geography, date, or "
        "output is executable.\n\n"
        "## Product capabilities\n\n"
        + markdown_table(registry["capabilities"])
        + "\n\n## Source capabilities\n\n"
        + markdown_table(registry["sources"])
        + "\n\nRegistry SHA-256: `"
        + str(registry["capability_registry_sha256"])
        + "`\n"
    )


def public_json(registry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": registry["schema_version"],
        "registry_version": registry["registry_version"],
        "release": registry["release"],
        "status_definitions": registry["status_definitions"],
        "capabilities": registry["capabilities"],
        "sources": registry["sources"],
        "capability_registry_sha256": registry["capability_registry_sha256"],
    }


def write_generated_surfaces(root: Path) -> list[Path]:
    registry = load_capability_registry(root / "configs" / "public_capabilities.yaml")
    outputs = {
        root / "docs" / "generated" / "capabilities.md": registry_markdown(registry),
        root
        / "prompts"
        / "flavortown_sauce_wizard"
        / "capabilities.json": json.dumps(
            public_json(registry),
            indent=2,
            sort_keys=True,
        )
        + "\n",
    }
    for path, contents in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8", newline="\n")
    return sorted(outputs)
