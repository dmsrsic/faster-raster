# Artifact Catalog

The artifact catalog is the local index of verified complete source artifacts.

- `reports/artifacts/artifact_catalog.json`
- `reports/artifacts/artifact_catalog.jsonl`
- `reports/artifacts/artifact_catalog_verification.json`

The JSON snapshot is sorted and content-hashed. The JSONL journal is append-only. Entries are keyed by whole-object SHA256, so identical content deduplicates while accumulating source, request, task, temporal, and receipt provenance.

Catalog verification fails closed when artifacts are missing, tampered, symlinked, or inconsistent with recorded size and checksum.

## Empty catalog state

Before the first committed complete artifact, catalog verification reports `catalog_status: not_initialized` and `verification_status: NOT_APPLICABLE`. A missing catalog is blocking only when a successful materialization run claims a catalog update.
