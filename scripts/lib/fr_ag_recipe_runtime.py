from __future__ import annotations

import csv
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import (
    Image,
    ImageChops,
    ImageDraw,
    ImageEnhance,
    ImageFilter,
    ImageFont,
    ImageOps,
)


CLASS_NAMES = {
    1: "Corn",
    2: "Cotton",
    4: "Sorghum",
    5: "Soybeans",
    6: "Sunflower",
    24: "Winter wheat",
    26: "Double crop wheat/soy",
    29: "Millet",
    36: "Alfalfa",
    37: "Other hay",
    61: "Fallow/idle cropland",
    111: "Open water",
    121: "Developed/open",
    122: "Developed/low",
    123: "Developed/medium",
    124: "Developed/high",
    131: "Barren",
    141: "Deciduous forest",
    142: "Evergreen forest",
    152: "Shrubland",
    176: "Grassland/pasture",
}

CLASS_COLORS = {
    1: (255, 212, 0),
    2: (255, 38, 38),
    4: (255, 127, 0),
    5: (38, 115, 0),
    6: (255, 204, 102),
    24: (168, 112, 0),
    26: (115, 115, 0),
    29: (115, 40, 40),
    36: (255, 165, 226),
    37: (165, 245, 141),
    61: (191, 191, 119),
    111: (79, 129, 189),
    121: (225, 225, 225),
    122: (209, 209, 209),
    123: (174, 174, 174),
    124: (130, 130, 130),
    131: (204, 191, 163),
    141: (90, 160, 90),
    142: (40, 110, 60),
    152: (196, 180, 84),
    176: (232, 209, 125),
}


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def _class_color(code: int) -> tuple[int, int, int]:
    if code in CLASS_COLORS:
        return CLASS_COLORS[code]

    return (
        50 + (code * 47) % 180,
        50 + (code * 83) % 180,
        50 + (code * 131) % 180,
    )


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    names = [
        "/usr/share/fonts/truetype/dejavu/"
        + ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
        "/usr/share/fonts/truetype/liberation2/"
        + (
            "LiberationSans-Bold.ttf"
            if bold
            else "LiberationSans-Regular.ttf"
        ),
    ]

    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass

    return ImageFont.load_default()


def _fit(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(
        image.convert("RGB"),
        size,
        method=Image.Resampling.LANCZOS,
    )


def _option_value(argv: list[str], flag: str) -> str | None:
    prefix = flag + "="

    for index, value in enumerate(argv):
        if value.startswith(prefix):
            return value[len(prefix):]

        if value == flag and index + 1 < len(argv):
            return argv[index + 1]

    return None


def _has_option(argv: list[str], flag: str) -> bool:
    return any(
        value == flag or value.startswith(flag + "=")
        for value in argv
    )


def _remove_option(
    argv: list[str],
    flag: str,
    takes_value: bool,
) -> str | None:
    prefix = flag + "="

    for index in range(1, len(argv)):
        value = argv[index]

        if value.startswith(prefix):
            result = value[len(prefix):]
            del argv[index]
            return result

        if value == flag:
            del argv[index]

            if takes_value:
                if index >= len(argv):
                    raise SystemExit(f"{flag} requires a value")

                return argv.pop(index)

            return "true"

    return None


def _ensure_option(
    argv: list[str],
    flag: str,
    value: Any,
) -> None:
    if value is None or _has_option(argv, flag):
        return

    argv.extend([flag, str(value)])


def _parse_bbox(value: str) -> tuple[float, float, float, float]:
    parts = tuple(float(part) for part in value.split(","))

    if len(parts) != 4:
        raise SystemExit(
            "--bbox must be west,south,east,north"
        )

    west, south, east, north = parts

    if west >= east or south >= north:
        raise SystemExit(
            "--bbox must satisfy west < east and south < north"
        )

    return parts


def _asset_paths(
    handoff: Path,
    year: int,
) -> dict[str, Path | None]:
    data = handoff / "data"

    def first(patterns: list[str]) -> Path | None:
        for pattern in patterns:
            matches = sorted(data.glob(pattern))

            if matches:
                return matches[0]

        return None

    return {
        "natural": first([
            f"naip_{year}_natural_color.cog.tif",
            "naip_*_natural_color.cog.tif",
        ]),
        "ndvi": first([
            f"naip_{year}_ndvi_color.cog.tif",
            "naip_*_ndvi_color.cog.tif",
        ]),
        "cdl_classes": first([
            f"cdl_{year}_classes.cog.tif",
            "cdl_*_classes.cog.tif",
        ]),
        "cdl_color": first([
            f"cdl_{year}_color.cog.tif",
            "cdl_*_color.cog.tif",
        ]),
        "hillshade": first([
            "three_dep_hillshade.cog.tif",
            "*hillshade*.cog.tif",
        ]),
    }


def _numbers_match(
    values: Any,
    target: tuple[float, float, float, float],
) -> bool:
    if not isinstance(values, (list, tuple)):
        return False

    if len(values) != 4:
        return False

    try:
        numbers = tuple(float(value) for value in values)
    except (TypeError, ValueError):
        return False

    return all(
        abs(left - right) <= 0.000001
        for left, right in zip(numbers, target)
    )


def _object_has_bbox(
    value: Any,
    target: tuple[float, float, float, float],
) -> bool:
    if _numbers_match(value, target):
        return True

    if isinstance(value, dict):
        return any(
            _object_has_bbox(child, target)
            for child in value.values()
        )

    if isinstance(value, list):
        return any(
            _object_has_bbox(child, target)
            for child in value
        )

    return False


def _metadata_payload(handoff: Path) -> tuple[list[Any], str]:
    objects: list[Any] = []
    text_parts: list[str] = []

    candidates = [
        handoff / "manifest.json",
        handoff / "README.md",
        handoff / "execution_summary.md",
    ]

    candidates.extend(
        sorted((handoff / "metadata").glob("*.json"))
        if (handoff / "metadata").is_dir()
        else []
    )

    for path in candidates:
        if not path.is_file():
            continue

        if path.stat().st_size > 5_000_000:
            continue

        try:
            text = path.read_text(
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            continue

        text_parts.append(text)

        if path.suffix == ".json":
            try:
                objects.append(json.loads(text))
            except json.JSONDecodeError:
                pass

    return objects, "\n".join(text_parts)


def _natural_pixel_size(path: Path) -> float | None:
    try:
        result = subprocess.run(
            ["gdalinfo", "-json", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
        info = json.loads(result.stdout)
        transform = info.get("geoTransform")

        if not transform or len(transform) < 6:
            return None

        return max(
            abs(float(transform[1])),
            abs(float(transform[5])),
        )
    except Exception:
        return None


def _compatibility(
    handoff: Path,
    recipe: dict[str, Any],
    bbox: tuple[float, float, float, float],
    start: str,
    end: str,
    year: int,
) -> dict[str, Any]:
    assets = _asset_paths(handoff, year)
    objects, metadata_text = _metadata_payload(handoff)

    required = recipe["required_assets"]

    asset_checks = {
        name: (
            assets.get(name) is not None
            and assets[name].is_file()
        )
        for name in required
    }

    bbox_match = any(
        _object_has_bbox(obj, bbox)
        for obj in objects
    )

    if not bbox_match:
        coordinate_tokens = [
            f"{value:.3f}"
            for value in bbox
        ]
        bbox_match = all(
            token in metadata_text
            for token in coordinate_tokens
        )

    timeframe_match = (
        start in metadata_text
        and end in metadata_text
    )

    year_match = (
        str(year) in metadata_text
        and all(
            str(year) in assets[name].name
            for name in ("natural", "ndvi", "cdl_classes")
            if assets.get(name) is not None
        )
    )

    natural = assets.get("natural")
    pixel_size = (
        _natural_pixel_size(natural)
        if natural is not None
        else None
    )

    maximum_pixel_size = float(
        recipe["maximum_naip_pixel_size_m"]
    )

    resolution_match = (
        pixel_size is not None
        and pixel_size <= maximum_pixel_size + 0.01
    )

    compatible = (
        all(asset_checks.values())
        and bbox_match
        and timeframe_match
        and year_match
        and resolution_match
    )

    return {
        "compatible": compatible,
        "handoff": str(handoff),
        "assets": {
            name: (
                str(path)
                if path is not None
                else None
            )
            for name, path in assets.items()
        },
        "checks": {
            "required_assets": asset_checks,
            "bbox_exact": bbox_match,
            "timeframe_exact": timeframe_match,
            "year_exact": year_match,
            "resolution_acceptable": resolution_match,
        },
        "natural_pixel_size_m": pixel_size,
        "maximum_naip_pixel_size_m": maximum_pixel_size,
    }


def _find_handoff(
    root: Path,
    recipe: dict[str, Any],
    bbox: tuple[float, float, float, float],
    start: str,
    end: str,
    year: int,
    created_after: float | None = None,
) -> tuple[Path | None, dict[str, Any] | None]:
    handoff_root = root / "outputs" / "handoffs"

    if not handoff_root.is_dir():
        return None, None

    candidates = [
        path
        for path in handoff_root.iterdir()
        if path.is_dir() and (path / "data").is_dir()
    ]

    candidates.sort(
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    last_report = None

    for candidate in candidates:
        if (
            created_after is not None
            and candidate.stat().st_mtime < created_after - 3
        ):
            continue

        report = _compatibility(
            candidate,
            recipe,
            bbox,
            start,
            end,
            year,
        )

        last_report = report

        if report["compatible"]:
            return candidate, report

    return None, last_report


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def _align_assets(
    assets: dict[str, Path | None],
    work: Path,
) -> tuple[dict[str, Path], dict[str, Any]]:
    natural = assets["natural"]

    if natural is None:
        raise RuntimeError("Natural-color COG is missing")

    info_result = subprocess.run(
        ["gdalinfo", "-json", str(natural)],
        check=True,
        capture_output=True,
        text=True,
    )

    info = json.loads(info_result.stdout)
    source_width, source_height = info["size"]
    transform = info["geoTransform"]

    xmin = float(transform[0])
    ymax = float(transform[3])
    xmax = xmin + source_width * float(transform[1])
    ymin = ymax + source_height * float(transform[5])

    target_width = 1800
    target_height = max(
        1,
        round(
            source_height
            * target_width
            / source_width
        ),
    )

    outputs: dict[str, Path] = {}

    policies = {
        "natural": "cubic",
        "ndvi": "cubic",
        "cdl_classes": "near",
        "hillshade": "bilinear",
    }

    for name, method in policies.items():
        source = assets.get(name)

        if source is None:
            continue

        warped = work / f"{name}.tif"
        png = work / f"{name}.png"

        _run([
            "gdalwarp",
            "-q",
            "-overwrite",
            "-of",
            "GTiff",
            "-t_srs",
            "EPSG:3857",
            "-te",
            str(xmin),
            str(ymin),
            str(xmax),
            str(ymax),
            "-ts",
            str(target_width),
            str(target_height),
            "-r",
            method,
            str(source),
            str(warped),
        ])

        _run([
            "gdal_translate",
            "-q",
            "-of",
            "PNG",
            "-ot",
            "Byte",
            str(warped),
            str(png),
        ])

        warped.unlink(missing_ok=True)
        outputs[name] = png

    return outputs, {
        "crs": "EPSG:3857",
        "width": target_width,
        "height": target_height,
        "extent": [xmin, ymin, xmax, ymax],
    }


def _full_class_counts(
    cdl_path: Path,
    work: Path,
) -> tuple[Counter[int], float]:
    xyz = work / "cdl_full.xyz"

    _run([
        "gdal_translate",
        "-q",
        "-of",
        "XYZ",
        str(cdl_path),
        str(xyz),
    ])

    counter: Counter[int] = Counter()

    with xyz.open(
        "r",
        encoding="utf-8",
        errors="replace",
    ) as stream:
        for line in stream:
            parts = line.split()

            if len(parts) != 3:
                continue

            try:
                value = int(round(float(parts[2])))
            except ValueError:
                continue

            if value not in {0, 255}:
                counter[value] += 1

    info_result = subprocess.run(
        ["gdalinfo", "-json", str(cdl_path)],
        check=True,
        capture_output=True,
        text=True,
    )

    info = json.loads(info_result.stdout)
    transform = info["geoTransform"]

    pixel_area_square_meters = abs(
        float(transform[1])
        * float(transform[5])
    )

    xyz.unlink(missing_ok=True)

    return counter, pixel_area_square_meters


def _boundary_overlay(
    base: Image.Image,
    classes: Image.Image,
) -> Image.Image:
    horizontal = ImageChops.difference(
        classes,
        ImageChops.offset(classes, 1, 0),
    )

    vertical = ImageChops.difference(
        classes,
        ImageChops.offset(classes, 0, 1),
    )

    edges = ImageChops.lighter(
        horizontal,
        vertical,
    ).point(
        lambda value: 255 if value else 0
    )

    edges = edges.filter(ImageFilter.MaxFilter(3))

    halo = edges.filter(ImageFilter.MaxFilter(5))

    result = base.convert("RGBA")

    halo_layer = Image.new(
        "RGBA",
        base.size,
        (0, 0, 0, 0),
    )

    halo_layer.putalpha(
        halo.point(
            lambda value: 165 if value else 0
        )
    )

    line_layer = Image.new(
        "RGBA",
        base.size,
        (255, 190, 25, 0),
    )

    line_layer.putalpha(
        edges.point(
            lambda value: 245 if value else 0
        )
    )

    result.alpha_composite(halo_layer)
    result.alpha_composite(line_layer)

    return result.convert("RGB")


def _write_wrapped(
    draw: ImageDraw.ImageDraw,
    position: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    maximum_characters: int,
    line_height: int,
) -> int:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []

    for word in words:
        candidate = " ".join(current + [word])

        if (
            current
            and len(candidate) > maximum_characters
        ):
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)

    if current:
        lines.append(" ".join(current))

    x, y = position

    for line in lines:
        draw.text(
            (x, y),
            line,
            font=font,
            fill=fill,
        )
        y += line_height

    return y


def _open_preview(path: Path) -> None:
    if shutil.which("explorer.exe") and shutil.which("wslpath"):
        windows_path = subprocess.run(
            ["wslpath", "-w", str(path.resolve())],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        subprocess.Popen(
            ["explorer.exe", windows_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        return

    if shutil.which("xdg-open"):
        subprocess.Popen(
            ["xdg-open", str(path.resolve())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def render_recipe(
    root: Path,
    handoff: Path,
    recipe: dict[str, Any],
    compatibility: dict[str, Any],
    requested_name: str,
    bbox: tuple[float, float, float, float],
    start: str,
    end: str,
    year: int,
    open_preview: bool,
) -> Path:
    assets = {
        name: (
            Path(value)
            if value is not None
            else None
        )
        for name, value in compatibility["assets"].items()
    }

    stamp = _utc_stamp()
    output = (
        handoff
        / "preview"
        / f"{recipe['recipe_id']}_{stamp}"
    )

    work = output / "_work"
    output.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    aligned, grid = _align_assets(
        assets,
        work,
    )

    natural = Image.open(
        aligned["natural"]
    ).convert("RGB")

    ndvi = Image.open(
        aligned["ndvi"]
    ).convert("RGB")

    classes = Image.open(
        aligned["cdl_classes"]
    ).convert("L")

    natural = ImageOps.autocontrast(
        natural,
        cutoff=1,
    )

    natural = ImageEnhance.Contrast(
        natural
    ).enhance(1.08)

    natural = ImageEnhance.Color(
        natural
    ).enhance(1.06)

    ndvi = ImageOps.autocontrast(
        ndvi,
        cutoff=1,
    )

    ndvi = ImageEnhance.Contrast(
        ndvi
    ).enhance(1.10)

    ndvi = ImageEnhance.Color(
        ndvi
    ).enhance(1.08)

    if hasattr(classes, "get_flattened_data"):
        class_values = list(
            classes.get_flattened_data()
        )
    else:
        class_values = list(classes.getdata())

    class_color = Image.new(
        "RGB",
        classes.size,
    )

    class_color.putdata([
        (
            _class_color(value)
            if value not in {0, 255}
            else (30, 30, 30)
        )
        for value in class_values
    ])

    cdl_path = assets["cdl_classes"]

    if cdl_path is None:
        raise RuntimeError("CDL class COG is missing")

    class_counts, pixel_area = _full_class_counts(
        cdl_path,
        work,
    )

    total_count = sum(class_counts.values()) or 1
    top_classes = class_counts.most_common(7)

    inventory_csv = output / "class_inventory.csv"

    with inventory_csv.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as stream:
        writer = csv.writer(stream)

        writer.writerow([
            "class_code",
            "class_name",
            "pixel_count",
            "fraction",
            "approximate_hectares",
        ])

        for code, count in class_counts.most_common():
            writer.writerow([
                code,
                CLASS_NAMES.get(code, f"Class {code}"),
                count,
                count / total_count,
                count * pixel_area / 10000,
            ])

    ndvi_boundaries = _boundary_overlay(
        ndvi,
        classes,
    )

    natural_boundaries = _boundary_overlay(
        natural,
        classes,
    )

    ndvi_cdl = Image.blend(
        ndvi,
        class_color,
        0.34,
    )

    hillshade = None

    if "hillshade" in aligned:
        hillshade = Image.open(
            aligned["hillshade"]
        ).convert("L")

        hillshade = ImageOps.colorize(
            hillshade,
            black="#252525",
            white="#f1f1f1",
        )

    preview_type = recipe["preview"]

    if preview_type == "ndvi_cdl_boundaries":
        main = ndvi_boundaries
        main_caption = (
            "NAIP NDVI rendering with USDA CDL "
            "class boundaries"
        )

    elif preview_type == "field_structure":
        field_base = Image.blend(
            natural,
            ndvi,
            0.22,
        )

        main = _boundary_overlay(
            field_base,
            classes,
        )

        main_caption = (
            "Natural color and NDVI field structure "
            "with CDL boundaries"
        )

    elif preview_type == "class_inventory":
        main = _boundary_overlay(
            class_color,
            classes,
        )

        main_caption = (
            "USDA CDL class inventory with preserved "
            "categorical boundaries"
        )

    elif preview_type == "crop_terrain":
        if hillshade is None:
            raise RuntimeError(
                "Terrain recipe requires hillshade"
            )

        terrain_base = Image.blend(
            ndvi,
            hillshade,
            0.18,
        )

        main = _boundary_overlay(
            terrain_base,
            classes,
        )

        main_caption = (
            "NDVI and CDL crop structure with "
            "subordinate 3DEP terrain context"
        )

    else:
        raise RuntimeError(
            f"Unknown preview type: {preview_type}"
        )

    dashboard = Image.new(
        "RGB",
        (3840, 2160),
        (241, 244, 247),
    )

    draw = ImageDraw.Draw(dashboard)

    title_font = _font(46, bold=True)
    subtitle_font = _font(23)
    section_font = _font(24, bold=True)
    body_font = _font(19)
    small_font = _font(17)

    draw.text(
        (45, 28),
        f"FasterRaster Cook — {recipe['title']}",
        font=title_font,
        fill=(24, 39, 54),
    )

    derivation_network_bytes = int(compatibility.get("network_bytes", 0))
    execution_summary = (
        "asset-level reuse · zero additional network transfer"
        if derivation_network_bytes == 0
        else f"asset-level reuse + selective acquisition · {derivation_network_bytes:,} network bytes"
    )

    draw.text(
        (47, 87),
        f"{start} through {end} · {execution_summary}",
        font=subtitle_font,
        fill=(68, 82, 96),
    )

    main_box = (45, 140, 2285, 1520)

    dashboard.paste(
        _fit(
            main,
            (
                main_box[2] - main_box[0],
                main_box[3] - main_box[1],
            ),
        ),
        (main_box[0], main_box[1]),
    )

    draw.rectangle(
        main_box,
        outline=(80, 94, 108),
        width=3,
    )

    draw.rectangle(
        (45, 1470, 2285, 1520),
        fill=(250, 251, 252),
    )

    draw.text(
        (60, 1482),
        main_caption,
        font=body_font,
        fill=(31, 45, 58),
    )

    card = (2325, 140, 3795, 1520)

    draw.rounded_rectangle(
        card,
        radius=18,
        fill=(249, 251, 252),
        outline=(199, 209, 218),
        width=2,
    )

    x = 2360
    y = 175

    draw.text(
        (x, y),
        "RECIPE AND REUSE RECEIPT",
        font=section_font,
        fill=(26, 43, 59),
    )

    y += 46

    receipt_lines = [
        f"Recipe: {recipe['recipe_id']}",
        f"Order: {requested_name}",
        f"Published handoff: {compatibility.get('published_handoff_id', handoff.name)}",
        f"Crop-cookie: {list(bbox)}",
        f"Timeframe: {start} through {end}",
        f"CDL year: {year}",
        "Resolution policy: per-asset plan",
        f"Network bytes this derivation: {derivation_network_bytes:,}",
        (
            "NAIP pixel size: "
            f"{compatibility['natural_pixel_size_m']:.3f} m"
        ),
        (
            "Maximum accepted: "
            f"{compatibility['maximum_naip_pixel_size_m']:.3f} m"
        ),
        (
            f"Inspection grid: {grid['width']:,} × "
            f"{grid['height']:,}"
        ),
        "Imagery resampling: cubic",
        "Categorical resampling: nearest",
    ]

    for line in receipt_lines:
        draw.text(
            (x, y),
            line,
            font=body_font,
            fill=(47, 62, 77),
        )
        y += 31

    y += 15

    draw.text(
        (x, y),
        "TOP CDL CLASSES",
        font=section_font,
        fill=(26, 43, 59),
    )

    y += 43

    for code, count in top_classes:
        percentage = count / total_count * 100
        hectares = count * pixel_area / 10000

        draw.rectangle(
            (x, y + 2, x + 25, y + 25),
            fill=_class_color(code),
            outline=(80, 80, 80),
        )

        draw.text(
            (x + 39, y),
            (
                f"{code:>3} "
                f"{CLASS_NAMES.get(code, f'Class {code}'):<22} "
                f"{percentage:5.1f}% · {hectares:,.1f} ha"
            ),
            font=body_font,
            fill=(47, 62, 77),
        )

        y += 32

    y += 14

    draw.text(
        (x, y),
        "INSPECTION FOCUS",
        font=section_font,
        fill=(26, 43, 59),
    )

    y += 42

    for focus in recipe["inspection_focus"]:
        y = _write_wrapped(
            draw,
            (x, y),
            "• " + focus,
            body_font,
            (37, 100, 62),
            62,
            27,
        )

    panel_y = 1570
    panel_height = 465
    panel_width = 735
    panel_gap = 18

    panel_images = [
        ("NAIP natural color", natural),
        ("NAIP NDVI", ndvi),
        ("USDA CDL classes", class_color),
        ("NDVI + CDL overlay", ndvi_cdl),
    ]

    if hillshade is not None:
        panel_images.append(
            ("USGS 3DEP hillshade", hillshade)
        )
    else:
        panel_images.append(
            ("Natural + CDL boundaries", natural_boundaries)
        )

    for index, (label, image) in enumerate(panel_images):
        left = 45 + index * (
            panel_width + panel_gap
        )

        dashboard.paste(
            _fit(
                image,
                (
                    panel_width,
                    panel_height - 42,
                ),
            ),
            (left, panel_y),
        )

        draw.rectangle(
            (
                left,
                panel_y,
                left + panel_width,
                panel_y + panel_height,
            ),
            outline=(155, 166, 177),
            width=2,
        )

        draw.rectangle(
            (
                left,
                panel_y + panel_height - 42,
                left + panel_width,
                panel_y + panel_height,
            ),
            fill=(250, 251, 252),
        )

        draw.text(
            (
                left + 9,
                panel_y + panel_height - 33,
            ),
            label,
            font=small_font,
            fill=(36, 49, 61),
        )

    preview = output / (
        f"{recipe['recipe_id']}_4k.png"
    )

    dashboard.save(
        preview,
        format="PNG",
        optimize=True,
    )

    receipt = {
        "schema_version": 2,
        "status": "PASS",
        "operation": "ag_recipe_local_derivation",
        "recipe_id": recipe["recipe_id"],
        "recipe_title": recipe["title"],
        "requested_name": requested_name,
        "published_handoff_id": compatibility.get("published_handoff_id", handoff.name),
        "network_bytes": derivation_network_bytes,
        "bbox_epsg_4326": list(bbox),
        "timeframe": {
            "start": start,
            "end": end,
            "cdl_year": year,
        },
        "compatibility": {
            **compatibility,
            "assets": {
                name: str(Path(value).relative_to(handoff)) if value is not None else None
                for name, value in compatibility["assets"].items()
            },
        },
        "grid": grid,
        "class_inventory": str(inventory_csv.relative_to(handoff)),
        "top_classes": [
            {
                "class_code": code,
                "class_name": CLASS_NAMES.get(
                    code,
                    f"Class {code}",
                ),
                "pixel_count": count,
                "fraction": count / total_count,
                "approximate_hectares": (
                    count * pixel_area / 10000
                ),
            }
            for code, count in top_classes
        ],
        "preview": {
            "path": str(preview.relative_to(handoff)),
            "sha256": _sha256(preview),
            "width": dashboard.width,
            "height": dashboard.height,
        },
    }

    receipt_path = output / "recipe_receipt.json"

    receipt_path.write_text(
        json.dumps(
            receipt,
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )

    checksums = output / "checksums.sha256"

    with checksums.open(
        "w",
        encoding="utf-8",
    ) as stream:
        for path in sorted(output.iterdir()):
            if (
                path.is_file()
                and path != checksums
            ):
                stream.write(
                    f"{_sha256(path)}  {path.name}\n"
                )

    shutil.rmtree(
        work,
        ignore_errors=True,
    )

    print()
    print("======================================================")
    print("FASTERRASTER AG RECIPE OUTPUTS: READY FOR PUBLICATION")
    print("======================================================")
    published_id = compatibility.get("published_handoff_id", handoff.name)
    print(f"recipe: {recipe['recipe_id']}")
    print(f"target_handoff_id: {published_id}")
    print(f"network_bytes: {derivation_network_bytes}")
    print(f"preview: {preview.relative_to(handoff)}")
    print(f"receipt: {receipt_path.relative_to(handoff)}")
    print(f"class_inventory: {inventory_csv.relative_to(handoff)}")
    print(f"checksums: {checksums.relative_to(handoff)}")
    print(f"preview_sha256: {_sha256(preview)}")

    if open_preview:
        _open_preview(preview)
        print("PREVIEW_LAUNCH: PASS")

    return preview


def bootstrap(root: Path, argv: list[str]) -> None:
    """Run recipe mode explicitly; leave legacy direct mode untouched."""
    if not _has_option(argv, "--recipe"):
        return

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from faster_raster.ag_execution import run_recipe_cli

    exit_code = run_recipe_cli(root, argv, renderer=render_recipe)

    if exit_code is not None:
        raise SystemExit(exit_code)
