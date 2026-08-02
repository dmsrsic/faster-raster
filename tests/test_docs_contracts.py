from pathlib import Path

import pytest

from scripts.check_docs_contracts import check_markdown_commands


ROOT = Path(__file__).resolve().parents[1]


def test_command_classifier_rejects_unclassified_blocks(tmp_path):
    (tmp_path / "README.md").write_text("```bash\nfr doctor --offline\n```\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    assert check_markdown_commands(tmp_path)


def test_command_classifier_accepts_explicit_classification(tmp_path):
    (tmp_path / "README.md").write_text("```bash { .offline-smoke }\nfr doctor --offline\n```\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "docs_command_smoke.yaml").write_text(
        "schema_version: fasterraster.docs-command-smoke/v1\ncommands:\n  - id: doctor\n    classification: offline-smoke\n    argv: [doctor, --offline]\n",
        encoding="utf-8",
    )
    assert check_markdown_commands(tmp_path) == []


def test_command_classifier_rejects_appended_network_flag(tmp_path):
    (tmp_path / "README.md").write_text("```bash { .offline-smoke }\nfr doctor --offline --allow-network\n```\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "docs_command_smoke.yaml").write_text(
        "schema_version: fasterraster.docs-command-smoke/v1\ncommands:\n  - id: doctor\n    classification: offline-smoke\n    argv: [doctor, --offline]\n",
        encoding="utf-8",
    )
    assert check_markdown_commands(tmp_path)


def test_command_classifier_rejects_continuation_drift_and_malformed_quotes(tmp_path):
    (tmp_path / "README.md").write_text(
        "```bash { .offline-smoke }\nfr init study.md \\\n+  --template ag-naip-classification --allow-network\n```\n"
        "```bash { .offline-smoke }\nfr doctor \"unterminated\n```\n",
        encoding="utf-8",
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "docs_command_smoke.yaml").write_text(
        "schema_version: fasterraster.docs-command-smoke/v1\ncommands:\n  - id: init\n    classification: offline-smoke\n    argv: [init, study.md, --template, ag-naip-classification]\n",
        encoding="utf-8",
    )
    errors = check_markdown_commands(tmp_path)
    assert any("network authorization" in error for error in errors)
    assert any("malformed" in error for error in errors)


def _write_docs_fixture(tmp_path: Path, body: str, manifest_commands: str) -> list[str]:
    (tmp_path / "README.md").write_text(f"```bash {{ .offline-smoke }}\n{body}\n```\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "docs_command_smoke.yaml").write_text(
        "schema_version: fasterraster.docs-command-smoke/v1\ncommands:\n" + manifest_commands,
        encoding="utf-8",
    )
    return check_markdown_commands(tmp_path)


def test_command_classifier_requires_exact_positional_argv(tmp_path):
    errors = _write_docs_fixture(
        tmp_path,
        "fr validate other-study.md",
        "  - id: validate\n    classification: offline-smoke\n    argv: [validate, study.md]\n",
    )
    assert any("absent from" in error for error in errors)


@pytest.mark.parametrize(
    "body",
    [
        "fr doctor --offline\ncurl https://example.test",
        "fr doctor --offline && curl https://example.test",
        "fr doctor --offline | powershell",
        "fr doctor --offline > execute.ps1",
        "fr doctor --offline $(curl https://example.test)",
        "fr doctor --offline `curl https://example.test`",
        "TOKEN=value fr doctor --offline",
    ],
)
def test_command_classifier_rejects_extra_commands_and_shell_execution(tmp_path, body):
    errors = _write_docs_fixture(
        tmp_path,
        body,
        "  - id: doctor\n    classification: offline-smoke\n    argv: [doctor, --offline]\n",
    )
    assert errors


def test_command_classifier_rejects_duplicate_manifest_bindings(tmp_path):
    errors = _write_docs_fixture(
        tmp_path,
        "fr doctor --offline",
        "  - id: doctor\n    classification: offline-smoke\n    argv: [doctor, --offline]\n"
        "  - id: doctor-copy\n    classification: offline-smoke\n    argv: [doctor, --offline]\n",
    )
    assert any("duplicate offline-smoke command" in error for error in errors)


def test_command_classifier_rejects_missing_manifest_binding(tmp_path):
    errors = _write_docs_fixture(tmp_path, "fr doctor --offline", "")
    assert any("no manifest" in error or "absent from" in error for error in errors)


def test_command_classifier_rejects_unterminated_continuation(tmp_path):
    errors = _write_docs_fixture(
        tmp_path,
        "fr doctor --offline \\",
        "  - id: doctor\n    classification: offline-smoke\n    argv: [doctor, --offline]\n",
    )
    assert any("unterminated" in error for error in errors)


def test_public_text_has_no_mojibake_or_bom():
    paths = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md")), *sorted((ROOT / "release").glob("*.md"))]
    for path in paths:
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), path
        text = raw.decode("utf-8")
        assert not any(marker in text for marker in ("â", "Ã", "ï»¿")), path
