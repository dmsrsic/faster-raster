from pathlib import Path
import shutil

import pytest
import yaml

from faster_raster import __version__
from faster_raster.capability_registry import load_capability_registry
from faster_raster.grounding_bundle import build_grounding_bundle


ROOT = Path(__file__).resolve().parents[1]


def _temporary_registry(tmp_path: Path) -> Path:
    path = tmp_path / "configs" / "public_capabilities.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "configs" / "public_capabilities.yaml", path)
    artifact = tmp_path / "docs" / "validation" / "ames_prism_dem_ndvi_2023.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "docs" / "validation" / "ames_prism_dem_ndvi_2023.md", artifact)
    return path


def test_beta5_release_identity_is_synchronized():
    assert __version__ == "1.0.0b5"
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "1.0.0b5"' in pyproject
    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    assert 'version: "1.0.0b5"' in citation
    registry = load_capability_registry()
    assert registry["release"]["public_release"] == "v1.0.0-beta.5"
    assert registry["release"]["published_package_version"] == "1.0.0b5"
    assert registry["release"]["package_version"] == __version__


def test_frozen_capability_membership():
    registry = load_capability_registry()
    rows = {row["capability_id"]: row for row in registry["capabilities"] + registry["sources"]}
    assert rows["index_guided_hybrid_classification"]["release_state"] == "published"
    assert rows["prism_daily"]["release_state"] == "unreleased_public"
    assert rows["earth_engine_compute_contract"]["public_execution"] == "contract_compilation_only"
    assert rows["private_execution_backend"]["release_state"] == "private"
    assert rows["sauce_pack"]["release_state"] == "unreleased_public"
    assert rows["sauce_pack_v2"]["release_state"] == "unreleased_public"
    for row in rows.values():
        assert set(("release_state", "introduced_in", "evidence_levels", "evidence_refs", "public_execution", "scientific_scope")) <= set(row)
        if row["release_state"] == "published" and row["capability_id"] != "index_guided_hybrid_classification":
            assert row["introduced_in"] is None


def test_live_evidence_record_is_bound_to_the_declared_public_commit():
    registry = load_capability_registry()
    evidence = registry["evidence_records"]["prism_ames_2023"]
    assert evidence["commit"] == "edfa3cdbfe4e75e194a3fe25012970a3bcc325a6"
    assert "live_dataset_certified" in evidence["evidence_levels"]


def test_live_tier_must_be_supported_by_referenced_record(tmp_path, monkeypatch):
    source = (ROOT / "configs" / "public_capabilities.yaml").read_text(encoding="utf-8")
    source = source.replace("    evidence_levels: [live_dataset_certified]\ncapabilities:", "    evidence_levels: [fixture_validated]\ncapabilities:")
    path = tmp_path / "configs" / "public_capabilities.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    artifact = tmp_path / "docs" / "validation" / "ames_prism_dem_ndvi_2023.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("fixture", encoding="utf-8")
    with pytest.raises(ValueError, match="live evidence"):
        load_capability_registry(path)


def test_missing_evidence_commit_is_rejected_in_a_source_checkout(tmp_path, monkeypatch):
    source = (ROOT / "configs" / "public_capabilities.yaml").read_text(encoding="utf-8")
    path = tmp_path / "configs" / "public_capabilities.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    artifact = tmp_path / "docs" / "validation" / "ames_prism_dem_ndvi_2023.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("fixture", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(
        "faster_raster.capability_registry.subprocess.run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 1})(),
    )
    with pytest.raises(ValueError, match="declared commit"):
        load_capability_registry(path)


def test_evidence_rejects_missing_and_unsafe_artifacts(tmp_path):
    path = _temporary_registry(tmp_path)
    (tmp_path / "docs" / "validation" / "ames_prism_dem_ndvi_2023.md").unlink()
    with pytest.raises(ValueError, match="missing or unsafe artifact"):
        load_capability_registry(path)

    path = _temporary_registry(tmp_path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["evidence_records"]["prism_ames_2023"]["artifact"] = "../private/evidence.md"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="missing or unsafe artifact"):
        load_capability_registry(path)


def test_evidence_rejects_malformed_commit_identifier(tmp_path):
    path = _temporary_registry(tmp_path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["evidence_records"]["prism_ames_2023"]["commit"] = "edfa3cd"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid evidence commit"):
        load_capability_registry(path)


def test_multiple_evidence_records_support_live_level_union(tmp_path):
    path = _temporary_registry(tmp_path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["evidence_records"]["prism_route_2023"] = {
        "scope": "Ames bounded public route",
        "date": "2026-07-31",
        "commit": "edfa3cdbfe4e75e194a3fe25012970a3bcc325a6",
        "artifact": "docs/validation/ames_prism_dem_ndvi_2023.md",
        "evidence_levels": ["live_route_certified"],
    }
    prism = next(row for row in payload["sources"] if row["capability_id"] == "prism_daily")
    prism["evidence_levels"] = ["live_dataset_certified", "live_route_certified", "fixture_validated"]
    prism["evidence_refs"] = ["prism_ames_2023", "prism_route_2023"]
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    loaded = load_capability_registry(path)
    loaded_prism = next(row for row in loaded["sources"] if row["capability_id"] == "prism_daily")
    assert loaded_prism["evidence_refs"] == ["prism_ames_2023", "prism_route_2023"]


def test_evidence_commit_must_be_in_current_public_history(tmp_path, monkeypatch):
    path = _temporary_registry(tmp_path)
    (tmp_path / ".git").mkdir()
    responses = iter(
        [
            type("Result", (), {"returncode": 0})(),
            type("Result", (), {"returncode": 1})(),
        ]
    )
    monkeypatch.setattr("faster_raster.capability_registry.subprocess.run", lambda *args, **kwargs: next(responses))
    with pytest.raises(ValueError, match="current public history"):
        load_capability_registry(path)


def test_grounding_uses_capability_v2_without_registry_identity_schemas():
    bundle = build_grounding_bundle(ROOT)
    assert "fasterraster.capability-registry/v2" in bundle["contract_schema_versions"]
    assert "fasterraster.capability-registry/v1" not in bundle["contract_schema_versions"]
    assert not any(item["role"].startswith("handle") for item in bundle["files"])
