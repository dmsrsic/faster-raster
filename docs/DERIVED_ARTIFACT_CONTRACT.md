# Derived Artifact Contract

Derived artifacts live separately from source artifacts under `cache/derived/sha256/AA/BB/FULL_SHA256.tif`.

The derivation plan binds the operation, source SHA256, logical source path, source size, expected gzip container, expected GeoTIFF output, byte limits, expansion-ratio limits, storage root policies, implementation version, and explicit approval requirement. The plan hash is deterministic and excludes timestamps, user names, temporary directories, and absolute machine paths.

Execution requires `--allow-derivation` and `--approve-plan-sha256 FULL_SHA256`.
