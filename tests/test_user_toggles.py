from __future__ import annotations

from pathlib import Path

from faster_raster.user_toggles import load_user_toggles, effective_toggles, validate_user_toggles, write_effective_reports

TOGGLES = Path("configs/user_toggles.example.yaml")


def test_user_toggles_load_and_validate():
    data = load_user_toggles(TOGGLES)
    assert validate_user_toggles(data) == []
    effective = effective_toggles(data)
    assert effective["network_mode"] == "off"
    assert effective["source_scope"]["no_auth_only"] is True
    assert effective["promotion_policy"]["mode"] == "proposal_only"


def test_user_toggles_reject_secret_like_value():
    data = load_user_toggles(TOGGLES)
    data["output"]["note"] = "token=abcdefghi12345"
    assert any("raw secret-like value" in err for err in validate_user_toggles(data))


def test_write_effective_reports(tmp_path):
    report = write_effective_reports(load_user_toggles(TOGGLES), tmp_path/"toggles.json", tmp_path/"toggles.md")
    assert report["status"] == "PASS"
    assert (tmp_path/"toggles.json").exists()
    assert "Network mode" in (tmp_path/"toggles.md").read_text()
