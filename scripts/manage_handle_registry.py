"""Offline maintainer tooling for the public FasterRaster Handle Registry."""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from typing import Iterable

import yaml

# Direct repository execution (`python scripts/manage_handle_registry.py`) does
# not put the checkout root on sys.path; package execution already does.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faster_raster.community_handles import (
    INTERESTS,
    HandleValidationError,
    build_manual_record,
    load_records,
    render_json,
    render_public_index,
)


def _surface_contents(root: Path, records: Iterable[dict[str, object]]) -> dict[Path, str]:
    records = list(records)
    return {
        root / "docs" / "community" / "index.md": render_public_index(records),
        root / "docs" / "generated" / "handles.json": render_json(records),
    }


def check(root: Path) -> list[Path]:
    """Validate all records and return generated files that are out of date."""
    records = load_records(root)
    stale = []
    for path, contents in _surface_contents(root, records).items():
        if not path.is_file() or path.read_text(encoding="utf-8") != contents:
            stale.append(path)
    return stale


def _atomic_write(changes: dict[Path, bytes]) -> None:
    backups = {path: path.read_bytes() if path.exists() else None for path in changes}
    temporary: list[tuple[Path, Path]] = []
    created_dirs: list[Path] = []
    created_dir_set: set[Path] = set()
    try:
        for path, contents in changes.items():
            missing: list[Path] = []
            parent = path.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for directory in reversed(missing):
                directory.mkdir()
                if directory not in created_dir_set:
                    created_dirs.append(directory)
                    created_dir_set.add(directory)
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
                handle.write(contents)
                temp_path = Path(handle.name)
            temporary.append((path, temp_path))
        for path, temp_path in temporary:
            os.replace(temp_path, path)
    except Exception:
        for path, temp_path in temporary:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        for path, contents in backups.items():
            try:
                if contents is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(contents)
            except OSError:
                pass
        for directory in sorted(created_dirs, key=lambda item: len(item.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise


def activate(*, root: Path, handle: str, joined_at: str, interests: list[str], approved_request: bool) -> Path:
    """Create one approved manual record and refresh both public surfaces."""
    if not approved_request:
        raise HandleValidationError("activation requires explicit approved-request confirmation")
    root = root.resolve()
    records = load_records(root)
    record = build_manual_record(root=root, handle=handle, joined_at=joined_at, interests=interests)
    if any(item["handle"] == record["handle"] for item in records):
        raise HandleValidationError(f"duplicate handle: {record['handle']}")
    if any(item["member_id"] == record["member_id"] for item in records):
        raise HandleValidationError(f"duplicate member_id: {record['member_id']}")
    all_records = sorted([*records, record], key=lambda item: item["handle"])
    record_path = root / "community" / "handles" / f"{record['handle']}.yaml"
    if record_path.exists():
        raise HandleValidationError(f"record already exists: {record_path.name}")
    record_bytes = yaml.safe_dump(record, sort_keys=False, allow_unicode=False).replace("\r\n", "\n").encode("utf-8")
    changes = {record_path: record_bytes}
    changes.update({path: contents.encode("utf-8") for path, contents in _surface_contents(root, all_records).items()})
    _atomic_write(changes)
    return record_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Maintain the FasterRaster public handle registry offline.")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository checkout (default: current directory)")
    commands = parser.add_subparsers(dest="command", required=True)
    check_parser = commands.add_parser("check", help="validate records and generated public surfaces")
    check_parser.add_argument("--root", type=Path, default=argparse.SUPPRESS)
    check_parser.set_defaults(handler="check")
    activate_parser = commands.add_parser("activate", help="activate one explicitly approved Issue Form request")
    activate_parser.add_argument("--root", type=Path, default=argparse.SUPPRESS)
    activate_parser.add_argument("--handle", required=True)
    activate_parser.add_argument("--joined-at", required=True, help="explicit maintainer acceptance date, YYYY-MM-DD")
    activate_parser.add_argument("--interest", action="append", choices=sorted(INTERESTS), required=True)
    activate_parser.add_argument("--confirm-approved-request", action="store_true", required=True)
    activate_parser.set_defaults(handler="activate")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    try:
        if args.handler == "check":
            stale = check(root)
            if stale:
                for path in stale:
                    print(f"stale generated surface: {path}")
                return 1
            print("handle registry is valid and generated surfaces are current")
            return 0
        path = activate(
            root=root,
            handle=args.handle,
            joined_at=args.joined_at,
            interests=args.interest,
            approved_request=args.confirm_approved_request,
        )
        print(f"activated manual handle record: {path}")
        return 0
    except (HandleValidationError, OSError, TypeError, ValueError) as exc:
        print(f"handle registry error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
