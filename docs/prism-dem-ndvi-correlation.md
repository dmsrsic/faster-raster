# PRISM × DEM × NDVI correlation audit

`prism_dem_ndvi_correlation_audit` is a guarded normal-workfile workflow for an
exploratory environmental association study. It joins four established source
contracts in one finalized handoff:

- PRISM daily precipitation ZIP packages, validated and decoded through the
  complete-object PRISM product pipeline;
- raw four-band NAIP, used to calculate numeric NDVI locally;
- USGS 3DEP elevation, acquired as a bounded floating-point DEM export;
- same-year USDA CDL classes, retained as crop-context evidence.

The three continuous variables are harmonized to one explicit EPSG:5070 grid.
Daily PRISM depth surfaces are summed across the declared bounded precipitation
window. Elevation and NDVI are averaged to the common grid. CDL is displayed in
the preview but is not silently converted into a numerical correlation input.

## Scientific scope

The workflow reports Pearson correlation, Spearman rank correlation, the
precipitation–NDVI partial correlation controlling elevation, and a standardized
linear model of NDVI from precipitation and elevation. It deliberately does not
calculate ordinary iid p-values because neighboring grid cells are spatially
autocorrelated.

The selected NAIP acquisition dates are recorded in the final receipt. The workflow does not assume that the declared precipitation window immediately precedes the imagery observation.

The supported claim is:

> Exploratory spatial association among accumulated PRISM precipitation,
> USGS 3DEP elevation, and NAIP-derived NDVI on one declared common grid.

Unsupported claims include causal precipitation effects, field-scale PRISM
truth, independent ground-truth accuracy, and conventional iid significance.

## Workfile requirements

The first public workflow is intentionally bounded:

- `data.reuse: never` for a clean auditable acquisition;
- `data.allow_network: true`;
- `data.allow_materialization: true`;
- all four source IDs pinned explicitly;
- a precipitation window no longer than 31 days;
- an explicit common resolution between 1,000 and 10,000 metres;
- an explicit total network ceiling.

See `examples/champaign-prism-dem-ndvi-correlation.fr.md` for a seven-day test.

## Normal lifecycle

```bash
fr validate examples/champaign-prism-dem-ndvi-correlation.fr.md
fr plan examples/champaign-prism-dem-ndvi-correlation.fr.md
fr cook examples/champaign-prism-dem-ndvi-correlation.fr.md
fr inspect latest --verbose
```

The finalized handoff contains source rasters, the accumulated precipitation
COG, the common-grid elevation and NDVI COGs, pairwise sample and correlation
files, source and harmonization receipts, checksums, and a six-panel preview.
