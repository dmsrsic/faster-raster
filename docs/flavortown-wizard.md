# Flavortown Sauce Wizard

The FasterRaster Flavortown Sauce Wizard is a public research, authoring, and
audit assistant for declarative Source Packs.

[Open the FasterRaster Flavortown Sauce Wizard](https://chatgpt.com/g/g-6a692bb17b9c8191a318997fd0435bf7-fasterraster-flavortown-sauce-wizard)

Source Packs, Sauce Time, preview templates, opaque credential references, and
the frozen Source Pack handoff are **Unreleased / experimental**. The Wizard
does not make them released capabilities.

## Evidence-to-validation workflow

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

The Wizard helps research, build, audit, and package public Source Pack files.
Official provider evidence is still required. A similar example can suggest a
shape, but it cannot prove an endpoint, collection, asset role, raster identity,
date, CRS, nodata value, license, or provider behavior.

The Wizard is not the validator. Checked-in JSON Schemas, the public capability
registry, and public CLI validators are authoritative. Run:

```sh
fr sauce validate path/to/source.sauce
fr sauce explain path/to/source.sauce --json
fr sauce test path/to/source.sauce
fr sauce compile path/to/source.sauce \
  --out build/source-pack-plan.json
fr sauce pack path/to/source.sauce --out dist/
```

`compile` writes the deterministic `fasterraster.source-pack-plan/v1` frozen
handoff only for executable `READY` or `CREDENTIAL_REQUIRED` plans. Provider
evidence that is incomplete, an unresolved temporal choice, an unsupported
family contract, a host mismatch, or unsafe content blocks executable
compilation before network access.

## Credential and execution boundary

The Wizard never needs a resolved credential. A public `credential_ref` is an
opaque identifier, not a token, password, cookie, authorization header, signed
URL, browser session, or environment value. No public CLI command resolves it.

Public Source Pack execution is limited to what the capability registry
declares—currently offline validation/planning, fixture tests, deterministic
packaging, and explicitly authorized bounded public probes. Authenticated
materialization and parallel execution are separate backend responsibilities
and are unavailable from the public repository. A compatible third-party
backend can implement the versioned handoff without using any private
FasterRaster code: it must verify v1 hashes and readiness, enforce request,
redirect, asset-host, timeout, request, item, and byte ceilings, resolve opaque
references only at request time, and emit redacted deterministic evidence.

## Time and preview truth

Sauce Time never silently changes the requested date. When an exact request is
unavailable, the plan remains `AWAITING_TEMPORAL_SELECTION` until a listed
candidate is selected explicitly; the resolution record preserves both the
original and selected time.

Preview templates define reusable layout, role, theme, and target-CRS
contracts. They do not add source, credential, acquisition, materialization, or
analysis capabilities.
