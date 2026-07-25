#!/usr/bin/env python3
"""Opt-in, bounded live probe for the raw four-band NAIP exporter."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from faster_raster.ag_classification import validate_naip_multispectral
from faster_raster.ag_classification_acquisition import (
    validate_raw_naip_acquisition_evidence,
)
from faster_raster.ag_geography import validate_bbox_text


MAXIMUM_PROBE_BYTES = 10_000_000


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Run a tiny opt-in live export through the production tiled NAIP "
            "acquisition path and verify raw bands 0,1,2,3."
        )
    )
    result.add_argument(
        "--live",
        action="store_true",
        help="required acknowledgement that this probe uses network transfer",
    )
    result.add_argument("--bbox", required=True, help="west,south,east,north")
    result.add_argument("--year", required=True, type=int)
    result.add_argument("--output", required=True, type=Path)
    result.add_argument("--resolution", type=float, default=1.2)
    result.add_argument(
        "--maximum-bytes",
        type=int,
        default=MAXIMUM_PROBE_BYTES,
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not args.live:
        raise SystemExit("--live is required; no network request was made")
    bbox = validate_bbox_text(args.bbox)
    if args.maximum_bytes <= 0 or args.maximum_bytes > MAXIMUM_PROBE_BYTES:
        raise SystemExit(
            f"--maximum-bytes must be between 1 and {MAXIMUM_PROBE_BYTES}"
        )
    if args.output.exists():
        raise SystemExit(f"probe output already exists: {args.output}")

    root = Path(__file__).resolve().parent.parent
    command = [
        sys.executable,
        str(root / "scripts/fr-cook-ag"),
        "--asset-only",
        "--assets",
        "naip_multispectral",
        "--output-dir",
        str(args.output),
        "--name",
        f"naip_multispectral_probe_{args.year}",
        "--bbox=" + ",".join(str(value) for value in bbox),
        "--start",
        f"{args.year}-01-01",
        "--end",
        f"{args.year}-12-31",
        "--cdl-year",
        str(args.year),
        "--portion",
        "native",
        "--naip-resolution",
        str(args.resolution),
        "--service-tile-size",
        "128",
        "--max-total-bytes",
        str(args.maximum_bytes),
        "--preview-width",
        "640",
    ]
    subprocess.run(command, cwd=root, check=True)
    manifest_path = args.output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    request_evidence = validate_raw_naip_acquisition_evidence(
        manifest,
        requested_year=args.year,
    )
    raster_path = (
        args.output / "data" / f"naip_{args.year}_multispectral.cog.tif"
    )
    raster_evidence = validate_naip_multispectral(raster_path)
    print(
        json.dumps(
            {
                "status": "PASS",
                "network_bytes": int(manifest.get("network_bytes", 0)),
                "request_evidence": request_evidence,
                "raster_evidence": raster_evidence,
                "manifest": str(manifest_path),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
