# Determinism, provenance, and reuse

FasterRaster binds reproducibility to content and contracts:

- deterministic template rendering and planning;
- explicit source, year, bounds, CRS, grid, and resampling;
- source and derived-object SHA-256 checksums;
- acquisition, workflow, mapping, handoff, and publication receipts;
- stable ordering for requests, workers, and receipt records;
- transactional finalization;
- strict compatibility checks before reuse.

`reuse: only` prohibits network activity. If any required compatible asset is missing, incomplete, wrong-year, wrong-grid, corrupt, or unsupported, the operation fails closed.

Verify a result from inside its final directory:

```sh
sha256sum -c checksums.sha256
```

The repository's offline release gate also checks schema determinism and confirms that validation does not mutate tracked reports.
