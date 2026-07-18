from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from faster_raster import ag_execution
from faster_raster.ag_assets import (
    AssetRecord,
    asset_plan_document,
    compile_asset_plan,
    inspect_asset,
    spatial_relationship,
)
from faster_raster.ag_execution import RecipeExecutionError, execute_recipe, handoff_transaction
from faster_raster.ag_recipes import RecipeLoadError, load_named_recipe, load_recipe


RECIPE_IDS = [
    "crop_vigor_classification",
    "irrigation_field_structure",
    "crop_class_area_inventory",
    "crop_terrain_relationship",
]
BBOX = (-98.905, 38.300, -98.875, 38.330)


def record(
    name: str,
    path: Path,
    *,
    bbox=BBOX,
    year: int | None = 2023,
    pixel_size_m: float = 1.0,
    crs: str = "EPSG:3857",
    valid: bool = True,
    handoff: str | None = None,
) -> AssetRecord:
    return AssetRecord(
        asset_name=name,
        source_family=("USDA_CDL" if name.startswith("cdl") else "USGS_3DEP" if name == "hillshade" else "USGS_NAIP"),
        temporal_key=None if name == "hillshade" else year,
        bbox_epsg_4326=bbox,
        extent_native=(0.0, 0.0, 10.0, 10.0),
        crs=crs,
        pixel_size=(pixel_size_m, pixel_size_m),
        pixel_size_m=pixel_size_m,
        width=10,
        height=10,
        nodata=(None,),
        semantic_type="categorical" if name.startswith("cdl") else "continuous",
        checksum=None,
        local_path=str(path),
        originating_handoff=handoff or str(path.parent.parent),
        validation_state="valid" if valid else "invalid",
        validation_errors=() if valid else ("gdalinfo_failed",),
        can_crop_locally=valid,
        requires_reprojection=crs != "EPSG:3857",
    )


@pytest.fixture()
def recipe_root() -> Path:
    return Path(__file__).resolve().parent.parent


@pytest.fixture()
def recipe(recipe_root):
    return load_named_recipe(recipe_root, "crop_vigor_classification")


def test_all_four_v2_recipes_validate(recipe_root):
    for recipe_id in RECIPE_IDS:
        loaded = load_named_recipe(recipe_root, recipe_id)
        assert loaded.schema_version == 2
        assert loaded.recipe_id == recipe_id


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.pop("purpose"), "purpose"),
        (lambda value: value.update(required_assets=["natural", "bogus"]), "bogus"),
        (lambda value: value.update(required_assets=["natural", "natural"]), "unique"),
        (lambda value: value.update(preview="unsupported"), "unsupported"),
        (lambda value: value["resampling"].update(categorical="cubic"), "categorical"),
        (lambda value: value.update(maximum_naip_pixel_size_m=0), "greater than 0"),
    ],
)
def test_invalid_recipe_schema_rejected(tmp_path, recipe_root, mutation, message):
    source = recipe_root / "recipes/ag/crop_vigor_classification.json"
    value = json.loads(source.read_text())
    mutation(value)
    path = tmp_path / "crop_vigor_classification.json"
    path.write_text(json.dumps(value))
    with pytest.raises(RecipeLoadError, match=message):
        load_recipe(path)


def test_recipe_id_must_match_filename(tmp_path, recipe_root):
    value = json.loads((recipe_root / "recipes/ag/crop_vigor_classification.json").read_text())
    path = tmp_path / "different_name.json"
    path.write_text(json.dumps(value))
    with pytest.raises(RecipeLoadError, match="does not match filename"):
        load_recipe(path)


def test_invalid_recipe_fails_before_execution_or_network(tmp_path, monkeypatch, recipe_root):
    recipes = tmp_path / "recipes/ag"
    recipes.mkdir(parents=True)
    value = json.loads((recipe_root / "recipes/ag/crop_vigor_classification.json").read_text())
    del value["purpose"]
    (recipes / "bad.json").write_text(json.dumps({**value, "recipe_id": "bad"}))
    monkeypatch.setattr(
        ag_execution,
        "execute_recipe",
        lambda *args, **kwargs: pytest.fail("execution/cache/network must not start"),
    )
    code = ag_execution.run_recipe_cli(
        tmp_path,
        [
            "fr-cook-ag", "--recipe", "bad", "--bbox=-98.9,38.3,-98.8,38.4",
            "--start", "2023-04-01", "--end", "2023-10-31", "--cdl-year", "2023",
        ],
        renderer=lambda *args: pytest.fail("renderer must not start"),
    )
    assert code == 2


def test_spatial_relationships_are_explicit():
    assert spatial_relationship(BBOX, BBOX) == "exact"
    assert spatial_relationship((-99.0, 38.0, -98.0, 39.0), BBOX) == "contains"
    assert spatial_relationship((-98.89, 38.31, -98.8, 38.5), BBOX) == "partial_overlap"
    assert spatial_relationship((-100.0, 37.0, -99.5, 37.5), BBOX) == "no_overlap"


def test_exact_area_reuses_directly(recipe, tmp_path):
    decision = compile_asset_plan(recipe, [record("natural", tmp_path / "n.tif")], BBOX, 2023, "auto")[0]
    assert decision.action == "reuse_direct"


def test_larger_cached_area_reuses_with_crop(recipe, tmp_path):
    larger = (-99.0, 38.0, -98.0, 39.0)
    decision = compile_asset_plan(recipe, [record("natural", tmp_path / "n.tif", bbox=larger)], BBOX, 2023, "auto")[0]
    assert decision.action == "reuse_crop"


def test_partial_overlap_is_not_fully_compatible(recipe, tmp_path):
    partial = (-98.89, 38.31, -98.8, 38.5)
    decision = compile_asset_plan(recipe, [record("natural", tmp_path / "n.tif", bbox=partial)], BBOX, 2023, "auto")[0]
    assert decision.action == "acquire"
    assert decision.rejected_candidates[0]["spatial_relationship"] == "partial_overlap"


def test_wrong_cdl_year_is_rejected(recipe, tmp_path):
    decisions = compile_asset_plan(recipe, [record("cdl_classes", tmp_path / "c.tif", year=2022)], BBOX, 2023, "auto")
    cdl = next(item for item in decisions if item.asset_name == "cdl_classes")
    assert cdl.action == "acquire"
    assert "temporal_key_2022" in cdl.rejected_candidates[0]["reasons"][0]


def test_coarse_naip_is_rejected(recipe, tmp_path):
    decision = compile_asset_plan(recipe, [record("natural", tmp_path / "n.tif", pixel_size_m=2.0)], BBOX, 2023, "auto")[0]
    assert decision.action == "acquire"
    assert any("exceeds" in reason for reason in decision.rejected_candidates[0]["reasons"])


def test_corrupt_unreadable_raster_is_invalid(tmp_path):
    path = tmp_path / "naip_2023_natural_color.cog.tif"
    path.write_bytes(b"not a raster")

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], stderr="not recognized")

    inspected = inspect_asset(path, "natural", tmp_path, gdalinfo=fail)
    assert inspected.validation_state == "invalid"
    assert any("gdalinfo_failed" in error for error in inspected.validation_errors)


def test_reuse_only_marks_missing_asset_as_blocking(recipe, tmp_path):
    assets = [
        record("natural", tmp_path / "natural.tif"),
        record("cdl_classes", tmp_path / "cdl.tif"),
    ]
    decisions = compile_asset_plan(recipe, assets, BBOX, 2023, "only")
    missing = next(item for item in decisions if item.asset_name == "ndvi")
    assert missing.action == "reject"


def test_resolver_assembles_assets_from_multiple_handoffs(recipe, tmp_path):
    paths = [tmp_path / "one/data/n.tif", tmp_path / "two/data/v.tif", tmp_path / "three/data/c.tif"]
    assets = [
        record("natural", paths[0], handoff=str(tmp_path / "one")),
        record("ndvi", paths[1], handoff=str(tmp_path / "two")),
        record("cdl_classes", paths[2], handoff=str(tmp_path / "three")),
    ]
    decisions = compile_asset_plan(recipe, assets, BBOX, 2023, "auto")
    assert {item.action for item in decisions} == {"reuse_direct"}
    assert len({item.candidate.originating_handoff for item in decisions if item.candidate}) == 3


def test_selective_plan_requests_only_missing_asset(recipe, tmp_path):
    assets = [
        record("natural", tmp_path / "n.tif"),
        record("cdl_classes", tmp_path / "c.tif"),
    ]
    decisions = compile_asset_plan(recipe, assets, BBOX, 2023, "auto")
    plan = asset_plan_document(
        recipe, decisions, bbox=BBOX, start="2023-04-01", end="2023-10-31", year=2023, reuse_mode="auto"
    )
    assert plan["network_required_assets"] == ["ndvi"]


def test_semantic_resampling_policies(recipe, tmp_path):
    decisions = compile_asset_plan(recipe, [], BBOX, 2023, "never")
    by_name = {item.asset_name: item for item in decisions}
    assert by_name["cdl_classes"].resampling == "nearest"
    assert by_name["natural"].resampling not in {"nearest", "mode"}
    assert by_name["ndvi"].resampling not in {"nearest", "mode"}


def test_transaction_failure_never_publishes_completed_handoff(tmp_path):
    final = tmp_path / "outputs/handoffs/example"
    with pytest.raises(RuntimeError, match="boom"):
        with handoff_transaction(final) as staging:
            (staging / "partial.txt").write_text("diagnostic")
            raise RuntimeError("boom")
    assert not final.exists()
    failed = list(final.parent.glob(".failed-example-*"))
    assert len(failed) == 1
    report = json.loads((failed[0] / "failure_report.json").read_text())
    assert report["completed_handoff_created"] is False


def _fake_renderer(root, handoff, recipe_raw, compatibility, name, bbox, start, end, year, open_preview):
    output = handoff / "preview" / recipe_raw["recipe_id"]
    output.mkdir(parents=True)
    preview = output / f"{recipe_raw['recipe_id']}_4k.png"
    preview.write_bytes(b"preview")
    (output / "class_inventory.csv").write_text("class_code,pixel_count\n1,1\n")
    return preview


def _prepare_execution_fakes(monkeypatch, tmp_path, recipe, missing=()):
    sources = tmp_path / "sources"
    inventory = []
    for name in recipe.required_assets:
        if name in missing:
            continue
        path = sources / name / "data" / f"{name}.tif"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
        inventory.append(record(name, path, handoff=str(path.parent.parent)))
    monkeypatch.setattr(ag_execution, "discover_cached_assets", lambda *args, **kwargs: inventory)
    monkeypatch.setattr(ag_execution, "_warp_reused_asset", lambda source, destination, bbox, resampling: destination.write_bytes(source.read_bytes()))

    def verify(staging, recipe_value, bbox, year):
        paths = ag_execution._find_resolved_paths(staging, year, recipe_value.required_assets)
        return {
            name: replace(record(name, path), local_path=str(path), originating_handoff=str(staging))
            for name, path in paths.items()
        }

    monkeypatch.setattr(ag_execution, "_verify_resolved", verify)
    return inventory


def test_reuse_only_executes_with_zero_network_and_accurate_receipt(tmp_path, monkeypatch, recipe, recipe_root):
    _prepare_execution_fakes(monkeypatch, tmp_path, recipe)
    monkeypatch.setattr(
        ag_execution,
        "_run_selective_acquisition",
        lambda *args, **kwargs: pytest.fail("reuse-only must make zero acquisition calls"),
    )
    raw = json.loads((recipe_root / "recipes/ag/crop_vigor_classification.json").read_text())
    execute_recipe(
        tmp_path,
        recipe=recipe,
        recipe_raw=raw,
        name="reuse_only",
        bbox=BBOX,
        start="2023-04-01",
        end="2023-10-31",
        year=2023,
        reuse_mode="only",
        open_preview=False,
        max_total_bytes=1_000_000,
        service_tile_size=100,
        renderer=_fake_renderer,
    )
    receipt_path = next((tmp_path / "outputs/handoffs").glob("reuse_only_*/preview/*/recipe_receipt.json"))
    receipt = json.loads(receipt_path.read_text())
    assert receipt["total_network_bytes"] == 0
    assert {item["action"] for item in receipt["assets"]} == {"reuse_direct"}
    assert all(item["validation_result"] == "PASS" for item in receipt["assets"])
    handoff = receipt_path.parents[2]
    plan = json.loads((handoff / "asset_plan.json").read_text())
    manifest = json.loads((handoff / "manifest.json").read_text())
    assert plan["published_handoff_id"] == handoff.name
    assert receipt["published_handoff_id"] == handoff.name
    assert all("source_handoff_id" in item for item in receipt["assets"])
    assert all("source_handoff" not in item and "source_path" not in item for item in receipt["assets"])
    assert all("source_handoff_id" in layer for layer in manifest["layers"])
    for item in plan["assets"]:
        candidate = item["candidate"]
        assert candidate is not None
        assert "source_handoff_id" in candidate and "source_relative_path" in candidate
        assert "local_path" not in candidate and "originating_handoff" not in candidate
    published_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in handoff.rglob("*")
        if path.is_file() and path.suffix in {".json", ".md", ".txt", ".csv"}
    )
    assert ".staging-" not in published_text


def test_reuse_only_missing_asset_fails_without_network_or_completed_output(tmp_path, monkeypatch, recipe, recipe_root):
    _prepare_execution_fakes(monkeypatch, tmp_path, recipe, missing={"ndvi"})
    monkeypatch.setattr(
        ag_execution,
        "_run_selective_acquisition",
        lambda *args, **kwargs: pytest.fail("reuse-only must make zero acquisition calls"),
    )
    raw = json.loads((recipe_root / "recipes/ag/crop_vigor_classification.json").read_text())
    with pytest.raises(RecipeExecutionError, match="ndvi"):
        execute_recipe(
            tmp_path, recipe=recipe, recipe_raw=raw, name="missing", bbox=BBOX,
            start="2023-04-01", end="2023-10-31", year=2023, reuse_mode="only",
            open_preview=False, max_total_bytes=1_000_000, service_tile_size=100,
            renderer=_fake_renderer,
        )
    assert not list((tmp_path / "outputs/handoffs").glob("missing_*"))


def test_selective_execution_requests_only_missing_ndvi(tmp_path, monkeypatch, recipe, recipe_root):
    _prepare_execution_fakes(monkeypatch, tmp_path, recipe, missing={"ndvi"})
    requested = []

    def acquire(root, staging, assets, **kwargs):
        requested.extend(assets)
        path = staging / "data/naip_2023_ndvi_color.cog.tif"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"ndvi")
        return {"network_bytes": 4, "requests": [], "layers": []}

    monkeypatch.setattr(ag_execution, "_run_selective_acquisition", acquire)
    raw = json.loads((recipe_root / "recipes/ag/crop_vigor_classification.json").read_text())
    execute_recipe(
        tmp_path, recipe=recipe, recipe_raw=raw, name="selective", bbox=BBOX,
        start="2023-04-01", end="2023-10-31", year=2023, reuse_mode="auto",
        open_preview=False, max_total_bytes=1_000_000, service_tile_size=100,
        renderer=_fake_renderer,
    )
    assert requested == ["ndvi"]
