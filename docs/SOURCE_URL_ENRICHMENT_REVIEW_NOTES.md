# Source URL Enrichment Review Notes

This folder now contains two source URL structure manifests with different trust levels.

## Conservative seed

`research/source_url_structures_manifest.yaml`

This is the original conservative seed generated from FasterRaster's current registry, docs, and golden fixtures. It intentionally keeps many values as `unknown` until verified against official documentation.

## Reviewed external-AI enrichment

`research/source_url_structures_manifest.reviewed_ai_enrichment.yaml`

This file captures the user-provided Gemini-style enrichment after review. The pasted enrichment included useful leads, but it also contained unusable `[cite:*]` markers and several values presented as confirmed without repo-verifiable evidence. Those claims were preserved as candidates and marked with statuses such as:

- `needs_official_verification`
- `external_candidate_unverified`
- `inferred_unverified`
- `template_guidance_only`

Do not copy entries from this reviewed file into `configs/source_registry.yaml` until the source family has been checked against official documentation and, where appropriate, a bounded offline-safe fixture has been added.

## Runtime behavior

No runtime behavior, adapters, source registry entries, downloads, or live network probes were changed as part of this review step.
