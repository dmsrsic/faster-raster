# Earth Engine compute contracts

Earth Engine is a separate execution family, not a STAC asset-access mode.
Static STAC metadata can identify a dataset, but raster bytes are produced by a
bounded computation request rather than downloaded from a STAC Item asset.

The public `earth_engine_compute` contract freezes:

- dataset ID and image or image-collection type;
- declared bands, data types, and categorical or continuous semantics;
- an allowlist of closed selection operations;
- study AOI and a complete output grid;
- deterministic collection ordering and terminal selection;
- opaque credential and Cloud project references;
- request, dimensions, band-count, and uncompressed-response ceilings.

The compiler emits operations such as `load_image`, `load_collection`,
`filter_bounds`, `filter_date`, deterministic acquisition-time and
`system:index` ordering, `select_first`, and `select_bands`. It never accepts
arbitrary JavaScript, Python, serialized Earth Engine expressions, or
provider-supplied executable code.

`materialization_content_sha256` binds byte-producing scientific inputs without
credential aliases or runtime permission. The full materialization-request hash
also binds authorization intent and ceilings.

The shipped NASADEM, Sentinel-2 harmonized, and WorldCover examples prove
public schema and compiler behavior only. Private Earth Engine execution is not
implemented or claimed by the public repository. Categorical WorldCover
requests require nearest-neighbor resampling.
