# Artifact Receipts

Artifact receipts record whole-object SHA256, object size, content-addressed path, probe-prefix continuity, expected and detected magic/content family, container validation status, transfer headers, and credential absence.

Receipt contract hashes exclude generated timestamps and normalize paths before hashing. Independent verification recomputes receipt hashes, object hashes, sizes, prefix continuity, and container validation status.

No response bytes, credentials, Authorization values, cookie values, or signed query secrets are written to receipts.

## Failed-run verification

A failed materialization run may preserve receipt contract integrity while still failing as release evidence. Verification separates contract integrity, execution outcome, artifact verification, catalog verification, and release evidence status so failed runs cannot appear as successful materialization evidence.
