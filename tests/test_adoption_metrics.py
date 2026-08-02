import json
from pathlib import Path
import re

import pytest

from scripts.archive_github_traffic import main, sanitize


ROOT = Path(__file__).resolve().parents[1]


def test_sanitize_is_deterministic_and_normalized():
    payload = {
        "clones": {"count": 3, "uniques": 2},
        "views": {"count": 7, "uniques": 5},
    }
    first = sanitize(payload, date="2026-08-01", gap_note="fixture")
    second = sanitize(payload, date="2026-08-01", gap_note="fixture")
    assert first == second
    assert first["metrics"]["repository_clone_count"] == 3
    assert "people" in first["gap_note"]


def test_sanitize_rejects_negative_or_non_integer_metrics():
    with pytest.raises(ValueError):
        sanitize({"clones": {"count": -1, "uniques": 1}, "views": {"count": 2, "uniques": 1}}, date="2026-08-01")
    with pytest.raises(ValueError):
        sanitize({"clones": {"count": 1.5, "uniques": 1}, "views": {"count": 2, "uniques": 1}}, date="2026-08-01")
    with pytest.raises(ValueError):
        sanitize({"clones": {"count": 1, "uniques": 1}, "views": {"count": 2, "uniques": None}}, date="2026-08-01")
    with pytest.raises(ValueError):
        sanitize({"clones": {"count": 1, "uniques": 1}, "views": {"count": 2, "uniques": 1}}, date="2026-8-1")


@pytest.mark.parametrize("payload", [None, [], {"clones": []}, {"clones": {}, "views": {}, "extra": {}}])
def test_sanitize_rejects_malformed_top_level_and_nested_payloads(payload):
    with pytest.raises(ValueError):
        sanitize(payload, date="2026-08-01")


def test_sanitize_rejects_unknown_nested_fields():
    payload = {
        "clones": {"count": 1, "uniques": 1, "unexpected": []},
        "views": {"count": 2, "uniques": 1},
    }
    with pytest.raises(ValueError):
        sanitize(payload, date="2026-08-01")


def test_sanitize_validates_optional_github_series():
    payload = {
        "clones": {
            "count": 1,
            "uniques": 1,
            "clones": [{"timestamp": "2026-08-01T00:00:00Z", "count": 1, "uniques": 1}],
        },
        "views": {
            "count": 2,
            "uniques": 1,
            "views": [{"timestamp": "2026-08-01T00:00:00Z", "count": 2, "uniques": 1}],
        },
    }
    assert sanitize(payload, date="2026-08-01")["metrics"]["repository_clone_count"] == 1
    payload["clones"]["clones"] = "not-an-array"
    with pytest.raises(ValueError, match="series"):
        sanitize(payload, date="2026-08-01")


@pytest.mark.parametrize(
    "entry",
    [
        {"timestamp": "bad", "count": 1, "uniques": 1},
        {"timestamp": "2026-08-01T00:00:00Z", "count": -1, "uniques": 1},
        {"timestamp": "2026-08-01T00:00:00Z", "count": 1, "uniques": 1, "extra": 2},
    ],
)
def test_sanitize_rejects_malformed_series_entries(entry):
    payload = {
        "clones": {"count": 1, "uniques": 1, "clones": [entry]},
        "views": {"count": 2, "uniques": 1},
    }
    with pytest.raises(ValueError):
        sanitize(payload, date="2026-08-01")


def test_missing_secret_safely_skips_snapshot(tmp_path, monkeypatch):
    monkeypatch.delenv("FASTER_RASTER_GITHUB_TRAFFIC_TOKEN", raising=False)
    output = tmp_path / "2026-08-01.json"
    assert main(["--output", str(output), "--date", "2026-08-01"]) == 0
    assert not output.exists()


def test_fixture_writes_only_sanitized_aggregate(tmp_path):
    fixture = tmp_path / "traffic.json"
    fixture.write_text(
        json.dumps({"clones": {"count": 1, "uniques": 1}, "views": {"count": 2, "uniques": 2}}),
        encoding="utf-8",
    )
    output = tmp_path / "archive" / "2026-08-01.json"
    assert main(["--fixture", str(fixture), "--output", str(output), "--date", "2026-08-01"]) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert set(payload) == {"schema_version", "date", "source", "metrics", "gap_note"}
    assert "clones" not in json.dumps(payload)


def test_malformed_fixture_writes_no_snapshot_or_traceback(tmp_path, capsys):
    fixture = tmp_path / "malformed.json"
    fixture.write_text("[not an object]", encoding="utf-8")
    output = tmp_path / "archive" / "2026-08-01.json"
    assert main(["--fixture", str(fixture), "--output", str(output), "--date", "2026-08-01"]) == 0
    assert not output.exists()
    assert "Traceback" not in capsys.readouterr().out


def test_same_date_is_deterministically_replaced(tmp_path):
    fixture = tmp_path / "traffic.json"
    output = tmp_path / "archive" / "2026-08-01.json"
    fixture.write_text(json.dumps({"clones": {"count": 1, "uniques": 1}, "views": {"count": 2, "uniques": 2}}), encoding="utf-8")
    assert main(["--fixture", str(fixture), "--output", str(output), "--date", "2026-08-01"]) == 0
    first = output.read_bytes()
    fixture.write_text(json.dumps({"clones": {"count": 3, "uniques": 2}, "views": {"count": 4, "uniques": 3}}), encoding="utf-8")
    assert main(["--fixture", str(fixture), "--output", str(output), "--date", "2026-08-01"]) == 0
    assert output.read_bytes() != first
    assert json.loads(output.read_text(encoding="utf-8"))["metrics"]["repository_clone_count"] == 3


def test_duplicate_date_with_identical_input_is_byte_deduplicated(tmp_path):
    fixture = tmp_path / "traffic.json"
    output = tmp_path / "archive" / "2026-08-01.json"
    fixture.write_text(json.dumps({"clones": {"count": 1, "uniques": 1}, "views": {"count": 2, "uniques": 2}}), encoding="utf-8")
    assert main(["--fixture", str(fixture), "--output", str(output), "--date", "2026-08-01"]) == 0
    first = output.read_bytes()
    assert main(["--fixture", str(fixture), "--output", str(output), "--date", "2026-08-01"]) == 0
    assert output.read_bytes() == first


def test_archive_workflow_is_manual_only_and_branch_scoped():
    workflow = (ROOT / ".github" / "workflows" / "archive-github-traffic.yml").read_text(encoding="utf-8")
    assert "schedule:" not in workflow
    assert "workflow_dispatch:" in workflow
    assert "HEAD:metrics-archive" in workflow
    assert "contents: write" in workflow
    assert "analytics" not in workflow.lower()
    assert workflow.count("contents: write") == 1
    uses = re.findall(r"uses:\s*([^\s#]+)", workflow)
    assert uses
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", value) for value in uses)
    assert "git push origin HEAD:metrics-archive" in workflow


def test_metric_dictionary_states_honest_semantics():
    dictionary = (ROOT / "docs" / "adoption-metrics.md").read_text(encoding="utf-8")
    assert "Does not represent" in dictionary
    assert "Repository clone count" in dictionary
    assert "Active registered FasterRaster handles" in dictionary
    assert "No Pages beacon, cookie, fingerprint, installation ID" in dictionary
