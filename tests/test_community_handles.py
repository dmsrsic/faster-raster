from pathlib import Path
import base64
import hashlib
import json
import re
import shutil

import pytest
import yaml

from faster_raster.community_handles import HandleValidationError, build_manual_record, load_records, normalize_handle, render_json, render_public_index, validate_record, write_surfaces
from scripts import manage_handle_registry
from scripts.manage_handle_registry import activate, check


ROOT = Path(__file__).resolve().parents[1]


def _member_id(raw: bytes) -> str:
    return "frh_" + base64.b32encode(raw).decode("ascii").rstrip("=").lower()


@pytest.mark.parametrize("value", ["Aster", "ab", "a" * 31, "two--hyphens", "bad_name", "ümlaut"])
def test_handle_normalization_is_conservative(value):
    with pytest.raises(HandleValidationError):
        normalize_handle(value)


def test_empty_registry_is_deterministic():
    first = render_public_index([])
    second = render_public_index([])
    assert first == second
    assert "No public records yet" in first


def test_registry_has_no_seed_records():
    assert not list((ROOT / "community" / "handles").glob("*.yaml"))


def test_checked_in_empty_surfaces_match_generators():
    records = load_records(ROOT)
    assert (ROOT / "docs" / "community" / "index.md").read_text(encoding="utf-8") == render_public_index(records)
    assert (ROOT / "docs" / "generated" / "handles.json").read_text(encoding="utf-8") == render_json(records)


def test_handle_surfaces_generate_twice_to_identical_temporary_bytes(tmp_path):
    roots = [tmp_path / "first", tmp_path / "second"]
    for root in roots:
        shutil.copytree(ROOT / "community", root / "community")
        write_surfaces(root)
    relative_outputs = [path.relative_to(roots[0]) for path in write_surfaces(roots[0])]
    assert relative_outputs
    for relative in relative_outputs:
        assert (roots[0] / relative).read_bytes() == (roots[1] / relative).read_bytes()


def _record(**changes):
    raw_key = bytes(range(32))
    record = {
        "schema_version": "fasterraster.handle/v1",
        "handle": "pixel-ranger",
        "member_id": _member_id(bytes(16)),
        "joined_at": "2026-08-01",
        "status": "active",
        "visibility": "public",
        "control": {
            "method": "ed25519",
            "public_key": base64.urlsafe_b64encode(raw_key).decode().rstrip("="),
            "public_key_fingerprint": "sha256:" + hashlib.sha256(raw_key).hexdigest(),
            "sequence": 1,
            "last_claim_sha256": "0" * 64,
        },
        "profile": {"interests": ["stac"]},
    }
    for key, value in changes.items():
        if key in {"control", "profile"}:
            record[key].update(value)
        else:
            record[key] = value
    return record


def test_valid_record_and_minimal_public_json():
    record = validate_record(_record(), root=ROOT)
    assert record["handle"] == "pixel-ranger"
    assert "public_key" not in json.loads(render_json([record]))["records"][0]["control"]
    assert record["control"]["public_key"] not in render_public_index([record])


def test_v2_manual_record_has_only_review_control(tmp_path):
    shutil.copytree(ROOT / "community", tmp_path / "community")
    record = build_manual_record(
        root=tmp_path,
        handle="pixel-ranger",
        joined_at="2026-08-04",
        interests=["stac", "reproducibility"],
    )
    assert record["schema_version"] == "fasterraster.handle/v2"
    assert record["control"] == {"method": "maintainer-reviewed-request"}
    assert "member_id" in record and record["member_id"].startswith("frh_")
    assert "github" not in json.dumps(record).lower()
    with pytest.raises(HandleValidationError):
        validate_record({**_record(), "schema_version": "fasterraster.handle/v1", "control": {"method": "maintainer-reviewed-request"}}, root=tmp_path)


def test_manual_activation_writes_record_and_surfaces(tmp_path):
    shutil.copytree(ROOT / "community", tmp_path / "community")
    path = activate(
        root=tmp_path,
        handle="pixel-ranger",
        joined_at="2026-08-04",
        interests=["stac"],
        approved_request=True,
    )
    assert path == tmp_path / "community" / "handles" / "pixel-ranger.yaml"
    records = load_records(tmp_path)
    assert records[0]["control"] == {"method": "maintainer-reviewed-request"}
    assert not check(tmp_path)
    assert "pixel-ranger" in (tmp_path / "docs" / "community" / "index.md").read_text(encoding="utf-8")
    assert "public_key" not in (tmp_path / "docs" / "generated" / "handles.json").read_text(encoding="utf-8")


def test_registry_check_reports_stale_surface(tmp_path):
    shutil.copytree(ROOT / "community", tmp_path / "community")
    write_surfaces(tmp_path)
    assert not check(tmp_path)
    index = tmp_path / "docs" / "community" / "index.md"
    index.write_text(index.read_text(encoding="utf-8") + "stale\n", encoding="utf-8")
    assert check(tmp_path) == [index]


@pytest.mark.parametrize("failure_at", [1, 2])
def test_manual_activation_rolls_back_partial_replacements(tmp_path, monkeypatch, failure_at):
    shutil.copytree(ROOT / "community", tmp_path / "community")
    original_replace = manage_handle_registry.os.replace
    calls = 0

    def fail_on_second_replace(source, destination):
        nonlocal calls
        calls += 1
        if calls == failure_at:
            raise OSError("simulated replacement failure")
        return original_replace(source, destination)

    monkeypatch.setattr(manage_handle_registry.os, "replace", fail_on_second_replace)
    with pytest.raises(OSError, match="simulated"):
        activate(
            root=tmp_path,
            handle="pixel-ranger",
            joined_at="2026-08-04",
            interests=["stac"],
            approved_request=True,
        )
    assert not list((tmp_path / "community" / "handles").glob("*.yaml"))
    assert not (tmp_path / "docs").exists()


def test_manual_activation_rollback_preserves_preexisting_directories_and_files(tmp_path, monkeypatch):
    shutil.copytree(ROOT / "community", tmp_path / "community")
    (tmp_path / "docs").mkdir()
    unrelated = tmp_path / "docs" / "keep.txt"
    unrelated.write_text("unrelated\n", encoding="utf-8")
    original_replace = manage_handle_registry.os.replace

    def fail_on_second_replace(source, destination):
        if destination.name == "handles.json":
            raise OSError("simulated replacement failure")
        return original_replace(source, destination)

    monkeypatch.setattr(manage_handle_registry.os, "replace", fail_on_second_replace)
    with pytest.raises(OSError, match="simulated"):
        activate(
            root=tmp_path,
            handle="pixel-ranger",
            joined_at="2026-08-04",
            interests=["stac"],
            approved_request=True,
        )
    assert (tmp_path / "docs").is_dir()
    assert unrelated.read_text(encoding="utf-8") == "unrelated\n"
    assert not (tmp_path / "docs" / "community").exists()
    assert not (tmp_path / "docs" / "generated").exists()
    assert not list((tmp_path / "community" / "handles").glob("*.yaml"))


def test_manual_activation_rolls_back_temporary_staging_failure(tmp_path, monkeypatch):
    shutil.copytree(ROOT / "community", tmp_path / "community")
    original_named_temporary_file = manage_handle_registry.tempfile.NamedTemporaryFile
    calls = 0

    def fail_on_second_temporary_file(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated temporary-file failure")
        return original_named_temporary_file(*args, **kwargs)

    monkeypatch.setattr(manage_handle_registry.tempfile, "NamedTemporaryFile", fail_on_second_temporary_file)
    with pytest.raises(OSError, match="simulated temporary-file failure"):
        activate(
            root=tmp_path,
            handle="pixel-ranger",
            joined_at="2026-08-04",
            interests=["stac"],
            approved_request=True,
        )
    assert not list((tmp_path / "community" / "handles").glob("*.yaml"))
    assert not (tmp_path / "docs").exists()


def test_member_id_and_schema_encodings_are_canonical():
    with pytest.raises(HandleValidationError):
        validate_record(_record(member_id="frh_" + "a" * 25 + "b"), root=ROOT)
    schema = json.loads((ROOT / "schemas" / "fasterraster-handle-v1.schema.json").read_text(encoding="utf-8"))
    v2_schema = json.loads((ROOT / "schemas" / "fasterraster-handle-v2.schema.json").read_text(encoding="utf-8"))
    claim_schema = json.loads((ROOT / "schemas" / "fasterraster-handle-claim-v1.schema.json").read_text(encoding="utf-8"))
    assert "(?!.*--)" in schema["properties"]["handle"]["pattern"]
    assert "(?!.*--)" in claim_schema["properties"]["handle"]["pattern"]
    assert schema["properties"]["member_id"]["pattern"].endswith("[aeimquy4]$")
    assert claim_schema["properties"]["member_id"]["pattern"].endswith("[aeimquy4]$")
    assert schema["properties"]["control"]["properties"]["public_key"]["pattern"].endswith("[AEIMQUYcgkosw048]$")
    assert v2_schema["properties"]["schema_version"]["const"] == "fasterraster.handle/v2"
    assert v2_schema["properties"]["control"]["oneOf"][0]["properties"]["method"]["const"] == "maintainer-reviewed-request"
    assert claim_schema["properties"]["claim_nonce"]["pattern"].endswith("[AQgw]$")
    assert claim_schema["properties"]["signature"]["pattern"].endswith("[AQgw]$")

    assert not re.fullmatch(schema["properties"]["member_id"]["pattern"], "frh_" + "a" * 25 + "b")
    assert not re.fullmatch(schema["properties"]["control"]["properties"]["public_key"]["pattern"], "A" * 42 + "B")
    assert not re.fullmatch(claim_schema["properties"]["claim_nonce"]["pattern"], "A" * 21 + "B")
    assert not re.fullmatch(claim_schema["properties"]["signature"]["pattern"], "A" * 85 + "B")


@pytest.mark.parametrize(
    "control",
    [
        {"public_key": "A" * 42 + "B"},
        {"public_key": base64.urlsafe_b64encode(bytes(range(32))).decode()},
    ],
)
def test_public_key_rejects_padding_and_noncanonical_padding_bits(control):
    with pytest.raises(HandleValidationError, match="public key"):
        validate_record(_record(control=control), root=ROOT)


def test_load_records_rejects_duplicate_handle(tmp_path):
    shutil.copytree(ROOT / "community", tmp_path / "community")
    handles = tmp_path / "community" / "handles"
    first = _record()
    second_key = bytes([2]) * 32
    second = _record(
        member_id=_member_id(bytes([1]) * 16),
        control={
            "public_key": base64.urlsafe_b64encode(second_key).decode().rstrip("="),
            "public_key_fingerprint": "sha256:" + hashlib.sha256(second_key).hexdigest(),
        },
    )
    (handles / "pixel-ranger.yaml").write_text(yaml.safe_dump(first, sort_keys=False), encoding="utf-8")
    (handles / "second-file.yaml").write_text(yaml.safe_dump(second, sort_keys=False), encoding="utf-8")
    with pytest.raises(HandleValidationError, match="duplicate handle"):
        load_records(tmp_path)


def test_load_records_rejects_duplicate_handle_member_and_key(tmp_path):
    shutil.copytree(ROOT / "community", tmp_path / "community")
    handles = tmp_path / "community" / "handles"
    first = _record()
    second = _record(handle="pixel-ranger-two", member_id=_member_id(bytes([1]) * 16))
    for name, record in (("pixel-ranger.yaml", first), ("pixel-ranger-two.yaml", second)):
        (handles / name).write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")
    with pytest.raises(HandleValidationError, match="control public key"):
        load_records(tmp_path)


def test_load_records_rejects_duplicate_member_id(tmp_path):
    shutil.copytree(ROOT / "community", tmp_path / "community")
    handles = tmp_path / "community" / "handles"
    first = _record()
    second_key = bytes([1]) * 32
    second = _record(
        handle="pixel-ranger-two",
        control={
            "public_key": base64.urlsafe_b64encode(second_key).decode().rstrip("="),
            "public_key_fingerprint": "sha256:" + hashlib.sha256(second_key).hexdigest(),
        },
    )
    (handles / "pixel-ranger.yaml").write_text(yaml.safe_dump(first, sort_keys=False), encoding="utf-8")
    (handles / "pixel-ranger-two.yaml").write_text(yaml.safe_dump(second, sort_keys=False), encoding="utf-8")
    with pytest.raises(HandleValidationError, match="duplicate member_id"):
        load_records(tmp_path)


def test_load_records_rejects_filename_mismatch_and_nonreusable_hash(tmp_path):
    shutil.copytree(ROOT / "community", tmp_path / "community")
    (tmp_path / "community" / "handles" / "wrong-name.yaml").write_text(
        yaml.safe_dump(_record(), sort_keys=False), encoding="utf-8"
    )
    with pytest.raises(HandleValidationError, match="filename"):
        load_records(tmp_path)

    (tmp_path / "community" / "handles" / "wrong-name.yaml").unlink()
    (tmp_path / "community" / "nonreusable-handle-hashes.yaml").write_text(
        "sha256:\n  - " + hashlib.sha256(b"pixel-ranger").hexdigest() + "\n", encoding="utf-8"
    )
    with pytest.raises(HandleValidationError, match="reserved"):
        validate_record(_record(), root=tmp_path)


def test_forbidden_nested_content_is_rejected():
    with pytest.raises(HandleValidationError, match="forbidden field"):
        validate_record(_record(profile={"interests": ["stac"], "nested": {"email": "x"}}), root=ROOT)


@pytest.mark.parametrize(
    "changes",
    [
        {"joined_at": "2026-8-1"},
        {"unexpected": "field"},
        {"profile": {"interests": ["stac", "stac"]}},
        {"control": {"public_key_fingerprint": "sha256:" + "f" * 64}},
        {"control": {"sequence": 0}},
    ],
)
def test_record_validation_rejects_invalid_fields(changes):
    with pytest.raises(HandleValidationError):
        validate_record(_record(**changes), root=ROOT)


def test_nonreusable_hashes_must_be_canonical(tmp_path):
    shutil.copytree(ROOT / "community", tmp_path / "community")
    (tmp_path / "community" / "nonreusable-handle-hashes.yaml").write_text(
        "sha256:\n  - " + ("A" * 64) + "\n", encoding="utf-8"
    )
    with pytest.raises(HandleValidationError, match="invalid hash"):
        validate_record(_record(), root=tmp_path)


def test_registry_rendering_is_sorted_and_deterministic(tmp_path):
    shutil.copytree(ROOT / "community", tmp_path / "community")
    handles = tmp_path / "community" / "handles"
    records = []
    for index, handle in enumerate(("zulu-raster", "alpha-raster"), start=1):
        key = bytes([index]) * 32
        record = _record(
            handle=handle,
            member_id=_member_id(bytes([index]) * 16),
            control={
                "public_key": base64.urlsafe_b64encode(key).decode().rstrip("="),
                "public_key_fingerprint": "sha256:" + hashlib.sha256(key).hexdigest(),
            },
        )
        records.append(record)
        (handles / f"{handle}.yaml").write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")
    loaded = load_records(tmp_path)
    assert [record["handle"] for record in loaded] == ["alpha-raster", "zulu-raster"]
    assert render_json(loaded) == render_json(load_records(tmp_path))
    assert render_public_index(loaded) == render_public_index(load_records(tmp_path))


@pytest.mark.parametrize("handle", ["admin", "github-ranger", "nasa-team"])
def test_reserved_and_affiliation_handles_are_rejected(handle):
    with pytest.raises(HandleValidationError):
        validate_record(_record(handle=handle), root=ROOT)


def test_handle_request_issue_form_contract():
    form = yaml.safe_load(
        (ROOT / ".github" / "ISSUE_TEMPLATE" / "fasterraster-handle-request.yml").read_text(encoding="utf-8")
    )
    assert form["name"] == "Request a FasterRaster handle"
    assert form["title"] == "[Handle request] "
    assert form.get("assignees", []) == []
    assert form.get("labels", []) == []

    fields = {item["id"]: item for item in form["body"] if "id" in item}
    assert set(fields) == {"requested_handle", "interests", "acknowledgements"}
    assert fields["requested_handle"]["validations"]["required"] is True
    assert fields["interests"]["validations"]["required"] is True
    assert fields["interests"]["attributes"]["multiple"] is True
    assert fields["interests"]["attributes"]["options"] == [
        "remote-sensing",
        "reproducibility",
        "source-packs",
        "stac",
        "classification",
        "climate",
        "cartography",
    ]
    assert fields["acknowledgements"]["validations"]["required"] is True
    assert all(option["required"] is True for option in fields["acknowledgements"]["attributes"]["options"])


def test_handle_request_form_states_public_request_only_boundaries():
    form = yaml.safe_load(
        (ROOT / ".github" / "ISSUE_TEMPLATE" / "fasterraster-handle-request.yml").read_text(encoding="utf-8")
    )
    form_text = json.dumps(form).lower()
    assert "github username" in form_text and "public" in form_text
    for term in ("cloned", "installed", "executed", "used"):
        assert term in form_text
    assert "does not create an active registry record" in form_text
    assert "maintainer review" in form_text
    for term in ("private key", "credential", "token", "password", "contact information"):
        assert term in form_text

    forbidden_fields = ("email", "real name", "legal name", "location", "biography", "private key", "password", "token", "credential")
    for field in (item for item in form["body"] if "id" in item):
        field_name = f'{field["id"]} {field.get("attributes", {}).get("label", "")}'.lower()
        assert not any(forbidden in field_name for forbidden in forbidden_fields)


def test_handle_request_documentation_contract():
    join = (ROOT / "docs" / "community" / "join.md").read_text(encoding="utf-8")
    privacy = (ROOT / "docs" / "community" / "privacy.md").read_text(encoding="utf-8")
    assert "https://github.com/dmsrsic/faster-raster/issues/new?template=fasterraster-handle-request.yml" in join
    assert "does not create an active registry record" in join
    assert "maintainer-reviewed-request" in join
    assert "Automatic activation" in privacy and "identity verification remain disabled" in privacy
    assert "does not automatically copy that username into the generated Handle Registry index" in privacy
