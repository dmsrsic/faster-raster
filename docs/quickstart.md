# Five-minute quickstart

This quickstart creates and validates the small Meridian human-development study without contacting a raster service.

```sh
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

For a live cook, explicitly set `data.allow_network: true` in the workfile, confirm the study byte ceiling, then run:

```sh
fr cook studies/meridian.fr.md --reuse auto --no-open
fr inspect latest --verbose
```

Final handoffs are under `outputs/handoffs/`. See [Network and byte budgets](network-byte-budgets.md) before enabling network access.

## Explore index-guided classification offline

Index discovery and workfile validation do not contact a provider:

```sh
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
