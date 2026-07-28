# Known limitations

- The public release is a beta; interfaces and output contracts may change.
- Python 3.12 on Ubuntu is the public CI target.
- Source coverage is limited by provider geography, exact year, catalog state, and service availability.
- CDL mapped-development results are a crop-focused proxy with classification and source-production uncertainty.
- Context imagery is visual evidence only and is temporally distinct when its year differs from the analytical endpoint.
- PRISM daily ZIP has a deterministic official path, bounded probe, guarded
  complete-object materialization, selected-raster validation, and a shipped
  bounded correlation workflow. Live operations remain explicit and
  byte-capped; routine CI remains offline.
- Credentialed and paid-source paths are not production integrations in this beta.
- Unreleased Source Packs can express opaque credential requirements, but the
  public runtime cannot resolve them and stops before network access.
- Source Packs are declarative only; arbitrary Python, dynamic imports, shell
  hooks, and unrestricted templates are unsupported.
- Sauce Time alternatives use available metadata. Unknown coverage or quality
  remains unknown, and an explicit selection is required.
- Coherent NAIP–CDL temporal repair is advisory until explicitly selected.
  Imagery-only repair can create a scientifically important year mismatch that
  remains visible in receipts and requires explicit acceptance.
- Preview templates define layouts and roles; they do not expand a source's
  executable capabilities.
- Equal-area reprojection makes categorical area physically meaningful, but
  rasterized boundaries still have finite-grid discretization error. Native
  categorical pixel counts and equal-area estimates are reported separately.
- Confidence is maximum model class probability relative to the configured
  threshold; it is not independent accuracy or calibrated certainty.
- The NAIP–CDL classifier is a bounded weak-supervised analytical workflow,
  not a general-purpose or authoritative land-cover model.
- Raw NAIP values are not automatically atmospherically corrected surface
  reflectance. Indices and thresholds can be scene-relative and may not
  transfer across dates, mosaics, sensors, or radiometric products.
- Four-band NAIP lacks SWIR1 and SWIR2, so NDMI and NBR are unavailable.
  Green–NIR wet-surface response is named separately and is not canopy-water
  measurement.
- Index scores and target similarities are not probabilities. Weak-label
  agreement is not independent accuracy.
- Bounded nested spatial validation reduces, but cannot eliminate,
  multiple-comparison and overfitting risk in automatic multi-index search.
- Gray spectral similarity does not prove mining, abandonment, contamination,
  ownership, safety, land use, or physical causation.
- No authoritative urbanization model, causal model, or cluster execution
  service is shipped.
- Doctor recommendations are conservative heuristics, not performance or completion guarantees.

Always retain the workfile, source mapping, methodology, limitations, receipts, and checksums with a result.
