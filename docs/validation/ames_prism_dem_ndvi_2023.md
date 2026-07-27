# Ames PRISM × DEM × NDVI validation (2023)

## Result

A live validation over Ames, Iowa completed with checksum-verified outputs and a coherent six-panel preview. The result is suitable as an exploratory spatial-association audit, not as a causal analysis or independent accuracy assessment.

## Declared study contract

- AOI: `[-93.75, 41.96, -93.53, 42.12]` in EPSG:4326
- NAIP year and acquisition date: 2023; 2023-09-02
- CDL year: 2023
- PRISM window: 2023-08-26 through 2023-09-01
- Analysis grid: EPSG:5070 at 4 km
- Common valid cells: 25
- Natural-colour coverage: 100%
- Numeric NDVI coverage: 100%

## Numeric results

- Pearson precipitation–NDVI: `-0.33497135934523453`
- Spearman precipitation–NDVI: `-0.3676923076923077`
- Partial precipitation–NDVI controlling elevation: `-0.3323044374498886`
- Pearson elevation–NDVI: `0.16626506009138725`
- Standardised model R²: `0.13501767864833158`

The negative precipitation–NDVI association is directionally consistent across Pearson, rank, partial-correlation, and standardised-model estimates. The sample is small and spatially autocorrelated, so no IID significance or causal claim is supported.

## Engineering findings

1. PRISM task compilation must honour `time.dates` before legacy top-level date fields.
2. Live PRISM COGs may omit the nonstandard `STATISTICS_NNULL` tag; provider sidecar statistics and streamed nodata counts remain authoritative.
3. Local NAIP NDVI derivation must use the dataset coverage mask and reject constant red or NIR bands.
4. The successful validation used locked NAIP catalogue records and numeric server-side band arithmetic for NDVI. Integrating that tiled server-side path into the normal cook remains a separate production change.

## Repository hygiene

This note intentionally excludes usernames, machine paths, local state directories, logs, downloaded rasters, and generated handoff receipts.
