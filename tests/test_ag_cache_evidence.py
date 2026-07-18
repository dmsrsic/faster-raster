from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from faster_raster import ag_execution
from faster_raster.ag_assets import inspect_asset
from faster_raster.ag_execution import RecipeExecutionError, _validate_recipe_outputs


def _gdal_info() -> dict:
    return {
        "size": [10, 10],
        "geoTransform": [-100.0, 0.001, 0.0, 39.0, 0.0, -0.001],
        "coordinateSystem": {
            "wkt": 'GEOGCRS["WGS 84",ID["EPSG",4326]]'
        },
        "wgs84Extent": {
            "type": "Polygon",
            "coordinates": [[[-100.0, 38.99], [-100.0, 39.0], [-99.99, 39.0], [-99.99, 38.99], [-100.0, 38.99]]],
        },
        "bands": [{"band": 1, "noDataValue": 0}],
    }


def test_manifest_and_filename_year_conflict_rejects_asset(tmp_path):
    path = tmp_path / "cdl_2023_classes.cog.tif"
    path.write_bytes(b"fixture")

    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout=json.dumps(_gdal_info()), stderr="")

    asset = inspect_asset(
        path,
        "cdl_classes",
        tmp_path,
        layer_evidence={"_manifest_cdl_year": 2022},
        gdalinfo=runner,
    )

    assert asset.validation_state == "invalid"
    assert "temporal_evidence_conflict:2023!=2022" in asset.validation_errors


def test_reuse_never_skips_cache_discovery(tmp_path, monkeypatch):
    recipe_root = Path(__file__).resolve().parent.parent
    recipe = ag_execution.load_named_recipe(recipe_root, "crop_vigor_classification")
    raw = json.loads((recipe_root / "recipes/ag/crop_vigor_classification.json").read_text())
    monkeypatch.setattr(
        ag_execution,
        "discover_cached_assets",
        lambda *args, **kwargs: pytest.fail("reuse never must not scan cached assets"),
    )
    monkeypatch.setattr(
        ag_execution,
        "_run_selective_acquisition",
        lambda *args, **kwargs: (_ for _ in ()).throw(RecipeExecutionError("stop after planning")),
    )

    with pytest.raises(RecipeExecutionError, match="stop after planning"):
        ag_execution.execute_recipe(
            tmp_path,
            recipe=recipe,
            recipe_raw=raw,
            name="fresh",
            bbox=(-98.905, 38.3, -98.875, 38.33),
            start="2023-04-01",
            end="2023-10-31",
            year=2023,
            reuse_mode="never",
            open_preview=False,
            max_total_bytes=1_000_000,
            service_tile_size=100,
            renderer=lambda *args: pytest.fail("renderer must not run"),
        )


def test_output_validation_requires_preview_and_inventory(tmp_path):
    output = tmp_path / "preview"
    output.mkdir()
    preview = output / "preview.png"
    preview.write_bytes(b"png")
    with pytest.raises(RecipeExecutionError, match="class_inventory"):
        _validate_recipe_outputs(preview)
    (output / "class_inventory.csv").write_text("class,count\n1,1\n")
    _validate_recipe_outputs(preview)
