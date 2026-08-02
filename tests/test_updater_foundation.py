from __future__ import annotations

import hashlib
import json
import socket
import subprocess
from pathlib import Path

import pytest

from faster_raster.adapter_contract import stable_json
from faster_raster.updater import install_state, release_client, service
from faster_raster.updater.models import InstallationState, ReleaseAsset, ReleaseManifest, UpdateChannel
from faster_raster.updater.service import _recommendation, check, status


ROOT = Path(__file__).resolve().parents[1]


def _asset(name: str, url: str, data: bytes, *, kind: str = "wheel") -> dict[str, object]:
    return {
        "kind": kind,
        "name": name,
        "url": url,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }


def _release_fixture(
    *,
    tag: str = "v1.0.0-beta.6",
    package_version: str = "1.0.0b6",
    prerelease: bool = True,
    wheel_data: bytes = b"wheel",
) -> tuple[list[dict[str, object]], bytes, dict[str, object]]:
    wheel_url = f"https://github.com/dmsrsic/faster-raster/releases/download/{tag}/faster_raster-{package_version}-py3-none-any.whl"
    manifest_url = f"https://github.com/dmsrsic/faster-raster/releases/download/{tag}/{release_client.MANIFEST_NAME}"
    manifest_payload: dict[str, object] = {
        "schema_version": "fasterraster.release-manifest/v1",
        "tag": tag,
        "package_version": package_version,
        "channel": "beta" if prerelease else "stable",
        "requires_python": ">=3.12",
        "updater_manifest_major": 1,
        "release_notes_url": f"https://github.com/dmsrsic/faster-raster/releases/tag/{tag}",
        "assets": [_asset(wheel_url.rsplit("/", 1)[-1], wheel_url, wheel_data)],
    }
    manifest_bytes = (json.dumps(manifest_payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    api_assets = [
        {
            "name": release_client.MANIFEST_NAME,
            "browser_download_url": manifest_url,
            "size": len(manifest_bytes),
            "digest": "sha256:" + hashlib.sha256(manifest_bytes).hexdigest(),
        },
        {
            "name": wheel_url.rsplit("/", 1)[-1],
            "browser_download_url": wheel_url,
            "size": len(wheel_data),
            "digest": "sha256:" + hashlib.sha256(wheel_data).hexdigest(),
        },
    ]
    release = {"draft": False, "prerelease": prerelease, "tag_name": tag, "assets": api_assets}
    return [release], manifest_bytes, manifest_payload


def _patch_release_fetch(monkeypatch: pytest.MonkeyPatch, api: list[dict[str, object]], manifest: bytes, *, calls: list[str] | None = None) -> None:
    def fake_fetch(url: str, *, maximum: int, timeout: float = 5.0) -> bytes:
        if calls is not None:
            calls.append(url)
        return json.dumps(api).encode() if url == release_client.API_URL else manifest

    monkeypatch.setattr(release_client, "_fetch", fake_fetch)


def test_status_is_offline_and_receipt_is_stable(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTERRASTER_STATE_HOME", str(tmp_path / "state"))
    first = status(root=tmp_path)
    second = status(root=tmp_path)
    assert first.status == "offline"
    assert first.receipt_sha256 == second.receipt_sha256
    assert not list((tmp_path / "state").rglob("*.json"))


def test_check_requires_explicit_network_authorization_and_writes_canonical_receipt(tmp_path, monkeypatch):
    state_home = tmp_path / "state"
    monkeypatch.setenv("FASTERRASTER_STATE_HOME", str(state_home))
    result = check(channel=UpdateChannel.BETA, allow_network=False, root=tmp_path)
    assert result.status == "blocked"
    assert result.error == "network access requires --allow-network"
    receipt = next(state_home.glob("update/*.json"))
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert "receipt_sha256" not in payload
    assert hashlib.sha256(receipt.read_bytes()).hexdigest() == receipt.stem
    assert json.loads(stable_json(payload)) == payload
    schema = json.loads((ROOT / "schemas" / "fasterraster-update-check-receipt-v1.schema.json").read_text(encoding="utf-8"))
    assert set(payload) == set(schema["required"])
    assert set(payload) <= set(schema["properties"])
    assert schema["additionalProperties"] is False
    assert "receipt_sha256" not in schema["required"]
    assert result.as_dict()["receipt_sha256"] == receipt.stem
    serialized = json.dumps(payload, sort_keys=True)
    assert str(tmp_path) not in serialized
    assert not any(field in serialized for field in ("timestamp", "username", "machine_id", "handle", "telemetry"))


def test_no_apply_parser_surface():
    from faster_raster.fr_cli import build_parser

    parser = build_parser()
    assert "apply" not in parser.format_help()
    with pytest.raises(SystemExit):
        parser.parse_args(["update", "apply"])


def test_status_is_socket_free(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("unexpected socket")

    monkeypatch.setattr(socket.socket, "connect", denied)
    assert status(root=tmp_path).status == "offline"


def test_unauthorized_check_is_socket_free_and_process_free(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTERRASTER_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(socket.socket, "connect", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected socket")))
    monkeypatch.setattr(release_client, "discover", lambda channel: (_ for _ in ()).throw(AssertionError("unexpected discovery")))
    assert check(channel=UpdateChannel.BETA, allow_network=False, root=tmp_path).status == "blocked"


def test_blocked_check_writes_only_update_state(tmp_path, monkeypatch):
    for name in ("CONFIG", "STATE", "CACHE", "TEMP"):
        monkeypatch.setenv(f"FASTERRASTER_{name}_HOME", str(tmp_path / name.lower()))
    result = check(channel=UpdateChannel.BETA, allow_network=False, root=tmp_path)
    assert result.status == "blocked"
    assert (tmp_path / "state" / "update").is_dir()
    assert not (tmp_path / "config").exists()
    assert not (tmp_path / "cache").exists()
    assert not (tmp_path / "temp").exists()


def test_ambiguous_origin_is_conservative():
    state = InstallationState("1.0.0b5.dev0", "3.12.3", "absent", "package_index_or_unknown")
    candidate = ReleaseManifest(
        "fasterraster.release-manifest/v1",
        "v1.0.0-beta.6",
        "1.0.0b6",
        "beta",
        ">=3.12",
        1,
        "https://github.com/dmsrsic/faster-raster/releases/tag/v1.0.0-beta.6",
        (ReleaseAsset("wheel", "faster_raster-1.0.0b6-py3-none-any.whl", "https://github.com/dmsrsic/faster-raster/releases/download/v1.0.0-beta.6/faster_raster-1.0.0b6-py3-none-any.whl", "0" * 64, 1),),
    )
    assert _recommendation(state, candidate)["action"] == "unsupported"


def test_discover_binds_manifest_and_assets_to_one_release(monkeypatch):
    api, manifest_bytes, _ = _release_fixture()
    calls: list[str] = []
    _patch_release_fetch(monkeypatch, api, manifest_bytes, calls=calls)
    result = release_client.discover(UpdateChannel.BETA)
    assert result[0].tag == "v1.0.0-beta.6"
    assert calls == [release_client.API_URL, api[0]["assets"][0]["browser_download_url"]]


def test_discover_uses_declared_request_and_byte_limits(monkeypatch):
    api, manifest_bytes, _ = _release_fixture()
    calls: list[tuple[str, int]] = []

    def fake_fetch(url: str, *, maximum: int, timeout: float = 5.0) -> bytes:
        calls.append((url, maximum))
        return json.dumps(api).encode() if url == release_client.API_URL else manifest_bytes

    monkeypatch.setattr(release_client, "_fetch", fake_fetch)
    release_client.discover(UpdateChannel.BETA)
    assert calls == [
        (release_client.API_URL, release_client.MAX_API_BYTES),
        (api[0]["assets"][0]["browser_download_url"], release_client.MAX_MANIFEST_BYTES),
    ]


def test_discover_rejects_cross_release_manifest_url(monkeypatch):
    api, manifest_bytes, _ = _release_fixture()
    api[0]["assets"][0]["browser_download_url"] = api[0]["assets"][0]["browser_download_url"].replace("beta.6", "beta.5")
    _patch_release_fetch(monkeypatch, api, manifest_bytes)
    with pytest.raises(release_client.ReleaseClientError, match="selected release"):
        release_client.discover(UpdateChannel.BETA)


@pytest.mark.parametrize("field", ["browser_download_url", "size", "digest"])
def test_discover_rejects_listed_asset_metadata_mismatch(monkeypatch, field):
    api, manifest_bytes, _ = _release_fixture()
    if field == "browser_download_url":
        api[0]["assets"][1][field] = api[0]["assets"][1][field].replace(".whl", "-other.whl")
    elif field == "size":
        api[0]["assets"][1][field] += 1
    else:
        api[0]["assets"][1][field] = "sha256:" + "f" * 64
    _patch_release_fetch(monkeypatch, api, manifest_bytes)
    with pytest.raises(release_client.ReleaseClientError):
        release_client.discover(UpdateChannel.BETA)


def test_discover_rejects_duplicate_and_malformed_asset_metadata(monkeypatch):
    api, manifest_bytes, _ = _release_fixture()
    api[0]["assets"].append(dict(api[0]["assets"][1]))
    _patch_release_fetch(monkeypatch, api, manifest_bytes)
    with pytest.raises(release_client.ReleaseClientError, match="malformed"):
        release_client.discover(UpdateChannel.BETA)

    api, manifest_bytes, _ = _release_fixture()
    api[0]["assets"][1]["digest"] = "sha256:not-a-digest"
    _patch_release_fetch(monkeypatch, api, manifest_bytes)
    with pytest.raises(release_client.ReleaseClientError, match="digest"):
        release_client.discover(UpdateChannel.BETA)


def test_discover_rejects_shell_injection_asset_name(monkeypatch):
    api, manifest_bytes, manifest = _release_fixture()
    manifest["assets"][0]["name"] += ";calc.exe"
    manifest_bytes = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    api[0]["assets"][0]["size"] = len(manifest_bytes)
    api[0]["assets"][0]["digest"] = "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
    _patch_release_fetch(monkeypatch, api, manifest_bytes)
    with pytest.raises(release_client.ReleaseClientError):
        release_client.discover(UpdateChannel.BETA)


def test_discover_rejects_manifest_asset_digest_mismatch(monkeypatch):
    api, manifest_bytes, _ = _release_fixture()
    api[0]["assets"][0]["digest"] = "sha256:" + "0" * 64
    _patch_release_fetch(monkeypatch, api, manifest_bytes)
    with pytest.raises(release_client.ReleaseClientError, match="manifest digest"):
        release_client.discover(UpdateChannel.BETA)


def test_discover_rejects_manifest_payload_asset_mismatch(monkeypatch):
    api, manifest_bytes, manifest = _release_fixture()
    manifest["assets"][0]["size_bytes"] = 999
    manifest_bytes = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    api[0]["assets"][0]["size"] = len(manifest_bytes)
    api[0]["assets"][0]["digest"] = "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
    _patch_release_fetch(monkeypatch, api, manifest_bytes)
    with pytest.raises(release_client.ReleaseClientError, match="metadata"):
        release_client.discover(UpdateChannel.BETA)


def test_discover_rejects_missing_github_digest(monkeypatch):
    api, manifest_bytes, _ = _release_fixture()
    del api[0]["assets"][1]["digest"]
    _patch_release_fetch(monkeypatch, api, manifest_bytes)
    with pytest.raises(release_client.ReleaseClientError, match="digest"):
        release_client.discover(UpdateChannel.BETA)


def test_discover_rejects_malformed_manifest_json(monkeypatch):
    api, _, _ = _release_fixture()
    bad = b"not-json"
    api[0]["assets"][0]["size"] = len(bad)
    api[0]["assets"][0]["digest"] = "sha256:" + hashlib.sha256(bad).hexdigest()
    _patch_release_fetch(monkeypatch, api, bad)
    with pytest.raises(release_client.ReleaseClientError, match="valid JSON"):
        release_client.discover(UpdateChannel.BETA)


def test_discover_rejects_malformed_release_json(monkeypatch):
    monkeypatch.setattr(release_client, "_fetch", lambda *args, **kwargs: b"not-json")
    with pytest.raises(release_client.ReleaseClientError, match="valid JSON"):
        release_client.discover(UpdateChannel.BETA)


def test_discover_rejects_python_incompatibility(monkeypatch):
    api, manifest_bytes, _ = _release_fixture()
    _patch_release_fetch(monkeypatch, api, manifest_bytes)
    monkeypatch.setattr(release_client.platform, "python_version", lambda: "3.11.0")
    with pytest.raises(release_client.ReleaseClientError, match="Python"):
        release_client.discover(UpdateChannel.BETA)


def test_discover_stable_excludes_prereleases(monkeypatch):
    stable_api, stable_manifest, _ = _release_fixture(tag="v1.0.0", package_version="1.0.0", prerelease=False)
    beta_api, _, _ = _release_fixture()
    _patch_release_fetch(monkeypatch, stable_api + beta_api, stable_manifest)
    assert release_client.discover(UpdateChannel.STABLE)[0].tag == "v1.0.0"


def test_discover_beta_orders_candidates_deterministically(monkeypatch):
    older_api, _, _ = _release_fixture(tag="v1.0.0-beta.5", package_version="1.0.0b5")
    newer_api, newer_manifest, _ = _release_fixture(tag="v1.0.0-beta.6", package_version="1.0.0b6")
    _patch_release_fetch(monkeypatch, older_api + newer_api, newer_manifest)
    assert release_client.discover(UpdateChannel.BETA)[0].tag == "v1.0.0-beta.6"


def test_discover_skips_drafts_and_dev_only_releases(monkeypatch):
    draft_api, draft_manifest, _ = _release_fixture()
    draft_api[0]["draft"] = True
    _patch_release_fetch(monkeypatch, draft_api, draft_manifest)
    assert release_client.discover(UpdateChannel.BETA) == ()

    dev_api, dev_manifest, _ = _release_fixture(tag="v1.0.0-beta.6.dev1", package_version="1.0.0b6.dev1")
    _patch_release_fetch(monkeypatch, dev_api, dev_manifest)
    assert release_client.discover(UpdateChannel.BETA) == ()


def test_fetch_enforces_host_size_timeout_and_redirect_limits(monkeypatch):
    with pytest.raises(release_client.ReleaseClientError, match="allowlisted"):
        release_client._fetch("http://example.test/metadata", maximum=10)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, amount):
            return b"x" * amount

    class Opener:
        def open(self, request, timeout):
            assert timeout == 5.0
            return Response()

    monkeypatch.setattr(release_client.urllib.request, "build_opener", lambda handler: Opener())
    with pytest.raises(release_client.ReleaseClientError, match="byte limit"):
        release_client._fetch("https://api.github.com/metadata", maximum=10)

    handler = release_client._BoundedRedirect()
    with pytest.raises(release_client.ReleaseClientError, match="allowlisted"):
        handler.redirect_request(None, None, 302, "", {}, "http://example.test/next")
    handler.count = release_client.MAX_REDIRECTS
    with pytest.raises(release_client.ReleaseClientError, match="redirect limit"):
        handler.redirect_request(None, None, 302, "", {}, "https://github.com/next")


def test_git_status_failure_is_blocked(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text('name = "faster-raster"\n', encoding="utf-8")
    responses = iter(
        [
            subprocess.CompletedProcess([], 0, stdout=str(tmp_path), stderr=""),
            subprocess.CompletedProcess([], 1, stdout="", stderr="git failed"),
        ]
    )
    monkeypatch.setattr(install_state.subprocess, "run", lambda *args, **kwargs: next(responses))
    assert install_state._git_context(tmp_path) == "dirty_checkout"


@pytest.mark.parametrize(
    ("responses", "expected"),
    [
        ([subprocess.CompletedProcess([], 128, stdout="", stderr="fatal: not a git repository")], "absent"),
        ([subprocess.CompletedProcess([], 1, stdout="", stderr="git failed")], "dirty_checkout"),
    ],
)
def test_git_absence_and_failed_rev_parse_are_distinct(tmp_path, monkeypatch, responses, expected):
    monkeypatch.setattr(install_state.subprocess, "run", lambda *args, **kwargs: responses.pop(0))
    assert install_state._git_context(tmp_path) == expected


@pytest.mark.parametrize(
    ("status_output", "expected"),
    [("", "clean_checkout"), (" M pyproject.toml\n", "dirty_checkout")],
)
def test_git_clean_and_dirty_states(tmp_path, monkeypatch, status_output, expected):
    (tmp_path / "pyproject.toml").write_text('name = "faster-raster"\n', encoding="utf-8")
    responses = iter(
        [
            subprocess.CompletedProcess([], 0, stdout=str(tmp_path), stderr=""),
            subprocess.CompletedProcess([], 0, stdout=status_output, stderr=""),
        ]
    )
    monkeypatch.setattr(install_state.subprocess, "run", lambda *args, **kwargs: next(responses))
    assert install_state._git_context(tmp_path) == expected


@pytest.mark.parametrize(
    ("direct_url", "expected"),
    [
        ('{"url":"file:///checkout","dir_info":{"editable":true}}', "editable"),
        ('{"url":"file:///tmp/faster_raster.whl"}', "local_wheel"),
        ('{"url":"file:///tmp/faster_raster.tar.gz"}', "local_sdist"),
        (None, "package_index_or_unknown"),
        ('{"url":"file:///tmp/source"}', "ambiguous"),
        ("[]", "ambiguous"),
        ('{"url":"file:///checkout","dir_info":null}', "ambiguous"),
    ],
)
def test_distribution_origin_classes(monkeypatch, direct_url, expected):
    class Distribution:
        def read_text(self, name):
            assert name == "direct_url.json"
            return direct_url

    monkeypatch.setattr(install_state.importlib.metadata, "distribution", lambda name: Distribution())
    assert install_state._distribution_origin() == expected


@pytest.mark.parametrize(
    ("active", "candidate_version", "expected_candidate"),
    [
        ("1.0.0b5", "1.0.0b4", None),
        ("1.0.0b5", "1.0.0b5", None),
        ("1.0.0b5", "1.0.0b6", "1.0.0b6"),
    ],
)
def test_check_filters_older_same_and_newer_candidates(tmp_path, monkeypatch, active, candidate_version, expected_candidate):
    monkeypatch.setenv("FASTERRASTER_STATE_HOME", str(tmp_path / "state"))
    state = InstallationState(active, "3.12.3", "absent", "local_wheel")
    candidate = ReleaseManifest(
        "fasterraster.release-manifest/v1",
        "v1.0.0-beta." + candidate_version.rsplit("b", 1)[-1],
        candidate_version,
        "beta",
        ">=3.12",
        1,
        "https://github.com/dmsrsic/faster-raster/releases/tag/v1.0.0-beta.6",
        (),
    )
    monkeypatch.setattr(service, "inspect_installation", lambda root=None: state)
    monkeypatch.setattr(service, "discover", lambda channel: (candidate,))
    result = service.check(channel=UpdateChannel.BETA, allow_network=True, root=tmp_path)
    assert (result.candidate.package_version if result.candidate else None) == expected_candidate


@pytest.mark.parametrize(
    ("git_context", "origin", "action"),
    [
        ("dirty_checkout", "local_wheel", "blocked"),
        ("clean_checkout", "local_wheel", "manual_git_fast_forward"),
        ("absent", "editable", "manual_editable_reinstall"),
        ("absent", "local_wheel", "manual_wheel_install"),
        ("absent", "local_sdist", "manual_wheel_install"),
        ("absent", "package_index_or_unknown", "unsupported"),
    ],
)
def test_recommendations_are_structured_and_nonexecuting(git_context, origin, action):
    state = InstallationState("1.0.0b4", "3.12.3", git_context, origin)
    candidate = ReleaseManifest(
        "fasterraster.release-manifest/v1",
        "v1.0.0-beta.5",
        "1.0.0b5",
        "beta",
        ">=3.12",
        1,
        "https://github.com/dmsrsic/faster-raster/releases/tag/v1.0.0-beta.5",
        (ReleaseAsset("wheel", "faster_raster-1.0.0b5-py3-none-any.whl", "https://github.com/dmsrsic/faster-raster/releases/download/v1.0.0-beta.5/faster_raster-1.0.0b5-py3-none-any.whl", "0" * 64, 1),),
    )
    recommendation = _recommendation(state, candidate)
    assert recommendation["action"] == action
    assert all(isinstance(value, str) or isinstance(value, list) for value in recommendation.values())
