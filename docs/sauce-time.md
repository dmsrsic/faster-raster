# Sauce Time

Sauce Time is the explicit temporal-repair protocol
`fasterraster.temporal-alternatives/v1`. It is **Unreleased / experimental**.
The requested date remains authoritative. FasterRaster can rank bounded
metadata candidates, but it does not change a Source Pack or workfile until a
candidate is selected explicitly.

## Exact time unavailable

Ask the shipped PRISM fixture for a date it does not contain:

```sh
fr sauce time alternatives \
  examples/sauce-packs/prism-daily.sauce \
  --requested 2021-01-01 \
  --json
```

The result is content-bound and stops for review:

```json
{
  "schema_version": "fasterraster.temporal-alternatives/v1",
  "requested_time": "2021-01-01",
  "status": "AWAITING_TEMPORAL_SELECTION",
  "selection_required": true,
  "original_request_unchanged": true,
  "search_contract_sha256": "...",
  "candidates": [
    {
      "candidate_time": "2022-01-01",
      "distance_days": 365,
      "coverage_fraction": 1.0,
      "rank": 1,
      "reason_codes": ["complete_coverage", "closest_time", "source_verified"]
    }
  ]
}
```

Unknown cloud, nodata, coverage, transfer, or resolution values remain the
literal string `unknown`; they are never invented to improve a rank.

Ranking is deterministic: same provider/product/processing family, complete or
highest known coverage, temporal and seasonal distance, known cloud/nodata
quality, asset compatibility and resolution, accessibility and transfer
estimate, verification status, then earlier time and candidate ID.

## Explicit selection

Create a new resolution contract:

```sh
fr sauce time select \
  examples/sauce-packs/prism-daily.sauce \
  --requested 2021-01-01 \
  --candidate 2022-01-01 \
  --out build/prism-temporal-resolution.json \
  --json
```

The result uses `fasterraster.temporal-resolution/v1`, binds the search and
ranked-alternatives hashes, records `explicit_user_selection`, and has a new
`resolved_contract_sha256`. The original Source Pack is not modified.

NAIP–CDL classification recovery uses the related
`fasterraster.classification-temporal-alternatives/v1` contract. It ranks
coherent imagery/weak-label year pairs before imagery-only repair, stops at
`AWAITING_TEMPORAL_SELECTION`, performs no raster acquisition, and records
unknown coverage as unknown. Explicit selection creates
`fasterraster.classification-temporal-resolution/v1` with original and resolved
year pairs plus immutable hashes.

For noninteractive use, pass both year arguments to `fr plan`, `fr explain`,
or `fr cook`:

```sh
fr plan study.fr.md \
  --resolve-imagery-year 2019 \
  --resolve-cdl-year 2019
```

Supplying only one argument is rejected. These arguments create a resolution
contract; they do not silently edit the workfile or authorize unbounded source
access.

Candidates outside `temporal.tolerance_days` are excluded. If none remain, the
status is `NO_TEMPORAL_ALTERNATIVES`, not a fabricated substitution.
