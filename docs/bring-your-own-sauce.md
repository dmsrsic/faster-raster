# Bring Your Own Sauce

Bring Your Own Sauce, also called the Flavortown Sauce Wizard, is the public
declarative source-extension seam. Its technical contract is
`fasterraster.source-pack/v1`. It is **Unreleased / experimental** in this
source tree and is not part of the published beta.4 contract.

[Open the FasterRaster Flavortown Sauce Wizard](https://chatgpt.com/g/g-6a692bb17b9c8191a318997fd0435bf7-fasterraster-flavortown-sauce-wizard)
to research, draft, or audit a pack from official provider documentation. The
Wizard never replaces the checked-in schema, capability registry, or public CLI
validators and never needs a resolved credential.

A Source Pack is a directory or deterministic ZIP archive. It declares a
source family, media and asset roles, temporal behavior, CRS, nodata or mask
semantics, safe resampling, host scope, request and byte ceilings, and optional
preview defaults. It cannot execute Python, import a module, invoke a shell
hook, or evaluate an unrestricted template.

## Evidence-to-validation path

```text
Official provider documentation
-> Flavortown Sauce Wizard
-> generated or audited declarative Source Pack
-> fr sauce validate
-> fr sauce explain --json
-> fr sauce test
-> optional explicitly authorized bounded probe
-> deterministic pack/archive
-> private execution only where a compatible private resolver/backend exists
```

An existing example is not provider evidence. Validation reports structural
schema validity, family-contract validity, provider-evidence completeness,
offline-planning readiness, credential requirements, and temporal-selection
state separately.

## Offline-first path

From a fresh clone:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install .

fr capabilities --json
fr sauce validate examples/sauce-packs/prism-daily.sauce
fr sauce explain examples/sauce-packs/prism-daily.sauce --json
fr sauce test examples/sauce-packs/prism-daily.sauce
```

Those commands make zero network requests. The shipped PRISM example reuses
the existing `generic_https_template` adapter family and official URL-template
shape. Its `golden_plan.json` proves deterministic offline compilation.

Create a new pack:

```sh
fr sauce init my-source
fr sauce validate my-source.sauce
fr sauce explain my-source.sauce --json
fr sauce test my-source.sauce
fr sauce compile my-source.sauce --out build/source-pack-plan.json
fr sauce materialize-request build/source-pack-plan.json \
  --out build/source-materialization-request.json \
  --role precipitation --full-object
fr sauce pack my-source.sauce --out dist/
```

`init` refuses to overwrite an existing directory unless `--force` is
explicit. The archive command sorts paths, fixes ZIP timestamps and file modes,
adds `CHECKSUMS.sha256`, excludes caches, and rejects traversal, symlinks, local
absolute paths, and secret-bearing content.

`compile` emits the deterministic `fasterraster.source-pack-plan/v1` frozen
handoff. It includes identity, endpoint/template or local-reference contract,
request/redirect/asset host boundaries, media and roles, time and explicit
resolution, CRS, semantic type, nodata/mask/resampling, archive selection,
resource ceilings, opaque credential requirements, public capability status,
stable hashes, and corrective blocked details. Provider-evidence or temporal
blocked states cannot become executable handoffs.

Reusable Source Pack facts remain separate from per-study intent.
`materialize-request` emits the deterministic
`fasterraster.source-materialization-request/v1` contract without network
access. Static HTTPS and verified-local sources require an explicit
`--full-object`; STAC and ImageServer sources require `--bbox` and
`--bbox-crs`; ImageServer additionally requires bounded `--width` and
`--height`. Requested roles must be a nonempty subset of the frozen plan.
The request is bound to the exact plan hash and cannot change time, endpoint,
hosts, credentials, or resource ceilings.

Source Pack v1 remains Unreleased. The additional archive, ImageServer,
STAC-bbox, and verified-local delivery fields are additive safety facts for
the single existing v1 meaning; no released contract is reinterpreted.

## Complete manifest example

```yaml
schema_version: fasterraster.source-pack/v1
pack_id: prism-daily-public
display_name: PRISM Daily Precipitation
description: Official bounded PRISM daily ZIP path.
adapter:
  family: static_https_template
  url_template: https://data.prism.oregonstate.edu/time_series/{region}/an/{resolution}/{variable}/{temporal_frequency}/{year}/prism_{variable}_{region}_25m_{yyyymmdd}.zip
  media_types: [application/zip]
  asset_roles: [precipitation]
capabilities:
  planning: true
  preview: true
  materialization: false
  analysis: false
  temporal_discovery: true
source:
  semantic_type: continuous
  crs: EPSG:4326
  resampling: bilinear
  nodata: -9999
  mask_policy: explicit_nodata
access:
  authentication_scheme: none
  credential_ref: null
  allowed_hosts: [data.prism.oregonstate.edu]
  redirect_hosts: []
  asset_hosts: []
network:
  max_requests: 1
  max_bytes: 65536
  max_asset_bytes: 64000000
  max_total_bytes: 64000000
  timeout_seconds: 8
  maximum_redirects: 0
  max_parallel_requests: 1
temporal:
  mode: exact
  requested: "2023-01-01"
  tolerance_days: 800
  template_variables:
    region: us
    resolution: 4km
    variable: ppt
    temporal_frequency: daily
    year: "2023"
    yyyymmdd: "20230101"
preview:
  template_id: general_multisource_v1
  role: environmental_context
  theme: climate_continuous
  target_crs: EPSG:4326
```

The executable example is
[`examples/sauce-packs/prism-daily.sauce`](https://github.com/dmsrsic/faster-raster/tree/main/examples/sauce-packs/prism-daily.sauce).
The schema is
[`schemas/source_pack.schema.json`](https://github.com/dmsrsic/faster-raster/blob/main/schemas/source_pack.schema.json).

## Bounded probe

Network access is a separate explicit step:

```sh
fr sauce probe examples/sauce-packs/prism-daily.sauce \
  --allow-network \
  --out build/prism-probe.json
```

The probe makes at most the declared request count, reads at most the declared
byte ceiling, validates redirects against a separate allowlist, records request
and byte evidence, and never promotes the response to a materialized asset.
Routine CI does not run it.

## Credential references

The Copernicus example demonstrates the public half of credential pass-through:

```yaml
access:
  authentication_scheme: bearer
  credential_ref: copernicus-production
  allowed_hosts: [stac.dataspace.copernicus.eu]
  redirect_hosts: []
  asset_hosts: [stac.dataspace.copernicus.eu]
```

`credential_ref` is an opaque identifier, never a token. Offline planning ends
in `CREDENTIAL_REQUIRED`. `fr sauce probe` stops before network access because
the public runtime has no resolver. A private backend may consume the frozen
credential requirement only if it declares a compatible resolver capability.

Authenticated execution is separate and unavailable from this public
repository. A third-party backend can consume the frozen versioned handoff
without private FasterRaster code, but it must verify hashes and readiness,
enforce every host/resource ceiling, resolve credentials only at request time,
and keep resolved values out of logs, receipts, cache keys, errors, and
serialized artifacts.

Source Packs reject credentials in URLs, query parameters, headers, fixtures,
environment snapshots, archives, and token-looking strings. Resolved secrets
must never enter public plans, logs, cache keys, receipts, or archive checksums.

Sauce Time never silently changes the requested date. Preview templates define
reusable layout and role contracts; they do not add source capabilities.
