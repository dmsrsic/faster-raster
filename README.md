# FasterRaster

FasterRaster v0 is a deterministic HTTPS/API URL generation and raster harmonization planning tool.

It accepts a semantic `research_spec.json` and compiles:

1. `acquisition_manifest.jsonl` containing planned HTTPS/API requests.
2. `harmonization_plan.json` containing deterministic raster alignment instructions.

v0 deliberately does not download rasters, run GDAL, perform edge analytics, classify imagery, or model correlations.

## Commands

```bash
faster-raster validate <spec>
faster-raster resolve-sources <spec>
faster-raster plan-urls <spec> --out <manifest>
faster-raster plan-harmonization <spec> --manifest <manifest> --out <plan>
faster-raster inspect-manifest <manifest>
faster-raster inspect-harmonization <plan>
```

## Design Rule

User input is semantic. URLs are compiled outputs.

