# Bring Your Own Sauce

Bring Your Own Sauce, also called the Flavortown Sauce Wizard, is the public
declarative source-extension seam. Its technical contract is
`fasterraster.source-pack/v1`. It is **Unreleased / experimental** in this
source tree and is not part of the published beta.4 contract.

A Source Pack is a directory or deterministic ZIP archive. It declares a
source family, media and asset roles, temporal behavior, CRS, nodata or mask
semantics, safe resampling, host scope, request and byte ceilings, and optional
preview defaults. It cannot execute Python, import a module, invoke a shell
hook, or evaluate an unrestricted template.

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
fr sauce pack my-source.sauce --out dist/
```

`init` refuses to overwrite an existing directory unless `--force` is
explicit. The archive command sorts paths, fixes ZIP timestamps and file modes,
adds `CHECKSUMS.sha256`, excludes caches, and rejects traversal, symlinks, local
absolute paths, and secret-bearing content.

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
network:
  max_requests: 1
  max_bytes: 65536
  timeout_seconds: 8
  maximum_redirects: 0
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
```

`credential_ref` is an opaque identifier, never a token. Offline planning ends
in `CREDENTIAL_REQUIRED`. `fr sauce probe` stops before network access because
the public runtime has no resolver. A private backend may consume the frozen
credential requirement only if it declares a compatible resolver capability.

Source Packs reject credentials in URLs, query parameters, headers, fixtures,
environment snapshots, archives, and token-looking strings. Resolved secrets
must never enter public plans, logs, cache keys, receipts, or archive checksums.
