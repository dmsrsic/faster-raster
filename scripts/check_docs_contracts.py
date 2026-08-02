"""Check built-site references and explicit FasterRaster command classifications."""
from __future__ import annotations

import argparse
import html.parser
import re
import shlex
import yaml
from pathlib import Path
from urllib.parse import unquote, urlparse

CLASSIFICATIONS = {"offline-smoke", "manual-network", "release-operator", "illustrative"}
SMOKE_MANIFEST = "configs/docs_command_smoke.yaml"
FENCE_RE = re.compile(r"^\s*```([^\n]*)\n(.*?)^\s*```", re.MULTILINE | re.DOTALL)
NETWORK_FLAGS = {
    "--allow-network",
    "--allow-live",
    "--allow-materialization",
    "--allow-preview",
    "--allow-derivation",
    "--network",
    "--live",
    "--execute",
    "--refresh",
    "--refresh-sources",
    "--resolve-credentials",
    "--credential",
    "--credentials",
    "--api-key",
    "--access-token",
    "--password",
    "--secret",
    "--token",
    "--auth",
    "--approve-plan-sha256",
    "--probe-run-id",
    "--probe-receipt-sha256",
}


class _HTMLReferences(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []
        self.anchors: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        for key in ("href", "src"):
            if values.get(key):
                self.references.append(values[key] or "")
        if values.get("id"):
            self.anchors.add(values["id"] or "")


def check_markdown_commands(root: Path) -> list[str]:
    errors: list[str] = []
    manifest_path = root / SMOKE_MANIFEST
    manifest = None
    if manifest_path.is_file():
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema_version") != "fasterraster.docs-command-smoke/v1"
            or not isinstance(manifest.get("commands"), list)
        ):
            errors.append(f"{manifest_path}: unsupported documentation smoke manifest")
            manifest = None
    smoke_commands: set[tuple[str, ...]] = set()
    if manifest:
        seen_ids: set[str] = set()
        for entry in manifest.get("commands", []):
            if not isinstance(entry, dict):
                errors.append(f"{manifest_path}: invalid command entry")
                continue
            entry_id = entry.get("id")
            if not isinstance(entry_id, str) or not entry_id or entry_id in seen_ids:
                errors.append(f"{manifest_path}: command ids must be unique non-empty strings")
            seen_ids.add(entry_id)
            if entry.get("classification") != "offline-smoke":
                continue
            argv = entry.get("argv")
            if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
                errors.append(f"{manifest_path}: invalid offline-smoke entry")
                continue
            command = tuple(["fr", *argv])
            if command in smoke_commands:
                errors.append(f"{manifest_path}: duplicate offline-smoke command")
            smoke_commands.add(command)

    def logical_commands(body: str) -> tuple[list[tuple[str, ...]], bool, list[str], list[str], list[str]]:
        commands: list[tuple[str, ...]] = []
        errors_for_body: list[str] = []
        unsafe_shell: list[str] = []
        extra_commands: list[str] = []
        current: str | None = None
        found_fr = False

        def finish(value: str) -> None:
            if re.search(r"(?:&&|\|\||[;|<>]|\$\(|`)", value):
                unsafe_shell.append("offline command contains a shell operator, substitution, or redirection")
                return
            try:
                tokens = tuple(shlex.split(value))
            except ValueError:
                errors_for_body.append("malformed FasterRaster command quoting")
                return
            if tokens and tokens[0] == "fr":
                commands.append(tokens)
            elif tokens:
                extra_commands.append(value)

        for line in body.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            stripped = re.sub(r"^\$\s*", "", stripped)
            if re.search(r"(?:^|\s)fr(?:\s|$)", stripped):
                found_fr = True
            if current is not None:
                if current.endswith("\\"):
                    current = current[:-1].rstrip() + " " + stripped
                    continue
                finish(current)
                current = None
            if stripped.startswith("fr") and (len(stripped) == 2 or stripped[2].isspace()):
                current = stripped
            else:
                finish(stripped)
        if current is not None:
            if current.endswith("\\"):
                errors_for_body.append("unterminated FasterRaster command continuation")
            else:
                finish(current)
        return commands, found_fr, errors_for_body, unsafe_shell, extra_commands

    for path in [root / "README.md", *sorted((root / "docs").rglob("*.md"))]:
        text = path.read_text(encoding="utf-8")
        for match in FENCE_RE.finditer(text):
            info, body = match.groups()
            commands, has_fr, command_errors, unsafe_shell, extra_commands = logical_commands(body)
            if not has_fr:
                continue
            found = set(re.findall(r"(?:class|classification)\s*[=:]\s*([a-z-]+)", info))
            found.update(item for item in CLASSIFICATIONS if item in info)
            if len(found) != 1:
                errors.append(f"{path}: FasterRaster command block needs exactly one classification")
            if command_errors:
                errors.extend(f"{path}: {message}" for message in command_errors)
            if "offline-smoke" in found:
                if not smoke_commands:
                    errors.append(f"{path}: offline-smoke command has no manifest")
                errors.extend(f"{path}: {message}" for message in unsafe_shell)
                if extra_commands:
                    errors.append(f"{path}: offline-smoke block contains an undeclared extra command")
                for tokens in commands:
                    if any(token.split("=", 1)[0] in NETWORK_FLAGS for token in tokens):
                        errors.append(f"{path}: offline-smoke command contains a network authorization flag")
                    if tokens not in smoke_commands:
                        errors.append(f"{path}: offline-smoke command is absent from {SMOKE_MANIFEST}: {' '.join(tokens)}")
    return errors


def check_site(site: Path, source_root: Path | None = None) -> list[str]:
    errors: list[str] = []
    for page in sorted(site.rglob("*.html")):
        parser = _HTMLReferences()
        parser.feed(page.read_text(encoding="utf-8", errors="replace"))
        for reference in parser.references:
            parsed = urlparse(reference)
            if parsed.scheme or reference.startswith("//") or reference.startswith("mailto:"):
                continue
            target = unquote(parsed.path)
            if not target:
                continue
            if target.startswith("/faster-raster/"):
                candidate = (site / target.removeprefix("/faster-raster/")).resolve()
            elif target.startswith("/"):
                candidate = (site / target.lstrip("/")).resolve()
            else:
                candidate = (page.parent / target).resolve()
            if target.endswith("/"):
                candidate = candidate / "index.html"
            if not candidate.exists():
                errors.append(f"{page}: missing local reference {reference}")
            elif parsed.fragment and candidate.suffix.lower() == ".html":
                target_parser = _HTMLReferences()
                target_parser.feed(candidate.read_text(encoding="utf-8", errors="replace"))
                if parsed.fragment not in target_parser.anchors:
                    errors.append(f"{page}: missing anchor {reference}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path, nargs="?", default=Path(__file__).resolve().parents[1])
    parser.add_argument("--site", type=Path)
    args = parser.parse_args(argv)
    errors = check_markdown_commands(args.root)
    if args.site:
        errors.extend(check_site(args.site, args.root))
    for error in errors:
        print(error)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
