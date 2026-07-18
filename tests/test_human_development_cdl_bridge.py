from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio
from PIL import Image
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from faster_raster import fr_cli
from faster_raster.ag_execution import RecipeExecutionError, _assert_no_staging_provenance
from faster_raster.ag_geography import SourceCoverageError, validate_cdl_catalog
from faster_raster.cdl_acquisition import (
    ArcGISClient,
    CacheAsset,
    acquire_cdl_epoch,
    discover_cdl_coverage,
    find_cached_cdl_asset,
    find_cached_naip_context,
    inspect_raw_cdl,
    sha256_file,
)
from faster_raster.development_sources import (
    ANNUAL_NLCD_MAPPING,
    USDA_CDL_MAPPING,
    validate_source_mapping,
)
from faster_raster.human_development import (
    HumanDevelopmentError,
    TargetGrid,
    analyze_interval,
    build_service_target_grid,
    classify_change,
    harmonize_epoch,
)
from faster_raster.human_development_cdl_preview import render_cdl_proxy_preview
from faster_raster.human_development_live import _public_resolved_config, compile_live_cdl_plan
from faster_raster.local_paths import resolve_local_paths
from faster_raster.workfiles import WorkfileError, load_workfile


ROOT = Path(__file__).resolve().parent.parent
AOI = (-116.410, 43.540, -116.380, 43.570)


def _write_tif(path: Path, values: np.ndarray, *, crs: str = "EPSG:5070", transform=None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 1 if values.ndim == 2 else values.shape[0]
    height, width = values.shape[-2:]
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=count,
        dtype=str(values.dtype),
        crs=crs,
        transform=transform or from_origin(0, height * 30, 30, 30),
        nodata=0 if count == 1 else None,
    ) as sink:
        sink.write(values if values.ndim == 3 else values[np.newaxis, ...])


def _tiff_bytes(values: np.ndarray) -> bytes:
    count = 1 if values.ndim == 2 else values.shape[0]
    height, width = values.shape[-2:]
    with MemoryFile() as memory:
        with memory.open(
            driver="GTiff",
            width=width,
            height=height,
            count=count,
            dtype=str(values.dtype),
            crs="EPSG:5070",
            transform=from_origin(0, height * 30, 30, 30),
        ) as sink:
            sink.write(values if values.ndim == 3 else values[np.newaxis, ...])
        return memory.read()


def _catalog_feature(year: int, object_id: int = 1) -> dict:
    return {
        "attributes": {
            "OBJECTID": object_id,
            "Name": f"{year}_30m_cdls",
            "Year": year,
            "MinPS": 0,
            "MaxPS": 50200,
            "LowPS": 30,
            "HighPS": 1920,
        }
    }


def test_mapping_contracts_and_shared_change_primitive() -> None:
    assert ANNUAL_NLCD_MAPPING.developed_ranks == {21: 1, 22: 2, 23: 3, 24: 4}
    assert ANNUAL_NLCD_MAPPING.invalid_codes == (250,)
    assert USDA_CDL_MAPPING.developed_ranks == {121: 1, 122: 2, 123: 3, 124: 4}
    assert USDA_CDL_MAPPING.invalid_codes == (0, 81, 255)
    before = np.array([0, 1, 121, 1, 121, 121, 124, 1, 99], dtype=np.uint8)
    after = np.array([1, 1, 121, 121, 1, 122, 121, 2, 1], dtype=np.uint8)
    assert classify_change(before, after, USDA_CDL_MAPPING).tolist() == list(range(8)) + [0]
    assert validate_source_mapping("usda_nass_cdl_imageserver", USDA_CDL_MAPPING.mapping_id) is USDA_CDL_MAPPING
    with pytest.raises(ValueError, match="incompatible"):
        validate_source_mapping("usgs_annual_nlcd", USDA_CDL_MAPPING.mapping_id)
    assert len(USDA_CDL_MAPPING.sha256) == 64


def test_declared_and_undeclared_raw_classes_are_explicit(tmp_path: Path) -> None:
    path = tmp_path / "raw.tif"
    _write_tif(path, np.array([[121, 1], [99, 255]], dtype=np.uint8))
    inspection = inspect_raw_cdl(path)
    assert inspection["observed_class_values"] == [1, 99, 121, 255]
    assert inspection["undeclared_values_classified_invalid"] == [99]
    assert inspection["valid_pixel_count"] == 2


@pytest.mark.parametrize(
    ("requested", "features", "message"),
    [
        (2016, [_catalog_feature(2015)], "no substitution"),
        (2016, [], "no catalog footprint"),
    ],
)
def test_exact_year_catalog_rejects_wrong_year_and_missing_aoi(
    requested: int, features: list[dict], message: str
) -> None:
    available = {"features": [{"attributes": {"Year": 2015}}, {"attributes": {"Year": 2016}}]}
    with pytest.raises(SourceCoverageError, match=message):
        validate_cdl_catalog({"features": features}, available, requested_year=requested)


def test_mocked_cdl_discovery_requires_exact_records_and_rat_contract() -> None:
    class FakeClient:
        total_bytes = 321
        requests = [{"label": "metadata"}]

        def json(self, endpoint, params, *, label):
            if label == "cdl_service_metadata":
                return {
                    "bandCount": 1,
                    "pixelType": "U8",
                    "noDataValue": 0,
                    "capabilities": "Catalog,Mensuration,Image,Metadata",
                    "meanPixelSize": 30,
                    "defaultResamplingMethod": "Nearest",
                }
            if label == "cdl_available_years_query":
                return {"features": [{"attributes": {"Year": year}} for year in (2008, 2016, 2021)]}
            if label == "cdl_raster_attribute_table":
                return {
                    "features": [
                        {"attributes": {"Value": code, "Class_Names": USDA_CDL_MAPPING.class_labels[code]}}
                        for code in USDA_CDL_MAPPING.valid_codes
                    ]
                }
            year = int(label.split("_")[1])
            return {"features": [_catalog_feature(year, {2008: 12, 2016: 4, 2021: 36}[year])]}

    result = discover_cdl_coverage(FakeClient(), AOI, [2008, 2016, 2021])
    assert [item["catalog_record_ids"] for item in result["epochs"]] == [[12], [4], [36]]
    assert all(item["exact_coverage_status"] == "PASS" for item in result["epochs"])
    assert result["attribute_table_developed_classes"] == {
        str(code): USDA_CDL_MAPPING.class_labels[code] for code in (121, 122, 123, 124)
    }


def test_raw_export_acceptance_rejections_and_empty_inputs(tmp_path: Path) -> None:
    accepted = tmp_path / "accepted.tif"
    _write_tif(accepted, np.array([[121, 122], [1, 124]], dtype=np.uint8))
    assert inspect_raw_cdl(accepted)["band_count"] == 1

    multiband = tmp_path / "rendered.tif"
    _write_tif(multiband, np.ones((3, 2, 2), dtype=np.uint8))
    with pytest.raises(HumanDevelopmentError, match="one-band"):
        inspect_raw_cdl(multiband)

    empty = tmp_path / "empty.tif"
    empty.touch()
    with pytest.raises(HumanDevelopmentError, match="empty"):
        inspect_raw_cdl(empty)

    invalid = tmp_path / "invalid.tif"
    _write_tif(invalid, np.zeros((2, 2), dtype=np.uint8))
    with pytest.raises(HumanDevelopmentError, match="no declared valid"):
        inspect_raw_cdl(invalid)


def test_bounded_multitile_raw_export_and_nearest_grid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_request(self, endpoint, params, *, label, raster=False):
        width, height = (int(value) for value in params["size"].split(","))
        payload = _tiff_bytes(np.full((height, width), 121, dtype=np.uint8))
        if self.total_bytes + len(payload) > self.byte_ceiling:
            raise HumanDevelopmentError("configured byte ceiling exceeded")
        self.total_bytes += len(payload)
        self.requests.append(
            {"label": label, "content_type": "image/tiff", "bytes": len(payload), "request_kind": "raster_export"}
        )
        return payload, "image/tiff"

    monkeypatch.setattr(ArcGISClient, "request", fake_request)
    grid = TargetGrid("EPSG:5070", from_origin(0, 90, 30, 30), 5, 3, 30.0, (0.0, 0.0))
    destination = tmp_path / "epoch.tif"
    receipt = acquire_cdl_epoch(
        bbox=AOI,
        year=2008,
        grid=grid,
        request_tile_ceiling=2,
        byte_ceiling=1_000_000,
        destination=destination,
        allow_network=True,
        catalog_record_ids=[12],
    )
    assert receipt["export_request_count"] == 6
    assert receipt["total_network_bytes"] > 0
    assert receipt["resampling"] == "nearest"
    assert receipt["raster"]["observed_class_values"] == [121]
    with rasterio.open(destination) as dataset:
        assert dataset.transform == grid.transform
        assert dataset.crs.to_string() == "EPSG:5070"


@pytest.mark.parametrize(
    ("payload", "content_type", "message"),
    [
        (b"<html>failure</html>", "text/html", "error document"),
        (b'{"error":"failure"}', "application/json", "error document"),
        (_tiff_bytes(np.ones((3, 2, 2), dtype=np.uint8)), "image/tiff", "multiband"),
    ],
)
def test_export_rejects_error_documents_and_rendered_pixels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
    content_type: str,
    message: str,
) -> None:
    def fake_request(self, endpoint, params, *, label, raster=False):
        return payload, content_type

    monkeypatch.setattr(ArcGISClient, "request", fake_request)
    grid = TargetGrid("EPSG:5070", from_origin(0, 60, 30, 30), 2, 2, 30.0, (0.0, 0.0))
    with pytest.raises(HumanDevelopmentError, match=message):
        acquire_cdl_epoch(
            bbox=AOI,
            year=2008,
            grid=grid,
            request_tile_ceiling=2,
            byte_ceiling=100_000,
            destination=tmp_path / "bad.tif",
            allow_network=True,
            catalog_record_ids=[12],
        )


def test_arcgis_network_permission_and_byte_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(HumanDevelopmentError, match="network is disabled"):
        ArcGISClient(byte_ceiling=10, allow_network=False).request("https://invalid", {}, label="disabled")

    class Response:
        headers = {"Content-Type": "application/octet-stream"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, _size):
            if hasattr(self, "sent"):
                return b""
            self.sent = True
            return b"1234"

    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: Response())
    with pytest.raises(HumanDevelopmentError, match="byte ceiling"):
        ArcGISClient(byte_ceiling=3, allow_network=True).request("https://example.test", {}, label="bounded")


def test_service_grid_is_deterministic_and_globally_snapped() -> None:
    first = build_service_target_grid(AOI)
    second = build_service_target_grid(AOI)
    assert first.fingerprint == second.fingerprint
    assert first.as_dict() == second.as_dict()
    assert first.crs == "EPSG:5070"
    assert first.resolution_m == 30
    assert first.transform.a == 30 and first.transform.e == -30
    assert first.transform.c % 30 == 0 and first.transform.f % 30 == 0


def test_live_workfile_is_offline_validated_and_contract_errors_are_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("validation made a network request")),
    )
    live_path = ROOT / "examples" / "meridian-cdl-development-change.fr.md"
    live = load_workfile(live_path, repository_root=ROOT)
    assert [epoch.year for epoch in live.spec.epochs] == [2008, 2016, 2021]
    assert all(epoch.land_cover_path is None for epoch in live.spec.epochs)

    text = live_path.read_text(encoding="utf-8")
    variants = [
        ("allow_network: true", "allow_network: false", "explicit data.allow_network"),
        ("source_id: usda_nass_cdl_imageserver", "source_id: untrusted_source", "source_id"),
        ("mapping_id: usda_cdl_development_proxy_v1", "mapping_id: annual_nlcd_development_v1", "incompatible"),
    ]
    for old, new, message in variants:
        path = tmp_path / f"{len(list(tmp_path.iterdir()))}.fr.md"
        path.write_text(text.replace(old, new), encoding="utf-8")
        with pytest.raises(WorkfileError, match=message):
            load_workfile(path, repository_root=ROOT)

    secret = tmp_path / "secret.fr.md"
    secret.write_text(text.replace("sources:\n", "sources:\n  api_token: forbidden\n"), encoding="utf-8")
    with pytest.raises(WorkfileError, match="credentials"):
        load_workfile(secret, repository_root=ROOT)


def test_cdl_sparse_and_abstract_transitions_reconcile(tmp_path: Path) -> None:
    grid = TargetGrid("EPSG:5070", from_origin(0, 120, 30, 30), 4, 4, 30.0, (0.0, 0.0))
    before = np.array(
        [[1, 121, 122, 123], [124, 1, 2, 3], [121, 122, 123, 124], [0, 99, 1, 121]],
        dtype=np.uint8,
    )
    after = np.array(
        [[1, 121, 123, 122], [1, 121, 2, 4], [122, 121, 124, 123], [1, 1, 121, 1]],
        dtype=np.uint8,
    )
    before_path, after_path = tmp_path / "before.tif", tmp_path / "after.tif"
    _write_tif(before_path, before, transform=grid.transform)
    _write_tif(after_path, after, transform=grid.transform)
    result = analyze_interval(
        before_year=2008,
        after_year=2021,
        before_land_cover=before_path,
        after_land_cover=after_path,
        before_imperviousness=None,
        after_imperviousness=None,
        destination=tmp_path / "analysis",
        grid=grid,
        window_size=16,
        mapping=USDA_CDL_MAPPING,
    )
    stats = result["statistics"]
    assert stats["transition_reconciliation"]["reconciles"] is True
    sparse = json.loads(Path(result["source_transition_json"]).read_text(encoding="utf-8"))
    pairs = [(row["baseline_source_class"], row["comparison_source_class"]) for row in sparse["rows"]]
    assert pairs == sorted(pairs)
    assert sparse["total_pixels"] == stats["valid_comparison"]["pixels"]
    abstract = json.loads(Path(result["abstract_transition_json"]).read_text(encoding="utf-8"))
    assert len(abstract["rows"]) == 25
    assert abstract["total_pixels"] == stats["valid_comparison"]["pixels"]


def _ag_cache(tmp_path: Path, *, valid_checksum: bool = True) -> Path:
    handoff = tmp_path / "handoffs" / "ag-final"
    cdl = handoff / "data" / "cdl.tif"
    naip = handoff / "data" / "naip.tif"
    transform = from_origin(-116.42, 43.58, 0.02, 0.02)
    _write_tif(cdl, np.full((3, 3), 121, dtype=np.uint8), crs="EPSG:4326", transform=transform)
    _write_tif(naip, np.full((3, 3, 3), 80, dtype=np.uint8), crs="EPSG:4326", transform=transform)
    (handoff / "manifest.json").write_text(
        json.dumps(
            {
                "operation_status": "completed",
                "order": {"cdl_year": 2021, "bbox_epsg_4326": list(AOI)},
                "layers": [
                    {"name": "cdl_classes", "semantic_type": "categorical", "output": "data/cdl.tif"},
                    {"name": "natural", "semantic_type": "continuous", "output": "data/naip.tif"},
                ],
            }
        ),
        encoding="utf-8",
    )
    cdl_hash = sha256_file(cdl) if valid_checksum else "0" * 64
    (handoff / "checksums.sha256").write_text(
        f"{cdl_hash}  data/cdl.tif\n{sha256_file(naip)}  data/naip.tif\n",
        encoding="utf-8",
    )
    return handoff.parent


def test_cross_workflow_cdl_and_naip_reuse_requires_verified_evidence(tmp_path: Path) -> None:
    root = _ag_cache(tmp_path)
    grid = build_service_target_grid(AOI)
    cdl = find_cached_cdl_asset(root, AOI, 2021, grid)
    context = find_cached_naip_context(root, AOI, 2021)
    assert cdl is not None and cdl.action == "reuse_crop" and cdl.evidence["checksums"]
    assert context is not None and context.semantic_type == "natural_color_context"
    assert find_cached_cdl_asset(_ag_cache(tmp_path / "bad", valid_checksum=False), AOI, 2021, grid) is None
    assert find_cached_naip_context(tmp_path / "missing", AOI, 2021) is None


def test_strict_reuse_plan_has_zero_requests_and_correct_intervals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workfile = load_workfile(
        ROOT / "examples" / "meridian-cdl-development-change-reuse-only.fr.md",
        repository_root=ROOT,
    )
    grid = build_service_target_grid(AOI)
    assets = {}
    for year in (2008, 2016, 2021):
        path = tmp_path / f"{year}.tif"
        _write_tif(path, np.full((grid.height, grid.width), 121, dtype=np.uint8), transform=grid.transform)
        assets[year] = CacheAsset(
            path, "origin", sha256_file(path), year, "reuse_exact",
            "usda_nass_cdl_imageserver", "categorical_raw_classes", {"checksums": True, "finalized": True},
        )
    monkeypatch.setattr("faster_raster.human_development_live.find_cached_cdl_asset", lambda root, bbox, year, target: assets[year])
    monkeypatch.setattr("faster_raster.human_development_live.find_cached_naip_context", lambda *args: None)
    monkeypatch.setattr(
        "faster_raster.human_development_live.discover_cdl_coverage",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("strict reuse called network discovery")),
    )
    monkeypatch.setenv("FASTERRASTER_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("FASTERRASTER_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("FASTERRASTER_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("FASTERRASTER_TEMP_HOME", str(tmp_path / "temp"))
    plan = compile_live_cdl_plan(ROOT, workfile, resolve_local_paths(tmp_path), output_dir=tmp_path / "plan")
    assert plan["network_requests"] == 0
    assert [item["planned_action"] for item in plan["asset_plan"]["epochs"]] == ["reuse_exact"] * 3
    assert plan["asset_plan"]["adjacent_intervals"] == [
        {"before_year": 2008, "after_year": 2016},
        {"before_year": 2016, "after_year": 2021},
    ]
    assert plan["asset_plan"]["endpoint_comparison"] == {"before_year": 2008, "after_year": 2021}
    assert plan["asset_plan"]["context_imagery"]["blocking"] is False

def test_completed_config_redacts_runtime_temp_path() -> None:
    resolved = {"values": {"temporary_root": {"value": "/tmp/fasterraster", "origin": "default"}}}
    published = _public_resolved_config(resolved)
    assert published["values"]["temporary_root"]["value"] is None
    assert published["values"]["temporary_root"]["publication_policy"] == "runtime_only_path_not_published"
    assert resolved["values"]["temporary_root"]["value"] == "/tmp/fasterraster"



def test_cdl_preview_truthful_metadata_and_4k_dimensions(tmp_path: Path) -> None:
    grid = TargetGrid("EPSG:5070", from_origin(0, 480, 30, 30), 16, 16, 30.0, (0.0, 0.0))
    epoch_results = []
    for year, code in ((2008, 121), (2016, 122), (2021, 124)):
        source = tmp_path / f"source-{year}.tif"
        _write_tif(source, np.full((16, 16), code, dtype=np.uint8), transform=grid.transform)
        epoch_results.append(
            harmonize_epoch(
                year=year,
                land_cover_path=source,
                imperviousness_path=None,
                destination=tmp_path / "epochs" / str(year),
                grid=grid,
                window_size=16,
                mapping=USDA_CDL_MAPPING,
            )
        )
    intervals = []
    for before, after in zip(epoch_results, epoch_results[1:]):
        intervals.append(
            analyze_interval(
                before_year=before["year"],
                after_year=after["year"],
                before_land_cover=before["land_cover"],
                after_land_cover=after["land_cover"],
                before_imperviousness=None,
                after_imperviousness=None,
                destination=tmp_path / "intervals" / f"{before['year']}_{after['year']}",
                grid=grid,
                window_size=16,
                mapping=USDA_CDL_MAPPING,
            )
        )
    endpoint = analyze_interval(
        before_year=2008,
        after_year=2021,
        before_land_cover=epoch_results[0]["land_cover"],
        after_land_cover=epoch_results[-1]["land_cover"],
        before_imperviousness=None,
        after_imperviousness=None,
        destination=tmp_path / "endpoint",
        grid=grid,
        window_size=16,
        mapping=USDA_CDL_MAPPING,
    )
    preview = render_cdl_proxy_preview(
        tmp_path / "preview.png",
        study_name="truthful-cdl",
        comparison_mode="multi_epoch_time_series",
        bbox=AOI,
        epoch_results=epoch_results,
        endpoint_result=endpoint,
        source_contract={
            "source_id": "usda_nass_cdl_imageserver",
            "mapping_id": USDA_CDL_MAPPING.mapping_id,
            "mapping_contract_sha256": USDA_CDL_MAPPING.sha256,
        },
        grid=grid.as_dict(),
        context_result=None,
        interval_results=intervals,
        network_bytes=123,
        reused_bytes=456,
    )
    with Image.open(preview) as image:
        assert image.size == (3840, 2160)
        assert image.info["source"] == "USDA CDL-derived mapped development proxy change"
        assert image.info["developed_classes"] == "121,122,123,124"
        assert "crop-focused" in image.info["qualification"]
        assert image.info["mapping_id"] == USDA_CDL_MAPPING.mapping_id
    assert b"synthetic" not in preview.read_bytes().lower()


def test_staging_provenance_and_lazy_dependency_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    staging = tmp_path / ".study.staging-abc"
    staging.mkdir()
    (staging / "receipt.json").write_text(json.dumps({"path": str(staging)}), encoding="utf-8")
    with pytest.raises(RecipeExecutionError, match="transient staging provenance"):
        _assert_no_staging_provenance(staging)

    check = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import faster_raster.fr_cli; "
            "assert 'rasterio' not in sys.modules; assert 'numpy' not in sys.modules",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, check.stderr

    real_import = __import__

    def missing(name, *args, **kwargs):
        if name == "faster_raster.human_development_workflow":
            raise ModuleNotFoundError("No module named 'rasterio'", name="rasterio")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", missing)
    with pytest.raises(fr_cli.CommandError, match="NumPy and Rasterio.*rasterio"):
        fr_cli._human_execute()
