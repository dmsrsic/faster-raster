# FasterRaster beta roadmap

Version: 1.0 beta closeout

This roadmap describes direction, not shipped functionality. The current beta
is deliberately bounded: deterministic recipe compilation and acquisition,
USDA CDL and local Annual NLCD human-development studies, agricultural
CDL/NAIP recipes, finalized handoffs, reproducible reuse, and classification-
directed NAIP hybrid publications.

## Next minor release

- Define source-aware deterministic classification contracts for pixel, patch,
  object, temporal, reference-polygon, bounding-box, and reference-raster
  inputs.
- Use high-resolution NAIP as the primary classification demonstration
  substrate while retaining exact source, year, grid, and evidence contracts.
- Add deterministic spatial partitions, a local reference executor,
  scheduler-neutral job descriptions, map/reduce statistics, resumability, and
  explicit failure recovery.
- Add provider-neutral credential references and one tightly gated source
  proof; never embed credentials in recipes or receipts.

## Medium term

- Corroborate and weakly supervise classifications with CDL, NLCD, elevation,
  PRISM, nighttime lights, and user labels.
- Add a watershed measurement workflow.
- Add an NDVI workflow and a separately calibrated, scientifically qualified
  yield workflow.
- Add a nighttime-lights workflow.
- Fuse human-development and PRISM evidence for qualified urbanization and
  climate studies.
- Add Slurm array execution behind the scheduler-neutral partition contract.

## Later research

- Evaluate object and temporal classification at regional scale.
- Study uncertainty propagation across weak-supervision sources.
- Explore portable execution beyond the local and Slurm reference executors
  only after determinism, resumption, and failure semantics are proven.

The beta does not implement a general classification engine, watershed
analysis, NDVI or yield estimation, nighttime lights, urbanization/PRISM
fusion, gated EarthExplorer access, requester-pays S3, Slurm, Dask, Ray,
Kubernetes, or arbitrary raster algebra.
