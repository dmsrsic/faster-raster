from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from faster_raster.adapter_contract import stable_json
from faster_raster.capability_registry import load_capability_registry


SCHEMA_VERSION = "fasterraster.grounding-bundle/v1"
DEFAULT_ROOT = Path(__file__).resolve().parent.parent

GROUNDING_FILES: tuple[tuple[str, str], ...] = (
    ("schemas/source_pack.schema.json", "source_pack_schema"),
    ("schemas/credential_requirement.schema.json", "credential_schema"),
    ("schemas/temporal_alternatives.schema.json", "temporal_schema"),
    ("schemas/temporal_resolution.schema.json", "temporal_schema"),
    (
        "schemas/classification_temporal_alternatives.schema.json",
        "classification_temporal_schema",
    ),
    (
        "schemas/classification_temporal_resolution.schema.json",
        "classification_temporal_schema",
    ),
    (
        "schemas/categorical_area_accounting.schema.json",
        "scientific_accounting_schema",
    ),
    ("schemas/preview_template.schema.json", "preview_schema"),
    ("schemas/capability_registry.schema.json", "capability_schema"),
    ("configs/public_capabilities.yaml", "capability_registry"),
    ("configs/preview_templates.yaml", "preview_registry"),
    ("docs/generated/capabilities.md", "capability_reference"),
    ("docs/bring-your-own-sauce.md", "source_pack_guide"),
    ("docs/sauce-time.md", "temporal_guide"),
    ("docs/preview-templates.md", "preview_guide"),
    ("docs/ag-classification.md", "classification_methodology"),
    (
        "docs/index-guided-classification.md",
        "hybrid_classification_methodology",
    ),
    ("docs/limitations.md", "limitations"),
    (
        "examples/sauce-packs/prism-daily.sauce/sauce.yaml",
        "no_auth_example",
    ),
    (
        "examples/sauce-packs/prism-daily.sauce/golden_plan.json",
        "no_auth_golden_plan",
    ),
    (
        "examples/sauce-packs/prism-daily.sauce/probe_fixture.json",
        "temporal_fixture",
    ),
    (
        "examples/sauce-packs/copernicus-cdse.sauce/sauce.yaml",
        "credential_example",
    ),
    (
        "examples/sauce-packs/copernicus-cdse.sauce/golden_plan.json",
        "credential_golden_plan",
    ),
    (
        "examples/sauce-packs/copernicus-cdse.sauce/probe_fixture.json",
        "temporal_fixture",
    ),
    (
        "prompts/flavortown_sauce_wizard/CLI_REFERENCE.md",
        "cli_reference",
    ),
    (
        "prompts/flavortown_sauce_wizard/PUBLIC_PRIVATE_BOUNDARY.md",
        "architecture_boundary",
    ),
    (
        "prompts/flavortown_sauce_wizard/SYSTEM_GUIDE.md",
        "system_guide",
    ),
    (
        "prompts/flavortown_sauce_wizard/capabilities.json",
        "machine_capabilities",
    ),
)


def _canonical_grounding_bytes(path: Path) -> bytes:
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"grounding input is not valid UTF-8 text: {path}"
        ) from exc
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _grounding_file_record(
    path: Path,
    *,
    relative_path: str,
    role: str,
) -> dict[str, Any]:
    content = _canonical_grounding_bytes(path)
    return {
        "path": relative_path,
        "role": role,
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def build_grounding_bundle(root: Path = DEFAULT_ROOT) -> dict[str, Any]:
    registry = load_capability_registry(root / "configs" / "public_capabilities.yaml")
    files: list[dict[str, Any]] = []
    for relative_path, role in GROUNDING_FILES:
        path = root / relative_path
        if not path.is_file():
            raise ValueError(f"grounding input is missing: {relative_path}")
        files.append(
            _grounding_file_record(
                path,
                relative_path=relative_path,
                role=role,
            )
        )
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "bundle_version": "1.0.0",
        "release": registry["release"],
        "capability_registry_sha256": registry["capability_registry_sha256"],
        "validation_authority": [
            "fr validate",
            "fr sauce validate",
            "fr preview-templates validate",
            "checked-in public JSON Schemas",
        ],
        "contract_schema_versions": [
            "fasterraster.source-pack/v1",
            "fasterraster.credential-requirement/v1",
            "fasterraster.temporal-alternatives/v1",
            "fasterraster.temporal-resolution/v1",
            "fasterraster.classification-temporal-alternatives/v1",
            "fasterraster.classification-temporal-resolution/v1",
            "fasterraster.categorical-area-accounting/v1",
            "fasterraster.preview-template/v1",
            "fasterraster.capability-registry/v1",
        ],
        "files": files,
    }
    return {
        **unsigned,
        "grounding_bundle_sha256": hashlib.sha256(
            stable_json(unsigned).encode("utf-8")
        ).hexdigest(),
    }


def write_grounding_bundle(root: Path = DEFAULT_ROOT) -> Path:
    destination = (
        root
        / "prompts"
        / "flavortown_sauce_wizard"
        / "grounding_bundle.json"
    )
    payload = json.dumps(build_grounding_bundle(root), indent=2, sort_keys=True) + "\n"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(payload, encoding="utf-8", newline="\n")
    temporary.replace(destination)
    return destination
