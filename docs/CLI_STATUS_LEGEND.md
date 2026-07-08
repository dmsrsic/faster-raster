# CLI Status Legend

- `verified_now`: source has a current bounded proof.
- `reused_existing_result`: status is reused from an existing report.
- `credential_gated`: source needs auth/session/requester-pays handling.
- `adapter_needed`: source needs adapter/probe design.
- `mirror_candidate`: source may be a mirror and needs provenance validation.
- `future_unverified`: source is not ready for probing.
- `blocked`: source is blocked by policy or missing prerequisites.
- `failed_probe`: a probe ran and failed.
- `skipped_policy`: source was skipped by policy; this is not a crash.
