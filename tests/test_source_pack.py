from __future__ import annotations

import hashlib
import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest
import yaml

from faster_raster import fr_cli
from faster_raster.adapter_contract import stable_json
from faster_raster.source_pack import (
    compile_source_materialization_request,
    compile_source_pack_handoff,
    compile_source_pack_plan,
    explain_source_pack,
    pack_source_pack,
    probe_source_pack,
    scaffold_source_pack,
    test_source_pack as run_source_pack_test,
    validate_source_materialization_request,
    validate_source_pack,
)


ROOT = Path(__file__).resolve().parent.parent
PRISM = ROOT / "examples" / "sauce-packs" / "prism-daily.sauce"
CDSE = ROOT / "examples" / "sauce-packs" / "copernicus-cdse.sauce"
ARCGIS = ROOT / "examples" / "sauce-packs" / "usda-cdl-imageserver.sauce"
LOCAL = ROOT / "examples" / "sauce-packs" / "verified-local-raster.sauce"


def _manifest(path: Path) -> dict:
    return yaml.safe_load((path / "sauce.yaml").read_text(encoding="utf-8"))


def _write_manifest(path: Path, value: dict) -> None:
    (path / "sauce.yaml").write_text(
        yaml.safe_dump(value, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )


def test_shipped_source_packs_validate_and_pass_offline_goldens():
    for path in (PRISM, CDSE, ARCGIS, LOCAL):
        validation = validate_source_pack(path)
        assert validation["status"] == "PASS"
        assert validation["family_contract_status"] == "VALID"
        assert validation["provider_evidence_status"] == "COMPLETE"
        assert run_source_pack_test(path)["status"] == "PASS"


def test_materialization_requests_are_family_specific_hash_bound_and_deterministic():
    cases = (
        (
            PRISM,
            {
                "requested_asset_roles": ["precipitation"],
                "full_object": True,
            },
        ),
        (
            CDSE,
            {
                "requested_asset_roles": ["red"],
                "bbox": [-77.1, 38.8, -76.9, 39.0],
                "bbox_crs": "EPSG:4326",
            },
        ),
        (
            ARCGIS,
            {
                "requested_asset_roles": ["classification"],
                "bbox": [-77.1, 38.8, -76.9, 39.0],
                "bbox_crs": "EPSG:4326",
                "output_width": 512,
                "output_height": 256,
            },
        ),
        (
            LOCAL,
            {
                "requested_asset_roles": ["classification"],
                "full_object": True,
            },
        ),
    )
    hashes: set[str] = set()
    for pack, kwargs in cases:
        plan = compile_source_pack_plan(pack)
        first = compile_source_materialization_request(plan, **kwargs)
        second = compile_source_materialization_request(plan, **kwargs)
        assert first == second
        assert first == json.loads(
            (pack / "golden_materialization_request.json").read_text(
                encoding="utf-8"
            )
        )
        assert validate_source_materialization_request(plan, first) == first
        assert first["source_plan_sha256"] == plan["plan_sha256"]
        assert first["original_source_plan_unchanged"] is True
        hashes.add(first["materialization_request_sha256"])
    assert len(hashes) == 4


@pytest.mark.parametrize(
    ("pack", "kwargs", "message"),
    (
        (
            CDSE,
            {"requested_asset_roles": ["red"], "full_object": True},
            "requires an explicit spatial bbox",
        ),
        (
            PRISM,
            {
                "requested_asset_roles": ["precipitation"],
                "bbox": [0, 0, 1, 1],
                "bbox_crs": "EPSG:4326",
            },
            "requires an explicit full-object request",
        ),
        (
            ARCGIS,
            {
                "requested_asset_roles": ["classification"],
                "bbox": [1, 0, 0, 1],
                "bbox_crs": "EPSG:4326",
                "output_width": 1,
                "output_height": 1,
            },
            "strictly increasing",
        ),
        (
            ARCGIS,
            {
                "requested_asset_roles": ["classification"],
                "bbox": [0, 0, 1, 1],
                "bbox_crs": "EPSG:3857",
                "output_width": 1,
                "output_height": 1,
            },
            "must exactly match",
        ),
        (
            ARCGIS,
            {
                "requested_asset_roles": ["classification"],
                "bbox": [0, 0, 1, 1],
                "bbox_crs": "EPSG:4326",
                "output_width": 0,
                "output_height": 1,
            },
            "integers from 1 to 16384",
        ),
        (
            LOCAL,
            {
                "requested_asset_roles": ["undeclared"],
                "full_object": True,
            },
            "must be declared",
        ),
    ),
)
def test_materialization_request_rejects_invalid_family_intent(
    pack,
    kwargs,
    message,
):
    with pytest.raises(ValueError, match=message):
        compile_source_materialization_request(
            compile_source_pack_plan(pack),
            **kwargs,
        )


def test_materialization_request_rejects_plan_and_request_hash_mismatches():
    plan = compile_source_pack_plan(LOCAL)
    request = compile_source_materialization_request(
        plan,
        requested_asset_roles=["classification"],
        full_object=True,
    )
    changed_plan = json.loads(json.dumps(plan))
    changed_plan["plan_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="plan SHA-256 mismatch"):
        compile_source_materialization_request(
            changed_plan,
            requested_asset_roles=["classification"],
            full_object=True,
        )
    changed_request = json.loads(json.dumps(request))
    changed_request["source_plan_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="different plan hash"):
        validate_source_materialization_request(plan, changed_request)


def test_materialization_request_rejects_blocked_plans_and_override_fields():
    plan = compile_source_pack_plan(CDSE)
    blocked = json.loads(json.dumps(plan))
    blocked["status"] = "BLOCKED_PROVIDER_EVIDENCE"
    blocked["executable"] = False
    blocked["blocked_before_network"] = True
    blocked["plan_sha256"] = hashlib.sha256(
        stable_json(
            {
                key: item
                for key, item in blocked.items()
                if key != "plan_sha256"
            }
        ).encode("utf-8")
    ).hexdigest()
    with pytest.raises(ValueError, match="blocked or non-executable"):
        compile_source_materialization_request(
            blocked,
            requested_asset_roles=["red"],
            bbox=[-77.1, 38.8, -76.9, 39.0],
            bbox_crs="EPSG:4326",
        )

    request = compile_source_materialization_request(
        plan,
        requested_asset_roles=["red"],
        bbox=[-77.1, 38.8, -76.9, 39.0],
        bbox_crs="EPSG:4326",
    )
    for field, value in (
        ("network_policy", {"max_requests": 999}),
        ("credential", "secret"),
        ("resolved_time", "2099"),
        ("endpoint", "https://evil.example"),
    ):
        changed = json.loads(json.dumps(request))
        changed[field] = value
        with pytest.raises(ValueError, match="unknown or missing fields"):
            validate_source_materialization_request(plan, changed)


def test_materialization_request_cli_is_zero_network(tmp_path, monkeypatch):
    plan_path = tmp_path / "plan.json"
    request_path = tmp_path / "request.json"
    plan_path.write_text(
        json.dumps(compile_source_pack_plan(LOCAL)),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("network called")
        ),
    )
    assert (
        fr_cli.main(
            [
                "sauce",
                "materialize-request",
                str(plan_path),
                "--out",
                str(request_path),
                "--role",
                "classification",
                "--full-object",
            ]
        )
        == 0
    )
    assert validate_source_materialization_request(
        plan_path,
        request_path,
    )["pack_id"] == "verified-local-raster"


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


def test_arcgis_and_verified_local_adapter_families_are_explicit():
    arcgis = compile_source_pack_plan(ARCGIS)
    assert arcgis["endpoint_contract"]["family_contract"]["operation"] == "exportImage"
    assert arcgis["source_contract"]["semantic_type"] == "categorical"
    assert arcgis["source_contract"]["resampling"] == "nearest"
    local = compile_source_pack_plan(LOCAL)
    assert local["endpoint_contract"]["kind"] == "verified_local_reference"
    assert local["endpoint_contract"]["local_sha256"] == hashlib.sha256(
        (LOCAL / "fixture.tif").read_bytes()
    ).hexdigest()


def test_frozen_handoff_compiles_byte_identically_and_never_resolves_credentials(
    tmp_path,
):
    first = compile_source_pack_handoff(CDSE, tmp_path / "one.json")
    second = compile_source_pack_handoff(CDSE, tmp_path / "two.json")
    assert first["handoff_sha256"] == second["handoff_sha256"]
    assert (tmp_path / "one.json").read_bytes() == (tmp_path / "two.json").read_bytes()
    plan = json.loads((tmp_path / "one.json").read_text(encoding="utf-8"))
    assert plan["status"] == "CREDENTIAL_REQUIRED"
    assert plan["executable"] is True
    assert plan["credential_requirement"]["credential_ref"] == "copernicus-production"
    assert plan["credential_requirement"]["resolved_secret_present"] is False
    assert plan["network_policy"]["asset_hosts"] == ["stac.dataspace.copernicus.eu"]
    serialized = json.dumps(plan, sort_keys=True).lower()
    assert "authorization" not in serialized
    assert "bearer " not in serialized


def test_incomplete_provider_evidence_is_structurally_valid_but_not_executable(
    tmp_path,
):
    path = scaffold_source_pack(tmp_path / "incomplete")
    validation = validate_source_pack(path)
    assert validation["status"] == "PASS"
    assert validation["provider_evidence_status"] == "INCOMPLETE"
    plan = compile_source_pack_plan(path)
    assert plan["status"] == "BLOCKED_PROVIDER_EVIDENCE"
    assert plan["blocked_before_network"] is True
    with pytest.raises(ValueError, match="blocked Source Pack"):
        compile_source_pack_handoff(path, tmp_path / "blocked.json")


def test_stac_asset_scope_missing_role_and_unsupported_placeholder_fail_closed(
    tmp_path,
):
    stac = tmp_path / "stac.sauce"
    shutil.copytree(CDSE, stac)
    manifest = _manifest(stac)
    manifest["access"]["asset_hosts"] = ["assets.example.com"]
    _write_manifest(stac, manifest)
    result = validate_source_pack(stac)
    assert result["status"] == "FAIL"
    assert any("asset_host_scope" in item for item in result["errors"])

    fixture = json.loads((stac / "probe_fixture.json").read_text(encoding="utf-8"))
    fixture["family_contract"]["asset_host_scope"] = ["assets.example.com"]
    fixture["family_contract"]["asset_selection"]["required_roles"] = ["missing"]
    (stac / "probe_fixture.json").write_text(
        json.dumps(fixture, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    result = validate_source_pack(stac)
    assert any("required asset roles" in item for item in result["errors"])

    static = tmp_path / "static.sauce"
    shutil.copytree(PRISM, static)
    manifest = _manifest(static)
    manifest["adapter"]["url_template"] = "https://data.prism.oregonstate.edu/{password}.zip"
    manifest["temporal"]["template_variables"]["password"] = "opaque"
    _write_manifest(static, manifest)
    result = validate_source_pack(static)
    assert any("unsupported variables" in item for item in result["errors"])


def test_temporal_selection_and_line_endings_are_explicit_and_deterministic(
    tmp_path,
):
    path = tmp_path / "temporal.sauce"
    shutil.copytree(PRISM, path)
    manifest = _manifest(path)
    manifest["temporal"]["requested"] = "2021-01-01"
    _write_manifest(path, manifest)
    plan = compile_source_pack_plan(path)
    assert plan["status"] == "AWAITING_TEMPORAL_SELECTION"
    assert plan["executable"] is False
    selected = compile_source_pack_plan(path, selected_time="2022-01-01")
    assert selected["temporal_resolution"]["selection_method"] == "explicit_user_selection"
    assert selected["requested_time"] == "2021-01-01"
    assert selected["resolved_time"] == "2022-01-01"

    lf_hash = validate_source_pack(PRISM)["source_pack_sha256"]
    copied = tmp_path / "crlf.sauce"
    shutil.copytree(PRISM, copied)
    manifest_path = copied / "sauce.yaml"
    manifest_path.write_bytes(
        manifest_path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    )
    assert validate_source_pack(copied)["source_pack_sha256"] == lf_hash


def test_explanation_distinguishes_schema_family_evidence_and_readiness():
    result = explain_source_pack(CDSE)
    assert result["structural_status"] == "SCHEMA_VALID"
    assert result["family_contract_status"] == "VALID"
    assert result["provider_evidence_status"] == "COMPLETE"
    assert result["offline_planning_status"] == "READY_FOR_OFFLINE_DETERMINISTIC_PLANNING"
    assert result["handoff_schema_version"] == "fasterraster.source-pack-plan/v1"


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
