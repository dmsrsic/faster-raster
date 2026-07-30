# Public/private contract boundary

The public repository owns user intent and frozen, versioned contracts:
workfiles, Source Packs, temporal alternatives and resolutions, preview
templates, capability evidence, execution packages, schemas, and public-safe
receipts. It validates and hashes these contracts without resolving secrets.
A Source Pack materialization request binds a requested role and either a
WGS84 bounding box or explicit full-object intent to one exact frozen plan
hash. It cannot override endpoints, selected time, credential references, or
family safety limits.

A private execution backend may consume a frozen public contract and resolve
an opaque credential reference only when it declares a compatible resolver
capability. It may perform authenticated acquisition and parallel execution.
It must not reconstruct or override public source intent, temporal selection,
grid policy, byte ceilings, or scientific meaning.

The only credential fields allowed in a public contract are:

- authentication scheme;
- opaque `credential_ref`;
- allowed request hosts;
- allowed redirect hosts;
- resolver capability required.

Resolved tokens, passwords, cookies, signed URLs, authorization headers,
session values, secret-bearing subprocess arguments, and secret-derived hashes
are never public contract fields. Public execution without a resolver fails
before network access. Private receipts returned across the boundary may state
only nonsecret facts such as resolver type, opaque reference, host scope,
status, expiry class, and persistence status.

The current public tree does not contain the private backend. Its capability is
classified `private`, and public execution is `unavailable`.
