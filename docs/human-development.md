# Human-development workflow

The implemented workflow compares exact-year USDA CDL classes through the `usda_cdl_development_proxy_v1` mapping contract.

Typical analytical years are 2008, 2016, and 2021. Each epoch is harmonized to the explicit target grid with nearest-neighbor resampling. Endpoint trends use the common all-epoch valid footprint; adjacent intervals retain their pairwise valid-footprint evidence.

## Supported claim

The output describes **USDA CDL-derived mapped-development proxy change**.

## Unsupported claims

It does not establish authoritative urbanization, population or economic growth, construction timing, occupancy, cadastral approval, policy effects, or causality. CDL is crop-focused. Apparent transitions can reflect mapped change, ancillary non-agricultural classification changes, differences between CDL years, source-production differences, or a combination.

## Context imagery

Context imagery is visually informative but does not alter classifications, masks, transitions, or counts. Its year is recorded separately from the analytical years.

See [Hybrid publication](hybrid-publication.md) and [Known limitations](limitations.md).
