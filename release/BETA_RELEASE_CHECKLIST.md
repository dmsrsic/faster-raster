# Public beta release checklist

Published release: `v1.0.0-beta.5` / package `1.0.0b5`

Release date: `2026-08-02`

- [x] Release commit is on public `main`.
- [x] Package and runtime versions agree.
- [x] Capability-registry release metadata agrees.
- [x] Citation metadata agrees.
- [x] Complete offline test suite passes.
- [x] Strict documentation build passes.
- [x] Generated surfaces are deterministic.
- [x] Wheel and source distribution build successfully.
- [x] Fresh wheel smoke passes outside the checkout.
- [x] `SHA256SUMS` is generated from the final artifacts.
- [x] `fasterraster-release-v1.json` is generated from exact GitHub asset
      metadata and non-placeholder SHA-256 digests.
- [x] The annotated tag is immutable.
- [x] The GitHub release is marked prerelease.

The prior `v1.0.0-beta.4` tag and assets remain immutable.

The checked-in `v1.0.0-beta.5.manifest.example.json` remains an example only.
Its zero digests are never uploaded as release data.
