# v1 Canonical Raster Metadata

FasterRaster v1.0.0-alpha.1 records a canonical metadata contract for each verified derived GeoTIFF.

The contract separates confirmed embedded raster facts from declared-only semantic fields. Structural facts come from a read-only raster open and TIFF/GeoTIFF tags: driver, shape, band count, dtype, transform, CRS, bounds, nodata, block layout, and container details. CHIRPS semantic fields such as precipitation, daily support, and mm/day units are recorded as declared-only until a later milestone promotes semantic harmonization.

The metadata contract hash excludes machine-local paths and volatile runtime data. A verifier reopens the derived artifact independently and checks identity, container, spatial, band, semantic, and lineage status.
