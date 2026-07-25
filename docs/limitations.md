# Known limitations

- The public release is a beta; interfaces and output contracts may change.
- Python 3.12 on Ubuntu is the public CI target.
- Source coverage is limited by provider geography, exact year, catalog state, and service availability.
- CDL mapped-development results are a crop-focused proxy with classification and source-production uncertainty.
- Context imagery is visual evidence only and is temporally distinct when its year differs from the analytical endpoint.
- PRISM static ZIP support is fixture-only pending a currently verified deterministic endpoint.
- Credentialed and paid-source paths are not production integrations in this beta.
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
