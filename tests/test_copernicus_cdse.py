from __future__ import annotations

import json
from pathlib import Path

import yaml

from faster_raster.adapters import copernicus_cdse
from faster_raster import task_builder


def sample_task():
    return task_builder.default_task("sentinel_task", "Sentinel", [-83.2, 39.8, -83.19, 39.81], "EPSG:4326", "EPSG:5070", [2023], ["sentinel2"], [copernicus_cdse.SENTINEL2_L2A_SOURCE_ID])


def test_stac_search_payload_contains_collection():
    payload = copernicus_cdse.build_cdse_stac_search_payload(sample_task())
    assert payload["collections"] == ["sentinel-2-l2a"]
    assert payload["bbox"] == [-83.2, 39.8, -83.19, 39.81]


def test_parse_select_and_summarize_items():
    items = copernicus_cdse.parse_cdse_stac_items({"features": [{"id": "b", "properties": {"eo:cloud_cover": 20}}, {"id": "a", "properties": {"eo:cloud_cover": 5}}]})
    best = copernicus_cdse.select_best_sentinel2_item(items)
    assert best["id"] == "a"
    assert copernicus_cdse.summarize_sentinel2_item(best)["cloud_cover"] == 5


def test_search_plan_writes_without_network_or_credentials(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    task_builder.save_task(sample_task())
    monkeypatch.setenv("CDSE_ACCESS_TOKEN", "fake-token-value")
    plan = copernicus_cdse.create_search_plan("sentinel_task")
    assert plan["network_run"] is False
    assert plan["collection"] == "sentinel-2-l2a"
    assert Path(plan["json_path"]).exists()
    text = Path(plan["json_path"]).read_text() + Path(plan["md_path"]).read_text()
    assert "fake-token-value" not in text


def test_source_registry_has_cdse_entry():
    registry = yaml.safe_load(Path("configs/source_registry.yaml").read_text())
    assert "copernicus_sentinel2_l2a_cdse_stac" in registry["sources"]



class FakeHeaders(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class FakeResponse:
    def __init__(self, data: bytes, content_type: str = "application/json", status: int = 200):
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


def test_search_live_writes_bounded_stac_report_without_hrefs_or_token(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    task_builder.save_task(sample_task())
    monkeypatch.setenv("CDSE_ACCESS_TOKEN", "fake-token-value")
    payload = {
        "features": [{
            "id": "S2A_TEST",
            "collection": "sentinel-2-l2a",
            "bbox": [0, 1, 2, 3],
            "properties": {"datetime": "2023-06-01T00:00:00Z", "eo:cloud_cover": 7, "platform": "sentinel-2a", "constellation": "sentinel-2", "instruments": ["msi"], "s2:mgrs_tile": "31TCJ"},
            "assets": {"B04": {"href": "https://example/red.tif"}, "B03": {}, "B02": {}, "B08": {}, "visual": {}},
        }]
    }
    seen = {}
    def fake_urlopen(request, timeout=0):
        seen["authorization"] = request.headers.get("Authorization")
        return FakeResponse(json.dumps(payload).encode("utf-8"))
    monkeypatch.setattr(copernicus_cdse.urllib.request, "urlopen", fake_urlopen)
    report = copernicus_cdse.create_search_live("sentinel_task", max_items=5)
    assert seen["authorization"] == "Bearer fake-token-value"
    assert report["network_run"] is True
    assert report["no_downloads"] is True
    assert report["item_count"] == 1
    item = report["items"][0]
    assert item["has_red_band"] is True
    assert item["has_green_band"] is True
    assert item["has_blue_band"] is True
    assert item["has_nir_band"] is True
    assert item["hrefs_redacted"] is True
    text = Path(report["json_path"]).read_text() + Path(report["md_path"]).read_text()
    assert "fake-token-value" not in text
    assert "https://example/red.tif" not in text


def test_search_live_handles_credential_required_status(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    task_builder.save_task(sample_task())
    def fake_urlopen(request, timeout=0):
        raise copernicus_cdse.urllib.error.HTTPError(request.full_url, 403, "Forbidden", FakeHeaders({"Content-Type": "application/json"}), None)
    monkeypatch.setattr(copernicus_cdse.urllib.request, "urlopen", fake_urlopen)
    report = copernicus_cdse.create_search_live("sentinel_task")
    assert report["http_status"] == 403
    assert report["item_count"] == 0
    assert report["warnings"]
