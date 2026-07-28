from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
import yaml

from faster_raster import fr_cli
from faster_raster.source_pack import (
    compile_source_pack_plan,
    pack_source_pack,
    probe_source_pack,
    scaffold_source_pack,
    test_source_pack as run_source_pack_test,
    validate_source_pack,
)


ROOT = Path(__file__).resolve().parent.parent
PRISM = ROOT / "examples" / "sauce-packs" / "prism-daily.sauce"
CDSE = ROOT / "examples" / "sauce-packs" / "copernicus-cdse.sauce"


def _manifest(path: Path) -> dict:
    return yaml.safe_load((path / "sauce.yaml").read_text(encoding="utf-8"))


def _write_manifest(path: Path, value: dict) -> None:
    (path / "sauce.yaml").write_text(
        yaml.safe_dump(value, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )


def test_shipped_source_packs_validate_and_pass_offline_goldens():
    for path in (PRISM, CDSE):
        assert validate_source_pack(path)["status"] == "PASS"
        assert run_source_pack_test(path)["status"] == "PASS"


def test_scaffold_is_valid_and_never_overwrites_without_force(tmp_path):
    path = scaffold_source_pack(tmp_path / "my-source")
    assert path.name == "my-source.sauce"
    assert validate_source_pack(path)["status"] == "PASS"
    assert run_source_pack_test(path)["status"] == "PASS"
    with pytest.raises(ValueError, match="refusing to overwrite"):
        scaffold_source_pack(path)


def test_archive_is_byte_deterministic(tmp_path):
    first = pack_source_pack(PRISM, tmp_path / "one")
    second = pack_source_pack(PRISM, tmp_path / "two")
    assert first["archive_sha256"] == second["archive_sha256"]
    assert Path(first["archive_path"]).read_bytes() == Path(second["archive_path"]).read_bytes()
    assert validate_source_pack(Path(first["archive_path"]))["status"] == "PASS"


def test_path_traversal_archive_is_rejected(tmp_path):
    path = tmp_path / "bad.sauce.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("../sauce.yaml", "schema_version: fasterraster.source-pack/v1\n")
    result = validate_source_pack(path)
    assert result["status"] == "FAIL"
    assert "unsafe Source Pack path" in result["errors"][0]


def test_secret_query_and_token_fixture_are_rejected(tmp_path):
    path = scaffold_source_pack(tmp_path / "secret-source")
    manifest = _manifest(path)
    manifest["adapter"]["url_template"] = "https://example.com/{year}.tif?token=not-public"
    _write_manifest(path, manifest)
    (path / "probe_fixture.json").write_text(
        json.dumps({"token": "eyJabcdefghijklmno.abcdefghijk.abcdefghijk"}),
        encoding="utf-8",
    )
    result = validate_source_pack(path)
    assert result["status"] == "FAIL"
    assert any("secret-bearing query" in item for item in result["errors"])
    assert any("secret-bearing field" in item for item in result["errors"])


def test_unsafe_hosts_redirect_scope_and_signed_urls_are_rejected(tmp_path):
    path = scaffold_source_pack(tmp_path / "unsafe-network")
    manifest = _manifest(path)
    manifest["adapter"]["url_template"] = (
        "https://example.com/{year}.tif?X-Amz-Signature=synthetic"
    )
    manifest["access"]["allowed_hosts"] = ["*.example.com"]
    manifest["access"]["redirect_hosts"] = ["assets.example.com"]
    manifest["network"]["maximum_redirects"] = 1
    _write_manifest(path, manifest)
    result = validate_source_pack(path)
    assert result["status"] == "FAIL"
    assert any("invalid or unrestricted host" in item for item in result["errors"])
    assert any("subset of allowed_hosts" in item for item in result["errors"])
    assert any("secret-bearing query" in item for item in result["errors"])


def test_headers_and_authorization_values_are_rejected_from_fixtures(tmp_path):
    path = scaffold_source_pack(tmp_path / "secret-headers")
    (path / "probe_fixture.json").write_text(
        json.dumps(
            {
                "headers": {
                    "Authorization": "Bearer synthetic-not-a-real-token"
                }
            }
        ),
        encoding="utf-8",
    )
    result = validate_source_pack(path)
    assert result["status"] == "FAIL"
    assert any("secret-bearing field" in item for item in result["errors"])


def test_categorical_bilinear_resampling_is_rejected(tmp_path):
    path = scaffold_source_pack(tmp_path / "categorical")
    manifest = _manifest(path)
    manifest["source"]["semantic_type"] = "categorical"
    manifest["source"]["resampling"] = "bilinear"
    _write_manifest(path, manifest)
    result = validate_source_pack(path)
    assert result["status"] == "FAIL"
    assert any("categorical sources require" in item for item in result["errors"])


def test_arcgis_and_verified_local_adapter_families_validate(tmp_path):
    arcgis = scaffold_source_pack(tmp_path / "arcgis-source")
    arcgis_manifest = _manifest(arcgis)
    arcgis_manifest["adapter"].update(
        {
            "family": "arcgis_imageserver",
            "endpoint": "https://example.com/arcgis/rest/services/demo/ImageServer",
            "url_template": None,
        }
    )
    _write_manifest(arcgis, arcgis_manifest)
    assert validate_source_pack(arcgis)["status"] == "PASS"

    local = scaffold_source_pack(tmp_path / "local-source")
    payload = b"verified-local-raster-fixture"
    (local / "fixture.tif").write_bytes(payload)
    local_manifest = _manifest(local)
    local_manifest["adapter"].update(
        {
            "family": "verified_local_raster",
            "endpoint": None,
            "url_template": None,
            "local_path": "fixture.tif",
            "local_sha256": hashlib.sha256(payload).hexdigest(),
        }
    )
    local_manifest["access"]["allowed_hosts"] = []
    _write_manifest(local, local_manifest)
    assert validate_source_pack(local)["status"] == "PASS"


def test_credential_pack_plans_but_probe_stops_before_network():
    plan = compile_source_pack_plan(CDSE)
    assert plan["status"] == "CREDENTIAL_REQUIRED"
    assert plan["credential_requirement"]["credential_ref"] == "copernicus-production"
    assert plan["credential_requirement"]["resolved_secret_present"] is False
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("network must not be called")

    with pytest.raises(ValueError, match="stopped before network access"):
        probe_source_pack(CDSE, allow_network=True, urlopen=forbidden)
    assert called is False
    serialized = json.dumps(plan, sort_keys=True)
    assert "Bearer " not in serialized
    assert '"resolved_secret_present": true' not in serialized
    assert "authorization" not in serialized.lower()


def test_credential_probe_cli_reports_blocked_state(capsys):
    assert (
        fr_cli.main(
            [
                "sauce",
                "probe",
                str(CDSE),
                "--allow-network",
                "--json",
            ]
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "BLOCKED"
    assert "stopped before network access" in payload["message"]


class _Response:
    status = 206
    headers = {"Content-Type": "application/zip"}

    def __init__(self, body: bytes):
        self._body = io.BytesIO(body)

    def read(self, amount: int) -> bytes:
        return self._body.read(amount)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_probe_is_explicit_and_bounded():
    with pytest.raises(ValueError, match="explicit --allow-network"):
        probe_source_pack(PRISM, allow_network=False)
    result = probe_source_pack(
        PRISM,
        allow_network=True,
        urlopen=lambda request, timeout: _Response(b"PK\x03\x04fixture"),
    )
    assert result["status"] == "PASS"
    assert result["request_count"] == 1
    assert result["bytes_transferred"] == len(b"PK\x03\x04fixture")
    assert result["materialized_asset"] is False
    assert "?" not in result["request_url_redacted"]
