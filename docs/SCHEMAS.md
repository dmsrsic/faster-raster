# FasterRaster Public JSON Schemas

FasterRaster exports JSON Schema files for stable public contracts. These schemas are intended for external validation, documentation, fixture review, and integration tests outside the Python package.

Export schemas:

```bash
faster-raster export-schemas --out schemas/
```

The export is deterministic: the same code version writes byte-identical schema files.

## Agricultural and workfile schemas

- `agricultural_recipe_v3.schema.json` preserves the published beta.3
  NAIP–CDL classification contract.
- `agricultural_recipe_v4.schema.json` validates source-aware index requests,
  explicit general/specialist classes, bounded selection/calibration, and
  deterministic arbitration.
- `workfile_v1.schema.json` remains backward compatible and adds an optional
  V4-only classification override for the index-guided workflow.

The V4 recipe is also part of the discriminated agricultural recipe union.
Schema export ordering and JSON formatting are deterministic; generated
schemas are checked against the committed files in tests and release gates.

## `research_spec.schema.json`

Validates user-authored semantic research specs. It covers project metadata, AOI fields, target grid fields, source requests, and output settings.

## `source_registry.schema.json`

Validates maintainer-authored source registry entries. It documents required source capability fields including adapter, URL parameter names, service CRS, bbox request policy, year strategy, and service size limits.

## `acquisition_manifest_row.schema.json`

Validates one JSONL row from `acquisition_manifest.jsonl`. Each row is one planned offline request and must include explicit CRS fields, tile pixel dimensions, URL, request ID, and source metadata.

## `harmonization_plan.schema.json`

Validates `harmonization_plan.json`. The schema preserves request IDs, source bbox, CRS fields, tile dimensions, resampling policy, planned outputs, and validation checks.

## `inspect_contract_report.schema.json`

Validates JSON emitted by:

```bash
faster-raster inspect-contract research_spec.json --json
```

This is useful for CI checks before URL planning.

## Boundary

Schema export does not create manifests, call network endpoints, inspect remote services, or require geospatial libraries.
