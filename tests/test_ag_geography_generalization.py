from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from faster_raster import ag_execution
from faster_raster.ag_assets import AssetRecord, compile_asset_plan, inspect_asset
from faster_raster.ag_execution import RecipeExecutionError, execute_recipe
from faster_raster.ag_geography import (
    BBoxValidationError,
    SourceCoverageError,
    asset_safety_profile,
    normalize_gdalinfo_paths,
    parse_requested_assets,
    required_source_families,
    validate_3dep_catalog,
    validate_aoi_safety,
    validate_bbox,
    validate_bbox_text,
    validate_cdl_catalog,
    validate_naip_catalog,
    validate_service_extent,
)
from faster_raster.ag_recipes import load_named_recipe
from faster_raster.workfiles import load_workfile


ROOT = Path(__file__).resolve().parent.parent
IDAHO = (-116.410, 43.540, -116.380, 43.570)
KANSAS = (-101.065, 39.360, -101.045, 39.380)
OHIO = (-83.010, 40.000, -82.980, 40.030)


def feature(year: int, resolution: float = 0.6) -> dict:
    return {
        "attributes": {
            "OBJECTID": year,
            "Name": f"source-{year}",
            "Year": year,
            "resolution_value": resolution,
            "resolution_units": "meters",
        }
    }


def response(*years: int) -> dict:
    return {"features": [feature(year) for year in years]}


def geographic_metadata(extent=(-125.0, 24.0, -66.0, 50.0)) -> dict:
    return {
        "fullExtent": {
            "xmin": extent[0],
            "ymin": extent[1],
            "xmax": extent[2],
            "ymax": extent[3],
            "spatialReference": {"wkid": 4326},
        }
    }


def record(
    name: str,
    path: Path,
    *,
    bbox=IDAHO,
    year: int | None = 2023,
    handoff: str | None = None,
) -> AssetRecord:
    return AssetRecord(
        asset_name=name,
        source_family=(
            "USDA_CDL"
            if name.startswith("cdl")
            else "USGS_3DEP"
            if name == "hillshade"
            else "USGS_NAIP"
        ),
        temporal_key=None if name == "hillshade" else year,
        bbox_epsg_4326=bbox,
        extent_native=(0.0, 0.0, 10.0, 10.0),
        crs="EPSG:3857",
        pixel_size=(1.0, 1.0),
        pixel_size_m=1.0,
        width=10,
        height=10,
        nodata=(None,),
        semantic_type="categorical" if name.startswith("cdl") else "continuous",
        checksum=None,
        local_path=str(path),
        originating_handoff=handoff or str(path.parent.parent),
        validation_state="valid",
        validation_errors=(),
        can_crop_locally=True,
        requires_reprojection=False,
    )


def test_valid_kansas_bbox_remains_accepted():
    assert validate_bbox(KANSAS) == KANSAS


def test_valid_idaho_bbox_is_accepted():
    assert validate_bbox(IDAHO) == IDAHO


def test_valid_ohio_bbox_is_accepted():
    assert validate_bbox(OHIO) == OHIO


def test_equal_west_east_is_rejected():
    with pytest.raises(BBoxValidationError, match="nonzero"):
        validate_bbox((-100.0, 40.0, -100.0, 41.0))


def test_reversed_latitude_order_is_rejected():
    with pytest.raises(BBoxValidationError, match="south < north"):
        validate_bbox((-100.0, 41.0, -99.0, 40.0))


def test_longitude_outside_range_is_rejected():
    with pytest.raises(BBoxValidationError, match="longitude"):
        validate_bbox((-181.0, 40.0, -99.0, 41.0))


def test_latitude_outside_range_is_rejected():
    with pytest.raises(BBoxValidationError, match="latitude"):
        validate_bbox((-100.0, -91.0, -99.0, 41.0))


def test_nonfinite_coordinates_are_rejected():
    with pytest.raises(BBoxValidationError, match="finite"):
        validate_bbox((-100.0, 40.0, math.inf, 41.0))


def test_nonnumeric_coordinates_are_rejected():
    with pytest.raises(BBoxValidationError, match="numeric"):
        validate_bbox_text("-100,forty,-99,41")


def test_antimeridian_crossing_is_explicitly_rejected():
    with pytest.raises(BBoxValidationError, match="antimeridian"):
        validate_bbox((170.0, 10.0, -170.0, 11.0))


def test_web_mercator_execution_limit_is_explicit():
    with pytest.raises(BBoxValidationError, match="EPSG:3857"):
        validate_bbox((-10.0, 85.0, -9.0, 86.0))


def test_aoi_safety_rejects_oversized_request_before_source_access():
    with pytest.raises(BBoxValidationError, match="safety envelope"):
        validate_aoi_safety(
            (-120.0, 30.0, -70.0, 50.0),
            maximum_network_bytes=1_000_000,
            asset_resolutions=asset_safety_profile({"natural", "ndvi"}, 0.6),
        )


def test_meridian_aoi_safety_accepts_bounded_recipe_assets():
    evidence = validate_aoi_safety(
        IDAHO,
        maximum_network_bytes=250_000_000,
        asset_resolutions=asset_safety_profile(
            {"natural", "ndvi", "cdl_classes"}, 1.0
        ),
    )
    assert evidence["estimated_uncompressed_asset_bytes"] < 250_000_000 * 16


def test_unknown_asset_is_rejected_before_source_access():
    with pytest.raises(BBoxValidationError, match="unknown"):
        parse_requested_assets(True, "natural,state_boundary")


def test_no_state_boundary_controls_active_source_eligibility():
    active = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "scripts/fr-cook-ag",
            "faster_raster/ag_execution.py",
            "faster_raster/ag_assets.py",
            "faster_raster/ag_geography.py",
        )
    ).lower()
    assert "not in kansas" not in active
    assert "kansas bbox" not in active
    assert "state_boundary" not in active


def test_naip_intersecting_catalog_permits_acquisition():
    result = validate_naip_catalog(response(2023), requested_year=2023)
    assert result["status"] == "PASS"
    assert result["catalog_match_count"] == 1


def test_naip_no_intersection_is_clear_coverage_failure():
    with pytest.raises(SourceCoverageError) as failure:
        validate_naip_catalog(
            response(),
            requested_year=2023,
            available_response=response(2021, 2022),
        )
    assert failure.value.code == "no_intersecting_imagery"


def test_naip_wrong_year_is_not_substituted():
    with pytest.raises(SourceCoverageError) as failure:
        validate_naip_catalog(response(2022), requested_year=2023)
    assert failure.value.code == "wrong_year_response"


def test_naip_available_year_evidence_is_reported():
    with pytest.raises(SourceCoverageError) as failure:
        validate_naip_catalog(
            response(),
            requested_year=2023,
            available_response=response(2019, 2021, 2021),
        )
    assert failure.value.evidence == {
        "requested_year": 2023,
        "available_intersecting_years": [2019, 2021],
    }


def test_naip_source_native_resolution_is_preserved():
    result = validate_naip_catalog(
        {"features": [feature(2023, 1.0), feature(2023, 0.6)]},
        requested_year=2023,
    )
    assert result["source_native_resolution_meters"] == 0.6


def test_source_extent_contains_meridian():
    result = validate_service_extent(
        geographic_metadata(), IDAHO, source="USDA_CDL"
    )
    assert result["fully_contains_requested_bbox"] is True


def test_source_extent_outside_coverage_is_distinct():
    with pytest.raises(SourceCoverageError) as failure:
        validate_service_extent(
            geographic_metadata((-110.0, 24.0, -66.0, 50.0)),
            IDAHO,
            source="USDA_CDL",
        )
    assert failure.value.code == "bbox_outside_coverage"


def test_invalid_source_extent_response_is_distinct():
    with pytest.raises(SourceCoverageError) as failure:
        validate_service_extent({}, IDAHO, source="USDA_CDL")
    assert failure.value.code == "invalid_response"


def test_source_error_response_is_service_unavailable():
    with pytest.raises(SourceCoverageError) as failure:
        validate_service_extent(
            {"error": {"code": 503}}, IDAHO, source="USDA_CDL"
        )
    assert failure.value.code == "service_unavailable"


def test_cdl_unavailable_year_is_distinct_from_noncoverage():
    with pytest.raises(SourceCoverageError) as failure:
        validate_cdl_catalog(response(), response(2021, 2022), requested_year=2023)
    assert failure.value.code == "requested_year_unavailable"


def test_cdl_geographic_noncoverage_is_distinct():
    with pytest.raises(SourceCoverageError) as failure:
        validate_cdl_catalog(response(), response(2023), requested_year=2023)
    assert failure.value.code == "bbox_outside_coverage"


def test_cdl_wrong_year_response_is_rejected():
    with pytest.raises(SourceCoverageError) as failure:
        validate_cdl_catalog(response(2022), response(2023), requested_year=2023)
    assert failure.value.code == "wrong_year_response"


def test_cdl_categorical_resampling_remains_nearest():
    result = validate_cdl_catalog(response(2023), response(2023), requested_year=2023)
    assert result["categorical_resampling"] == "nearest"


def test_3dep_is_not_required_without_hillshade():
    assert required_source_families(
        {"natural", "ndvi", "cdl_classes"}
    ) == ("USGS_NAIP", "USDA_CDL")


def test_3dep_is_required_for_hillshade():
    assert "USGS_3DEP" in required_source_families({"natural", "hillshade"})


def test_3dep_catalog_coverage_preserves_bilinear_policy():
    result = validate_3dep_catalog(response(2023))
    assert result["terrain_resampling"] == "bilinear"


def test_3dep_empty_catalog_is_clear_noncoverage():
    with pytest.raises(SourceCoverageError) as failure:
        validate_3dep_catalog(response())
    assert failure.value.code == "bbox_outside_coverage"


def test_idaho_exact_cached_asset_can_be_reused(tmp_path):
    recipe = load_named_recipe(ROOT, "crop_vigor_classification")
    decision = compile_asset_plan(
        recipe, [record("natural", tmp_path / "idaho.tif")], IDAHO, 2023, "auto"
    )[0]
    assert decision.action == "reuse_direct"


def test_larger_idaho_cached_asset_can_be_cropped(tmp_path):
    recipe = load_named_recipe(ROOT, "crop_vigor_classification")
    larger = (-116.42, 43.53, -116.37, 43.58)
    decision = compile_asset_plan(
        recipe,
        [record("natural", tmp_path / "larger.tif", bbox=larger)],
        IDAHO,
        2023,
        "auto",
    )[0]
    assert decision.action == "reuse_crop"


def test_kansas_named_asset_cannot_satisfy_idaho_by_name(tmp_path):
    recipe = load_named_recipe(ROOT, "crop_vigor_classification")
    candidate = record(
        "natural",
        tmp_path / "kansas_proof" / "data" / "natural.tif",
        bbox=KANSAS,
        handoff=str(tmp_path / "kansas_proof"),
    )
    decision = compile_asset_plan(recipe, [candidate], IDAHO, 2023, "auto")[0]
    assert decision.action == "acquire"
    assert decision.rejected_candidates[0]["spatial_relationship"] == "no_overlap"


def test_gdal_extent_is_authoritative_over_directory_name(tmp_path):
    path = tmp_path / "kansas_named_handoff" / "data" / "naip_2023_natural_color.cog.tif"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"raster-placeholder")
    gdal = {
        "size": [10, 10],
        "geoTransform": [-116.410, 0.003, 0, 43.570, 0, -0.003],
        "coordinateSystem": {"wkt": 'GEOGCRS["WGS 84",ID["EPSG",4326]]'},
        "wgs84Extent": {
            "type": "Polygon",
            "coordinates": [[
                [-116.410, 43.540],
                [-116.380, 43.540],
                [-116.380, 43.570],
                [-116.410, 43.570],
                [-116.410, 43.540],
            ]],
        },
        "bands": [{}],
    }
    inspected = inspect_asset(
        path,
        "natural",
        path.parents[1],
        gdalinfo=lambda *args, **kwargs: SimpleNamespace(
            stdout=json.dumps(gdal)
        ),
    )
    assert inspected.bbox_epsg_4326 == IDAHO


def test_cross_handoff_assembly_is_state_independent(tmp_path):
    recipe = load_named_recipe(ROOT, "crop_vigor_classification")
    assets = [
        record("natural", tmp_path / "alpha/data/n.tif", handoff=str(tmp_path / "alpha")),
        record("ndvi", tmp_path / "bravo/data/v.tif", handoff=str(tmp_path / "bravo")),
        record("cdl_classes", tmp_path / "charlie/data/c.tif", handoff=str(tmp_path / "charlie")),
    ]
    decisions = compile_asset_plan(recipe, assets, IDAHO, 2023, "auto")
    assert {item.action for item in decisions} == {"reuse_direct"}
    assert len({item.candidate.originating_handoff for item in decisions}) == 3


def test_active_preview_and_terminal_presentation_is_neutral():
    active = (ROOT / "scripts/fr-cook-ag").read_text(encoding="utf-8")
    assert "FasterRaster Cook — Agricultural Study" in active
    assert "agricultural_inspection_4k.png" in active
    assert "FASTERRASTER AGRICULTURAL COOK" in active
    assert "KANSAS AGRICULTURAL COOK" not in active


def test_gdal_verification_paths_are_handoff_relative(tmp_path):
    staging = tmp_path / ".meridian.staging-1234"
    source = staging / "data" / "natural.tif"
    normalized = normalize_gdalinfo_paths(
        {
            "description": str(source),
            "files": [str(source), "/vsimem/external-mask"],
            "size": [10, 10],
        },
        staging,
    )
    assert normalized["description"] == "data/natural.tif"
    assert normalized["files"] == [
        "data/natural.tif",
        "/vsimem/external-mask",
    ]
    assert ".staging-" not in json.dumps(normalized)


def test_renderer_does_not_claim_completion_before_publication():
    renderer = (
        ROOT / "scripts/lib/fr_ag_recipe_runtime.py"
    ).read_text(encoding="utf-8")
    publisher = (ROOT / "faster_raster/ag_execution.py").read_text(
        encoding="utf-8"
    )
    assert "AG RECIPE OUTPUTS: READY FOR PUBLICATION" in renderer
    assert "FASTERRASTER AG RECIPE: PASS" not in renderer
    assert "FASTERRASTER AG RECIPE: PASS" in publisher


def test_timestamped_backup_scripts_are_unchanged():
    expected = {
        "fr-cook-ag.backup.20260714T203054Z": (
            "ee5ad5ba7fd8804371066e1469a306bf117bf142cadf6bf785e93ea8db9decfc"
        ),
        "fr-cook-ag.backup.20260714T203213Z": (
            "1b888348dca0cae1a7a630517c0fe71e49012e72aff9155b700c489b3dacd4ee"
        ),
    }
    for name, digest in expected.items():
        assert hashlib.sha256((ROOT / "scripts" / name).read_bytes()).hexdigest() == digest


def test_legacy_direct_nonrecipe_help_remains_compatible():
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/fr-cook-ag"), "--help"],
        check=False,
        capture_output=True,
        text=True,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert completed.returncode == 0
    assert "--preflight-only" in completed.stdout


def test_meridian_workfile_validates_with_original_2023_request():
    loaded = load_workfile(
        ROOT / "examples/meridian-mixed-urban-ag.fr.md",
        repository_root=ROOT,
    )
    assert loaded.spec.area.bbox == IDAHO
    assert loaded.spec.time.crop_year == 2023
    assert loaded.spec.workflow_id == "irrigation_field_structure"


def test_workfile_planning_accepts_meridian_without_network(
    tmp_path, monkeypatch, capsys
):
    from faster_raster.fr_cli import main

    monkeypatch.setenv("FASTERRASTER_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("FASTERRASTER_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("FASTERRASTER_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("FASTERRASTER_TEMP_HOME", str(tmp_path / "temp"))
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: pytest.fail("offline planning contacted network"),
    )
    code = main(
        [
            "plan",
            str(ROOT / "examples/meridian-mixed-urban-ag.fr.md"),
            "--out",
            str(tmp_path / "plan"),
            "--json",
        ]
    )
    output = capsys.readouterr().out
    plan = json.loads(output)
    assert code in {0, 2}
    assert plan["study_name"] == "meridian-mixed-urban-ag-2023"
    assert re.search(r"\bkansas\b", output.lower()) is None
    assert plan["artifacts"]["directory"].startswith(str(tmp_path))


def _prepare_receipt_fakes(monkeypatch, tmp_path, recipe):
    inventory = []
    for name in recipe.required_assets:
        path = tmp_path / f"idaho-source-{name}" / "data" / f"{name}.tif"
        path.parent.mkdir(parents=True)
        path.write_bytes(name.encode())
        inventory.append(record(name, path, handoff=str(path.parent.parent)))
    monkeypatch.setattr(
        ag_execution, "discover_cached_assets", lambda *args, **kwargs: inventory
    )
    monkeypatch.setattr(
        ag_execution,
        "_warp_reused_asset",
        lambda source, destination, bbox, resampling: destination.write_bytes(
            source.read_bytes()
        ),
    )

    def verify(staging, recipe_value, bbox, year):
        paths = ag_execution._find_resolved_paths(
            staging, year, recipe_value.required_assets
        )
        return {
            name: replace(
                record(name, path),
                local_path=str(path),
                originating_handoff=str(staging),
            )
            for name, path in paths.items()
        }

    monkeypatch.setattr(ag_execution, "_verify_resolved", verify)


def _renderer(
    root,
    handoff,
    recipe_raw,
    compatibility,
    name,
    bbox,
    start,
    end,
    year,
    open_preview,
):
    output = handoff / "preview" / recipe_raw["recipe_id"]
    output.mkdir(parents=True)
    preview = output / f"{recipe_raw['recipe_id']}_4k.png"
    preview.write_bytes(b"preview")
    (output / "class_inventory.csv").write_text(
        "class_code,pixel_count\n1,1\n", encoding="utf-8"
    )
    return preview


def test_published_idaho_receipt_has_no_false_kansas_label(tmp_path, monkeypatch):
    recipe = load_named_recipe(ROOT, "crop_vigor_classification")
    _prepare_receipt_fakes(monkeypatch, tmp_path, recipe)
    raw = json.loads(
        (ROOT / "recipes/ag/crop_vigor_classification.json").read_text(
            encoding="utf-8"
        )
    )
    preview = execute_recipe(
        tmp_path,
        recipe=recipe,
        recipe_raw=raw,
        name="meridian_receipt",
        bbox=IDAHO,
        start="2023-04-01",
        end="2023-10-31",
        year=2023,
        reuse_mode="only",
        open_preview=False,
        max_total_bytes=1_000_000,
        service_tile_size=100,
        renderer=_renderer,
    )
    receipt = preview.parent / "recipe_receipt.json"
    assert "kansas" not in receipt.read_text(encoding="utf-8").lower()
    assert ".staging-" not in receipt.read_text(encoding="utf-8")


def _execute_coverage_failure(tmp_path, monkeypatch) -> Path:
    recipe = load_named_recipe(ROOT, "crop_vigor_classification")
    raw = json.loads(
        (ROOT / "recipes/ag/crop_vigor_classification.json").read_text(
            encoding="utf-8"
        )
    )
    monkeypatch.setattr(
        ag_execution,
        "_run_selective_acquisition",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RecipeExecutionError("USGS_NAIP coverage failure")
        ),
    )
    with pytest.raises(RecipeExecutionError, match="coverage failure"):
        execute_recipe(
            tmp_path,
            recipe=recipe,
            recipe_raw=raw,
            name="meridian_no_coverage",
            bbox=IDAHO,
            start="2023-04-01",
            end="2023-10-31",
            year=2023,
            reuse_mode="never",
            open_preview=False,
            max_total_bytes=1_000_000,
            service_tile_size=100,
            renderer=_renderer,
        )
    return tmp_path / "outputs" / "handoffs"


def test_source_coverage_failure_is_transactional(tmp_path, monkeypatch):
    handoffs = _execute_coverage_failure(tmp_path, monkeypatch)
    failed = list(handoffs.glob(".failed-meridian_no_coverage_*"))
    assert len(failed) == 1
    report = json.loads((failed[0] / "failure_report.json").read_text())
    assert report["completed_handoff_created"] is False


def test_no_completed_looking_handoff_after_coverage_failure(tmp_path, monkeypatch):
    handoffs = _execute_coverage_failure(tmp_path, monkeypatch)
    assert not list(handoffs.glob("meridian_no_coverage_*"))
