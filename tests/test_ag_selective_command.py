from __future__ import annotations

import json
import subprocess
from pathlib import Path

from faster_raster.ag_execution import _run_selective_acquisition
from faster_raster.ag_recipes import load_named_recipe


def test_internal_selective_command_uses_equals_for_negative_bbox(tmp_path):
    root = Path(__file__).resolve().parent.parent
    recipe = load_named_recipe(root, "crop_vigor_classification")
    staging = tmp_path / "staging"
    staging.mkdir()
    captured = []

    def runner(command, **kwargs):
        captured.extend(command)
        (staging / "manifest.json").write_text(
            json.dumps({"network_bytes": 0, "requests": [], "layers": []})
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    _run_selective_acquisition(
        root,
        staging,
        ["ndvi"],
        name="test",
        bbox=(-100.98, 38.005, -100.979, 38.006),
        start="2023-04-01",
        end="2023-10-31",
        year=2023,
        recipe=recipe,
        max_total_bytes=250_000_000,
        service_tile_size=400,
        runner=runner,
    )

    assert "--bbox=-100.98,38.005,-100.979,38.006" in captured
    assert "--assets" in captured
    assert captured[captured.index("--assets") + 1] == "ndvi"
