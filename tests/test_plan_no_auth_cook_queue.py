from __future__ import annotations

import json
from pathlib import Path

from scripts.plan_no_auth_cook_queue import build_queue, load_yaml, write_reports
from faster_raster.user_toggles import load_user_toggles


def test_no_auth_cook_queue_prioritizes_safe_sources():
    rows = build_queue(load_yaml(Path("research/source_atlas_v0_4.yaml")), json.loads(Path("reports/source_unlock_plan.json").read_text()), load_user_toggles(Path("configs/user_toggles.example.yaml")))
    assert rows
    assert all(row["credential_requirement"] == "none" for row in rows)
    assert len(rows) <= 5
    assert any(row["source_id"] == "gridmet_daily" for row in rows)


def test_gridmet_marked_endpoint_uncertainty():
    rows = build_queue(load_yaml(Path("research/source_atlas_v0_4.yaml")), json.loads(Path("reports/source_unlock_plan.json").read_text()), load_user_toggles(Path("configs/user_toggles.example.yaml")))
    gridmet = next(row for row in rows if row["source_id"] == "gridmet_daily")
    assert gridmet["cook_status"] == "blocked_by_endpoint_uncertainty"


def test_cook_queue_reports_write(tmp_path):
    rows = build_queue(load_yaml(Path("research/source_atlas_v0_4.yaml")), json.loads(Path("reports/source_unlock_plan.json").read_text()), load_user_toggles(Path("configs/user_toggles.example.yaml")))
    write_reports(rows, tmp_path/"queue.json", tmp_path/"queue.md")
    assert json.loads((tmp_path/"queue.json").read_text())["cook_queue"]
    assert "No-Auth Cook Queue" in (tmp_path/"queue.md").read_text()
