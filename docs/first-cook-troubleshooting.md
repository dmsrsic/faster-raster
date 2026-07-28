# First-cook troubleshooting

Start with offline commands. Do not enable network merely to make an error
disappear.

| Symptom | Meaning | Corrective command |
|---|---|---|
| `fr doctor --offline` reports an unwritable default path | Local config/state/cache location is unavailable | `fr configure --project --cache-root .fasterraster/cache --state-root .fasterraster/state --temporary-root .fasterraster/tmp` |
| Workfile validation fails | Front matter or a workflow contract is invalid | `fr validate studies/my-study.fr.md --json` |
| Offline plan is blocked | Required compatible evidence is absent under the current policy | `fr explain studies/my-study.fr.md --offline --verbose --out build/explanation` |
| Requested year has no coverage | Exact-year behavior stopped correctly | `fr sauce time alternatives PACK --requested YYYY --json` or edit the workfile only after reviewing source-reported years |
| Classification has no coherent requested year pair | NAIP/CDL exact-time planning stopped before acquisition | Review the ranked pair, then pass both `--resolve-imagery-year YYYY --resolve-cdl-year YYYY` to `fr plan`, `fr explain`, or `fr cook` |
| Inspection says confidence provenance is legacy-unavailable | The handoff predates mandatory threshold evidence | Keep the legacy status explicit; rerun the analysis under the current contract rather than guessing a threshold |
| Reported class area differs from nominal pixel width × height | Physical area was measured on an equal-area grid | Inspect `analysis/classification/area_accounting.json`; compare reconciliation and reference CRS, not projected nominal pixel area |
| Source Pack validation fails | Schema, host, template, CRS, resampling, nodata, secret, or preview policy is unsafe | `fr sauce validate my-source.sauce --json` |
| Source Pack golden test fails | Authored contract and checked-in expected plan drifted | `fr sauce explain my-source.sauce --json`; review the diff before intentionally regenerating evidence |
| Probe says network permission is required | Network is disabled by default | Review the pack, then run `fr sauce probe PACK --allow-network --out build/probe.json` |
| Probe says a credential resolver is required | The public runtime has only an opaque credential requirement | Use `fr sauce explain PACK --json`; do not paste a token into the pack |
| Categorical resampling is rejected | Bilinear/cubic interpolation would invent class values | Set `source.resampling: nearest` or `mode`, then run `fr sauce validate PACK` |
| Preview role or template is unknown | The template references an unregistered declarative role | `fr preview-templates list` and `fr preview-templates validate TEMPLATE.yaml --json` |
| Reuse-only cook is blocked | No compatible checksum-bound local evidence exists | `fr explain WORKFILE --offline --reuse only --verbose`; choose a verified handoff or explicitly authorize bounded acquisition |
| Byte ceiling is exceeded | Planned or actual transfer crossed the contract | Reduce AOI/resolution or review and change the workfile ceiling; rerun `fr plan WORKFILE --offline` |
| Result is in `.staging-*` or `.failed-*` | Transactional finalization did not complete | `fr inspect latest --verbose`; read the failure receipt and start a new run |

If a command emits structured JSON, preserve it with the workfile and report it
in an issue without credentials, private endpoints, raw rasters, or local
machine paths.
