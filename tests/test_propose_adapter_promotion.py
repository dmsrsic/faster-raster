from __future__ import annotations

import json
from pathlib import Path

from scripts.propose_adapter_promotion import build_proposal, load_yaml, write_reports
from faster_raster.user_toggles import load_user_toggles


def test_gridmet_promotion_proposal_not_ready():
    proposal = build_proposal("gridmet_daily", load_yaml(Path("research/source_atlas_v0_4.yaml")), load_user_toggles(Path("configs/user_toggles.example.yaml")), Path("reports"))
    assert proposal["promotion_decision"] == "not_ready"
    assert proposal["endpoint_status"] == "missing_or_unknown"
    assert proposal["proposal_only"] is True
    assert proposal["runtime_registry_edit_forbidden"] is True


def test_promotion_reports_write(tmp_path):
    proposal = build_proposal("gridmet_daily", load_yaml(Path("research/source_atlas_v0_4.yaml")), load_user_toggles(Path("configs/user_toggles.example.yaml")), Path("reports"))
    json_path, md_path = write_reports(proposal, tmp_path)
    assert json.loads(json_path.read_text())["source_id"] == "gridmet_daily"
    assert "No runtime registry files were edited" in md_path.read_text()
