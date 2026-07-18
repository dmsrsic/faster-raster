from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from PIL import Image
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds

from faster_raster import fr_cli
from faster_raster.human_development import HumanDevelopmentError
from faster_raster.human_development_workflow import (
    compile_human_development_plan,
    execute_human_development,
)
from faster_raster.local_paths import resolve_local_paths
from faster_raster.workfiles import HumanDevelopmentWorkfileSpec, load_workfile


BASELINE = np.array(
    [
        [250, 11, 21, 22],
        [11, 11, 21, 23],
        [24, 24, 22, 31],
        [31, 41, 82, 90],
    ],
    dtype=np.uint8,
)
COMPARISON = np.array(
    [
        [11, 21, 21, 23],
        [11, 22, 31, 22],
        [24, 23, 21, 31],
        [22, 41, 23, 90],
    ],
    dtype=np.uint8,
)
TRANSFORM = from_origin(500_000, 2_000_000, 30, 30)


def _write_raster(path: Path, values: np.ndarray, *, nodata: float, dtype: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=values.shape[1],
        height=values.shape[0],
        count=1,
        dtype=dtype,
        crs="EPSG:5070",
        transform=TRANSFORM,
        nodata=nodata,
    ) as sink:
        sink.write(values.astype(dtype), 1)


def _fixture_workfile(tmp_path: Path, *, epochs: int = 3, imperviousness: bool = True, name: str = "synthetic-development") -> Path:
    inputs = tmp_path / "inputs"
    land_values = (BASELINE, BASELINE, COMPARISON)
    imp_values = (
        np.arange(16, dtype=np.uint8).reshape(4, 4),
        np.arange(16, dtype=np.uint8).reshape(4, 4) + 5,
        np.arange(16, dtype=np.uint8).reshape(4, 4) + 10,
    )
    years = (1985, 2025) if epochs == 2 else (1985, 2005, 2025)
    for index, year in enumerate(years):
        value_index = (0 if index == 0 else 2) if epochs == 2 else index
        _write_raster(inputs / f"land_{year}.tif", land_values[value_index], nodata=250, dtype="uint8")
        if imperviousness:
            _write_raster(inputs / f"imp_{year}.tif", imp_values[value_index], nodata=250, dtype="uint8")
    projected_bounds = (500_010, 1_999_890, 500_110, 1_999_990)
    bbox = transform_bounds("EPSG:5070", "EPSG:4326", *projected_bounds, densify_pts=21)
    epoch_lines = []
    for year in years:
        epoch_lines.extend(
            [
                f"  - year: {year}",
                f"    land_cover_path: inputs/land_{year}.tif",
            ]
        )
        if imperviousness:
            epoch_lines.append(f"    imperviousness_path: inputs/imp_{year}.tif")
    path = tmp_path / f"{name}.fr.md"
    path.write_text(
        "\n".join(
            [
                "---",
                "schema_version: fasterraster.work/v2",
                f"name: {name}",
                "workflow: human_development_change",
                "area:",
                "  bbox:",
                *(f"    - {value:.12f}" for value in bbox),
                "epochs:",
                *epoch_lines,
                "sources:",
                "  policy: pinned",
                "  source_id: usgs_annual_nlcd",
                "  collection: 1",
                "  version: 2",
                "  region: CU",
                "data:",
                "  reuse: auto",
                "processing:",
                "  target_crs: EPSG:5070",
                "  resolution_m: 30",
                "  window_size: 16",
                "limits:",
                "  maximum_download_mb: 1",
                "outputs:",
                "  preview: true",
                "  open_when_complete: false",
                "---",
                "",
                "# Synthetic development change",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _configure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    handoffs = tmp_path / "handoffs"
    monkeypatch.setenv("FASTERRASTER_HANDOFF_ROOT", str(handoffs))
    monkeypatch.setenv("FASTERRASTER_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("FASTERRASTER_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("FASTERRASTER_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("FASTERRASTER_TEMP_HOME", str(tmp_path / "temp"))
    return handoffs


def test_two_and_three_epoch_terminology(tmp_path: Path) -> None:
    two = load_workfile(_fixture_workfile(tmp_path / "two", epochs=2), repository_root=Path.cwd())
    three = load_workfile(_fixture_workfile(tmp_path / "three", epochs=3), repository_root=Path.cwd())
    assert isinstance(two.spec, HumanDevelopmentWorkfileSpec)
    assert two.spec.comparison_mode == "paired_comparison"
    assert three.spec.comparison_mode == "multi_epoch_time_series"


@pytest.mark.parametrize(
    ("years", "message"),
    [
        ((2005, 1985), "ordered by ascending year"),
        ((1985, 1985), "must be unique"),
    ],
)
def test_epoch_validation(tmp_path: Path, years: tuple[int, int], message: str) -> None:
    path = _fixture_workfile(tmp_path, epochs=2)
    text = path.read_text(encoding="utf-8")
    text = text.replace("  - year: 1985", f"  - year: {years[0]}")
    text = text.replace("  - year: 2025", f"  - year: {years[1]}")
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_workfile(path, repository_root=Path.cwd())


def test_complete_three_epoch_cli_and_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    handoffs = _configure(monkeypatch, tmp_path)
    workfile = _fixture_workfile(tmp_path, epochs=3)
    assert fr_cli.main(["validate", str(workfile)]) == 0
    assert fr_cli.main(["plan", str(workfile), "--out", str(tmp_path / "plan")]) == 0
    assert fr_cli.main(["explain", str(workfile)]) == 0
    assert fr_cli.main(["cook", str(workfile)]) == 0
    finalized = [path for path in handoffs.iterdir() if path.is_dir() and not path.name.startswith(".")]
    assert len(finalized) == 1
    handoff = finalized[0]
    assert fr_cli.main(["inspect", str(handoff), "--json"]) == 0
    monkeypatch.setattr(fr_cli, "open_local_preview", lambda *_args, **_kwargs: ["test-opener"])
    assert fr_cli.main(["open", str(handoff), "--json"]) == 0
    output = capsys.readouterr().out
    assert "Opened preview" in output

    receipt = json.loads((handoff / "workflow_receipt.json").read_text(encoding="utf-8"))
    endpoint_stats = json.loads(
        (handoff / receipt["endpoint_comparison"]["statistics"]).read_text(encoding="utf-8")
    )
    counts = [endpoint_stats["change_codes"][str(code)]["pixels"] for code in range(8)]
    assert counts == [1, 4, 2, 4, 1, 1, 3, 0]
    assert endpoint_stats["gross_development_gain"]["square_metres"] == 3_600.0
    assert endpoint_stats["apparent_development_loss"]["square_metres"] == 900.0
    assert endpoint_stats["net_development_change"]["square_metres"] == 2_700.0
    assert endpoint_stats["transition_reconciliation"] == {
        "valid_comparison_pixels": 15,
        "source_transition_pixels": 15,
        "abstract_transition_pixels": 15,
        "reconciles": True,
    }
    assert receipt["comparison_mode"] == "multi_epoch_time_series"
    assert len(receipt["adjacent_intervals"]) == 2
    assert receipt["total_network_bytes"] == 0
    assert receipt["total_reused_bytes"] > 0
    assert receipt["source_gate_result"] == "BLOCKED"
    preview = handoff / receipt["preview"]
    with Image.open(preview) as image:
        assert image.size == (3840, 2160)
        assert image.info["workflow"] == "human_development_change"
        assert "mapped cover change only" in image.info["evidence"]
    assert hashlib.sha256(preview.read_bytes()).hexdigest() == receipt["preview_sha256"]
    required = [
        "methodology_receipt.json",
        "workflow_receipt.json",
        "source_gate_report.json",
        "checksums.sha256",
        "analysis/intervals/1985_2005/change_codes.tif",
        "analysis/intervals/2005_2025/valid_comparison_mask.tif",
        "analysis/endpoint/1985_2025/source_transition_matrix.csv",
        "analysis/endpoint/1985_2025/abstract_transition_matrix.json",
    ]
    assert all((handoff / relative).is_file() for relative in required)
    published_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in handoff.rglob("*")
        if path.is_file() and path.suffix in {".json", ".csv", ".txt"}
    )
    assert ".staging-" not in published_text


def test_imperviousness_unavailable_is_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    handoffs = _configure(monkeypatch, tmp_path)
    workfile = _fixture_workfile(tmp_path, epochs=2, imperviousness=False, name="no-impervious")
    assert fr_cli.main(["cook", str(workfile)]) == 0
    handoff = next(path for path in handoffs.iterdir() if not path.name.startswith("."))
    evidence = json.loads(
        (handoff / "analysis" / "intervals" / "1985_2025" / "imperviousness_evidence.json").read_text(encoding="utf-8")
    )
    assert evidence["status"] == "unavailable"
    assert evidence["difference_raster"] is None


def test_transaction_failure_never_publishes_finalized_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handoffs = _configure(monkeypatch, tmp_path)
    workfile = load_workfile(_fixture_workfile(tmp_path, epochs=2, name="transaction-failure"), repository_root=Path.cwd())
    paths = resolve_local_paths(workfile.path.parent)
    plan = compile_human_development_plan(Path.cwd(), workfile, paths)
    import faster_raster.human_development_workflow as workflow

    monkeypatch.setattr(
        workflow,
        "render_human_development_preview",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(HumanDevelopmentError("preview failure")),
    )
    with pytest.raises(HumanDevelopmentError, match="preview failure"):
        execute_human_development(Path.cwd(), workfile=workfile, plan=plan, open_preview=False)
    assert not [path for path in handoffs.iterdir() if path.is_dir() and not path.name.startswith(".")]
    failed = [path for path in handoffs.iterdir() if path.name.startswith(".failed-")]
    assert len(failed) == 1
    assert (failed[0] / "failure_report.json").is_file()
