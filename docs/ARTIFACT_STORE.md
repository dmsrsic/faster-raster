# Artifact Store

Complete source artifacts are stored separately from v0.8 bounded prefix evidence.

- Bounded evidence prefixes: `cache/runtime/static_http_range/`
- Complete source artifacts: `cache/artifacts/sha256/`
- Materialization staging: `cache/staging/materialization/`

Complete artifacts use `cache/artifacts/sha256/AA/BB/FULL_SHA256.EXTENSION`. The extension comes from trusted source/container metadata. Staging writes use `.part` files and are promoted atomically only after transfer length checks, whole-object SHA256, probe-prefix continuity, and basic container validation pass.
