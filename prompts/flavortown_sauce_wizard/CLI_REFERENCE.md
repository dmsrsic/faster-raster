# Flavortown Sauce Wizard CLI reference

```sh
fr capabilities --json

fr sauce init my-source
fr sauce validate my-source.sauce
fr sauce explain my-source.sauce --json
fr sauce test my-source.sauce
fr sauce compile my-source.sauce --out build/source-pack-plan.json
fr sauce materialize-request build/source-pack-plan.json \
  --role raster \
  --bbox=-75,39,-74,40 \
  --out build/source-materialization-request.json
fr sauce probe my-source.sauce --allow-network --out build/probe.json
fr sauce pack my-source.sauce --out dist/

fr sauce time alternatives my-source.sauce --requested 2022 --json
fr sauce time select my-source.sauce \
  --requested 2022 \
  --candidate 2021 \
  --out build/temporal-resolution.json \
  --json

fr preview-templates list
fr preview-templates show ag_classification_audit_v1 --json
fr preview-templates validate general_multisource_v1
fr preview-templates validate path/to/template.yaml --json

fr validate path/to/study.fr.md
fr plan path/to/study.fr.md --offline --out build/plan
```

`sauce validate`, `sauce explain`, `sauce test`, `sauce compile`,
`sauce materialize-request`, Sauce Time fixture discovery, and
preview-template discovery are offline. `sauce compile` refuses blocked states
and writes a frozen `fasterraster.source-pack-plan/v1` handoff.
`sauce materialize-request` binds an explicit role and spatial intent to that
plan in a hashed `fasterraster.source-materialization-request/v1` contract; use
`--full-object` instead of `--bbox` for static archives and verified-local
rasters. `sauce probe` is the only Source Pack command above that may use
network; it requires explicit authorization and is bounded by the pack's
request, byte, timeout, host, and redirect limits.

Exit status zero means the requested command passed. A
`CREDENTIAL_REQUIRED`, `AWAITING_TEMPORAL_SELECTION`, or other blocked state is
not completed execution. Keep the structured JSON status and corrective
message intact when presenting it.
