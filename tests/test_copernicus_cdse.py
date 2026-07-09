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
