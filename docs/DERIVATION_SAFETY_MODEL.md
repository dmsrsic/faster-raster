# Derivation Safety Model

The gzip derivation writes only to `cache/staging/derivations` until the output is complete, fsynced, hashed, and structurally validated. Promotion into `cache/derived/sha256` uses an atomic rename. Existing identical content-addressed objects are reused; conflicting existing objects fail integrity checks.

Safety checks reject symlink sources, non-regular sources, destination symlinks, escaped roots, source checksum mismatches, invalid gzip streams, truncated gzip streams, malformed trailing members, byte-limit overflow, and expansion-ratio overflow.
