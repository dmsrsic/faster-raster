# Roadmap

This roadmap describes direction, not implemented functionality or a delivery promise.

Implemented in the current **Unreleased / experimental** public tranche:

- declarative `fasterraster.source-pack/v1` Source Packs;
- advisory, explicitly selected Sauce Time contracts;
- registry-driven reusable preview templates;
- CRS-aware categorical area accounting and confidence-threshold provenance;
- explicit coherent NAIP–CDL temporal repair;
- provider-neutral opaque credential-requirement contracts that never contain
  resolved secrets.

Potential directions include:

- additional public-source adapter families after bounded evidence;
- paid and restricted dataset adapters;
- richer classification contracts;
- scheduler-neutral execution packages;
- workstation-to-cluster execution.

Managed infrastructure, private integrations, paid-source adapters, enterprise authentication, specialized classifiers, and cluster services may be developed separately from the community core.

The published beta.4 does **not** include the Unreleased tranche. The public
runtime also does not resolve credentials or ship authenticated parallel
execution. Scientific correctness, deterministic recovery, provenance, byte
ceilings, and safe credential boundaries take precedence over expanding the
source or execution matrix.
