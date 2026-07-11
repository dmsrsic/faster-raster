# FasterRaster Task Compiler

The v0.7 task compiler connects a local task YAML to adapter-backed acquisition planning.

Workflow:

```bash
faster-raster task validate example_wave1_climate_stack --plain
faster-raster task compile example_wave1_climate_stack --plain
faster-raster task inspect-compile example_wave1_climate_stack --plain
```

`task compile` validates the task, resolves static HTTP range adapter coverage, and writes deterministic artifacts under `reports/task_compiles/TASK_ID/`.

Artifacts:

- `acquisition_manifest.jsonl`
- `acquisition_manifest.json`
- `compile_report.json`
- `compile_report.md`
- `validation_plan.json`
- `source_resolution.json`

The compiler performs no network requests. It creates executable bounded probe rows for runnable sources and evidence rows for fixture-only sources.

The unified adapter planning row records deterministic URL, redacted headers, byte cap, expected magic/content family, validation steps, checksum policy, harmonization readiness, and provenance.

PRISM remains fixture-only. It compiles as historical contract evidence and does not generate a fetch job.
