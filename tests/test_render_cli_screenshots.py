from __future__ import annotations

from pathlib import Path

from scripts import render_cli_screenshots
from faster_raster import cli_models as models


def test_render_cli_screenshots_creates_svg_and_text(tmp_path):
    out = tmp_path / "screens"
    render_cli_screenshots.main_args = None
    import subprocess, sys
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/render_cli_screenshots.py",
            "--atlas", "research/source_atlas_v0_4.yaml",
            "--stack", "reports/source_stack_matrix.json",
            "--unlocks", "reports/source_unlock_plan.json",
            "--out-dir", str(out),
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    svgs = sorted(out.glob("*.svg"))
    texts = sorted(out.glob("*.txt"))
    assert len(svgs) >= 6
    assert len(texts) >= 6
    joined = "\n".join(path.read_text(encoding="utf-8") for path in texts)
    assert "Pantry" in joined
    assert "sauce" in joined
    assert "dip check" in joined
    assert "Batcher" in joined
    source_count = len(models.load_sources())
    assert str(source_count) in (out / "01_pantry_sauces.txt").read_text(encoding="utf-8")
