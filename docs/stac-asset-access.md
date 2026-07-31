# STAC asset access

Source Pack v2 separates discovery from the way a selected asset becomes
readable. `stac_search` remains the provider-neutral discovery family. Its
`asset_access.mode` is one of:

| Mode | Stable identity | Public behavior |
|---|---|---|
| `direct_https` | Unsigned HTTPS asset | Compile and validate |
| `s3_public` | Bucket, key, and region | Compile and validate |
| `s3_requester_pays` | Bucket, key, and region | Compile consent-bound request |
| `brokered_signed_https` | Unsigned HTTPS asset | Compile resolver and destination scopes |
| `bearer_https` | Unsigned HTTPS asset | Compile asset-only credential requirement |
| `s3_compatible_credentialed` | Bucket, key, region, and endpoint | Schema-ready; execution explicitly deferred |

Provider names never select a mode. Earth Search, Planetary Computer, USGS, and
NASA are behavioral examples, not production executor types.

## Identity and authorization

Stable plans, hashes, cache identities, evidence, and receipts contain only the
unsigned object identity. A SAS URL, bearer token, AWS signature, cookie, or
signer response is temporary authority and must exist only in private runtime
memory.

Credential schemes belong in the reusable Source Pack. Opaque
`credential_ref` values belong in the study materialization request. Resolved
secrets never enter either public contract.

## Requester Pays

Chargeable S3 access is permitted only when all three independent gates pass:

1. the Source Pack declares `s3_requester_pays`;
2. the materialization request sets `allow_chargeable_access: true`;
3. the private runtime receives ephemeral permission for chargeable network access.

All gates are checked before credential resolution. The public repository
neither resolves AWS credentials nor performs chargeable requests.

## Canonical examples

Behavioral v2 fixtures live under `examples/sauce-packs-v2/`. Each includes a
manifest, provider-evidence fixture, frozen plan, and frozen materialization
request. They intentionally contain no live Item IDs, temporary URLs, tokens,
downloaded rasters, or provider-specific dispatch code.
