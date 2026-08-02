# Five-minute quickstart

This quickstart starts from the published beta.5 wheel and creates the small
Meridian human-development plan without contacting a raster service. Source
Pack and preview-template commands below are published experimental interfaces;
their bounded execution and evidence limits remain authoritative. The
index-guided workflow was introduced in beta.4.

```sh { .manual-network }
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install https://github.com/dmsrsic/faster-raster/releases/download/v1.0.0-beta.5/faster_raster-1.0.0b5-py3-none-any.whl

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
fr explain studies/meridian.fr.md --offline --verbose --out build/meridian-explanation
```

Review `studies/meridian.fr.md` and the plan artifacts before any live run. The workfile front matter is executable; its prose is explanatory and is never executed.

The offline plan is intentionally provisional when no verified cache exists:
exact-year coverage is `NOT_CHECKED`, metadata requests remain zero, and the
plan records `requires_coverage_validation: true`. It cannot be executed as
evidence. Re-plan without `--offline` only when you are ready to authorize the
bounded metadata coverage check.

## Published experimental capabilities

The following commands are available in the beta.5 package. They exercise
published experimental Source Pack and preview-template contracts offline,
including schema, host, CRS, nodata, resampling, temporal, preview,
secret-exclusion, canonicalization, and deterministic-plan checks. Their
presence in the release does not imply universal materialization or analysis.
Continue with [Bring Your Own Sauce](bring-your-own-sauce.md) or the
[first-cook troubleshooting matrix](first-cook-troubleshooting.md).

For a new provider, start from official documentation and
[open the FasterRaster Flavortown Sauce Wizard](https://chatgpt.com/g/g-6a692bb17b9c8191a318997fd0435bf7-fasterraster-flavortown-sauce-wizard).
Return to `fr sauce validate`, `fr sauce explain --json`, and `fr sauce test`
for authoritative checks; the Wizard is an authoring and audit aid.

For a live cook, explicitly set `data.allow_network: true` in the workfile, confirm the study byte ceiling, then run:

```sh { .offline-smoke }
fr capabilities --json
fr sauce validate examples/sauce-packs/prism-daily.sauce
fr sauce test examples/sauce-packs/prism-daily.sauce
fr preview-templates list
```

```sh { .manual-network }
fr cook studies/meridian.fr.md --reuse auto --no-open
fr inspect latest --verbose
```

Final handoffs are under `outputs/handoffs/`. See [Network and byte budgets](network-byte-budgets.md) before enabling network access.

## Explore index-guided classification offline

Index discovery and workfile validation do not contact a provider:

```sh { .offline-smoke }
fr indices list
fr indices show ndvi
fr init studies/index-hybrid.fr.md \
  --template ag-naip-index-hybrid-classification \
  --name index-hybrid-demo \
  --bbox -83.2000 39.8500 -83.1990 39.8510 \
  --years 2023
fr validate studies/index-hybrid.fr.md
fr plan studies/index-hybrid.fr.md --offline
fr explain studies/index-hybrid.fr.md --offline
```

Review source-band compatibility, persisted indices, specialist parents,
selection mode, candidate bounds, expected rasters, and transfer ceiling before
enabling a cook. See
[Index-guided hybrid classification](index-guided-classification.md).

If planning reports that a requested NAIP/CDL pair is unavailable, review its
ranked coherent and imagery-only alternatives. A noninteractive coherent
selection is explicit:
```sh { .illustrative }
fr plan studies/index-hybrid.fr.md \
  --resolve-imagery-year 2019 \
  --resolve-cdl-year 2019
```

The paired arguments create a hashed resolution contract; they do not edit the
workfile or download rasters during selection.
