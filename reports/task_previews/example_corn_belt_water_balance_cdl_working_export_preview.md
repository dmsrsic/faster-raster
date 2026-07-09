# CDL Working Export Preview Evidence

- Generated UTC: `2026-07-09T03:29:37.407411+00:00`
- PNG: `reports/task_previews/example_corn_belt_water_balance_cdl_working_export_preview.png`
- Source cache: `reports/task_previews/cdl_manual_audit/export_task_expand10_no_time_png32_1cb4875beee09d2f.png`
- SHA256: `1cb4875beee09d2f17442208982f4e16e010369e7bfec90e65415439e17e19a5`

## Decision

CDL exportImage works for the task-expanded AOI when no time parameter is used.

## Best candidate

- bbox: `task_expand10`
- time_variant: `no_time`
- format: `png32`
- bytes: `74523`
- unique colors: `28`
- nontransparent pixels: `262144`
- meaningful image: `True`

## Implementation fix

In real_preview.py, add an exportImage candidate cascade and select the first meaningful image by diagnostics. For the current task AOI, no_time should beat time=2023.

## Guardrails

- Keep bounded byte caps.
- Keep network opt-in.
- Keep source registry and source atlas immutable during preview runs.
- Treat one-color transparent/black images as low-information or NoData unless a better export candidate wins.