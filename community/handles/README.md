# Public FasterRaster handles

Records are maintainer-approved, pseudonymous, and opt-in. A v2 record using
`maintainer-reviewed-request` records manual approval only; it is not proof of
control. Ed25519 control records remain a later, separately reviewed workflow.
Do not add personal contact data, credentials, links, biographies, issue
metadata, or executable content.

From an approved public Issue Form request, a maintainer may run:

```bash
python scripts/manage_handle_registry.py activate \
  --handle pixel-ranger \
  --joined-at 2026-08-04 \
  --interest stac \
  --confirm-approved-request
```

The command stores no GitHub username or issue identifier. Run the offline
check before committing:

```bash
python scripts/manage_handle_registry.py check
```
