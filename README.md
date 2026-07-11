# FasterRaster

FasterRaster v0 is a deterministic HTTPS/API URL generation, adapter planning, and raster harmonization planning tool.

It accepts a semantic `research_spec.json` and compiles:

1. `acquisition_manifest.jsonl` containing planned HTTPS/API requests.
2. `harmonization_plan.json` containing deterministic raster alignment instructions.

v0 deliberately does not download rasters, run GDAL, perform edge analytics, classify imagery, or model correlations.

v0.7 adds a no-network task compiler and scheduler-ready execution package path:

```text
task contract -> source resolution -> adapter planning -> acquisition manifest -> validation plan -> execution package -> DAG -> system grade
```

Static HTTP range sources compile into bounded probe jobs. Fixture-only sources, including PRISM in v0.7, remain historical evidence rows and do not generate fetch jobs.

## Commands

```bash
faster-raster validate <spec>
faster-raster resolve-sources <spec>
faster-raster plan-urls <spec> --out <manifest>
faster-raster plan-harmonization <spec> --manifest <manifest> --out <plan>
faster-raster inspect-manifest <manifest>
faster-raster inspect-harmonization <plan>
faster-raster task compile example_wave1_climate_stack --plain
faster-raster task package example_wave1_climate_stack --plain
faster-raster grade system --plain
```

## Design Rule

User input is semantic. URLs are compiled outputs.
