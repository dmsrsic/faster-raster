# gridMET Daily Unlock Note - v0.4.1

`gridmet_daily` remains the top no-auth unlock candidate because it is climate-grid data, likely metadata-probeable, and does not appear to require credentials in the current atlas.

## Current Decision

`blocked_by_endpoint_uncertainty`

The current repo contains the official gridMET documentation page but no verified OPeNDAP, THREDDS, or metadata/capability endpoint URL. Per v0.4.1 rules, no network probe was run and no endpoint was invented.

## Candidate Expectations

- Source family: gridMET
- Provider: University of Idaho / gridMET
- Expected access mode: OPeNDAP or related NetCDF service
- Expected format: NetCDF
- Likely temporal key: daily date or year-organized variable file
- Likely spatial key: grid coordinates with possible source-side subset after metadata resolution
- AOI subsetting: unknown, requires endpoint metadata
- Bounded metadata probe: appropriate after endpoint verification

## Next Step

Verify the official metadata/capability URL, then update only the research atlas entry and run:

```bash
python scripts/probe_atlas_source.py   --allow-network   --atlas research/source_atlas_v0_4.yaml   --source-id gridmet_daily   --max-bytes 65536   --out reports/atlas_probe_gridmet_daily.json   --markdown reports/atlas_probe_gridmet_daily.md
```
