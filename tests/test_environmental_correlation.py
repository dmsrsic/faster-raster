from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pytest

from faster_raster.environmental_correlation import (
    EnvironmentalCorrelationError,
    correlation_report,
    run_self_check,
)
from faster_raster.workfiles import WorkfileSpec


def _workfile_payload() -> dict:
    return {
        "schema_version": "fasterraster.work/v1",
        "name": "champaign-prism-dem-ndvi",
        "workflow": "prism-dem-ndvi-correlation-audit",
        "area": {"bbox": [-88.55, 39.75, -87.75, 40.45]},
        "time": {
            "start": "2023-04-01",
            "end": "2023-10-31",
            "crop_year": 2023,
        },
        "sources": {
            "policy": "pinned",
            "natural_imagery": "usgs_naip_imageserver",
            "crop_classes": "usda_nass_cdl_imageserver",
            "terrain": "usgs_3dep_imageserver",
            "precipitation": "prism_daily_ppt_static_zip",
        },
        "data": {
            "reuse": "never",
            "allow_network": True,
            "allow_materialization": True,
        },
        "processing": {
            "resolution_m": 4000,
            "service_tile_size": 1800,
        },
        "limits": {"maximum_download_mb": 750},
        "outputs": {"preview": True, "open_when_complete": False},
        "correlation": {
            "precipitation_start": "2023-06-09",
            "precipitation_end": "2023-06-15",
            "maximum_precipitation_days": 7,
            "minimum_valid_cells": 12,
            "naip_analysis_resolution_m": 30,
            "elevation_resolution_m": 30,
        },
    }


def test_environmental_workfile_contract_is_explicit_and_bounded():
    spec = WorkfileSpec.model_validate(_workfile_payload())
    assert spec.workflow_id == "prism_dem_ndvi_correlation_audit"
    assert spec.correlation is not None
    assert spec.correlation.precipitation_start == date(2023, 6, 9)
    assert spec.correlation.precipitation_end == date(2023, 6, 15)
    assert spec.data.allow_materialization is True
    assert spec.sources.precipitation == "prism_daily_ppt_static_zip"


def test_environmental_workfile_rejects_unpinned_or_unapproved_execution():
    payload = _workfile_payload()
    payload["data"]["allow_materialization"] = False
    with pytest.raises(ValueError, match="allow_materialization"):
        WorkfileSpec.model_validate(payload)

    payload = _workfile_payload()
    payload["sources"]["terrain"] = "some_other_dem"
    with pytest.raises(ValueError, match="pinned NAIP, CDL, 3DEP"):
        WorkfileSpec.model_validate(payload)


def test_pairwise_and_partial_correlations_use_only_common_valid_cells():
    elevation = np.arange(25, dtype=np.float64).reshape(5, 5)
    precipitation = elevation * 2.0 + 5.0
    ndvi = precipitation * 0.04 - elevation * 0.01
    precipitation[0, 0] = -9999.0
    report = correlation_report(
        precipitation,
        elevation,
        ndvi,
        precipitation_nodata=-9999.0,
        elevation_nodata=-9999.0,
        ndvi_nodata=-9999.0,
        minimum_valid_cells=12,
    )
    assert report["status"] == "PASS"
    assert report["common_valid_cell_count"] == 24
    assert report["methods"]["pearson"]["precipitation__elevation"] == pytest.approx(1.0)
    assert report["interpretation_guard"]["p_values_computed"] is False


def test_correlation_rejects_too_few_common_cells():
    values = np.arange(9, dtype=np.float64).reshape(3, 3)
    with pytest.raises(EnvironmentalCorrelationError, match="insufficient_common_grid_cells"):
        correlation_report(
            values,
            values,
            values,
            precipitation_nodata=-9999.0,
            elevation_nodata=-9999.0,
            ndvi_nodata=-9999.0,
            minimum_valid_cells=12,
        )


def test_environmental_self_check_writes_cog_and_omits_iid_significance():
    result = run_self_check()
    assert result["status"] == "PASS"
    assert result["cog_write"] is True
    assert result["no_p_values"] is True


def test_harmonization_receipt_verifies_after_handoff_relative_path(tmp_path):
    import json

    import rasterio
    from rasterio.transform import from_origin

    from faster_raster.prism_harmonization import plan_prism_harmonization
    from faster_raster.raster_harmonization import (
        execute_raster_harmonization,
        verify_harmonization_receipt,
    )

    handoff = tmp_path / "handoff"
    source = tmp_path / "source.tif"
    values = np.arange(100, dtype=np.float32).reshape(10, 10)
    with rasterio.open(
        source,
        "w",
        driver="GTiff",
        width=10,
        height=10,
        count=1,
        dtype="float32",
        crs="EPSG:5070",
        transform=from_origin(0, 10_000, 1000, 1000),
        nodata=-9999.0,
    ) as dataset:
        dataset.write(values, 1)
    plan = plan_prism_harmonization(
        source,
        aoi_bbox=(0, 0, 10_000, 10_000),
        aoi_crs="EPSG:5070",
        target_crs="EPSG:5070",
        target_resolution=2000,
        target_origin=(0, 0),
        resampling_method="average",
        max_output_pixels=100,
    )
    receipt = execute_raster_harmonization(
        plan,
        artifact_root=handoff / "data" / "harmonized" / "sha256",
        staging_root=tmp_path / "staging",
        max_output_bytes=16 * 1024 * 1024,
    )
    output = Path(receipt["output_artifact_path"])
    public = dict(receipt)
    public["output_artifact_path"] = output.relative_to(handoff).as_posix()
    public["receipt_path"] = "receipts/harmonization.json"
    receipt_path = handoff / "receipts" / "harmonization.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(json.dumps(public), encoding="utf-8")
    assert verify_harmonization_receipt(receipt_path)["verification_status"] == "PASS"


def test_normal_inspect_surfaces_environmental_correlation_summary(tmp_path):
    import json

    from PIL import Image

    from faster_raster.preview_open import inspect_handoff

    handoff = tmp_path / "handoff"
    (handoff / "analysis").mkdir(parents=True)
    (handoff / "preview").mkdir(parents=True)
    Image.new("RGB", (20, 20), "white").save(handoff / "preview" / "preview.png")
    (handoff / "manifest.json").write_text(
        json.dumps(
            {
                "operation_status": "completed",
                "workflow": "prism_dem_ndvi_correlation_audit",
                "network_bytes": 123,
                "warnings": ["exploratory only"],
            }
        ),
        encoding="utf-8",
    )
    (handoff / "workflow_receipt.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "workflow": "prism_dem_ndvi_correlation_audit",
                "assets": [],
                "generated_output_paths": ["analysis/correlation_summary.json"],
            }
        ),
        encoding="utf-8",
    )
    (handoff / "analysis" / "correlation_summary.json").write_text(
        json.dumps(
            {
                "common_valid_cell_count": 42,
                "precipitation_period": {
                    "start": "2023-06-09",
                    "end": "2023-06-15",
                    "day_count": 7,
                },
                "target_crs": "EPSG:5070",
                "target_resolution_m": 4000,
                "methods": {
                    "pearson": {"precipitation__ndvi": 0.25},
                    "spearman_rank": {"precipitation__ndvi": 0.2},
                    "partial_correlation": {
                        "precipitation__ndvi_controlling_elevation": 0.1
                    },
                    "standardized_linear_model": {"r_squared": 0.2},
                },
                "scientific_claim": "exploratory spatial association",
                "unsupported_claims": ["causation"],
            }
        ),
        encoding="utf-8",
    )
    report = inspect_handoff(handoff)
    environmental = report["environmental_correlation"]
    assert environmental["available"] is True
    assert environmental["common_valid_cell_count"] == 42
    assert environmental["pearson"]["precipitation__ndvi"] == 0.25
