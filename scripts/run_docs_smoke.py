"""Execute the fixed offline-smoke command manifest without a shell."""
from __future__ import annotations

import argparse
import os
import socket
import tempfile
from pathlib import Path

import yaml

from faster_raster import fr_cli


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path(__file__).resolve().parents[1] / "configs" / "docs_command_smoke.yaml")
    args = parser.parse_args(argv)
    manifest = yaml.safe_load(args.manifest.read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != "fasterraster.docs-command-smoke/v1"
        or not isinstance(manifest.get("commands"), list)
    ):
        raise SystemExit("unsupported smoke manifest")
    commands = manifest.get("commands", [])
    if not isinstance(commands, list) or any(
        not isinstance(entry, dict)
        or entry.get("classification") != "offline-smoke"
        or not isinstance(entry.get("argv"), list)
        or not all(isinstance(item, str) for item in entry.get("argv", []))
        for entry in commands
    ):
        raise SystemExit("invalid offline smoke manifest")
    repository_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="fasterraster-doc-smoke-") as directory:
        root = Path(directory)
        os.environ.update(
            {
                "FASTERRASTER_CONFIG_HOME": str(root / "config"),
                "FASTERRASTER_STATE_HOME": str(root / "state"),
                "FASTERRASTER_CACHE_HOME": str(root / "cache"),
                "FASTERRASTER_TEMP_HOME": str(root / "temp"),
            }
        )
        previous_cwd = Path.cwd()
        os.chdir(root)
        original_connect = socket.socket.connect
        socket.socket.connect = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("offline documentation smoke attempted a socket connection")
        )
        try:
            for entry in commands:
                if entry.get("classification") != "offline-smoke":
                    continue
                argv = [
                    str(repository_root / item_text) if (item_text := str(item)).startswith("examples/") else item_text
                    for item in entry["argv"]
                ]
                try:
                    code = fr_cli.main(argv)
                except SystemExit as exc:
                    code = int(exc.code or 0)
                if code != 0:
                    raise SystemExit(f"offline smoke failed: {entry.get('id')}")
        finally:
            socket.socket.connect = original_connect
            os.chdir(previous_cwd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
