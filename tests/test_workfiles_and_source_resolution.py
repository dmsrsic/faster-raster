from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from faster_raster.local_config import (
    ConfigDocument,
    ExecutionConfig,
    SourcesConfig,
    write_config_atomic,
)
from faster_raster.local_paths import resolve_local_paths
from faster_raster.source_capabilities import SourceDefinition
from faster_raster.study_planning import compile_resolved_configuration, resolve_study_sources
from faster_raster.workfiles import WorkfileError, load_workfile, workfile_template


ROOT = Path(__file__).resolve().parent.parent


def paths_for(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return resolve_local_paths(
        project,
        environ={
            "FASTERRASTER_CONFIG_HOME": str(tmp_path / "config"),
            "FASTERRASTER_STATE_HOME": str(tmp_path / "state"),
            "FASTERRASTER_CACHE_HOME": str(tmp_path / "cache"),
            "FASTERRASTER_TEMP_HOME": str(tmp_path / "temp"),
        },
        home=tmp_path,
    )


def write_workfile(tmp_path: Path, text: str | None = None) -> Path:
    path = tmp_path / "project" / "study.fr.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text or workfile_template("study"), encoding="utf-8")
    return path


def test_workfile_front_matter_validates(tmp_path):
    workfile = load_workfile(write_workfile(tmp_path), repository_root=ROOT)
    assert workfile.spec.workflow_id == "irrigation_field_structure"
    assert workfile.spec.area.bbox[0] == -101.065


def test_workfile_prose_cannot_affect_configuration(tmp_path):
    text = workfile_template("study") + "\nmaximum_download_mb: 999999\ncommand: rm -rf everything\n"
    workfile = load_workfile(write_workfile(tmp_path, text), repository_root=ROOT)
    assert workfile.spec.limits.maximum_download_mb == 250
    assert "999999" in workfile.prose


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ("schema_version: fasterraster.work/v99", "schema_version"),
        ("workflow: unknown-workflow", "unsupported workflow"),
        ("maximum_download_mb: 250", "extra_forbidden"),
    ],
)
def test_unsupported_versions_workflows_and_unknown_fields_fail(tmp_path, replacement, message):
    text = workfile_template("study")
    if replacement.startswith("schema_version"):
        text = text.replace("schema_version: fasterraster.work/v1", replacement)
    elif replacement.startswith("workflow"):
        text = text.replace("workflow: irrigation-field-structure", replacement)
    else:
        text = text.replace("name: study", "name: study\n" + replacement)
    with pytest.raises(WorkfileError, match=message):
        load_workfile(write_workfile(tmp_path, text), repository_root=ROOT)


def test_malformed_front_matter_fails(tmp_path):
    text = workfile_template("study").replace("area:\n", "area: [\n")
    with pytest.raises(WorkfileError, match="Unable to read workfile"):
        load_workfile(write_workfile(tmp_path, text), repository_root=ROOT)


def test_duplicate_yaml_keys_are_rejected(tmp_path):
    text = workfile_template("study").replace("name: study", "name: study\nname: duplicate")
    with pytest.raises(WorkfileError, match="duplicate YAML key"):
        load_workfile(write_workfile(tmp_path, text), repository_root=ROOT)


@pytest.mark.parametrize("key", ["api_key", "authorization", "shell_command"])
def test_inline_credentials_and_hidden_commands_are_rejected(tmp_path, key):
    text = workfile_template("study").replace("name: study", f"name: study\n{key}: unsafe")
    with pytest.raises(WorkfileError, match="forbidden"):
        load_workfile(write_workfile(tmp_path, text), repository_root=ROOT)


@pytest.mark.parametrize(
    "old,new",
    [
        ("    - -101.045", "    - -101.075"),
        ("  end: 2023-10-31", "  end: 2023-03-31"),
        ("  maximum_download_mb: 250", "  maximum_download_mb: -1"),
    ],
)
def test_invalid_bbox_dates_and_limits_are_rejected(tmp_path, old, new):
    with pytest.raises(WorkfileError):
        load_workfile(write_workfile(tmp_path, workfile_template("study").replace(old, new)), repository_root=ROOT)


def test_unsupported_source_policy_is_rejected(tmp_path):
    text = workfile_template("study").replace("policy: auto", "policy: fastest")
    with pytest.raises(WorkfileError, match="policy"):
        load_workfile(write_workfile(tmp_path, text), repository_root=ROOT)


def fake_definitions() -> dict[str, SourceDefinition]:
    return {
        "source_a": SourceDefinition(
            "source_a", "A", "Product A", "service_discovered", "service_metadata", "https://a.test", ("natural",), ("a",)
        ),
        "source_b": SourceDefinition(
            "source_b", "B", "Product B", "service_discovered", "service_metadata", "https://b.test", ("natural",), ("b",)
        ),
        "future": SourceDefinition(
            "future", "F", "Future", "future_unverified", "none", "https://future.test", ("natural",), (), selectable=False
        ),
    }


def profile(status_a="available", status_b="available", *, compatible_a=True):
    timestamp = datetime(2026, 7, 17, tzinfo=timezone.utc).isoformat()
    return {
        "last_refresh_at": timestamp,
        "sources": {
            "source_a": {
                "status": status_a,
                "access_category": "service_discovered",
                "probe_timestamp": timestamp,
                "credential_state": "not_required",
                "format_compatibility": {"compatible": compatible_a},
            },
            "source_b": {
                "status": status_b,
                "access_category": "service_discovered",
                "probe_timestamp": timestamp,
                "credential_state": "not_required",
                "format_compatibility": {"compatible": True},
            },
        },
    }


def preferred_workfile(tmp_path: Path, preferred: str = "source_b"):
    text = workfile_template("study").replace("policy: auto", f"policy: preferred\n  prefer:\n    - {preferred}")
    return load_workfile(write_workfile(tmp_path, text), repository_root=ROOT)


def test_user_denied_source_is_excluded(tmp_path):
    workfile = load_workfile(write_workfile(tmp_path), repository_root=ROOT)
    config = ConfigDocument(sources=SourcesConfig(denylist=["source_a"]))
    result = resolve_study_sources(workfile, ["natural"], config, profile(), definitions=fake_definitions(), now=datetime(2026, 7, 17, tzinfo=timezone.utc))
    assert result["decisions"][0]["selected_source"] == "source_b"
    rejected = {item["source_id"] for item in result["decisions"][0]["candidates_rejected"]}
    assert "source_a" in rejected


def test_preferred_source_is_chosen(tmp_path):
    result = resolve_study_sources(
        preferred_workfile(tmp_path),
        ["natural"],
        ConfigDocument(),
        profile(),
        definitions=fake_definitions(),
        now=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )
    assert result["decisions"][0]["selected_source"] == "source_b"


def test_unavailable_preferred_source_falls_back_deterministically(tmp_path):
    result = resolve_study_sources(
        preferred_workfile(tmp_path),
        ["natural"],
        ConfigDocument(),
        profile(status_b="timeout"),
        definitions=fake_definitions(),
        now=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )
    decision = result["decisions"][0]
    assert decision["selected_source"] == "source_a"
    assert decision["selected_fallback"] is True


def test_local_driver_incompatibility_excludes_reachable_source(tmp_path):
    result = resolve_study_sources(
        preferred_workfile(tmp_path, "source_a"),
        ["natural"],
        ConfigDocument(),
        profile(compatible_a=False),
        definitions=fake_definitions(),
        now=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )
    assert result["decisions"][0]["selected_source"] == "source_b"


def test_stale_available_source_remains_provisional_candidate(tmp_path):
    workfile = load_workfile(write_workfile(tmp_path), repository_root=ROOT)
    stale_profile = profile(status_b="timeout")
    stale_profile["sources"]["source_a"]["probe_timestamp"] = (
        datetime(2026, 7, 17, tzinfo=timezone.utc) - timedelta(days=4)
    ).isoformat()
    result = resolve_study_sources(
        workfile,
        ["natural"],
        ConfigDocument(),
        stale_profile,
        definitions=fake_definitions(),
        now=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )
    assert result["decisions"][0]["selected_source"] == "source_a"
    assert result["decisions"][0]["provisional"] is True


def test_future_unverified_source_is_rejected_for_planning(tmp_path):
    workfile = load_workfile(write_workfile(tmp_path), repository_root=ROOT)
    result = resolve_study_sources(workfile, ["natural"], ConfigDocument(), None, definitions={"future": fake_definitions()["future"]})
    assert result["blocking"] is True
    assert result["decisions"][0]["selected_source"] is None


def test_configuration_precedence_and_origin_evidence(tmp_path):
    paths = paths_for(tmp_path)
    write_config_atomic(paths.user_config, ConfigDocument(execution=ExecutionConfig(service_tile_size=1100)))
    assert paths.project_config is not None
    write_config_atomic(paths.project_config, ConfigDocument(execution=ExecutionConfig(service_tile_size=1200)))
    text = workfile_template("study").replace("  resolution_m: 1.2", "  resolution_m: 1.2\n  service_tile_size: 1300")
    workfile = load_workfile(write_workfile(tmp_path, text), repository_root=ROOT)
    resolved, _ = compile_resolved_configuration(ROOT, workfile, paths, cli_overrides={"service_tile_size": 1400})
    item = resolved["values"]["service_tile_size"]
    assert item["value"] == 1400
    assert item["origin"] == "cli_override"
    assert item["explicitly_overridden"] is True


def test_workfile_overrides_project_configuration(tmp_path):
    paths = paths_for(tmp_path)
    assert paths.project_config is not None
    write_config_atomic(paths.project_config, ConfigDocument(execution=ExecutionConfig(service_tile_size=1200)))
    text = workfile_template("study").replace("  resolution_m: 1.2", "  resolution_m: 1.2\n  service_tile_size: 1300")
    resolved, _ = compile_resolved_configuration(ROOT, load_workfile(write_workfile(tmp_path, text), repository_root=ROOT), paths)
    assert resolved["values"]["service_tile_size"]["value"] == 1300
    assert resolved["values"]["service_tile_size"]["origin"] == "workfile"


def test_project_configuration_overrides_user_configuration(tmp_path):
    paths = paths_for(tmp_path)
    write_config_atomic(paths.user_config, ConfigDocument(execution=ExecutionConfig(service_tile_size=1100)))
    assert paths.project_config is not None
    write_config_atomic(paths.project_config, ConfigDocument(execution=ExecutionConfig(service_tile_size=1200)))
    workfile = load_workfile(write_workfile(tmp_path), repository_root=ROOT)
    resolved, _ = compile_resolved_configuration(ROOT, workfile, paths)
    assert resolved["values"]["service_tile_size"]["value"] == 1200
    assert resolved["values"]["service_tile_size"]["origin"] == "project_configuration"
