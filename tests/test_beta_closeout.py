from __future__ import annotations

import hashlib
import io
import json
import stat
import urllib.error
from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from PIL import Image
from rasterio.transform import from_origin

from faster_raster.development_sources import USDA_CDL_MAPPING
from faster_raster.human_development import (
    TargetGrid,
    analyze_common_all_epoch_footprint,
    analyze_interval,
    harmonize_epoch,
)
from faster_raster.human_development_cdl_preview import (
    ALL_TRANSITIONS_CHANGE_COLORS,
    DEVELOPMENT_CHANGE_COLORS,
    LEGEND_BAND_Y,
)
from faster_raster.human_development_preview import PANEL_BACKGROUND, _panel
from faster_raster.human_development_publication import (
    NetworkCeilingError,
    PublicationClient,
    PublicationError,
    PublicationOptions,
    SharedBudget,
    TileSpec,
    TileTimeoutError,
    _adaptive_tile,
    acquire_tiles,
    assemble,
    plan_tiles,
    publish_human_development_hybrid,
    query_catalog,
    render_hybrid,
    select_hotspot,
    validate_handoff,
)
from faster_raster.study_templates import (
    list_study_templates,
    render_study_template,
    show_study_template,
)
from faster_raster.workfiles import load_workfile


def write_tif(
    path: Path,
    values: np.ndarray,
    *,
    transform: Affine | None = None,
    crs: str = "EPSG:5070",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    array = values if values.ndim == 3 else values[np.newaxis, ...]
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=array.shape[2],
        height=array.shape[1],
        count=array.shape[0],
        dtype=array.dtype,
        crs=crs,
        transform=transform or from_origin(0, array.shape[1] * 30, 30, 30),
        nodata=0 if array.shape[0] == 1 else None,
    ) as sink:
        sink.write(array)


def checksums(root: Path) -> None:
    lines = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "checksums.sha256":
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            lines.append(f"{digest}  {path.relative_to(root).as_posix()}")
    (root / "checksums.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture()
def unequal_epochs(tmp_path: Path):
    grid = TargetGrid("EPSG:5070", from_origin(0, 60, 30, 30), 3, 2, 30, (0, 0))
    arrays = [
        np.array([[121, 1, 1], [121, 1, 1]], dtype=np.uint8),
        np.array([[121, 121, 1], [0, 1, 1]], dtype=np.uint8),
        np.array([[124, 121, 1], [124, 0, 1]], dtype=np.uint8),
    ]
    results = []
    for year, values in zip((2008, 2016, 2021), arrays):
        source = tmp_path / f"source-{year}.tif"
        write_tif(source, values, transform=grid.transform)
        results.append(harmonize_epoch(
            year=year,
            land_cover_path=source,
            imperviousness_path=None,
            destination=tmp_path / "epochs" / str(year),
            grid=grid,
            window_size=16,
            mapping=USDA_CDL_MAPPING,
        ))
    return grid, results


def test_common_all_epoch_mask_and_metrics_reconcile(unequal_epochs, tmp_path: Path):
    grid, results = unequal_epochs
    common = analyze_common_all_epoch_footprint(
        epoch_results=results,
        destination=tmp_path / "common",
        grid=grid,
        window_size=16,
        mapping=USDA_CDL_MAPPING,
    )
    with rasterio.open(common["mask"]) as source:
        mask = source.read((1,))[0]
    assert mask.tolist() == [[1, 1, 1], [0, 0, 1]]
    assert common["statistics"]["valid_footprint"]["pixels"] == 4
    developed = [
        item["common_all_epoch_footprint"]["developed_land"]["pixels"]
        for item in common["epoch_statistics"]
    ]
    assert developed == [1, 2, 2]
    assert [
        item["common_all_epoch_footprint"]["developed_land"][
            "percentage_of_common_footprint"
        ]
        for item in common["epoch_statistics"]
    ] == [25.0, 50.0, 50.0]


def test_pairwise_masks_and_code_seven_are_unchanged(unequal_epochs, tmp_path: Path):
    grid, results = unequal_epochs
    before = analyze_interval(
        before_year=2008,
        after_year=2016,
        before_land_cover=results[0]["land_cover"],
        after_land_cover=results[1]["land_cover"],
        before_imperviousness=None,
        after_imperviousness=None,
        destination=tmp_path / "pair",
        grid=grid,
        window_size=16,
        mapping=USDA_CDL_MAPPING,
    )
    pair_pixels = before["statistics"]["valid_comparison"]["pixels"]
    code_seven = before["statistics"]["change_codes"]["7"]["pixels"]
    analyze_common_all_epoch_footprint(
        epoch_results=results,
        destination=tmp_path / "common2",
        grid=grid,
        window_size=16,
        mapping=USDA_CDL_MAPPING,
    )
    assert before["statistics"]["valid_comparison"]["pixels"] == pair_pixels == 5
    assert before["statistics"]["change_codes"]["7"]["pixels"] == code_seven


def test_preview_emphasis_only_mutes_code_seven():
    assert DEVELOPMENT_CHANGE_COLORS[7] != ALL_TRANSITIONS_CHANGE_COLORS[7]
    for code in range(7):
        assert DEVELOPMENT_CHANGE_COLORS[code] == ALL_TRANSITIONS_CHANGE_COLORS[code]
    assert 985 < LEGEND_BAND_Y < 1085


def test_categorical_panel_padding_uses_panel_background():
    from PIL import ImageDraw

    canvas = Image.new("RGB", (240, 260), (0, 0, 0))
    tall = Image.new("RGB", (20, 100), (200, 20, 20))
    _panel(canvas, ImageDraw.Draw(canvas), (0, 0, 220, 250), "Title", tall, "Subtitle")
    assert canvas.getpixel((30, 110)) == PANEL_BACKGROUND


def test_template_listing_show_and_deterministic_generation(tmp_path: Path):
    ids = [item["template_id"] for item in list_study_templates()]
    assert ids == sorted(ids)
    assert set(ids) == {
        "human-development-cdl",
        "human-development-cdl-reuse",
        "ag-cdl-naip",
        "generic-cog",
    }
    assert show_study_template("human-development-cdl") == show_study_template(
        "human-development-cdl"
    )
    path = tmp_path / "custom.fr.md"
    path.write_text(
        render_study_template(
            "human-development-cdl",
            name="custom",
            bbox=(-116.5, 43.5, -116.4, 43.6),
            years=(2008, 2016, 2021),
        ),
        encoding="utf-8",
    )
    workfile = load_workfile(path, repository_root=Path.cwd())
    assert tuple(workfile.spec.area.bbox) == (-116.5, 43.5, -116.4, 43.6)
    assert [item.year for item in workfile.spec.epochs] == [2008, 2016, 2021]


def test_strict_reuse_template_and_invalid_template(tmp_path: Path):
    path = tmp_path / "reuse.fr.md"
    path.write_text(
        render_study_template("human-development-cdl-reuse"),
        encoding="utf-8",
    )
    spec = load_workfile(path, repository_root=Path.cwd()).spec
    assert spec.data.reuse == "only"
    assert spec.data.allow_network is False
    with pytest.raises(ValueError, match="unknown template"):
        render_study_template("not-real")


def synthetic_handoff(tmp_path: Path) -> Path:
    root = tmp_path / "handoff"
    transform = from_origin(0, 120, 30, 30)
    before = np.array(
        [[1, 1, 1, 1], [1, 121, 121, 1], [1, 121, 1, 1], [1, 1, 1, 1]],
        dtype=np.uint8,
    )
    after = np.array(
        [[1, 1, 1, 1], [1, 124, 124, 1], [1, 124, 121, 1], [1, 1, 1, 1]],
        dtype=np.uint8,
    )
    write_tif(root / "data/epochs/2008/land_cover.tif", before, transform=transform)
    write_tif(root / "data/epochs/2021/land_cover.tif", after, transform=transform)
    write_tif(
        root / "analysis/endpoint/2008_2021/change_codes.tif",
        np.array(
            [[1, 1, 1, 1], [1, 5, 5, 1], [1, 5, 2, 1], [1, 1, 1, 1]],
            dtype=np.uint8,
        ),
        transform=transform,
    )
    stats = {
        "before_year": 2008,
        "after_year": 2021,
        "valid_comparison": {"pixels": 16, "hectares": 1.44},
        "gross_development_gain": {"hectares": 0.0},
        "apparent_development_loss": {"hectares": 0.0},
        "net_development_change": {"hectares": 0.0},
        "transition_reconciliation": {"reconciles": True},
    }
    endpoint = root / "analysis/endpoint/2008_2021"
    endpoint.mkdir(parents=True, exist_ok=True)
    (endpoint / "statistics.json").write_text(json.dumps(stats), encoding="utf-8")
    (endpoint / "source_transition_matrix.json").write_text(
        json.dumps({
            "rows": [{
                "baseline_source_class": 121,
                "baseline_class_label": "Developed/Open Space",
                "comparison_source_class": 124,
                "comparison_class_label": "Developed/High Intensity",
                "pixel_count": 3,
            }]
        }),
        encoding="utf-8",
    )
    mapping = {**USDA_CDL_MAPPING.as_dict(), "sha256": USDA_CDL_MAPPING.sha256}
    (root / "source_mapping_contract.json").write_text(
        json.dumps(mapping), encoding="utf-8"
    )
    (root / "methodology_receipt.json").write_text(
        json.dumps({"source_qualification": "Synthetic CDL proxy limitation."}),
        encoding="utf-8",
    )
    receipt = {
        "final_status": "PASS",
        "workflow": "human_development_change",
        "source_mapping_contract": "source_mapping_contract.json",
        "mapping_contract_sha256": USDA_CDL_MAPPING.sha256,
        "endpoint_comparison": {
            "statistics": "analysis/endpoint/2008_2021/statistics.json"
        },
        "epochs": [2008, 2021],
        "target_grid": {
            "crs": "EPSG:5070",
            "transform": list(transform)[:6],
            "width": 4,
            "height": 4,
        },
        "requested_bbox_epsg_4326": [-116.5, 43.5, -116.4, 43.6],
        "methodology_receipt": "methodology_receipt.json",
    }
    (root / "workflow_receipt.json").write_text(
        json.dumps(receipt), encoding="utf-8"
    )
    (root / "manifest.json").write_text(
        json.dumps({
            "operation_status": "completed",
            "verification_status": "PASS",
            "workflow": "human_development_change",
            "workflow_receipt": "workflow_receipt.json",
            "methodology_receipt": "methodology_receipt.json",
        }),
        encoding="utf-8",
    )
    checksums(root)
    return root


def test_publication_handoff_validation_and_invalid_checksum(tmp_path: Path):
    handoff = synthetic_handoff(tmp_path)
    assert validate_handoff(handoff)["handoff_id"] == "handoff"
    path = handoff / "analysis/endpoint/2008_2021/change_codes.tif"
    path.write_bytes(path.read_bytes() + b"x")
    with pytest.raises(PublicationError, match="checksum"):
        validate_handoff(handoff)


def test_mapping_contract_controls_developed_replacement(tmp_path: Path):
    imagery = np.full((3, 2, 2), 90, dtype=np.uint8)
    write_tif(
        tmp_path / "imagery.tif", imagery, transform=from_origin(0, 2, 1, 1)
    )
    classes = np.array([[9, 1], [1, 9]], dtype=np.uint8)
    write_tif(
        tmp_path / "classes.tif", classes, transform=from_origin(0, 2, 1, 1)
    )
    mapping = {
        "mapping_id": "test",
        "source_id": "test",
        "source_semantic_type": "test",
        "nodata_code": 0,
        "valid_codes": [1, 9],
        "invalid_codes": [0],
        "developed_ranks": {"9": 1},
        "class_labels": {"1": "other", "9": "developed"},
        "scientific_claim": "test",
    }
    receipt = render_hybrid(
        tmp_path / "imagery.tif",
        tmp_path / "classes.tif",
        tmp_path / "hybrid.png",
        "developed-state",
        mapping,
    )
    assert receipt["imagery_replacement_classes"] == [9]
    output = np.asarray(Image.open(tmp_path / "hybrid.png"))
    assert tuple(output[0, 0]) == (90, 90, 90)
    assert tuple(output[0, 1]) != (90, 90, 90)


def test_change_hybrid_uses_codes_three_through_six(tmp_path: Path):
    write_tif(
        tmp_path / "imagery.tif",
        np.full((3, 2, 4), 80, dtype=np.uint8),
        transform=from_origin(0, 2, 1, 1),
    )
    write_tif(
        tmp_path / "change.tif",
        np.array([[2, 3, 4, 5], [6, 7, 1, 0]], dtype=np.uint8),
        transform=from_origin(0, 2, 1, 1),
    )
    receipt = render_hybrid(
        tmp_path / "imagery.tif",
        tmp_path / "change.tif",
        tmp_path / "hybrid.png",
        "change",
        {},
    )
    assert receipt["imagery_replacement_classes"] == [3, 4, 5, 6]
    assert receipt["imagery_replaced_pixels"] == 4


def test_hotspot_selection_is_deterministic(tmp_path: Path):
    values = np.ones((4, 4), dtype=np.uint8)
    values[2:, 2:] = 3
    path = tmp_path / "change.tif"
    write_tif(path, values, transform=from_origin(0, 120, 30, 30))
    first = select_hotspot(path, 60)
    assert first == select_hotspot(path, 60)
    assert (first["coarse_row"], first["coarse_column"]) == (2, 2)


class FakeCatalogClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def json(self, endpoint, params, label):
        self.calls.append((endpoint, params, label))
        return self.response


def test_exact_year_catalog_and_silent_substitution_rejection():
    client = FakeCatalogClient({
        "features": [{
            "attributes": {
                "OBJECTID": 7,
                "Name": "tile",
                "Year": 2021,
                "resolution_value": 0.6,
                "resolution_units": "meters",
            }
        }]
    })
    evidence = query_catalog(client, (-116.5, 43.5, -116.4, 43.6), 2021)
    assert evidence["record_ids"] == [7]
    assert evidence["silent_year_substitution_allowed"] is False
    assert client.calls[0][1]["where"] == "Year = 2021"
    wrong = FakeCatalogClient({
        "features": [{
            "attributes": {
                "OBJECTID": 8,
                "Name": "wrong",
                "Year": 2020,
                "resolution_value": 0.6,
                "resolution_units": "meters",
            }
        }]
    })
    with pytest.raises(PublicationError):
        query_catalog(wrong, (-116.5, 43.5, -116.4, 43.6), 2021)


def test_thread_safe_byte_ceiling():
    budget = SharedBudget(10)
    budget.add(6)
    with pytest.raises(NetworkCeilingError):
        budget.add(5)
    assert budget.total == 6

def test_transient_http_error_is_retried():
    calls = []
    sleeps = []

    def opener(request, timeout):
        calls.append((request, timeout))
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, None)
        response = io.BytesIO(b'{"ok":true}')
        response.headers = {"Content-Type": "application/json"}
        return response

    client = PublicationClient(
        1024,
        True,
        opener=opener,
        sleeper=sleeps.append,
        attempts=3,
    )
    payload = client.request(
        "https://example.invalid/query", {}, label="catalog", raster=False
    )
    assert payload == b'{"ok":true}'
    assert len(calls) == 2
    assert sleeps == [1]
    assert client.requests[0]["attempts"] == 2


def test_quick_smoke_script_contract():
    path = Path("scripts/fr-beta-smoke")
    contents = path.read_text(encoding="utf-8")
    assert path.stat().st_mode & stat.S_IXUSR
    assert "--quick" in contents and "--full" in contents
    assert "index_hash_before" in contents and "index_hash_after" in contents
    assert "--allow-network" not in contents



def test_worker_receipt_order_and_no_duplicate_tile_ids(tmp_path: Path, monkeypatch):
    tiles, transform, width, height = plan_tiles((0, 0, 4, 4), 1, tile_size=2)

    def fake(client, tile, year, resolution, directory, maximum_depth=2):
        values = np.full((3, tile.height, tile.width), tile.row_off + tile.col_off)
        path = directory / f"{tile.tile_id}.tif"
        write_tif(path, values, transform=Affine(1, 0, tile.bounds[0], 0, -1, tile.bounds[3]))
        return {
            "tile_id": tile.tile_id,
            "row_offset": tile.row_off,
            "column_offset": tile.col_off,
            "width": tile.width,
            "height": tile.height,
            "output": path.name,
        }

    monkeypatch.setattr(
        "faster_raster.human_development_publication._adaptive_tile", fake
    )
    one = acquire_tiles(object(), tiles, 2021, 1, tmp_path / "one", 1)
    two = acquire_tiles(object(), tiles, 2021, 1, tmp_path / "two", 2)
    assert [item["tile_id"] for item in one] == [item["tile_id"] for item in two]
    assemble(tmp_path / "one.tif", one, tmp_path / "one", transform, width, height)
    assemble(tmp_path / "two.tif", two, tmp_path / "two", transform, width, height)
    with rasterio.open(tmp_path / "one.tif") as a, rasterio.open(tmp_path / "two.tif") as b:
        assert np.array_equal(a.read(), b.read())
    with pytest.raises(PublicationError, match="duplicate"):
        acquire_tiles(object(), [tiles[0], tiles[0]], 2021, 1, tmp_path / "bad", 1)


def test_adaptive_subdivision(tmp_path: Path, monkeypatch):
    calls = []

    def fake(client, tile, year, resolution, destination):
        calls.append(tile.tile_id)
        if tile.depth == 0:
            raise TileTimeoutError("timeout")
        values = np.full((3, tile.height, tile.width), 5, dtype=np.uint8)
        write_tif(
            destination,
            values,
            transform=Affine(1, 0, tile.bounds[0], 0, -1, tile.bounds[3]),
        )
        return {
            "tile_id": tile.tile_id,
            "row_offset": tile.row_off,
            "column_offset": tile.col_off,
            "width": tile.width,
            "height": tile.height,
            "attempts": 1,
            "network_bytes": values.size,
            "output": destination.name,
        }

    monkeypatch.setattr(
        "faster_raster.human_development_publication._download_tile", fake
    )
    tile = TileSpec("root", 0, 0, 4, 4, (0, 0, 4, 4))
    receipt = _adaptive_tile(object(), tile, 2021, 1, tmp_path)
    assert receipt["status"] == "DOWNLOADED_AFTER_SUBDIVISION"
    assert len(receipt["children"]) == 4
    assert len(calls) == 5


def write_rgb(path: Path, width: int, height: int, transform: Affine):
    write_tif(
        path,
        np.full((3, height, width), 100, dtype=np.uint8),
        transform=transform,
    )


def test_strict_reuse_publication_zero_network_and_transaction(
    tmp_path: Path, monkeypatch
):
    handoff = synthetic_handoff(tmp_path)
    reusable = tmp_path / "reusable"
    write_rgb(
        reusable / "imagery/naip_2021_regional.tif",
        4,
        4,
        from_origin(0, 120, 30, 30),
    )
    write_rgb(
        reusable / "imagery/naip_2021_hotspot.tif",
        2,
        2,
        from_origin(0, 60, 30, 30),
    )
    (reusable / "context_catalog_evidence.json").parent.mkdir(
        parents=True, exist_ok=True
    )
    (reusable / "context_catalog_evidence.json").write_text(
        json.dumps({
            "status": "PASS",
            "source_id": "usgs_naip_imageserver",
            "requested_year": 2021,
            "record_ids": [7],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "faster_raster.human_development_publication._reuse_candidate",
        lambda *args: reusable,
    )
    publication_root = tmp_path / "publications"
    monkeypatch.setenv("FASTERRASTER_PUBLICATION_ROOT", str(publication_root))
    preview = publish_human_development_hybrid(
        Path.cwd(),
        handoff,
        PublicationOptions(
            mode="combined",
            imagery_year=2021,
            regional_resolution_m=30,
            hotspot_resolution_m=30,
            hotspot_size_m=60,
            maximum_download_mb=1,
            workers=2,
            reuse="only",
            allow_network=False,
        ),
    )
    publication = preview.parent.parent
    manifest = json.loads(
        (publication / "publication_manifest.json").read_text(encoding="utf-8")
    )
    assert Image.open(preview).size == (3840, 2160)
    assert manifest["network"]["metadata_requests"] == 0
    assert manifest["network"]["raster_requests"] == 0
    assert manifest["network"]["network_bytes"] == 0
    assert manifest["network"]["reused_bytes"] > 0
    assert manifest["verification_status"] == "PASS"
    assert not any(".staging-" in path.read_text(errors="ignore") for path in publication.rglob("*.json"))
    _verify = validate_handoff
    assert (publication / "checksums.sha256").is_file()
