from __future__ import annotations

from pathlib import Path


def test_docs_use_python3_for_json_tool():
    paths = list(Path("docs").glob("*.md")) + [Path("README_CLI_DEMO.md")]
    offenders = [str(path) for path in paths if "python -m json.tool" in path.read_text(encoding="utf-8")]
    assert offenders == []
