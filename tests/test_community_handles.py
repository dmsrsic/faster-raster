from pathlib import Path
import base64
import hashlib
import json
import re
import shutil

import pytest
import yaml

from faster_raster.community_handles import HandleValidationError, load_records, normalize_handle, render_json, render_public_index, validate_record, write_surfaces


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


def test_member_id_and_schema_encodings_are_canonical():
    with pytest.raises(HandleValidationError):
        validate_record(_record(member_id="frh_" + "a" * 25 + "b"), root=ROOT)
    schema = json.loads((ROOT / "schemas" / "fasterraster-handle-v1.schema.json").read_text(encoding="utf-8"))
    claim_schema = json.loads((ROOT / "schemas" / "fasterraster-handle-claim-v1.schema.json").read_text(encoding="utf-8"))
    assert "(?!.*--)" in schema["properties"]["handle"]["pattern"]
    assert "(?!.*--)" in claim_schema["properties"]["handle"]["pattern"]
    assert schema["properties"]["member_id"]["pattern"].endswith("[aeimquy4]$")
    assert claim_schema["properties"]["member_id"]["pattern"].endswith("[aeimquy4]$")
    assert schema["properties"]["control"]["properties"]["public_key"]["pattern"].endswith("[AEIMQUYcgkosw048]$")
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
