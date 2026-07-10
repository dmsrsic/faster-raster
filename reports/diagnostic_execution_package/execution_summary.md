# FasterRaster Execution Package

- Package ID: `fr_exec_51438d1287b43021`
- Created by: `FasterRaster 0.6.0`
- Request count: `2`
- Total job count: `8`
- Dependency count: `6`
- Manifest SHA256: `fd46106cde9e8c51b0aae26296b46a48ec75d65ca47ed4e911329723693151cc`
- Harmonization SHA256: `4493ea4f494d589fc098bdb7744e07caef4bb15141a1877d12c9044205e4e2c6`
- Validation status: `PASS`
- DAG validation: `PASS`

## Stage Counts

- `fetch`: `2`
- `harmonize`: `2`
- `inspect_output`: `2`
- `validate_download`: `2`

## Scheduler Notes

- jobs.jsonl is deterministic and can be mapped to Slurm arrays, Snakemake rules, Nextflow processes, Prefect tasks, AWS Batch jobs, or Ray tasks.
- This package is a preflight orchestration artifact only; it does not download, validate bytes, or harmonize rasters.
- Stage dependencies are represented as job_id strings and can be translated into scheduler-native dependency syntax.

## Example Job

```json
{
  "adapter": "arcgis_imageserver",
  "dependencies": [],
  "expected_cache_path": "cache/cdl/2023/crop_type/386c2589dc0bf1a2c7f02d2e9728dbebf9121d68c571e03548332243d300d2eb.tiff",
  "expected_input_path": "https://pdi.scinet.usda.gov/image/rest/services/CDL_WM/ImageServer/exportImage?bbox=-83.20000000%2C39.80000000%2C-82.90000000%2C40.10000000&bboxSR=4326&f=image&format=tiff&imageSR=3857&size=1114%2C1453&time=2023",
  "expected_output_path": "cache/cdl/2023/crop_type/386c2589dc0bf1a2c7f02d2e9728dbebf9121d68c571e03548332243d300d2eb.tiff",
  "failure_policy_id": "default",
  "job_id": "cdl_2023_crop_type_tile_000001__fetch",
  "max_bytes": null,
  "request_id": "cdl_2023_crop_type_tile_000001",
  "resampling": "nearest",
  "resources": {
    "cpus": 1,
    "memory_mb": 1024
  },
  "retry_count": 2,
  "semantic_type": "categorical",
  "source_id": "cdl",
  "stage": "fetch",
  "target_grid_crs": "EPSG:5070",
  "thematic_layer": "crop_type",
  "tile_id": "000001",
  "timeout_seconds": 3600,
  "url": "https://pdi.scinet.usda.gov/image/rest/services/CDL_WM/ImageServer/exportImage?bbox=-83.20000000%2C39.80000000%2C-82.90000000%2C40.10000000&bboxSR=4326&f=image&format=tiff&imageSR=3857&size=1114%2C1453&time=2023",
  "year": 2023
}
```
