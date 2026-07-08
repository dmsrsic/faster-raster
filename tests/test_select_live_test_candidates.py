from __future__ import annotations

import json
from pathlib import Path

from scripts.select_live_test_candidates import load_pack, select_candidates, write_reports
from faster_raster.user_toggles import load_user_toggles


def test_select_live_test_candidates_fail_closed_without_endpoints():
    rows = select_candidates(load_pack(Path("reports/endpoint_readiness_pack_v0_5_3.json")), load_user_toggles(Path("configs/user_toggles.example.yaml")))
    assert len(rows) <= 3
    assert rows
    assert all(row["candidate_result_class"] in {"docs_ready_endpoint_needed", "adapter_needed", "skip_for_now"} for row in rows)
    assert rows[0]["source_id"] == "gridmet_daily"


def test_live_candidate_reports_write(tmp_path):
    rows = select_candidates(load_pack(Path("reports/endpoint_readiness_pack_v0_5_3.json")), load_user_toggles(Path("configs/user_toggles.example.yaml")))
    write_reports(rows, tmp_path/"live.json", tmp_path/"live.md")
    assert json.loads((tmp_path/"live.json").read_text())["live_test_candidates"]
    assert "Live Test Candidates" in (tmp_path/"live.md").read_text()
