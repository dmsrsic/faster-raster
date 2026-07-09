# Manual CDL Endpoint Audit Now

- Generated UTC: 2026-07-09T03:25:04.806558+00:00
- Base: `https://pdi.scinet.usda.gov/image/rest/services/CDL_WM/ImageServer`
- Metadata status: `200` JSON: `True`
- Service name: `CDL_WM`
- Capabilities: `Catalog,Mensuration,Image,Metadata`
- TimeInfo: `{"endTimeField": "Year", "startTimeField": "Year", "timeExtent": [852076800000, 1735689600000], "timeReference": null}`

## Best export candidates

| score | bbox | time_variant | fmt | http | bytes | unique | nontransparent | meaningful | cache |
|---:|---|---|---|---:|---:|---:|---:|---|---|
| 138 | task_expand10 | no_time | png32 | 200 | 74523 | 28 | 262144 | True | `reports/task_previews/cdl_manual_audit/export_task_expand10_no_time_png32_1cb4875beee09d2f.png` |
| 138 | task_expand10 | no_time | png | 200 | 73652 | 28 | 262144 | True | `reports/task_previews/cdl_manual_audit/export_task_expand10_no_time_png_bb9f867777f60cc3.png` |
| 134 | iowa_known_ag | time_mid_2023_epoch | png32 | 200 | 73313 | 24 | 262144 | True | `reports/task_previews/cdl_manual_audit/export_iowa_known_ag_time_mid_2023_epoch_png32_8fc93fe7ac58eccf.png` |
| 134 | iowa_known_ag | time_mid_2023_epoch | png | 200 | 71737 | 24 | 262144 | True | `reports/task_previews/cdl_manual_audit/export_iowa_known_ag_time_mid_2023_epoch_png_187136a253e49275.png` |
| 134 | iowa_known_ag | time_2023_interval | png32 | 200 | 73313 | 24 | 262144 | True | `reports/task_previews/cdl_manual_audit/export_iowa_known_ag_time_2023_interval_png32_8fc93fe7ac58eccf.png` |
| 134 | iowa_known_ag | time_2023_interval | png | 200 | 71737 | 24 | 262144 | True | `reports/task_previews/cdl_manual_audit/export_iowa_known_ag_time_2023_interval_png_187136a253e49275.png` |
| 134 | iowa_known_ag | mosaic_year_eq_2023 | png32 | 200 | 73313 | 24 | 262144 | True | `reports/task_previews/cdl_manual_audit/export_iowa_known_ag_mosaic_year_eq_2023_png32_8fc93fe7ac58eccf.png` |
| 134 | iowa_known_ag | mosaic_year_eq_2023 | png | 200 | 71737 | 24 | 262144 | True | `reports/task_previews/cdl_manual_audit/export_iowa_known_ag_mosaic_year_eq_2023_png_187136a253e49275.png` |
| 130 | task_original | no_time | png32 | 200 | 8771 | 20 | 262144 | True | `reports/task_previews/cdl_manual_audit/export_task_original_no_time_png32_001fad419cfab20e.png` |
| 130 | task_original | no_time | png | 200 | 8377 | 20 | 262144 | True | `reports/task_previews/cdl_manual_audit/export_task_original_no_time_png_458326671673b6cb.png` |
| 130 | iowa_known_ag | no_time | png32 | 200 | 74332 | 20 | 262144 | True | `reports/task_previews/cdl_manual_audit/export_iowa_known_ag_no_time_png32_4bf23978fe0d38ab.png` |
| 130 | iowa_known_ag | no_time | png | 200 | 73396 | 20 | 262144 | True | `reports/task_previews/cdl_manual_audit/export_iowa_known_ag_no_time_png_3c652394aa2f41ae.png` |

## Best identify candidates

| score | bbox | time_variant | http | bytes | meaningful_count | values |
|---:|---|---|---:|---:|---:|---|
| 0 | task_original | no_time | 200 | 254 | 0 | `[]` |
| 0 | task_original | time_year_string | 200 | 254 | 0 | `[]` |
| 0 | task_original | time_mid_2023_epoch | 200 | 254 | 0 | `[]` |
| 0 | task_original | time_2023_interval | 200 | 254 | 0 | `[]` |
| 0 | task_original | mosaic_year_eq_2023 | 200 | 254 | 0 | `[]` |
| 0 | task_original | mosaic_year_string_2023 | 200 | 214 | 0 | `[]` |
| 0 | task_expand10 | no_time | 200 | 254 | 0 | `[]` |
| 0 | task_expand10 | time_year_string | 200 | 254 | 0 | `[]` |
| 0 | task_expand10 | time_mid_2023_epoch | 200 | 254 | 0 | `[]` |
| 0 | task_expand10 | time_2023_interval | 200 | 254 | 0 | `[]` |
| 0 | task_expand10 | mosaic_year_eq_2023 | 200 | 254 | 0 | `[]` |
| 0 | task_expand10 | mosaic_year_string_2023 | 200 | 214 | 0 | `[]` |

## Decision

Best path: exportImage `task_expand10` `no_time` `png32`.