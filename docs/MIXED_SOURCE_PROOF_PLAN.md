# Mixed Source Proof Plan

Purpose: demonstrate two different public access modes under one FasterRaster preflight model without large downloads or raster harmonization.

## Sources

### PRISM Static HTTPS ZIP

- Current status: `static_verified`
- Access mode: deterministic HTTPS ZIP URL
- Probe behavior: bounded stream read only
- Existing proof: PRISM daily ZIP endpoint can return bounded bytes
- Runtime implication: remains compatible with `generic_https_template` style planning for verified legacy/static paths

### Daymet Single-Pixel Public REST

- Current status: `public_service_probe_candidate`
- Access mode: deterministic REST query from point, variables, start date, and end date
- Probe behavior: bounded text/CSV response read only
- Runtime implication: demonstrates service request compilation but does not imply raster/NCSS support

## Combined Proof Report Concept

A future combined proof report should include:

- source id
- access mode
- deterministic request URL
- HTTP status
- content type
- bytes read
- truncated flag
- SHA256 of bounded response bytes
- first response lines for text services
- error/failure classification

## Combined Execution Package Concept

A future execution package can include heterogeneous descriptors:

- static byte-range descriptor for PRISM ZIP
- REST text response descriptor for Daymet single-pixel
- shared limits: no large downloads, max bytes, no extraction
- shared provenance: source id, request id, timestamp, hash, status

## Boundaries

- Do not download full ZIPs.
- Do not parse or extract ZIP or NetCDF contents.
- Do not run raster harmonization.
- Do not register Daymet single-pixel as raster support.
- Keep NCSS classified separately as `credential_or_session_gated_research`.

## Purpose

This proof validates the mixed-source preflight direction: deterministic request planning and bounded diagnostics can cover both static object-style sources and public service-style sources without conflating them with raster execution.
