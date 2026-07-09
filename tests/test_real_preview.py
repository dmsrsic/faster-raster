from __future__ import annotations

import json
from pathlib import Path

import pytest

from faster_raster import real_preview, task_builder


class FakeHeaders(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class FakeResponse:
    def __init__(self, data: bytes, content_type: str = "image/png", status: int = 200):
        self.data = data
        self.headers = FakeHeaders({"Content-Type": content_type})
        self.status = status
        self.code = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size: int = -1):
        return self.data if size < 0 else self.data[:size]


def png_bytes(colors):
    width = len(colors)
    height = 1
    img = bytearray()
    for color in colors:
        img.extend(color)
    path = Path("/tmp/fr_test_preview.png")
    real_preview._write_png(path, width, height, img)
    return path.read_bytes()


def task_with_sources(sources):
    return task_builder.default_task(
        "real_preview_task",
        "Real Preview Task",
        [-83.2, 39.8, -83.19, 39.81],
        "EPSG:4326",
        "EPSG:5070",
        [2023],
        ["precipitation", "landcover", "elevation"],
        sources,
    )


def test_dry_run_preview_real_writes_plan_and_does_not_fetch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(real_preview.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network called")))
    report = real_preview.create_real_preview(task_with_sources(["cdl_arcgis_tiny_export", "usgs_3dep_dem"]))
    assert report["network_run"] is False
    assert report["real_fetch_attempted"] is False
    assert report["real_data_preview"] is True
    assert Path(report["json_path"]).exists()
    assert Path(report["md_path"]).exists()
    payload = json.loads(Path(report["json_path"]).read_text())
    assert payload["source_results"][0]["source_id"] == "cdl_arcgis_tiny_export"


def test_mocked_cdl_fetch_renders_png_and_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(real_preview.urllib.request, "urlopen", lambda *a, **k: FakeResponse(png_bytes([[10, 80, 20], [10, 80, 20], [220, 210, 30], [40, 40, 200]]), "image/png"))
    report = real_preview.create_real_preview(task_with_sources(["cdl_arcgis_tiny_export"]), allow_network=True, max_bytes_per_source=1000)
    assert report["network_run"] is True
    assert report["real_fetch_attempted"] is True
    assert report["real_data_preview"] is True
    assert report["real_raster_data_rendered"] is True
    assert Path(report["png_path"]).read_bytes().startswith(bytes([137]) + b"PNG")
    result = report["source_results"][0]
    assert result["source_id"] == "cdl_arcgis_tiny_export"
    assert result["bytes_read"] > 0
    assert result["sha256"]
    assert result["render_kind"] == "real_raster"
    assert result["cache_path"]
    assert result["image_width"] == 4
    assert result["image_height"] == 1
    assert result["unique_color_count"] == 3
    assert result["dominant_color_fraction"] == 0.5


def test_mocked_daymet_fetch_renders_point_result(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(real_preview.urllib.request, "urlopen", lambda *a, **k: FakeResponse(b"year,yday,prcp\n2023,1,2.5\n", "text/csv"))
    report = real_preview.create_real_preview(task_with_sources(["daymet_single_pixel_prcp_rest"]), allow_network=True, max_bytes_per_source=1000)
    result = report["source_results"][0]
    assert result["rendered"] is True
    assert result["render_kind"] == "real_point"
    assert result["real_point_data_rendered"] is True
    assert report["real_raster_data_rendered"] is False


def test_prism_archive_skipped_unless_include_archives():
    task = task_with_sources(["prism_daily_ppt_static_zip"])
    plain = real_preview.build_real_preview_plan(task)
    assert plain["source_results"][0]["warning"] == "archive_requires_explicit_include_archives"
    included = real_preview.build_real_preview_plan(task, include_archives=True)
    assert included["source_results"][0]["warning"] == "archive_preview_not_fetched_in_dry_run"


def test_3dep_skipped_with_safe_warning():
    plan = real_preview.build_real_preview_plan(task_with_sources(["usgs_3dep_dem"]))
    result = plan["source_results"][0]
    assert result["status"] == "adapter_needed"
    assert result["warning"] == "no_safe_tiny_dem_endpoint_yet"


def test_byte_cap_enforced(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(real_preview.urllib.request, "urlopen", lambda *a, **k: FakeResponse(b"abcdef", "image/png"))
    report = real_preview.create_real_preview(task_with_sources(["cdl_arcgis_tiny_export"]), allow_network=True, max_bytes_per_source=3)
    result = report["source_results"][0]
    assert result["status"] == "fetch_failed"
    assert "byte cap exceeded" in result["error"]


def test_malformed_response_warning_not_crash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    def fail(*args, **kwargs):
        raise OSError("bad response")
    monkeypatch.setattr(real_preview.urllib.request, "urlopen", fail)
    report = real_preview.create_real_preview(task_with_sources(["cdl_arcgis_tiny_export"]), allow_network=True)
    result = report["source_results"][0]
    assert result["status"] == "fetch_failed"
    assert result["warning"] == "real fetch failed; semantic fallback used"


def test_registry_and_atlas_unchanged_by_real_preview(tmp_path, monkeypatch):
    registry = Path("configs/source_registry.yaml").read_bytes()
    atlas = Path("research/source_atlas_v0_4.yaml").read_bytes()
    monkeypatch.chdir(tmp_path)
    real_preview.create_real_preview(task_with_sources(["cdl_arcgis_tiny_export"]))
    assert Path("/home/dmsrsic/raster-work/faster-raster/configs/source_registry.yaml").read_bytes() == registry
    assert Path("/home/dmsrsic/raster-work/faster-raster/research/source_atlas_v0_4.yaml").read_bytes() == atlas


def test_image_diagnostics_single_color():
    diagnostics = real_preview.diagnose_image(png_bytes([[1, 2, 3], [1, 2, 3], [1, 2, 3]]), content_type="image/png", bytes_read=900)
    assert diagnostics["unique_color_count"] == 1
    assert diagnostics["is_mostly_single_class"] is True
    assert diagnostics["is_probably_placeholder"] is True
    assert diagnostics["diagnostic_notes"]


def test_image_diagnostics_multi_color():
    diagnostics = real_preview.diagnose_image(png_bytes([[1, 2, 3], [4, 5, 6], [7, 8, 9], [10, 11, 12]]), content_type="image/png", bytes_read=5000)
    assert diagnostics["unique_color_count"] == 4
    assert diagnostics["is_mostly_single_class"] is False
    assert diagnostics["diversity_score"] > 0


def test_no_cache_raw_avoids_cache_but_renders(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(real_preview.urllib.request, "urlopen", lambda *a, **k: FakeResponse(png_bytes([[1, 2, 3], [4, 5, 6], [7, 8, 9]]), "image/png"))
    report = real_preview.create_real_preview(task_with_sources(["cdl_arcgis_tiny_export"]), allow_network=True, cache_raw=False)
    result = report["source_results"][0]
    assert result["rendered"] is True
    assert result["cache_path"] is None


def test_debug_artifacts_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(real_preview.urllib.request, "urlopen", lambda *a, **k: FakeResponse(png_bytes([[1, 2, 3], [4, 5, 6], [7, 8, 9]]), "image/png"))
    real_preview.create_real_preview(task_with_sources(["cdl_arcgis_tiny_export"]), allow_network=True, debug_artifacts=True)
    assert Path("reports/task_previews/real_preview_task_real_stack_preview_diagnostics.json").exists()
    assert Path("reports/task_previews/real_preview_task_real_stack_preview_diagnostics.md").exists()


def test_preview_size_respects_max_pixels():
    assert real_preview.effective_preview_size(512, 100) == 10
    url = real_preview.cdl_preview_url(task_with_sources(["cdl_arcgis_tiny_export"]), max_pixels=100, preview_size=512)
    assert "size=10%2C10" in url



def test_dry_run_includes_cdl_sample_verification_plan(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    task = task_with_sources(["cdl_arcgis_tiny_export"])
    before = json.dumps(task, sort_keys=True)
    report = real_preview.create_real_preview(task, sample_grid_size=5, preview_expand_factor=10, cdl_render_mode="auto")
    assert json.dumps(task, sort_keys=True) == before
    assert report["network_run"] is False
    assert report["real_fetch_attempted"] is False
    assert report["real_data_preview"] is True
    assert report["cdl_verification_run"] is False
    assert report["cdl_verify_samples_planned"] is True
    assert report["sample_grid_size"] == 5
    assert report["preview_expand_factor"] == 10
    assert report["cdl_render_mode"] == "auto"
    assert report["preview_fetch_bbox"] != report["bbox"]
    assert report["preview_fetch_bbox"][0] < report["bbox"][0]
    assert report["preview_fetch_bbox"][2] > report["bbox"][2]


def test_invalid_cdl_render_mode_fails_clearly():
    try:
        real_preview.create_real_preview(task_with_sources(["cdl_arcgis_tiny_export"]), cdl_render_mode="bad")
    except ValueError as exc:
        assert "invalid cdl_render_mode" in str(exc)
    else:
        raise AssertionError("invalid render mode should fail")


def test_single_color_cdl_png_with_meaningful_samples_becomes_manual_sample_result(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    png = png_bytes([[10, 80, 20], [10, 80, 20], [10, 80, 20]])

    def fake_urlopen(request, timeout=0):
        url = getattr(request, "full_url", str(request))
        if "identify" in url:
            return FakeResponse(b'{"value": "1", "name": "Corn"}', "application/json")
        return FakeResponse(png, "image/png")

    monkeypatch.setattr(real_preview.urllib.request, "urlopen", fake_urlopen)
    report = real_preview.create_real_preview(task_with_sources(["cdl_arcgis_tiny_export"]), allow_network=True, sample_grid_size=3)
    result = report["source_results"][0]
    assert report["cdl_verification_run"] is True
    assert report["cdl_meaningful_preview"] is True
    assert report["real_sample_layer_count"] == 1
    assert result["status"] == "real_data_verified_manual_samples"
    assert result["render_kind"] == "real_categorical_samples"
    assert result["rendered"] is True
    assert result["real_raster_rendered"] is False
    assert result["real_point_or_sample_data_rendered"] is True
    assert result["cdl_meaningful"] is True
    assert result["renderer_problem_suspected"] is True
    assert result["no_data_suspected"] is False
    assert result["sample_verification_attempted"] is True
    assert result["sample_points_count"] == 9
    assert result["sample_values_count"] == 9
    assert result["unique_sample_values"] == ["1"]
    assert result["sample_class_names"] == ["Corn"]


def test_single_color_cdl_png_with_no_sample_values_becomes_no_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    png = png_bytes([[10, 80, 20], [10, 80, 20], [10, 80, 20]])

    def fake_urlopen(request, timeout=0):
        url = getattr(request, "full_url", str(request))
        if "identify" in url:
            return FakeResponse(b'{"value": "0", "name": "NoData"}', "application/json")
        return FakeResponse(png, "image/png")

    monkeypatch.setattr(real_preview.urllib.request, "urlopen", fake_urlopen)
    report = real_preview.create_real_preview(task_with_sources(["cdl_arcgis_tiny_export"]), allow_network=True, sample_grid_size=3)
    result = report["source_results"][0]
    assert report["cdl_verification_run"] is True
    assert result["status"] == "no_data_or_placeholder"
    assert result["render_kind"] == "no_data_or_placeholder"
    assert result["rendered"] is False
    assert result["cdl_meaningful"] is False
    assert result["no_data_suspected"] is True
    assert result["warning"] == "CDL preview response was single-color and identify returned no meaningful class values"


def test_multicolor_cdl_png_remains_real_raster_rendered(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    png = png_bytes([[10, 80, 20], [220, 210, 30], [40, 40, 200]])
    monkeypatch.setattr(real_preview.urllib.request, "urlopen", lambda *a, **k: FakeResponse(png, "image/png"))
    report = real_preview.create_real_preview(task_with_sources(["cdl_arcgis_tiny_export"]), allow_network=True)
    result = report["source_results"][0]
    assert result["status"] == "real_raster_rendered"
    assert result["render_kind"] == "real_raster"
    assert result["sample_verification_attempted"] is False
    assert report["real_raster_layer_count"] == 1
