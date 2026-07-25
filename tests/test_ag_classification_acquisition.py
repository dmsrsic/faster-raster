from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from faster_raster import ag_execution
from faster_raster.ag_assets import AssetRecord, compile_asset_plan
from faster_raster.ag_execution import (
    RecipeExecutionError,
    _run_selective_acquisition,
    execute_recipe,
)
from faster_raster.ag_geography import (
    asset_safety_profile,
    estimate_uncompressed_asset_bytes,
)
from faster_raster.ag_recipes import load_named_recipe


ROOT = Path(__file__).resolve().parent.parent
BBOX = (-112.05, 33.40, -112.049, 33.401)


def _record(name: str, path: Path) -> AssetRecord:
    return AssetRecord(
        asset_name=name,
        source_family="USDA_CDL" if name == "cdl_classes" else "USGS_NAIP",
        temporal_key=2023,
        bbox_epsg_4326=BBOX,
        extent_native=(0.0, 0.0, 100.0, 100.0),
        crs="EPSG:3857",
        pixel_size=(30.0, 30.0) if name == "cdl_classes" else (1.2, 1.2),
        pixel_size_m=30.0 if name == "cdl_classes" else 1.2,
        width=100,
        height=100,
        nodata=(0,),
        semantic_type=(
            "categorical"
            if name == "cdl_classes"
            else "continuous_multiband_imagery"
        ),
        checksum="0" * 64,
        local_path=str(path),
        originating_handoff=str(path.parent.parent),
        validation_state="valid",
        validation_errors=(),
        can_crop_locally=True,
        requires_reprojection=False,
    )


def test_raw_asset_is_one_four_byte_pixel_safety_item():
    profile = asset_safety_profile(
        ["naip_multispectral", "cdl_classes"],
        1.2,
    )
    assert profile["naip_multispectral"] == (1.2, 4)
    assert "natural" not in profile
    assert "ndvi" not in profile
    raw_only = {"naip_multispectral": profile["naip_multispectral"]}
    four_band_bytes = estimate_uncompressed_asset_bytes(BBOX, raw_only)
    one_band_bytes = estimate_uncompressed_asset_bytes(
        BBOX,
        {"one_band": (1.2, 1)},
    )
    assert four_band_bytes == one_band_bytes * 4


def test_selective_plan_requests_only_missing_raw_imagery(tmp_path):
    recipe = load_named_recipe(ROOT, "naip_cdl_classification_audit")
    cdl = _record("cdl_classes", tmp_path / "data/cdl_2023_classes.cog.tif")
    decisions = compile_asset_plan(recipe, [cdl], BBOX, 2023, "auto")
    by_name = {item.asset_name: item for item in decisions}
    assert by_name["cdl_classes"].action == "reuse_direct"
    assert by_name["naip_multispectral"].action == "acquire"


def test_resolution_and_only_raw_asset_reach_child_acquisition_command(tmp_path):
    recipe = load_named_recipe(ROOT, "naip_cdl_classification_audit")
    staging = tmp_path / "staging"
    staging.mkdir()
    captured: list[str] = []

    def runner(command, **kwargs):
        captured.extend(command)
        (staging / "manifest.json").write_text(
            json.dumps({"network_bytes": 0, "requests": [], "layers": []}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    _run_selective_acquisition(
        ROOT,
        staging,
        ["naip_multispectral"],
        name="raw_test",
        bbox=BBOX,
        start="2023-04-01",
        end="2023-10-31",
        year=2023,
        recipe=recipe,
        max_total_bytes=75_000_000,
        service_tile_size=1800,
        naip_resolution_m=1.2,
        runner=runner,
    )
    assert captured[captured.index("--assets") + 1] == "naip_multispectral"
    assert captured[captured.index("--naip-resolution") + 1] == "1.2"


def test_tiled_exporter_invokes_unrendered_zero_based_raw_bands():
    source = (ROOT / "scripts/fr-cook-ag").read_text(encoding="utf-8")
    raw_branch = source.split(
        'if "naip_multispectral" in requested_assets:', 1
    )[1].split('if "ndvi" in requested_assets:', 1)[0]
    assert 'name="naip_multispectral"' in raw_branch
    assert "band_ids=(0, 1, 2, 3)" in raw_branch
    assert "rendering_rule=None" in raw_branch
    assert "NaturalColor" not in raw_branch
    assert "NDVI_Color" not in raw_branch


def test_missing_classifier_dependency_blocks_before_cache_or_network(
    tmp_path,
    monkeypatch,
):
    recipe = load_named_recipe(ROOT, "naip_cdl_classification_audit")
    monkeypatch.setattr(
        "faster_raster.ag_classification.classification_dependency_status",
        lambda: {"available": False},
    )
    monkeypatch.setattr(
        ag_execution,
        "discover_cached_assets",
        lambda *args, **kwargs: pytest.fail("cache inspection must not start"),
    )
    monkeypatch.setattr(
        ag_execution,
        "_run_selective_acquisition",
        lambda *args, **kwargs: pytest.fail("network acquisition must not start"),
    )
    with pytest.raises(
        RecipeExecutionError,
        match=r"pip install -e '\.\[classification\]'",
    ):
        execute_recipe(
            tmp_path,
            recipe=recipe,
            recipe_raw=recipe.model_dump(mode="json"),
            name="dependency_gate",
            bbox=BBOX,
            start="2023-04-01",
            end="2023-10-31",
            year=2023,
            reuse_mode="never",
            open_preview=False,
            max_total_bytes=75_000_000,
            service_tile_size=1800,
            renderer=lambda *args: pytest.fail("renderer must not start"),
            naip_resolution_m=1.2,
        )
