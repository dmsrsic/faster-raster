# Errors and recovery

## No coverage for the requested year

FasterRaster reports the failed exact year and available intersecting years
when known. For a Source Pack, run
`fr sauce time alternatives PACK --requested YYYY --json`, review the ranked
metadata, then create an explicit resolution with
`fr sauce time select PACK --requested YYYY --candidate YYYY --out resolution.json`.
Never relabel a later image as evidence for the earlier analytical year.

## Reuse-only is blocked

The cache does not contain fully compatible verified evidence. Run `fr explain ... --offline --verbose`, inspect the rejected candidates, then either provide the correct finalized handoff or intentionally permit bounded acquisition.

## Byte ceiling exceeded

Reduce the area, resolution, or publication mode, or raise the workfile ceiling after reviewing the estimate. Do not bypass the ceiling or reuse a partial download.

## Index source is incompatible

Read the structured capability failure: requested index, required/available
bands, missing bands, source asset, and alternative-source status. Four-band
NAIP cannot calculate NDMI (missing SWIR1) or NBR (missing SWIR2). Choose a
scientifically appropriate compatible index explicitly; FasterRaster never
silently substitutes one.

## Awaiting index selection

Recommendation mode completed bounded candidate calculation but did not
finalize. Inspect `selection_review.json`,
`analysis/indices/index_candidate_ranking.json`, and
`analysis/indices/index_validation_metrics.json`. Accept a guarded candidate in
an interactive rerun or encode a reviewed user-defined contract. Do not rename
the `_review` package or present it as a completed handoff.

## No automatic candidate meets the guard

Automatic selection stops when spatial support, minimum selection performance,
or complexity requirements are not met. Review calibration evidence, spatial
fold coverage, candidate bounds, and scientific class meaning. Do not lower a
guard merely to force a result.

## Failed handoff

Read its failure receipt. Correct the source, coverage, cache, or configuration problem and start a new run. Never rename `.failed-*` or `.staging-*` into a final handoff.

## Finding the result

Use:

```sh
fr inspect latest --verbose
```

Final study results are under `outputs/handoffs/`; hybrid outputs are under `outputs/publications/`. The printed path and `manifest.json` identify the relevant result.

See the [first-cook troubleshooting matrix](first-cook-troubleshooting.md) for
exact corrective commands.
