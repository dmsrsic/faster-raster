# Generic HTTPS Template Adapter

`generic_https_template` proves FasterRaster can compile deterministic raster acquisition manifests without ArcGIS ImageServer semantics.

The adapter formats HTTPS URLs from registry templates. It does not download rasters, inspect remote files, or call external endpoints.

The original `generic_demo_cog` fixture is retained for backward tests but should be treated as a deprecated demo. Real documented fixtures now include Annual NLCD AWS object paths and PRISM time-series zip paths.

## Registry Example

```yaml
sources:
  generic_demo_cog:
    adapter: generic_https_template
    provider: DEMO_PROVIDER
    product: Demo Cloud Raster
    url_template: "https://example.invalid/rasters/{product_slug}/{year}/{thematic_layer}/{tile_id}.tif"
    product_slug: demo-cog
    semantic_type: continuous
    native_crs: EPSG:4326
    default_export_image_crs: EPSG:4326
    bbox_request_policy: no_bbox_url_template
    supports_bbox_crs_param: false
    supports_tiling: true
    default_format: tif
```

## Spec Example

```json
{
  "id": "demo_cog",
  "registry_key": "generic_demo_cog",
  "years": [2023, 2024],
  "thematic_layers": ["ndvi", "elevation"],
  "acquisition_mode": "https_template",
  "semantic_type": "continuous",
  "resampling": "bilinear"
}
```

## Supported Placeholders

- `{product_slug}`
- `{year}`
- `{thematic_layer}`
- `{tile_id}`
- `{source_id}`
- `{registry_key}`
- `{default_format}`
- `{region}`
- `{h}`
- `{v}`
- `{product_code}`
- `{collection}`
- `{version}`
- `{variable}`
- `{yyyymmdd}`
- `{resolution}`
- `{temporal_frequency}`

Unknown placeholders fail during capability validation.

## Manifest Contract

Generic rows preserve the same core manifest fields as ArcGIS rows:

- `request_id`
- `source_id`
- `registry_key`
- `adapter`
- `provider`
- `product`
- `year`
- `thematic_layer`
- `tile_id`
- `url`
- `source_aoi_bbox`
- `source_aoi_crs`
- `bbox`
- `bbox_crs`
- `export_image_crs`
- `target_grid_crs`
- `tile_width_pixels`
- `tile_height_pixels`
- `semantic_type`
- `resampling`
- `status`

## Resampling

Continuous sources may use:

- `nearest`
- `bilinear`
- `cubic`

Categorical sources may use:

- `nearest`
- `mode`
