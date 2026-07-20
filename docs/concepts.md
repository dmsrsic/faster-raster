# Concepts and architecture

FasterRaster separates user intent from source-specific execution.

```text
study workfile
  → schema validation
  → source and exact-year resolution
  → bounded acquisition plan
  → target-grid and harmonization plan
  → workflow execution
  → transactional handoff
  → optional publication
  → checksum-bound reuse
```

## Study contract

The Markdown workfile's YAML front matter controls execution. The body is human documentation and cannot inject configuration or commands.

## Source-aware planning

Adapters translate semantic requests into source-specific bounded requests. Planning records rejected candidates and does not treat an unavailable preferred source as permission to use an unapproved year or source.

## Transactional results

Work is written to staging. Only a verified completion becomes a final handoff. Failed work remains visibly failed and cannot be mistaken for a completed publication.

## Evidence, not filenames

Reuse decisions rely on grids, bounds, years, semantics, checksums, and receipts. A matching filename is not evidence of compatibility.
