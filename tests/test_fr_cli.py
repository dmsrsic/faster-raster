from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from faster_raster import fr_cli
from faster_raster.local_paths import resolve_local_paths
from faster_raster.preview_open import finalized_preview, latest_handoff, open_local_preview
from faster_raster.workfiles import workfile_template


ROOT = Path(__file__).resolve().parent.parent


def isolate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FASTERRASTER_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("FASTERRASTER_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("FASTERRASTER_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("FASTERRASTER_TEMP_HOME", str(tmp_path / "temp"))
    monkeypatch.setenv("FASTERRASTER_AG_CACHE_ROOT", str(tmp_path / "no-handoffs"))


def sample(tmp_path: Path) -> Path:
    path = tmp_path / "study.fr.md"
    path.write_text(workfile_template("study"), encoding="utf-8")
    return path


def test_fr_init_creates_valid_template_without_overwrite(tmp_path, monkeypatch, capsys):
    isolate(monkeypatch, tmp_path)
    destination = tmp_path / "new.fr.md"
    assert fr_cli.main(["init", str(destination)]) == 0
    assert destination.read_text(encoding="utf-8").startswith("---\nschema_version: fasterraster.work/v1")
    original = destination.read_text(encoding="utf-8")
    assert fr_cli.main(["init", str(destination)]) == 2
    assert destination.read_text(encoding="utf-8") == original
    assert "No network requests were made" in capsys.readouterr().out


def test_fr_validate_performs_zero_network_requests(tmp_path, monkeypatch, capsys):
    isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "faster_raster.source_capabilities.BoundedHTTPTransport.request",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network called")),
    )
    assert fr_cli.main(["validate", str(sample(tmp_path))]) == 0
    assert "No network requests were made" in capsys.readouterr().out


def test_fr_plan_is_offline_by_default_and_writes_artifacts(tmp_path, monkeypatch, capsys):
    isolate(monkeypatch, tmp_path)
    out = tmp_path / "plan"
    assert fr_cli.main(["plan", str(sample(tmp_path)), "--out", str(out)]) == 0
    assert (out / "resolved_config.json").is_file()
    assert (out / "source_resolution.json").is_file()
    assert json.loads((out / "plan.json").read_text(encoding="utf-8"))["network_requests"] == 0
    assert "No network requests were made" in capsys.readouterr().out


def test_fr_explain_shows_source_selection_reasons(tmp_path, monkeypatch, capsys):
    isolate(monkeypatch, tmp_path)
    assert fr_cli.main(["explain", str(sample(tmp_path)), "--out", str(tmp_path / "explain"), "--verbose"]) == 0
    output = capsys.readouterr().out
    assert "Configuration precedence" in output
    assert "Local readiness:" in output
    assert "Remote source:" in output


def test_sources_evaluate_offline_writes_profile_and_self_cleans(tmp_path, monkeypatch):
    isolate(monkeypatch, tmp_path)
    assert fr_cli.main(["sources", "evaluate", "--offline"]) == 0
    profile = tmp_path / "state" / "capabilities" / "default.json"
    assert json.loads(profile.read_text(encoding="utf-8"))["evaluation"]["requests_made"] == 0
    assert not (tmp_path / "cache" / "probes").exists()


def test_fr_cook_routes_into_shared_execution_function(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "recipes" / "ag").mkdir(parents=True)
    (root / "recipes" / "ag" / "irrigation_field_structure.json").write_text('{"recipe_id":"irrigation_field_structure"}', encoding="utf-8")
    workfile = SimpleNamespace(
        spec=SimpleNamespace(
            workflow_id="irrigation_field_structure",
            name="study",
            area=SimpleNamespace(bbox=(-101.0, 39.0, -100.9, 39.1)),
            time=SimpleNamespace(start=SimpleNamespace(isoformat=lambda: "2023-04-01"), end=SimpleNamespace(isoformat=lambda: "2023-10-31"), crop_year=2023),
        )
    )
    plan = {
        "blocking": False,
        "rows": [],
        "resolved_config": {"values": {
            "reuse_mode": {"value": "auto"},
            "open_when_complete": {"value": False},
            "maximum_download_mb": {"value": 1},
            "service_tile_size": {"value": 128},
        }},
        "source_resolution": {"decisions": []},
    }
    monkeypatch.setattr(fr_cli, "_load_and_plan", lambda args: (root, workfile, None, plan))
    monkeypatch.setattr(fr_cli, "load_named_recipe", lambda root, recipe_id: SimpleNamespace(recipe_id=recipe_id))
    renderer = object()
    monkeypatch.setattr(fr_cli, "_recipe_renderer", lambda: renderer)
    called = {}

    def fake_execute(root_arg, **kwargs):
        called.update(kwargs)
        preview = root / "outputs" / "handoffs" / "final" / "preview" / "result.png"
        preview.parent.mkdir(parents=True)
        preview.write_bytes(b"png")
        return preview

    monkeypatch.setattr(fr_cli, "execute_recipe", fake_execute)
    args = SimpleNamespace(json=False)
    assert fr_cli.command_cook(args) == 0
    assert called["renderer"] is renderer
    final = root / "outputs" / "handoffs" / "final"
    assert (final / "resolved_config.json").is_file()
    assert (final / "source_resolution.json").is_file()
    assert (final / "checksums.sha256").is_file()


def _finalized(path: Path, *, preview=True) -> Path:
    path.mkdir(parents=True)
    (path / "manifest.json").write_text('{"operation_status":"completed","network_bytes":3}', encoding="utf-8")
    if preview:
        (path / "preview").mkdir()
        (path / "preview" / "result_4k.png").write_bytes(b"png")
    return path


def test_inspect_latest_ignores_staging_directories(tmp_path):
    root = tmp_path / "handoffs"
    final = _finalized(root / "final")
    staging = root / ".new.staging-123"
    staging.mkdir()
    (staging / "manifest.json").write_text('{"operation_status":"completed"}', encoding="utf-8")
    assert latest_handoff(root) == final


def test_open_latest_resolves_only_finalized_preview(tmp_path):
    final = _finalized(tmp_path / "handoffs" / "final")
    assert finalized_preview(final) == final / "preview" / "result_4k.png"


def test_wsl_open_uses_path_conversion_and_explorer(tmp_path):
    preview = tmp_path / "preview.png"
    preview.write_bytes(b"png")
    launched = []
    converted = []

    def converter(command, **kwargs):
        converted.append(command)
        return subprocess.CompletedProcess(command, 0, "C:\\preview.png\n", "")

    command = open_local_preview(
        preview,
        which=lambda name: f"/bin/{name}",
        converter=converter,
        launcher=lambda command, **kwargs: launched.append(command),
        is_wsl=True,
    )
    assert converted[0][:2] == ["wslpath", "-w"]
    assert command == ["explorer.exe", "C:\\preview.png"]
    assert launched == [command]


def test_native_linux_open_uses_configured_opener(tmp_path):
    preview = tmp_path / "preview.png"
    preview.write_bytes(b"png")
    launched = []
    command = open_local_preview(
        preview,
        configured_opener="image-viewer --new-window",
        which=lambda name: None,
        launcher=lambda command, **kwargs: launched.append(command),
        is_wsl=False,
    )
    assert command == ["image-viewer", "--new-window", str(preview.resolve())]


def test_default_generated_state_is_outside_repository(tmp_path):
    paths = resolve_local_paths(ROOT, environ={}, home=tmp_path)
    assert ROOT not in paths.capability_profile.parents
    assert paths.capability_profile == tmp_path / ".local" / "state" / "fasterraster" / "capabilities" / "default.json"


@pytest.mark.parametrize("command", ["init", "configure", "doctor", "sources", "validate", "plan", "explain", "cook", "inspect", "open"])
def test_every_command_has_useful_help(command, capsys):
    with pytest.raises(SystemExit) as raised:
        fr_cli.main([command, "--help"])
    assert raised.value.code == 0
    assert "usage:" in capsys.readouterr().out


def test_ordinary_configuration_error_has_no_traceback(tmp_path, capsys):
    invalid = tmp_path / "invalid.fr.md"
    invalid.write_text("not front matter", encoding="utf-8")
    assert fr_cli.main(["validate", str(invalid)]) == 2
    error = capsys.readouterr().err
    assert "ERROR:" in error
    assert "Traceback" not in error
