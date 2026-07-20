# Errors and recovery

## No coverage for the requested year

FasterRaster reports the failed exact year and available intersecting years when known. Select another year explicitly; never relabel a later image as evidence for the earlier analytical year.

## Reuse-only is blocked

The cache does not contain fully compatible verified evidence. Run `fr explain ... --offline --verbose`, inspect the rejected candidates, then either provide the correct finalized handoff or intentionally permit bounded acquisition.

## Byte ceiling exceeded

Reduce the area, resolution, or publication mode, or raise the workfile ceiling after reviewing the estimate. Do not bypass the ceiling or reuse a partial download.

## Failed handoff

Read its failure receipt. Correct the source, coverage, cache, or configuration problem and start a new run. Never rename `.failed-*` or `.staging-*` into a final handoff.

## Finding the result

Use:

```sh
fr inspect latest --verbose
```

Final study results are under `outputs/handoffs/`; hybrid outputs are under `outputs/publications/`. The printed path and `manifest.json` identify the relevant result.
