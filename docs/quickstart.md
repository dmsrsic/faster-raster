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
