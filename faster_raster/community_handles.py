from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import yaml

HANDLE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,28}[a-z0-9])$")
INTERESTS = {
    "remote-sensing",
    "reproducibility",
    "source-packs",
    "stac",
    "classification",
    "climate",
    "cartography",
}
FORBIDDEN_KEYS = {
    "email",
    "name",
    "real_name",
    "contact",
    "password",
    "token",
    "credential",
    "secret",
    "path",
    "html",
    "javascript",
    "url",
    "link",
    "biography",
    "location",
    "coordinates",
    "ip",
    "browser",
    "machine",
    "user_agent",
    "analytics",
}
RECORD_KEYS = {"schema_version", "handle", "member_id", "joined_at", "status", "visibility", "control", "profile"}
CONTROL_KEYS = {"method", "public_key", "public_key_fingerprint", "sequence", "last_claim_sha256"}
MANUAL_CONTROL_KEYS = {"method"}
PROFILE_KEYS = {"interests"}
MEMBER_ID_RE = re.compile(r"^frh_([a-z2-7]{26})$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _decode_member_id(value: Any) -> bytes:
    if not isinstance(value, str):
        raise HandleValidationError("member_id must be a random 128-bit base32 identifier")
    match = MEMBER_ID_RE.fullmatch(value)
    if match is None:
        raise HandleValidationError("member_id must be a random 128-bit base32 identifier")
    encoded = match.group(1)
    try:
        raw = base64.b32decode(encoded.upper() + "======", casefold=False)
    except (ValueError, base64.binascii.Error) as exc:
        raise HandleValidationError("member_id must be canonical base32") from exc
    if len(raw) != 16 or base64.b32encode(raw).decode("ascii").rstrip("=").lower() != encoded:
        raise HandleValidationError("member_id must be canonical base32")
    return raw


class HandleValidationError(ValueError):
    pass


def normalize_handle(value: str) -> str:
    if not isinstance(value, str) or value != value.lower() or not value.isascii():
        raise HandleValidationError("handles must be ASCII lowercase")
    if len(value) < 3 or len(value) > 30 or "--" in value or not HANDLE_RE.fullmatch(value):
        raise HandleValidationError("handle must match the lowercase 3-30 character policy")
    return value


def _rules(root: Path) -> tuple[set[str], tuple[str, ...]]:
    payload = yaml.safe_load((root / "community" / "reserved-handles.yaml").read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HandleValidationError("reserved handle policy is invalid")
    reserved = payload.get("reserved", [])
    prohibited = payload.get("prohibited_tokens", [])
    if not isinstance(reserved, list) or not isinstance(prohibited, list):
        raise HandleValidationError("reserved handle policy is invalid")
    return {normalize_handle(str(item)) for item in reserved}, tuple(str(item) for item in prohibited)


def _nonreusable_hashes(root: Path) -> set[str]:
    payload = yaml.safe_load((root / "community" / "nonreusable-handle-hashes.yaml").read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("sha256", []), list):
        raise HandleValidationError("non-reusable handle policy is invalid")
    hashes = {str(item) for item in payload["sha256"]}
    if any(not HASH_RE.fullmatch(item) for item in hashes):
        raise HandleValidationError("non-reusable handle policy contains an invalid hash")
    return hashes


def _decode_public_key(value: Any) -> bytes:
    if not isinstance(value, str) or not value or "=" in value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise HandleValidationError("control public key must be unpadded base64url")
    padding = "=" * (-len(value) % 4)
    try:
        raw = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (ValueError, TypeError, base64.binascii.Error) as exc:
        raise HandleValidationError("control public key must be valid base64url") from exc
    if len(raw) != 32 or base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != value:
        raise HandleValidationError("control public key must be a canonical raw Ed25519 key")
    return raw


def generate_member_id() -> str:
    """Return a fresh random 128-bit registry member identifier."""
    return "frh_" + base64.b32encode(secrets.token_bytes(16)).decode("ascii").rstrip("=").lower()


def build_manual_record(*, root: Path, handle: str, joined_at: str, interests: Iterable[str]) -> dict[str, Any]:
    """Build and validate a maintainer-reviewed, non-cryptographic record."""
    record = {
        "schema_version": "fasterraster.handle/v2",
        "handle": handle,
        "member_id": generate_member_id(),
        "joined_at": joined_at,
        "status": "active",
        "visibility": "public",
        "control": {"method": "maintainer-reviewed-request"},
        "profile": {"interests": list(interests)},
    }
    return validate_record(record, root=root)


def validate_record(record: dict[str, Any], *, root: Path) -> dict[str, Any]:
    if not isinstance(record, dict) or record.get("schema_version") not in {
        "fasterraster.handle/v1",
        "fasterraster.handle/v2",
    }:
        raise HandleValidationError("unsupported handle record schema")
    if set(record) != RECORD_KEYS:
        raise HandleValidationError("handle record contains unknown or missing fields")

    def scan(value: Any) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if str(key).lower() in FORBIDDEN_KEYS:
                    raise HandleValidationError(f"forbidden field: {key}")
                scan(nested)
        elif isinstance(value, list):
            for nested in value:
                scan(nested)

    scan(record)
    handle = normalize_handle(record["handle"])
    reserved, prohibited = _rules(root)
    normalized_hash = hashlib.sha256(handle.encode("ascii")).hexdigest()
    if handle in reserved or any(token and token in handle for token in prohibited) or normalized_hash in _nonreusable_hashes(root):
        raise HandleValidationError("handle is reserved or affiliation-claiming")
    _decode_member_id(record["member_id"])
    if not isinstance(record["joined_at"], str):
        raise HandleValidationError("joined_at must be an ISO date")
    try:
        parsed_date = date.fromisoformat(record["joined_at"])
    except ValueError as exc:
        raise HandleValidationError("joined_at must be an ISO date") from exc
    if parsed_date.isoformat() != record["joined_at"]:
        raise HandleValidationError("joined_at must use canonical YYYY-MM-DD form")
    if record["visibility"] != "public" or record["status"] not in {"active", "suspended", "retired", "removed"}:
        raise HandleValidationError("invalid status or visibility")
    profile = record["profile"]
    if not isinstance(profile, dict) or set(profile) != PROFILE_KEYS:
        raise HandleValidationError("profile must contain only interests")
    interests = profile["interests"]
    if not isinstance(interests, list) or len(interests) > 5 or len(set(interests)) != len(interests) or any(item not in INTERESTS for item in interests):
        raise HandleValidationError("profile interests must use the fixed unique enum")
    control = record["control"]
    if not isinstance(control, dict):
        raise HandleValidationError("record control must be an object")
    if record["schema_version"] == "fasterraster.handle/v1" or control.get("method") == "ed25519":
        if set(control) != CONTROL_KEYS or control["method"] != "ed25519":
            raise HandleValidationError("record control must declare the complete Ed25519 metadata")
        raw_key = _decode_public_key(control["public_key"])
        if not isinstance(control["public_key_fingerprint"], str) or not FINGERPRINT_RE.fullmatch(control["public_key_fingerprint"]):
            raise HandleValidationError("control public key fingerprint is invalid")
        expected_fingerprint = "sha256:" + hashlib.sha256(raw_key).hexdigest()
        if control["public_key_fingerprint"] != expected_fingerprint:
            raise HandleValidationError("control public key fingerprint does not match the key")
        if isinstance(control["sequence"], bool) or not isinstance(control["sequence"], int) or control["sequence"] < 1:
            raise HandleValidationError("control sequence must be a positive integer")
        if not isinstance(control["last_claim_sha256"], str) or not HASH_RE.fullmatch(control["last_claim_sha256"]):
            raise HandleValidationError("last claim hash is invalid")
    elif record["schema_version"] == "fasterraster.handle/v2" and set(control) == MANUAL_CONTROL_KEYS and control.get("method") == "maintainer-reviewed-request":
        pass
    else:
        raise HandleValidationError("record control is not a supported v2 method")
    return record


def load_records(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen_handles: set[str] = set()
    seen_members: set[str] = set()
    seen_keys: set[str] = set()
    for path in sorted((root / "community" / "handles").glob("*.yaml")):
        record = yaml.safe_load(path.read_text(encoding="utf-8"))
        validated = validate_record(record, root=root)
        control = validated["control"]
        if validated["handle"] in seen_handles:
            raise HandleValidationError(f"duplicate handle: {validated['handle']}")
        if validated["member_id"] in seen_members:
            raise HandleValidationError(f"duplicate member_id: {validated['member_id']}")
        if control.get("public_key") in seen_keys:
            raise HandleValidationError("duplicate control public key")
        if path.stem != validated["handle"]:
            raise HandleValidationError(f"record filename does not match handle: {path.name}")
        seen_handles.add(validated["handle"])
        seen_members.add(validated["member_id"])
        if control.get("public_key"):
            seen_keys.add(control["public_key"])
        records.append(validated)
    return sorted(records, key=lambda item: item["handle"])


def render_public_index(records: Iterable[dict[str, Any]]) -> str:
    rows = [record for record in records if record.get("status") == "active"]
    lines = [
        "# FasterRaster Handle Registry",
        "",
        f"Active registered FasterRaster handles: **{len(rows)}**.",
        "",
        "Handles are optional pseudonymous registry records. They are not accounts, unique-human counts, credentials, entitlements, endorsements, or proof of execution.",
        "",
        "| Handle | Joined | Interests | Status |",
        "|---|---|---|---|",
    ]
    for record in rows:
        lines.append(f"| `{record['handle']}` | {record['joined_at'][:7]} | {', '.join(record['profile']['interests']) or '—'} | {record['status']} |")
    if not rows:
        lines.append("| _No public records yet_ | — | — | — |")
    lines += ["", "See [privacy and governance](privacy.md) before joining.", ""]
    return "\n".join(lines)


def render_json(records: Iterable[dict[str, Any]]) -> str:
    public = []
    for record in records:
        item = dict(record)
        control = dict(item.get("control", {}))
        control.pop("public_key", None)
        item["control"] = control
        public.append(item)
    return json.dumps({"schema_version": "fasterraster.handle-index/v1", "records": public}, indent=2, sort_keys=True) + "\n"


def write_surfaces(root: Path) -> list[Path]:
    records = load_records(root)
    outputs = {
        root / "docs" / "community" / "index.md": render_public_index(records),
        root / "docs" / "generated" / "handles.json": render_json(records),
    }
    for path, contents in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8", newline="\n")
    return sorted(outputs)
