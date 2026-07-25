# FasterRaster

<p align="center">
  <img src="docs/assets/images/faster-raster-hero.webp" alt="Satellite above Earth projecting a bounded raster grid into a reproducible geospatial workflow" width="1600" height="900">
</p>

**Deterministic raster acquisition and harmonization.**

FasterRaster turns public raster sources into bounded, reproducible, and publication-ready geospatial workflows. It compiles explicit source contracts, validates inputs, harmonizes grids, and preserves the evidence required to reproduce every output.

**Public beta:** `v1.0.0-beta.1` (`1.0.0b1` as a Python package). The beta is suitable for technical evaluation and reproducible local studies; interfaces and output contracts may still change before a stable `v1.0.0` release.

FasterRaster is useful when a raster workflow needs more than a download script: exact source years, byte ceilings, categorical resampling rules, deterministic plans, transactional handoffs, checksums, provenance, and a clear record of what was reused or transferred.

## What the beta does

- Generates and validates Markdown study workfiles from shipped templates.
- Compiles deterministic source, acquisition, grid, and harmonization plans.
- Enforces exact-year coverage; it never silently substitutes an imagery year.
- Acquires USDA Cropland Data Layer (CDL) data for implemented studies.
- Runs multi-epoch mapped-development proxy analysis on a common valid footprint.
- Produces agricultural CDL/NAIP context workflows and bounded local execution.
- Finalizes results transactionally with manifests, receipts, checksums, and provenance.
- Replays compatible handoffs with strict zero-network `reuse: only` behavior.
- Publishes regional and deterministic 1 m hotspot human-development hybrids.

The human-development workflow uses CDL non-agricultural classes as a **crop-focused mapped-development proxy**. It is not authoritative evidence of urbanization, population or economic growth, construction dates, occupancy, cadastral approval, or causality.

## NAIP–CDL classification audit

The `naip_cdl_classification_audit` recipe acquires raw, unrendered four-band
NAIP imagery and classifies it from locally computed numeric spectral features.
Same-year USDA CDL superclasses provide weak supervision, while deterministic
spatial-block holdout, confidence and disagreement outputs, reproducible COGs,
and content-bound receipts make the result auditable. Reported metrics measure
agreement with those weak labels—not independent ground-truth accuracy.

![NAIP–CDL classification audit publication](docs/assets/naip-cdl-classification-audit.png)

*A finalized real-imagery publication showing the analytical classification,
source-derived views, confidence, and CDL agreement audit. Display styling does
not modify the analytical rasters.*

Create a deliberately small study, then validate and inspect its offline plan:

```sh
mkdir -p studies
fr templates show ag-naip-classification
fr init studies/naip-classification.fr.md \
  --template ag-naip-classification \
  --name bounded-naip-classification \
  --bbox -83.2000 39.8500 -83.1990 39.8510 \
  --years 2023
fr validate studies/naip-classification.fr.md
fr plan studies/naip-classification.fr.md --offline
fr explain studies/naip-classification.fr.md --offline
```

Review the workfile, same-year source evidence, transfer estimate, and byte
ceiling before any live `fr cook`. See the
[classification methodology and interpretation guide](docs/ag-classification.md).

## Five-minute offline start

Prerequisites are Python 3.12, a working GDAL/Rasterio runtime, and Git. Ubuntu is the public CI platform; WSL2 is exercised during local release validation. macOS and native Windows may work but are not beta CI targets yet.

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install .

fr --version
fr doctor --offline
fr templates list

mkdir -p studies
fr init studies/meridian.fr.md \
  --template human-development-cdl \
  --name meridian-cdl-development \
  --bbox -116.45 43.58 -116.35 43.68 \
  --years 2008 2016 2021

fr validate studies/meridian.fr.md
fr plan studies/meridian.fr.md --offline --out build/meridian-plan
```

Those commands make no network requests and write the deterministic plan beneath `build/meridian-plan/`. Review the generated workfile before enabling network access.

To install a built wheel instead:

```sh
python -m pip install dist/faster_raster-1.0.0b1-py3-none-any.whl
```

See [Installation](docs/installation.md) and the [five-minute quickstart](docs/quickstart.md) for clean-environment and live-study instructions.

## Normal CLI lifecycle

```text
doctor
  → templates
  → init
  → validate
  → plan
  → cook
  → inspect
  → publish
  → reuse-only verification
```

`validate` is offline. `plan --offline` prohibits even bounded source refresh. A live `cook` requires network permission in the workfile and remains subject to its byte ceiling. `reuse: auto` reuses verified compatible data and may acquire missing inputs when permitted; `reuse: only` makes zero network requests and fails closed if compatible evidence is missing; `reuse: never` deliberately reacquires data.

Final handoffs are written to `outputs/handoffs/<handoff-id>/`. Human-development publications are written to `outputs/publications/<publication-id>/`. Staging and failed directories are not finalized results. Use `fr inspect latest --verbose`, then verify `checksums.sha256` from inside the selected handoff or publication.

## Canonical visual example

![Buckeye–Verrado human-development hybrid](docs/assets/examples/buckeye-verrado-publication.png)

*Buckeye–Verrado, Arizona: CDL analytical years 2008, 2016, and 2021. The natural-color layer is 2023 NAIP visual context because no intersecting 2021 NAIP catalog records were available; it is not evidence of conditions in 2021. The classification is a CDL-derived mapped-development proxy with the limitations stated above.*

The [examples gallery](docs/examples.md) also includes Star, Idaho, where 2021 NAIP context matches the latest CDL analytical year.

## Sources and scope

Implemented beta paths include live USDA CDL acquisition; USGS NAIP context; bounded 3DEP use where a recipe requires it; bounded local workers; and offline compiler/evidence paths for the shipped public-source contracts. The static HTTP range wave includes CHIRPS, gridMET, TerraClimate, and WorldClim bounded probes. PRISM remains historical fixture evidence until a current deterministic endpoint is promoted through the source policy.

Exact-year behavior is strict. If a requested NAIP year has no intersecting records, FasterRaster reports the available intersecting years and stops. The user must explicitly choose another year. The Buckeye recovery from requested 2021 context to explicitly selected 2023 context demonstrates that contract; it is not silent substitution.

Read [Supported sources](docs/supported-sources.md), [network and byte budgets](docs/network-byte-budgets.md), and [known limitations](docs/limitations.md) before interpreting results.

## Determinism and evidence

Plans, grids, source mappings, receipts, and publication compatibility are content-bound. Raster transfers respect configured per-study ceilings. Finalization is transactional, source bytes are checksummed, and strict reuse verifies compatibility rather than trusting filenames. See [Determinism, provenance, and reuse](docs/determinism.md).

## Project status and boundaries

This beta is release-engineered around the implemented local community core. Additional source adapters, provider-neutral credential references, paid or restricted datasets, richer classification contracts, scheduler-neutral execution packages, and workstation-to-cluster execution are roadmap directions—not shipped capabilities or promises.

Public-source adapters and core orchestration are part of the community beta. Managed infrastructure, private integrations, paid-source adapters, enterprise authentication, specialized classifiers, and cluster services may be developed separately.

## Documentation and project policy

- [Documentation home](docs/index.md)
- [Terminal playground](docs/terminal-playground.md)
- [NAIP–CDL weak-supervised surface classification](docs/ag-classification.md)
- [Human-development methodology](docs/human-development.md)
- [Errors and recovery](docs/errors-recovery.md)
- [Release notes](release/v1.0.0-beta.1.md)
- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)
- [Citation](docs/citation.md) and [`CITATION.cff`](CITATION.cff)

FasterRaster is licensed under the [Mozilla Public License 2.0](LICENSE); see [NOTICE](NOTICE) for the repository copyright notice.

Use the [GitHub issue tracker](https://github.com/dmsrsic/faster-raster/issues) for reproducible bugs and feature discussions after the repository becomes public. Commercial or managed-service interest can also begin in a public issue or Discussion; the beta does not publish or invent a private contact address.
