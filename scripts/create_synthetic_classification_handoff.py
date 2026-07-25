#!/usr/bin/env python3
"""Create a deterministic, offline NAIP–CDL classification handoff."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import ColorInterp
from rasterio.shutil import copy as raster_copy
from rasterio.transform import from_origin

from faster_raster.ag_classification import spatial_fold
from faster_raster.ag_execution import execute_recipe
from faster_raster.ag_recipes import AgriculturalRecipeV3
from faster_raster.preview_open import inspect_handoff


YEAR = 2023
BBOX = (-112.05, 33.4, -112.04904, 33.40096)
TRANSFORM = from_origin(BBOX[0], BBOX[3], 0.00001, 0.00001)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_cog(path: Path, values: np.ndarray) -> Path:
    array = values if values.ndim == 3 else values[np.newaxis, ...]
    working = path.with_name(f".{path.name}.working.tif")
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        working,
        "w",
        driver="GTiff",
        width=array.shape[2],
        height=array.shape[1],
        count=array.shape[0],
        dtype=str(array.dtype),
        crs="EPSG:4326",
        transform=TRANSFORM,
        tiled=True,
        blockxsize=16,
        blockysize=16,
        compress="DEFLATE",
    ) as sink:
        sink.write(array)
        if array.shape[0] == 4:
            sink.colorinterp = (ColorInterp.undefined,) * 4
            sink.update_tags(
                FASTERRASTER_BAND_ORDER="red,green,blue,near_infrared"
            )
        sink.write_mask(np.full(array.shape[1:], 255, dtype=np.uint8))
    raster_copy(
        working,
        path,
        driver="COG",
        compress="DEFLATE",
        blocksize=512,
        overview_resampling="nearest",
    )
    working.unlink()
    return path


def _synthetic_sources(handoff: Path) -> tuple[Path, Path]:
    size = 96
    block = 16
    labels = np.zeros((size, size), dtype=np.uint8)
    by_fold: dict[int, list[tuple[int, int]]] = {0: [], 1: []}
    for block_row in range(size // block):
        for block_column in range(size // block):
            fold = spatial_fold(block_row, block_column, 20260724, 2)
            by_fold[fold].append((block_row, block_column))
    if min(map(len, by_fold.values())) < 6:
        raise RuntimeError("synthetic block grid cannot support six classes")
    assignments: list[tuple[tuple[int, int], int]] = []
    for code in range(1, 7):
        assignments.append((by_fold[0].pop(), code))
        assignments.append((by_fold[1].pop(), code))
    assignments.extend(
        (position, index % 6 + 1)
        for index, position in enumerate(by_fold[0] + by_fold[1])
    )
    for (block_row, block_column), code in assignments:
        labels[
            block_row * block : (block_row + 1) * block,
            block_column * block : (block_column + 1) * block,
        ] = code

    cdl_codes = np.asarray([0, 1, 61, 82, 123, 141, 111], dtype=np.uint8)
    signatures = np.asarray(
        [
            [0, 0, 0, 0],
            [60, 82, 42, 188],
            [175, 132, 84, 108],
            [132, 150, 126, 142],
            [108, 105, 116, 96],
            [48, 134, 54, 178],
            [34, 55, 88, 30],
        ],
        dtype=np.int16,
    )
    bands = np.moveaxis(signatures[labels], -1, 0)
    variation = (
        (np.arange(size)[:, None] % 5)
        + (np.arange(size)[None, :] % 3)
        - 3
    )
    bands = np.clip(bands + variation[np.newaxis, ...], 1, 254).astype(
        np.uint8
    )
    data = handoff / "data"
    raw = _write_cog(data / f"naip_{YEAR}_multispectral.cog.tif", bands)
    cdl = _write_cog(
        data / f"cdl_{YEAR}_classes.cog.tif",
        cdl_codes[labels],
    )
    layers = []
    for name, path in (
        ("naip_multispectral", raw),
        ("cdl_classes", cdl),
    ):
        layers.append(
            {
                "name": name,
                "output": path.relative_to(handoff).as_posix(),
                "output_sha256": _sha256(path),
            }
        )
    _write_json(
        handoff / "manifest.json",
        {
            "schema_version": 2,
            "operation_status": "completed",
            "verification_status": "PASS",
            "order": {
                "cdl_year": YEAR,
                "time_start": f"{YEAR}-01-01",
                "time_end": f"{YEAR}-12-31",
            },
            "layers": layers,
            "network_bytes": 0,
            "requests": [],
        },
    )
    return raw, cdl


def _recipe(root: Path) -> AgriculturalRecipeV3:
    raw = json.loads(
        (
            root / "recipes/ag/naip_cdl_classification_audit.json"
        ).read_text(encoding="utf-8")
    )
    raw["classification"].update(
        {
            "maximum_samples_per_class": 400,
            "minimum_training_samples_per_class": 20,
            "spatial_holdout_folds": 2,
            "spatial_holdout_fold": 0,
            "inference_window_size": 16,
            "n_estimators": 32,
            "max_depth": 12,
            "min_samples_leaf": 2,
        }
    )
    return AgriculturalRecipeV3.model_validate(raw)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "outputs/handoffs",
    )
    parser.add_argument(
        "--name",
        default="naip_classification_synthetic",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    recipe = _recipe(root)
    with tempfile.TemporaryDirectory(
        prefix="fasterraster-synthetic-classification-"
    ) as temporary:
        cache_root = Path(temporary) / "cache"
        source_handoff = cache_root / "synthetic_source_2023"
        _synthetic_sources(source_handoff)
        old_cache = os.environ.get("FASTERRASTER_AG_CACHE_ROOT")
        old_handoffs = os.environ.get("FASTERRASTER_HANDOFF_ROOT")
        os.environ["FASTERRASTER_AG_CACHE_ROOT"] = str(cache_root)
        os.environ["FASTERRASTER_HANDOFF_ROOT"] = str(
            args.output_root.resolve()
        )
        try:
            preview = execute_recipe(
                root,
                recipe=recipe,
                recipe_raw=recipe.model_dump(mode="json"),
                name=args.name,
                bbox=BBOX,
                start=f"{YEAR}-01-01",
                end=f"{YEAR}-12-31",
                year=YEAR,
                reuse_mode="only",
                open_preview=False,
                max_total_bytes=1_000_000,
                service_tile_size=128,
                renderer=lambda *unused: (_ for _ in ()).throw(
                    RuntimeError("legacy renderer must not run")
                ),
                naip_resolution_m=1.2,
            )
        finally:
            if old_cache is None:
                os.environ.pop("FASTERRASTER_AG_CACHE_ROOT", None)
            else:
                os.environ["FASTERRASTER_AG_CACHE_ROOT"] = old_cache
            if old_handoffs is None:
                os.environ.pop("FASTERRASTER_HANDOFF_ROOT", None)
            else:
                os.environ["FASTERRASTER_HANDOFF_ROOT"] = old_handoffs

    handoff = preview.parents[2]
    inspection = inspect_handoff(handoff)
    if inspection["status"] != "completed" or not inspection["preview"]:
        raise RuntimeError(f"synthetic handoff inspection failed: {inspection}")
    print(
        json.dumps(
            {
                "status": "PASS",
                "handoff": str(handoff),
                "preview": str(preview),
                "inspection": inspection,
                "network_bytes": 0,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
