from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import rasterio
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

from faster_raster.human_development import CHANGE_CODE_INFO, LAND_COVER_NODATA


CANVAS_SIZE = (3840, 2160)
LAND_COLORS = {
    11: (70, 120, 190),
    12: (220, 235, 245),
    21: (225, 205, 190),
    22: (235, 155, 135),
    23: (215, 85, 75),
    24: (155, 30, 45),
    31: (190, 175, 145),
    41: (90, 150, 80),
    42: (45, 105, 55),
    43: (105, 135, 75),
    52: (170, 165, 105),
    71: (205, 205, 125),
    81: (225, 220, 150),
    82: (190, 165, 80),
    90: (100, 150, 145),
    95: (125, 175, 165),
    LAND_COVER_NODATA: (35, 39, 45),
}
ALL_TRANSITIONS_CHANGE_COLORS = {
    0: (35, 39, 45),
    1: (205, 215, 205),
    2: (160, 80, 105),
    3: (220, 55, 45),
    4: (55, 115, 210),
    5: (245, 150, 40),
    6: (80, 175, 215),
    7: (170, 155, 190),
}
DEVELOPMENT_CHANGE_COLORS = {
    **ALL_TRANSITIONS_CHANGE_COLORS,
    7: (198, 194, 202),
}
CHANGE_COLORS = ALL_TRANSITIONS_CHANGE_COLORS
PANEL_BACKGROUND = (247, 246, 241)
LEGEND_BAND_Y = 1018


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    names = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _read_sample(path: Path, width: int, height: int, *, nearest: bool) -> np.ndarray:
    with rasterio.open(path) as source:
        return source.read(
            (1,),
            out_shape=(height, width),
            resampling=rasterio.enums.Resampling.nearest if nearest else rasterio.enums.Resampling.bilinear,
        )[0]


def _categorical_image(values: np.ndarray, colors: Mapping[int, tuple[int, int, int]]) -> Image.Image:
    result = np.zeros((*values.shape, 3), dtype=np.uint8)
    result[:] = (35, 39, 45)
    for code, color in colors.items():
        result[values == code] = color
    return Image.fromarray(result, mode="RGB")


def _continuous_image(values: np.ndarray, nodata: float = -9999.0) -> Image.Image:
    result = np.zeros((*values.shape, 3), dtype=np.uint8)
    valid = np.isfinite(values) & (values != nodata)
    if np.any(valid):
        clipped = np.clip(values, -30, 30)
        magnitude = np.abs(clipped) / 30.0
        positive = clipped >= 0
        result[valid & positive, 0] = (245 * magnitude[valid & positive]).astype(np.uint8)
        result[valid & positive, 1] = (200 * (1 - magnitude[valid & positive])).astype(np.uint8)
        result[valid & positive, 2] = 55
        result[valid & ~positive, 0] = 55
        result[valid & ~positive, 1] = (180 * (1 - magnitude[valid & ~positive])).astype(np.uint8)
        result[valid & ~positive, 2] = (235 * magnitude[valid & ~positive]).astype(np.uint8)
    result[~valid] = (35, 39, 45)
    return Image.fromarray(result, mode="RGB")


def _panel(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    title: str,
    image: Image.Image | None,
    subtitle: str,
) -> None:
    left, top, right, bottom = box
    draw.rounded_rectangle(box, radius=22, fill=(247, 246, 241), outline=(208, 207, 199), width=3)
    draw.text((left + 28, top + 20), title, fill=(31, 44, 55), font=_font(34, bold=True))
    draw.text((left + 28, top + 66), subtitle, fill=(82, 91, 96), font=_font(22))
    if image is not None:
        max_width = right - left - 56
        max_height = bottom - top - 130
        padded = Image.new("RGB", (max_width, max_height), PANEL_BACKGROUND)
        fitted = image.copy()
        fitted.thumbnail((max_width, max_height), Image.Resampling.NEAREST)
        padded.paste(
            fitted,
            ((max_width - fitted.width) // 2, (max_height - fitted.height) // 2),
        )
        canvas.paste(padded, (left + 28, top + 105))


def _text_panel(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    title: str,
    lines: Sequence[str],
) -> None:
    left, top, right, bottom = box
    draw.rounded_rectangle(box, radius=22, fill=(247, 246, 241), outline=(208, 207, 199), width=3)
    draw.text((left + 28, top + 22), title, fill=(31, 44, 55), font=_font(34, bold=True))
    y = top + 88
    for line in lines:
        draw.text((left + 32, y), line, fill=(50, 60, 66), font=_font(25))
        y += 42
        if y > bottom - 35:
            break


def _trend_panel(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    epoch_results: Sequence[Mapping[str, Any]],
) -> None:
    left, top, right, bottom = box
    draw.rounded_rectangle(box, radius=22, fill=(247, 246, 241), outline=(208, 207, 199), width=3)
    draw.text((left + 28, top + 22), "Developed area on all-epoch common valid footprint", fill=(31, 44, 55), font=_font(34, bold=True))
    plot = (left + 90, top + 115, right - 45, bottom - 95)
    draw.line((plot[0], plot[3], plot[2], plot[3]), fill=(100, 105, 107), width=3)
    draw.line((plot[0], plot[1], plot[0], plot[3]), fill=(100, 105, 107), width=3)
    values = [
        float(
            item["statistics"].get("common_all_epoch_footprint", {}).get(
                "developed_land", item["statistics"]["developed_land"]
            )["hectares"]
        )
        for item in epoch_results
    ]
    years = [int(item["year"]) for item in epoch_results]
    maximum = max(values) if values else 1.0
    minimum = min(values) if values else 0.0
    spread = max(maximum - minimum, 1.0)
    points = []
    for index, value in enumerate(values):
        fraction_x = index / max(len(values) - 1, 1)
        fraction_y = (value - minimum) / spread
        x = int(plot[0] + fraction_x * (plot[2] - plot[0]))
        y = int(plot[3] - fraction_y * (plot[3] - plot[1]))
        points.append((x, y))
        draw.ellipse((x - 8, y - 8, x + 8, y + 8), fill=(196, 55, 60))
        draw.text((x - 30, plot[3] + 16), str(years[index]), fill=(60, 68, 72), font=_font(20))
        draw.text((x - 50, y - 42), f"{value:.2f} ha", fill=(60, 68, 72), font=_font(19))
    if len(points) > 1:
        draw.line(points, fill=(196, 55, 60), width=6)


def render_human_development_preview(
    destination: Path,
    *,
    study_name: str,
    comparison_mode: str,
    epoch_results: Sequence[Mapping[str, Any]],
    endpoint_result: Mapping[str, Any],
    source_contract: Mapping[str, Any],
    grid: Mapping[str, Any],
    preview_emphasis: str = "development",
) -> Path:
    if preview_emphasis not in {"development", "all_transitions"}:
        raise ValueError(f"unsupported preview emphasis: {preview_emphasis}")
    change_palette = (
        DEVELOPMENT_CHANGE_COLORS
        if preview_emphasis == "development"
        else ALL_TRANSITIONS_CHANGE_COLORS
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGB", CANVAS_SIZE, (230, 229, 222))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, CANVAS_SIZE[0], 145), fill=(31, 44, 55))
    draw.text((72, 28), "Human Development Change", fill=(248, 247, 241), font=_font(52, bold=True))
    draw.text(
        (74, 91),
        f"{study_name} · {comparison_mode.replace('_', ' ')} · mapped land-cover evidence",
        fill=(205, 215, 219),
        font=_font(26),
    )

    panel_width = 1160
    panel_height = 820
    x_positions = (80, 1340, 2600)
    y_positions = (185, 1055)
    baseline = epoch_results[0]
    comparison = epoch_results[-1]
    baseline_values = _read_sample(Path(baseline["land_cover"]), 1060, 660, nearest=True)
    comparison_values = _read_sample(Path(comparison["land_cover"]), 1060, 660, nearest=True)
    change_values = _read_sample(Path(endpoint_result["change_codes"]), 1060, 660, nearest=True)
    _panel(
        canvas,
        draw,
        (x_positions[0], y_positions[0], x_positions[0] + panel_width, y_positions[0] + panel_height),
        f"Baseline land cover · {baseline['year']}",
        _categorical_image(baseline_values, LAND_COLORS),
        "Official Annual NLCD classes; developed = 21–24",
    )
    _panel(
        canvas,
        draw,
        (x_positions[1], y_positions[0], x_positions[1] + panel_width, y_positions[0] + panel_height),
        f"Comparison land cover · {comparison['year']}",
        _categorical_image(comparison_values, LAND_COLORS),
        "Nearest-neighbour harmonization to the deterministic grid",
    )
    _panel(
        canvas,
        draw,
        (x_positions[2], y_positions[0], x_positions[2] + panel_width, y_positions[0] + panel_height),
        "Endpoint change codes",
        _categorical_image(change_values, change_palette),
        f"{baseline['year']} → {comparison['year']}; invalid comparisons shown dark",
    )

    impervious_path = endpoint_result.get("imperviousness_difference")
    if impervious_path:
        difference = _read_sample(Path(impervious_path), 1060, 660, nearest=False)
        _panel(
            canvas,
            draw,
            (x_positions[0], y_positions[1], x_positions[0] + panel_width, y_positions[1] + panel_height),
            "Fractional imperviousness difference",
            _continuous_image(difference),
            "Percentage-point change; blue decrease · red increase",
        )
    else:
        _text_panel(
            draw,
            (x_positions[0], y_positions[1], x_positions[0] + panel_width, y_positions[1] + panel_height),
            "Fractional imperviousness unavailable",
            [
                "No difference raster was calculated.",
                "Both endpoint epochs must provide a pinned",
                "fractional-imperviousness GeoTIFF.",
                "",
                "Land-cover change analysis remains complete.",
            ],
        )
    _trend_panel(
        draw,
        (x_positions[1], y_positions[1], x_positions[1] + panel_width, y_positions[1] + panel_height),
        epoch_results,
    )
    statistics = endpoint_result["statistics"]
    reconciliation = statistics["transition_reconciliation"]
    evidence_lines = [
        f"Source: USGS Annual NLCD C{source_contract['collection']}.{source_contract['version']} ({source_contract['region']})",
        f"Epochs: {', '.join(str(item['year']) for item in epoch_results)}",
        f"Grid: {grid['crs']} · {grid['resolution_m']:.0f} m · {grid['width']}×{grid['height']}",
        f"Grid fingerprint: {grid['fingerprint_sha256'][:20]}…",
        f"Valid comparison: {statistics['valid_comparison']['pixels']:,} pixels",
        f"Gross gain: {statistics['gross_development_gain']['hectares']:.3f} ha",
        f"Apparent loss: {statistics['apparent_development_loss']['hectares']:.3f} ha",
        f"Net change: {statistics['net_development_change']['hectares']:.3f} ha",
        f"Transition reconciliation: {'PASS' if reconciliation['reconciles'] else 'FAIL'}",
        "",
        "Interpretation limit: mapped cover change only.",
        "No population, economic, construction-date,",
        "causality, or occupancy claim is made.",
    ]
    _text_panel(
        draw,
        (x_positions[2], y_positions[1], x_positions[2] + panel_width, y_positions[1] + panel_height),
        "Statistics and evidence",
        evidence_lines,
    )
    legend_y = LEGEND_BAND_Y
    draw.text((80, legend_y), "Change:", fill=(31, 44, 55), font=_font(22, bold=True))
    x = 190
    for code in range(8):
        draw.rectangle((x, legend_y, x + 24, legend_y + 24), fill=change_palette[code])
        draw.text((x + 32, legend_y - 2), f"{code} {CHANGE_CODE_INFO[code][0].replace('_', ' ')}", fill=(45, 53, 58), font=_font(18))
        x += 440
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("workflow", "human_development_change")
    metadata.add_text("comparison_mode", comparison_mode)
    metadata.add_text("preview_emphasis", preview_emphasis)
    metadata.add_text("evidence", "USGS Annual NLCD; developed classes 21-24; mapped cover change only")
    canvas.save(destination, "PNG", optimize=True, pnginfo=metadata)
    return destination
