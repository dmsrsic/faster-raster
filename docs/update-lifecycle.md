# Update lifecycle

The published beta.5 package and the current development checkout are separate identities. FasterRaster currently ships read-only update inspection:

```bash { .offline-smoke }
fr update status --json
```

`status` makes no network request. It reports the active package version, checkout cleanliness, and conservative installation-origin classification. It never includes a local path or username in JSON or receipts.

Release metadata checks require an explicit authorization:

```bash { .manual-network }
fr update check --channel beta --allow-network --json
```

The check is bounded to GitHub release metadata and a validated release manifest. It does not contact raster providers, execute pip or Git, download code, or apply an update. Dirty checkouts are blocked and all recommendations are fixed, reviewable command templates.

Successful or blocked checks write a deterministic receipt beneath the local FasterRaster update-state directory. The persisted canonical payload is hashed without a self-referential `receipt_sha256` field; the digest is the filename and is included in CLI JSON. Receipts contain no timestamps, local paths, usernames, machine identifiers, project data, or telemetry.

`fr update apply` is intentionally unavailable. Review the exact wheel URL and SHA-256, then perform a manual install only after inspecting the release notes. A defective beta is superseded by a new immutable beta; tags and assets are never replaced.
