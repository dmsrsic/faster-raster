# Security policy

## Supported version

Security fixes are currently considered for the latest `1.0.0` beta only. The project has not yet made a stable compatibility commitment.

## Reporting a vulnerability

Do not place credentials, private URLs, exploit details, or sensitive data in a public issue. Use GitHub's private vulnerability-reporting feature if it is enabled. If no private reporting channel is available, open a minimal [public issue](https://github.com/dmsrsic/faster-raster/issues) asking the maintainer to enable one; do not include sensitive details.

No email address is published for this local release candidate.

## Security boundaries

- Network access is disabled or explicit depending on the command and workfile policy.
- Credentials must be referenced outside workfiles and must never be embedded in receipts or committed configuration.
- Byte ceilings, exact-year checks, host/source policy, checksums, and transactional finalization are security and integrity controls.
- Generated execution packages do not authorize arbitrary shell execution.

Please report defects that bypass these controls, leak secrets or local paths, permit unbounded transfer, accept tampered evidence, or publish incomplete staging output.
