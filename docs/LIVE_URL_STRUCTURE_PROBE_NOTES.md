# Live URL Structure Probe Notes

A bounded opt-in live probe was run for representative FasterRaster URL families using:

```bash
python scripts/live_url_structure_probe.py   --allow-network   --max-bytes 65536   --chunk-size 16384   --timeout-seconds 20   --out-json reports/live_url_structure_probe_64k.json   --out-md reports/live_url_structure_probe_64k.md
```

This probe intentionally read at most 64 KiB per URL and did not update runtime source registries or golden fixtures.

## Results

| Probe | Source family | Result | HTTP | Bytes | Interpretation |
| --- | --- | --- | ---: | ---: | --- |
| `prism_daily_zip` | PRISM | PASS | 206 | 65536 | Static PRISM zip URL supports bounded byte-range streaming. |
| `cdl_imageserver_tiny_export` | CDL ImageServer | PASS | 200 | 1146 | Tiny ArcGIS `exportImage` request returned a small TIFF response. |
| `nlcd_aws_tile` | Annual NLCD | FAIL | 403 | 0 | Current example S3 object URL or access pattern needs official re-verification before relying on live fetch. |
| `nlcd_aws_mosaic` | Annual NLCD | FAIL | 403 | 0 | Current example S3 mosaic URL or access pattern needs official re-verification before relying on live fetch. |
| `daymet_ncss_tiny_query_experimental` | Daymet NCSS | FAIL | 401 | 0 | Experimental NCSS URL is not ready for runtime support; endpoint/version/query/auth assumptions need review. |

## Contract Impact

No runtime behavior was changed. The results support keeping NLCD and Daymet enrichment entries in `needs_official_verification` / `experimental` status until official documentation and access patterns are confirmed.

## Reports

- `reports/live_url_structure_probe_64k.json`
- `reports/live_url_structure_probe_64k.md`
