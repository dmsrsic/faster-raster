# Preview templates

Reusable preview templates separate layout and panel composition from the
lower-level render contract. The template schema is
`fasterraster.preview-template/v1`; the render contract remains authoritative
for sources, adapters, AOI, CRS, dimensions, bands, opacity, blend mode,
resampling, byte ceilings, network policy, capability hashes, theme hash, and
renderer version.

This feature is **Unreleased / experimental**. Existing alpha.2 and alpha.3
preview tasks now select registry entries instead of branching on task IDs in
the render-contract builder. Their render contracts additionally bind the
template ID, schema version, and template SHA-256.

## Discover and validate

```sh
fr preview-templates list
fr preview-templates show ag_classification_audit_v1 --json
fr preview-templates validate general_multisource_v1
fr preview-templates validate path/to/my-template.yaml --json
```

The agricultural classification audit uses five analytical panels:

```yaml
schema_version: fasterraster.preview-template/v1
template_id: ag_classification_audit_v1
layout:
  type: grid
  rows: 2
  columns: 3
panels:
  - {panel_id: natural, role: natural_color}
  - {panel_id: color_infrared, role: natural_color}
  - {panel_id: ndvi, role: environmental_context}
  - {panel_id: classes, role: classification}
  - {panel_id: confidence, role: confidence}
shared_extent: true
include_scale_bar: true
include_north_arrow: true
include_provenance_footer: true
audit_contract:
  minimum_font_size: 18
  maximum_panel_title_characters: 96
  required_legends: [broad_classes, confidence_states]
  required_explanations:
    [unknown_uncertain, confidence_threshold, decision_states]
  require_provenance_footer: true
  documentation_derivative: {width: 1920, height: 1080}
```

The hybrid audit adds specialist-classification and receipt-evidence roles in
an eight-panel template. Audit validation fails on missing legends, title
overflow, unsupported class codes, missing confidence-threshold provenance, or
an absent footer. Both classification templates produce a 3840×2160 primary
image and deterministic 1920×1080 documentation derivative.

The general template compares natural color, terrain, and environmental
context. User templates may reference registered roles; they cannot name a
callable or embed code. Validation checks layout bounds, panel capacity,
registered roles, dimensions, opacity, deterministic z-order tie-breaking,
required bands, theme compatibility, and categorical nearest/mode resampling.

The checked-in registry is
[`configs/preview_templates.yaml`](https://github.com/dmsrsic/faster-raster/blob/main/configs/preview_templates.yaml);
the JSON Schema is
[`schemas/preview_template.schema.json`](https://github.com/dmsrsic/faster-raster/blob/main/schemas/preview_template.schema.json).
