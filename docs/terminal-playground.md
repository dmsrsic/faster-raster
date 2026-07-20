# Terminal playground

The normal lifecycle is:

```text
doctor → templates → init → validate → plan → cook → inspect → publish → reuse-only verification
```

## Diagnose and create

```sh
fr doctor --offline
fr templates list
fr templates show human-development-cdl
fr init studies/example.fr.md --template human-development-cdl \
  --name example --bbox -116.45 43.58 -116.35 43.68 \
  --years 2008 2016 2021
```

## Validate, plan, and explain

```sh
fr validate studies/example.fr.md
fr plan studies/example.fr.md --offline --verbose --out build/example-plan
fr explain studies/example.fr.md --offline --verbose --out build/example-explanation
```

Validation is offline. `--offline` on planning prohibits source refresh as well as raster transfer.

## Cook and inspect

After explicitly allowing network use in the workfile:

```sh
fr cook studies/example.fr.md --reuse auto --no-open
fr inspect latest --verbose
```

Use the reported handoff path rather than guessing by timestamp. A final handoff contains `manifest.json`, receipts, methodology, checksums, and a preview. `.staging-*` and `.failed-*` directories are not final results.

## Publish

```sh
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

```sh
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
