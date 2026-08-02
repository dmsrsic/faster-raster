from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from faster_raster import __version__
from faster_raster.adapter_contract import stable_json


SCHEMA_VERSION = "fasterraster.capability-registry/v2"
DEFAULT_REGISTRY_PATH = (
    Path(__file__).resolve().parent.parent / "configs" / "public_capabilities.yaml"
)
STATUSES = {"released", "experimental", "private", "planned", "unsupported"}
RELEASE_STATES = {"published", "unreleased_public", "private", "planned", "unsupported"}
LEGACY_STATUS_MAP = {"released": "published", "experimental": "unreleased_public"}
BOOLEAN_FIELDS = {"planning", "preview", "materialization", "analysis"}
EVIDENCE_LEVELS = {
    "live_route_certified",
    "live_dataset_certified",
    "fixture_validated",
    "contract_validated",
}
TAG_RE = re.compile(r"^v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")


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
        raise ValueError("capability registry must define every compatibility status")
    release_definitions = raw.get("release_state_definitions")
    if not isinstance(release_definitions, dict) or set(release_definitions) != RELEASE_STATES:
        raise ValueError("capability registry must define every release state")
    evidence_definitions = raw.get("evidence_definitions")
    if not isinstance(evidence_definitions, dict) or set(evidence_definitions) != EVIDENCE_LEVELS:
        raise ValueError("capability registry must define every evidence level")
    evidence_records = raw.get("evidence_records", {})
    if not isinstance(evidence_records, dict):
        raise ValueError("capability registry evidence_records must be an object")
    root = registry_path.parent.parent
    for evidence_id, evidence in evidence_records.items():
        if not isinstance(evidence_id, str) or not isinstance(evidence, dict):
            raise ValueError("capability registry evidence records must be objects")
        required_evidence = {"scope", "date", "commit", "artifact", "evidence_levels"}
        if set(evidence) != required_evidence:
            raise ValueError(f"{evidence_id} has incomplete evidence metadata")
        artifact = str(evidence["artifact"])
        root_resolved = root.resolve()
        artifact_path = (root_resolved / artifact).resolve()
        try:
            artifact_path.relative_to(root_resolved)
        except ValueError as exc:
            raise ValueError(f"{evidence_id} references a missing or unsafe artifact") from exc
        if not artifact or not artifact_path.is_file():
            raise ValueError(f"{evidence_id} references a missing or unsafe artifact")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(evidence["date"])):
            raise ValueError(f"{evidence_id} has an invalid evidence date")
        commit = str(evidence["commit"])
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError(f"{evidence_id} has an invalid evidence commit")
        if not isinstance(evidence["evidence_levels"], list) or not evidence["evidence_levels"] or not set(evidence["evidence_levels"]).issubset(EVIDENCE_LEVELS):
            raise ValueError(f"{evidence_id} has invalid evidence levels")
        git_metadata = root_resolved / ".git"
        if git_metadata.exists():
            artifact_git_path = artifact_path.relative_to(root_resolved).as_posix()
            try:
                commit_check = subprocess.run(
                    ["git", "-C", str(root_resolved), "cat-file", "-e", f"{commit}:{artifact_git_path}"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise ValueError(f"{evidence_id} commit could not be inspected") from exc
            if commit_check.returncode != 0:
                raise ValueError(f"{evidence_id} does not bind to an artifact at its declared commit")
            try:
                ancestor_check = subprocess.run(
                    ["git", "-C", str(root_resolved), "merge-base", "--is-ancestor", commit, "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise ValueError(f"{evidence_id} public-history relationship could not be inspected") from exc
            if ancestor_check.returncode != 0:
                raise ValueError(f"{evidence_id} commit is not in the current public history")
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
            legacy_status = row.get("status")
            if legacy_status not in STATUSES:
                raise ValueError(f"{identifier} has an invalid status")
            required_v2 = {
                "release_state",
                "introduced_in",
                "evidence_levels",
                "evidence_refs",
                "public_execution",
                "scientific_scope",
            }
            if not required_v2.issubset(row):
                missing = sorted(required_v2.difference(row))
                raise ValueError(f"{identifier} is missing capability-registry/v2 fields: {', '.join(missing)}")
            release_state = row["release_state"]
            if release_state not in RELEASE_STATES:
                raise ValueError(f"{identifier} has an invalid release_state")
            introduced_in = row["introduced_in"]
            if introduced_in is not None and not TAG_RE.fullmatch(str(introduced_in)):
                raise ValueError(f"{identifier} has an invalid introduced_in tag")
            if not isinstance(row["evidence_levels"], list) or not row["evidence_levels"] or not all(level in EVIDENCE_LEVELS for level in row["evidence_levels"]):
                raise ValueError(f"{identifier} has invalid evidence_levels")
            if not isinstance(row["evidence_refs"], list) or not all(isinstance(ref, str) for ref in row["evidence_refs"]):
                raise ValueError(f"{identifier} evidence_refs must be strings")
            if any(ref not in evidence_records for ref in row["evidence_refs"]):
                raise ValueError(f"{identifier} references missing evidence")
            if any(level in {"live_route_certified", "live_dataset_certified"} for level in row["evidence_levels"]) and not row["evidence_refs"]:
                raise ValueError(f"{identifier} cannot claim live evidence without a checked-in record")
            referenced_levels = {
                level
                for ref in row["evidence_refs"]
                for level in evidence_records[ref]["evidence_levels"]
            }
            live_levels = set(row["evidence_levels"]) & {"live_route_certified", "live_dataset_certified"}
            if not live_levels.issubset(referenced_levels):
                raise ValueError(f"{identifier} claims live evidence not supported by its evidence records")
            for field in BOOLEAN_FIELDS:
                if not isinstance(row.get(field), bool):
                    raise ValueError(f"{identifier}.{field} must be boolean")
            if not row.get("credential_requirement") or not row["public_execution"]:
                raise ValueError(f"{identifier} is missing access or execution scope")
            if not isinstance(row["scientific_scope"], (str, type(None))):
                raise ValueError(f"{identifier}.scientific_scope must be a string or null")
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
        "| Capability | Release state | Evidence | Plan | Preview | Materialize | Analyze | Public execution |",
        "|---|---|---|:---:|:---:|:---:|:---:|---|",
    ]
    for row in rows:
        lines.append(
            "| {label} | `{release_state}` | `{evidence}` | {planning} | {preview} | "
            "{materialization} | {analysis} | `{execution}` |".format(
                label=str(row["label"]).replace("|", "\\|"),
                release_state=row["release_state"],
                evidence=", ".join(row["evidence_levels"]),
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
        f"(`{release['published_package_version']}`). Development tranche: "
        f"`{release['package_version']}` / `{release['contract_status']}`.\n\n"
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


def release_status_markdown(registry: Mapping[str, Any]) -> str:
    release = registry["release"]
    lines = [
        "# Release status",
        "",
        f"Published public release: `{release['public_release']}` (`{release['published_package_version']}`).",
        f"Current development identity: `{release['package_version']}`.",
        f"Next planned beta: `{release.get('next_release', 'not scheduled')}`.",
        "",
        "This page is generated from the capability registry. Release state, evidence, and execution boundary are independent claims.",
        "",
        "| Surface | Release state | Introduced in | Evidence | Public execution |",
        "|---|---|---|---|---|",
    ]
    for row in capability_rows(registry):
        lines.append(
            "| {label} | `{state}` | `{introduced}` | `{evidence}` | `{execution}` |".format(
                label=str(row["label"]).replace("|", "\\|"),
                state=row["release_state"],
                introduced=row.get("introduced_in") or "—",
                evidence=", ".join(row.get("evidence_levels", [])) or "none",
                execution=str(row["public_execution"]).replace("|", "\\|"),
            )
        )
    return "\n".join(lines) + "\n"


def public_json(registry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": registry["schema_version"],
        "registry_version": registry["registry_version"],
        "release": registry["release"],
        "status_definitions": registry["status_definitions"],
        "release_state_definitions": registry.get("release_state_definitions", {}),
        "evidence_definitions": registry.get("evidence_definitions", {}),
        "evidence_records": registry.get("evidence_records", {}),
        "capabilities": registry["capabilities"],
        "sources": registry["sources"],
        "capability_registry_sha256": registry["capability_registry_sha256"],
    }


def write_generated_surfaces(root: Path) -> list[Path]:
    registry = load_capability_registry(root / "configs" / "public_capabilities.yaml")
    def replace_block(path: Path, start: str, end: str, contents: str) -> str:
        current = path.read_text(encoding="utf-8")
        before, marker_and_after = current.split(start, 1)
        _, after = marker_and_after.split(end, 1)
        return before + start + "\n" + contents + "\n" + end + after

    readme = replace_block(
        root / "README.md",
        "<!-- BEGIN GENERATED CAPABILITY MATRIX -->",
        "<!-- END GENERATED CAPABILITY MATRIX -->",
        markdown_table(registry["capabilities"]),
    )
    supported = replace_block(
        root / "docs" / "supported-sources.md",
        "<!-- BEGIN GENERATED SOURCE CAPABILITY MATRIX -->",
        "<!-- END GENERATED SOURCE CAPABILITY MATRIX -->",
        markdown_table(registry["sources"]),
    )
    outputs = {
        root / "docs" / "generated" / "capabilities.md": registry_markdown(registry),
        root / "docs" / "generated" / "release-status.md": release_status_markdown(registry),
        root / "README.md": readme,
        root / "docs" / "supported-sources.md": supported,
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
