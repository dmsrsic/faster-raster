# v1.0.0-alpha.1 Acceptance

The alpha trust chain is:

verified source artifact -> approved derivation plan -> bounded decompression -> content-addressed derived artifact -> canonical raster metadata -> metadata verification -> metadata catalog -> offline lineage verification.

This milestone intentionally does not implement reprojection, resampling, AOI clipping, harmonization, multisource stacking, COG generation, Slurm packaging, or pixel statistics.

Acceptance uses the offline CHIRPS source artifact `975aa6e54a75551d76b4390a7a30c6fc813e86eabd967e5f65a9db2e0b4cb4d8` and verifies the derived GeoTIFF, metadata contract, catalog, lineage, system grade, and repository hygiene without live network.
