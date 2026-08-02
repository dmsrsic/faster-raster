# FasterRaster terminal playground

This guide is a self-contained beta workflow for creating studies, editing
recipes, cooking finalized handoffs, and publishing classification-directed
NAIP maps. Commands are run from the repository root and do not require Codex.

## 1. Activate and check the environment
```sh { .illustrative }
source .beta-tools/recipe-env/bin/activate
python --version                       # Python 3.12+
fr --help
fr doctor --offline
```

For another environment, install the checkout with
`python -m pip install -e '.[dev]'`. Keep credentials out of workfiles and
receipts.

## 2. Discover and generate recipes
```sh { .illustrative }
fr templates list
fr templates show human-development-cdl
mkdir -p studies
fr init studies/my-study.fr.md \
  --template human-development-cdl \
  --name my-study \
  --bbox -116.55 43.55 -116.30 43.75 \
  --years 2008 2016 2021
```

Built-in IDs are `human-development-cdl`,
`human-development-cdl-reuse`, `ag-cdl-naip`, and `generic-cog`.
Generation is deterministic and offline. `generic-cog` is a compile-oriented
HTTPS/COG scaffold; it does not promise analysis that the generic workflow
does not implement.

Generated files under `studies/` are ignored, while
`studies/README.md` remains tracked. Shipped `examples/` are not ignored.
To experiment with a shipped recipe without changing it:

```sh
cp examples/human-development-cdl.fr.md studies/copied-example.fr.md
```

Use a real filename reported by `find examples -name '*.fr.md'` if that
example name changes.

## 3. Edit, validate, plan, and explain

Open the Markdown workfile in any editor. Its front matter is the executable
contract; its Markdown body explains the study. Common safe edits are:

- `area.bbox`: EPSG:4326 `[minx, miny, maxx, maxy]`;
- `epochs`: exact requested years;
- `data.allow_network`, `data.reuse`, and
  `data.maximum_download_mb`: source policy and byte ceiling;
- context imagery enablement and exact context year;
- output preview emphasis: `development` (default) or
  `all_transitions`.

Then run:
```sh { .illustrative }
fr validate studies/my-study.fr.md
fr plan studies/my-study.fr.md --offline --verbose
fr explain studies/my-study.fr.md --offline --verbose
```

`--offline` prohibits source refresh. A live plan or cook must be an
intentional action with a bounded workfile ceiling.

## 4. Cook, inspect, and open
```sh { .illustrative }
fr cook studies/my-study.fr.md --reuse auto --no-open
fr inspect latest --verbose
fr open latest
```

For a live cook, set `data.allow_network: true` in the workfile first.
Final handoffs are under `outputs/handoffs/<handoff-id>/`.
Temporary `.staging-*` and `.failed-*` directories are not finalized
results.

For a deterministic zero-network replay, generate or edit a reuse-only recipe:
```sh { .illustrative }
fr init studies/my-replay.fr.md \
  --template human-development-cdl-reuse \
  --name my-replay \
  --bbox -116.55 43.55 -116.30 43.75 \
  --years 2008 2016 2021
fr validate studies/my-replay.fr.md
fr cook studies/my-replay.fr.md --offline --reuse only --no-open
```

`reuse: auto` uses verified compatible cache and may acquire missing inputs
when network is allowed. `reuse: only` requires verified compatible cache and
does no network work. `reuse: never` deliberately reacquires inputs and
therefore requires explicit network permission.

## 5. Complete study examples

Small Meridian CDL proxy study:
```sh { .illustrative }
fr init studies/meridian.fr.md \
  --template human-development-cdl \
  --name meridian-cdl-development \
  --bbox -116.45 43.58 -116.35 43.68 \
  --years 2008 2016 2021
fr validate studies/meridian.fr.md
fr plan studies/meridian.fr.md --verbose
fr cook studies/meridian.fr.md --reuse auto --no-open
```

Regional Star CDL proxy study:
```sh { .illustrative }
fr init studies/star-regional.fr.md \
  --template human-development-cdl \
  --name star-idaho-regional-growth \
  --bbox -116.58 43.58 -116.32 43.75 \
  --years 2008 2016 2021
fr validate studies/star-regional.fr.md
fr plan studies/star-regional.fr.md --verbose
fr explain studies/star-regional.fr.md --verbose
fr cook studies/star-regional.fr.md --reuse auto --no-open
```

CDL is a crop-focused development proxy here, not an authoritative urban-land
product. Interpret classes 121–124 through the source-mapping receipt. Area
trends use the all-epoch common valid footprint; interval comparisons keep
their pairwise valid footprints. Code 7 remains real class turnover even when
visually muted by `development` emphasis.

## 6. Publish hybrid maps

Publications consume a finalized human-development handoff and are written to
`outputs/publications/<publication-id>/`.

Regional change publication:
```sh { .illustrative }
fr publish human-development-hybrid outputs/handoffs/<handoff-id> \
  --mode regional-change \
  --imagery-year 2021 \
  --regional-resolution-m 4.2 \
  --maximum-download-mb 75 \
  --workers 2 \
  --reuse auto \
  --allow-network \
  --open
```

Classification-directed 1 m hotspot:
```sh { .illustrative }
fr publish human-development-hybrid outputs/handoffs/<handoff-id> \
  --mode hotspot \
  --imagery-year 2021 \
  --hotspot-resolution-m 1 \
  --hotspot-size-m 1024 \
  --maximum-download-mb 10 \
  --workers 2 \
  --reuse auto \
  --allow-network \
  --open
```

Use `--mode combined` to produce both the regional hybrid and hotspot inset
in a 3840 × 2160 publication. Other modes are `developed-state` (the
mapping contract controls the developed classes) and `regional-change`
(endpoint change codes 3–6 receive imagery).

An identical strict publication replay is:
```sh { .illustrative }
fr publish human-development-hybrid outputs/handoffs/<handoff-id> \
  --mode combined \
  --imagery-year 2021 \
  --regional-resolution-m 4.2 \
  --hotspot-resolution-m 1 \
  --hotspot-size-m 1024 \
  --maximum-download-mb 75 \
  --workers 2 \
  --reuse only \
  --open
```

Do not add `--allow-network` to that replay. Compatibility includes handoff
checksums, source, exact year, bounds, grid, record IDs, imagery checksum,
publication mode, and mapping hash.

## 7. Evidence, recovery, and hygiene

Verify any finalized result from its own directory:

```sh
cd outputs/handoffs/<handoff-id>
sha256sum -c checksums.sha256
cd ../../..
cd outputs/publications/<publication-id>
sha256sum -c checksums.sha256
cd ../../..
```

Handoffs and publications contain manifests, resolved configuration, source
and mapping evidence, receipts, methodology, limitations, preview assets, and
checksums. Acquisition receipts report network and reused bytes without
storing credentials or unnecessary full request URLs.

After a failed cook, read the error and failed receipt, correct the workfile or
cache problem, and start a new cook. Never rename a `.failed-*` directory
into a final handoff and never reuse unchecked files by filename alone.

The repository ignores generated studies and outputs. Before committing,
review `git status --short`; do not stage downloaded rasters, publications,
handoffs, caches, `.beta-tools`, or local state.

Run the bounded offline acceptance loop at any time:

```sh
./scripts/fr-beta-smoke --quick
```

For the slower release gate:

```sh
./scripts/fr-beta-smoke --full
```

The smoke script never silently downloads data. Full mode adds the complete
test suite, beta check, and system grader.
