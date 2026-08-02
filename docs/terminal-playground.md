# Terminal playground

The normal lifecycle is:

```text
doctor → templates → init → validate → plan → cook → inspect → publish → reuse-only verification
```

## Diagnose and create
```sh { .illustrative }
fr doctor --offline
fr templates list
fr templates show human-development-cdl
fr init studies/example.fr.md --template human-development-cdl \
  --name example --bbox -116.45 43.58 -116.35 43.68 \
  --years 2008 2016 2021
```

## Validate, plan, and explain
```sh { .illustrative }
fr validate studies/example.fr.md
fr plan studies/example.fr.md --offline --verbose --out build/example-plan
fr explain studies/example.fr.md --offline --verbose --out build/example-explanation
```

Validation is offline. `--offline` on planning prohibits source refresh as well as raster transfer.

## Discover and plan spectral indices
```sh { .illustrative }
fr indices list
fr indices list --json
fr indices show ndvi
fr indices show ndmi
fr templates show ag-naip-index-hybrid-classification
fr validate studies/index-hybrid.fr.md
fr plan studies/index-hybrid.fr.md --offline --verbose
fr explain studies/index-hybrid.fr.md --offline --verbose
```

`fr indices show ndmi` explains that ordinary four-band NAIP lacks SWIR1.
Recommendation mode prompts only in an interactive terminal. With
`--non-interactive` or `--json`, it calculates a review package, returns
`AWAITING_INDEX_SELECTION`, and never claims a finalized hybrid result.

## Cook and inspect

After explicitly allowing network use in the workfile:
```sh { .illustrative }
fr cook studies/example.fr.md --reuse auto --no-open
fr inspect latest --verbose
```

Use the reported handoff path rather than guessing by timestamp. A final handoff contains `manifest.json`, receipts, methodology, checksums, and a preview. `.staging-*` and `.failed-*` directories are not final results.

## Publish
```sh { .illustrative }
fr publish human-development-hybrid outputs/handoffs/<handoff-id> \
  --mode combined \
  --imagery-year 2021 \
  --regional-resolution-m 4.2 \
  --hotspot-resolution-m 1 \
  --hotspot-size-m 1024 \
  --maximum-download-mb 75 \
  --workers 2 \
  --reuse auto \
  --allow-network
```

Publications are written beneath `outputs/publications/`.

## Verify strict reuse

An identical compatible replay makes no network request:
```sh { .illustrative }
fr publish human-development-hybrid outputs/handoffs/<handoff-id> \
  --mode combined \
  --imagery-year 2021 \
  --regional-resolution-m 4.2 \
  --hotspot-resolution-m 1 \
  --hotspot-size-m 1024 \
  --maximum-download-mb 75 \
  --workers 2 \
  --reuse only
```

Do not add `--allow-network` to a strict replay. Compatibility includes source, exact year, bounds, grid, catalog record IDs, checksums, mapping hash, handoff checksums, and publication settings.
