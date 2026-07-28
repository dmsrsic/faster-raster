# Examples

## Buckeye–Verrado, Arizona

![Buckeye–Verrado publication](assets/examples/buckeye-verrado-publication.png)

CDL analytical years: **2008, 2016, 2021**. Imagery: **2023 NAIP**, used only as later visual context because no intersecting 2021 NAIP record was available. The map is a CDL-derived mapped-development proxy, not authoritative urbanization or evidence of 2021 imagery conditions.

## Star, Idaho

![Star, Idaho publication](assets/examples/star-idaho-publication.png)

CDL analytical years: **2008, 2016, 2021**. Imagery: **2021 NAIP visual context**. The classification uses the same crop-focused CDL mapped-development proxy and does not establish population, construction, economic, or causal change.

Both images are documentation-sized derivatives of checksum-verified FasterRaster local publications. No raw CDL or NAIP raster is included in the repository.

The redistribution review used the official [USDA CDL metadata statement](https://www.nass.usda.gov/Research_and_Science/Cropland/metadata/metadata_Cropland-Data-Layer-2024.htm), which identifies CDL as public domain, and the [USDA FSA NAIP acquisition notice](https://www.fsa.usda.gov/Internet/FSA_Notice/ap_26.pdf), which identifies acquired NAIP imagery as public domain. The committed derivatives preserve source attribution and do not change those source-data terms.

## Complete Star workflow record

The public workfile is
[`examples/star-idaho-regional-growth-cdl-development-change.fr.md`](https://github.com/dmsrsic/faster-raster/blob/main/examples/star-idaho-regional-growth-cdl-development-change.fr.md).
It declares CDL analytical years 2008, 2016, and 2021; EPSG:5070 at 30 metres;
nearest-neighbour categorical resampling; and a 25 MB network ceiling.

```sh
fr validate examples/star-idaho-regional-growth-cdl-development-change.fr.md
fr plan examples/star-idaho-regional-growth-cdl-development-change.fr.md --out build/star-plan
fr cook examples/star-idaho-regional-growth-cdl-development-change.fr.md --reuse auto --no-open
fr inspect latest --verbose
```

The documentation derivative is the 555,309-byte image above with SHA-256
`c3c475a6ea312ef39212a4a5a00211029309515ad3789486bd415a6ff8a4b5b0`.
Its source preview hash and derivation are retained in
[`provenance.json`](assets/examples/provenance.json). The historical
publication receipt used to create the derivative is not committed, so actual
network bytes and runtime are **not known from the public evidence** and are
not reconstructed or estimated here.

The interpretation boundary is unchanged: CDL classes 121 through 124 are a
crop-focused mapped-development proxy. This result does not establish
population growth, construction timing, occupancy, economic activity,
authoritative urbanization, or causality.
