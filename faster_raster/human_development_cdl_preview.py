from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import rasterio
from PIL import Image, ImageDraw, ImageFilter, ImageFont, PngImagePlugin
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT

from faster_raster.development_sources import USDA_CDL_MAPPING
from faster_raster.human_development import CHANGE_CODE_INFO, development_rank


CANVAS_SIZE = (3840, 2160)
ALL_TRANSITIONS_CHANGE_COLORS = {
    0: (35, 39, 45), 1: (211, 216, 205), 2: (151, 76, 100),
    3: (224, 54, 44), 4: (53, 112, 205), 5: (245, 147, 38),
    6: (76, 173, 213), 7: (169, 154, 189),
}
DEVELOPMENT_CHANGE_COLORS = {
    **ALL_TRANSITIONS_CHANGE_COLORS,
    7: (198, 194, 202),
}
CHANGE_COLORS = ALL_TRANSITIONS_CHANGE_COLORS
PANEL_BACKGROUND = (247, 246, 241)
LEGEND_BAND_Y = 1022
STATE_COLORS = {
    -1: (35, 39, 45), 0: (213, 208, 190), 1: (225, 205, 190),
    2: (235, 155, 135), 3: (215, 85, 75), 4: (155, 30, 45),
}


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            pass
    return ImageFont.load_default()


def _sample(path: Path, width: int, height: int, *, resampling: Resampling = Resampling.nearest) -> np.ndarray:
    with rasterio.open(path) as source:
        return source.read((1,), out_shape=(height, width), resampling=resampling)[0]


def _state_image(values: np.ndarray) -> Image.Image:
    ranks = development_rank(values, USDA_CDL_MAPPING)
    result = np.zeros((*ranks.shape, 3), dtype=np.uint8)
    for state, color in STATE_COLORS.items():
        result[ranks == state] = color
    return Image.fromarray(result, mode="RGB")


def _categorical(values: np.ndarray, colors: Mapping[int, tuple[int, int, int]]) -> Image.Image:
    result = np.zeros((*values.shape, 3), dtype=np.uint8)
    result[:] = (35, 39, 45)
    for code, color in colors.items():
        result[values == code] = color
    return Image.fromarray(result, mode="RGB")


def _naip(path: Path, grid: Mapping[str, Any], width: int, height: int) -> Image.Image:
    from affine import Affine

    transform = Affine(*grid["transform"])
    with rasterio.open(path) as source:
        if source.count < 3:
            raise ValueError("NAIP context must contain at least three bands")
        with WarpedVRT(
            source, crs=grid["crs"], transform=transform,
            width=int(grid["width"]), height=int(grid["height"]),
            resampling=Resampling.bilinear,
        ) as warped:
            bands = warped.read((1, 2, 3), out_shape=(3, height, width), resampling=Resampling.bilinear)
    rgb = np.moveaxis(np.clip(bands, 0, 255).astype(np.uint8), 0, 2)
    image = Image.fromarray(rgb, mode="RGB")
    return image


def _boundary_overlay(context: Image.Image, state: Image.Image) -> Image.Image:
    ranks = np.asarray(state.convert("L"))
    edges = np.zeros(ranks.shape, dtype=np.uint8)
    edges[:, 1:] |= ranks[:, 1:] != ranks[:, :-1]
    edges[1:, :] |= ranks[1:, :] != ranks[:-1, :]
    mask = Image.fromarray(edges * 255, mode="L").filter(ImageFilter.MaxFilter(3))
    overlay = Image.new("RGBA", context.size, (255, 79, 55, 0))
    overlay.putalpha(mask.point(lambda value: 220 if value else 0))
    result = context.convert("RGBA")
    result.alpha_composite(overlay)
    return result.convert("RGB")


def _card(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], title: str, subtitle: str = "") -> None:
    left, top, right, bottom = box
    draw.rounded_rectangle(box, radius=20, fill=(247, 246, 241), outline=(204, 205, 198), width=3)
    draw.text((left + 24, top + 17), title, fill=(31, 44, 55), font=_font(30, bold=True))
    if subtitle:
        draw.text((left + 24, top + 58), subtitle, fill=(78, 88, 93), font=_font(18))


def _image_card(canvas: Image.Image, draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], title: str, subtitle: str, image: Image.Image) -> None:
    _card(draw, box, title, subtitle)
    left, top, right, bottom = box
    inner_width = right - left - 44
    inner_height = bottom - top - 102
    padded = Image.new("RGB", (inner_width, inner_height), PANEL_BACKGROUND)
    target = image.copy()
    target.thumbnail((inner_width, inner_height), Image.Resampling.NEAREST)
    padded.paste(
        target,
        ((inner_width - target.width) // 2, (inner_height - target.height) // 2),
    )
    canvas.paste(padded, (left + 22, top + 88))


def _wrapped(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, *, width: int, font: ImageFont.ImageFont, fill: tuple[int, int, int], line_height: int) -> int:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        if current and draw.textlength(candidate, font=font) > width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height
    return y


def _context_pair(path: Path, grid: Mapping[str, Any], endpoint_values: np.ndarray, width: int, height: int) -> Image.Image:
    half = (width - 16) // 2
    natural = _naip(path, grid, half, height)
    state = _state_image(endpoint_values).resize((half, height), Image.Resampling.NEAREST)
    overlay = _boundary_overlay(natural, state)
    result = Image.new("RGB", (width, height), (35, 39, 45))
    result.paste(natural, (0, 0))
    result.paste(overlay, (half + 16, 0))
    return result


def render_cdl_proxy_preview(
    destination: Path,
    *,
    study_name: str,
    comparison_mode: str,
    bbox: Sequence[float],
    epoch_results: Sequence[Mapping[str, Any]],
    interval_results: Sequence[Mapping[str, Any]],
    endpoint_result: Mapping[str, Any],
    source_contract: Mapping[str, Any],
    grid: Mapping[str, Any],
    context_result: Mapping[str, Any] | None,
    network_bytes: int,
    reused_bytes: int,
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
    draw.rectangle((0, 0, 3840, 146), fill=(31, 44, 55))
    draw.text((60, 23), "USDA CDL-derived mapped development proxy change", fill=(250, 248, 241), font=_font(43, bold=True))
    draw.text((62, 85), f"{study_name} · {comparison_mode.replace('_', ' ')} · crop-focused source qualification preserved", fill=(204, 215, 219), font=_font(23))

    boxes = [
        (55, 175, 1245, 985), (1325, 175, 2515, 985), (2595, 175, 3785, 985),
        (55, 1085, 1245, 1965), (1325, 1085, 2515, 1965), (2595, 1085, 3785, 1965),
    ]
    baseline, endpoint = epoch_results[0], epoch_results[-1]
    before_values = _sample(Path(baseline["land_cover"]), 1090, 650)
    after_values = _sample(Path(endpoint["land_cover"]), 1090, 650)
    change_values = _sample(Path(endpoint_result["change_codes"]), 1090, 650)
    _image_card(canvas, draw, boxes[0], f"{baseline['year']} CDL development state", "Declared CDL values; developed proxy = 121–124", _state_image(before_values))
    _image_card(canvas, draw, boxes[1], f"{endpoint['year']} CDL development state", "Nearest-neighbour harmonization · EPSG:5070 · 30 m", _state_image(after_values))
    _image_card(canvas, draw, boxes[2], f"{baseline['year']}→{endpoint['year']} endpoint change", "Codes 0–7 · invalid comparisons shown dark", _categorical(change_values, change_palette))

    if context_result:
        context = _context_pair(Path(context_result["path"]), grid, after_values, 1090, 710)
        _image_card(canvas, draw, boxes[3], f"{context_result['year']} NAIP context + boundary", "Left: natural color · right: endpoint-state boundary · context only", context)
    else:
        _card(draw, boxes[3], "NAIP context unavailable", "Temporal CDL analysis remains complete")
        y = boxes[3][1] + 125
        for line in ("No compatible bounded endpoint context asset was available.", "NAIP is optional visual geography only.", "It is never used as historical transition evidence."):
            y = _wrapped(draw, boxes[3][0] + 30, y, line, width=boxes[3][2] - boxes[3][0] - 60, font=_font(23), fill=(52, 61, 66), line_height=36) + 16

    _card(draw, boxes[4], "Developed area on all-epoch common valid footprint", "Comparable CDL proxy states; adjacent intervals retain pairwise masks")
    left, top, right, bottom = boxes[4]
    plot = (left + 80, top + 120, right - 50, top + 500)
    draw.line((plot[0], plot[3], plot[2], plot[3]), fill=(94, 100, 103), width=3)
    values = [
        float(
            item["statistics"].get("common_all_epoch_footprint", {}).get(
                "developed_land", item["statistics"]["developed_land"]
            )["hectares"]
        )
        for item in epoch_results
    ]
    low, high = min(values), max(values)
    span = max(high - low, 0.1)
    points = []
    for index, (epoch, value) in enumerate(zip(epoch_results, values)):
        x = int(plot[0] + index / max(len(values) - 1, 1) * (plot[2] - plot[0]))
        y = int(plot[3] - (value - low) / span * (plot[3] - plot[1]))
        points.append((x, y))
        draw.ellipse((x - 8, y - 8, x + 8, y + 8), fill=(196, 55, 60))
        draw.text((x - 36, plot[3] + 14), str(epoch["year"]), fill=(55, 64, 69), font=_font(18))
        draw.text((x - 55, y - 36), f"{value:.2f} ha", fill=(55, 64, 69), font=_font(17))
    if len(points) > 1:
        draw.line(points, fill=(196, 55, 60), width=5)
    y = top + 555
    for item in interval_results:
        stats = item["statistics"]
        draw.text((left + 34, y), f"{item['before_year']}→{item['after_year']}: gain {stats['gross_development_gain']['hectares']:.2f} ha · apparent loss {stats['apparent_development_loss']['hectares']:.2f} ha · net {stats['net_development_change']['hectares']:+.2f} ha", fill=(48, 58, 63), font=_font(18))
        y += 34

    _card(draw, boxes[5], "Statistics, transitions, and source evidence", "Truthful proxy semantics · no causal inference")
    stats = endpoint_result["statistics"]
    source_table = json.loads(Path(endpoint_result["source_transition_json"]).read_text(encoding="utf-8"))
    top_source = sorted(source_table.get("rows", []), key=lambda row: (-row["pixel_count"], row["baseline_source_class"], row["comparison_source_class"]))[:3]
    lines = [
        f"Source: {source_contract['source_id']} · years {', '.join(str(item['year']) for item in epoch_results)}",
        f"AOI: [{', '.join(f'{value:.3f}' for value in bbox)}]",
        f"Target: {grid['crs']} · {grid['resolution_m']:.0f} m · {grid['width']}×{grid['height']}",
        f"Mapping: {source_contract['mapping_id']} · classes 121–124 · {source_contract['mapping_contract_sha256'][:16]}…",
        f"Valid area: {stats['valid_comparison']['hectares']:.2f} ha · invalid: {stats['invalid_comparison']['hectares']:.2f} ha",
        f"Gross gain: {stats['gross_development_gain']['hectares']:.2f} ha · apparent loss: {stats['apparent_development_loss']['hectares']:.2f} ha",
        f"Net: {stats['net_development_change']['hectares']:+.2f} ha · intensity ↑ {stats['development_intensity_increase']['hectares']:.2f} ha · ↓ {stats['development_intensity_decrease']['hectares']:.2f} ha",
        f"Network bytes: {network_bytes:,} · reused bytes: {reused_bytes:,}",
        f"Transition reconciliation: {'PASS' if stats['transition_reconciliation']['reconciles'] else 'FAIL'}",
    ]
    y = boxes[5][1] + 102
    for line in lines:
        draw.text((boxes[5][0] + 28, y), line, fill=(45, 55, 60), font=_font(17))
        y += 31
    draw.text((boxes[5][0] + 28, y + 6), "Top changed source transitions", fill=(31, 44, 55), font=_font(20, bold=True))
    y += 43
    for row in top_source:
        draw.text((boxes[5][0] + 30, y), f"{row['baseline_source_class']} {row['baseline_class_label']} → {row['comparison_source_class']} {row['comparison_class_label']}: {row['pixel_count']:,} px", fill=(48, 58, 63), font=_font(16))
        y += 28
    qualifications = (
        "CDL is crop-focused. Non-agricultural classes are a development proxy; apparent transitions may reflect real mapped change, ancillary classification, between-year classification, source-production differences, or a combination.",
        "No population, economic, construction-date, occupancy, cadastral-approval, authoritative Annual NLCD, or causal urban-expansion claim is made.",
    )
    for text in qualifications:
        y = _wrapped(draw, boxes[5][0] + 28, y + 10, text, width=boxes[5][2] - boxes[5][0] - 56, font=_font(16), fill=(104, 56, 52), line_height=24)

    legend_y = LEGEND_BAND_Y
    draw.text((60, legend_y), "Endpoint change:", fill=(31, 44, 55), font=_font(19, bold=True))
    x = 245
    for code in range(8):
        draw.rectangle((x, legend_y, x + 20, legend_y + 20), fill=change_palette[code])
        draw.text((x + 27, legend_y - 1), f"{code} {CHANGE_CODE_INFO[code][0].replace('_', ' ')}", fill=(43, 51, 56), font=_font(14))
        x += 440
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("workflow", "human_development_change")
    metadata.add_text("source", "USDA CDL-derived mapped development proxy change")
    metadata.add_text("mapping_id", USDA_CDL_MAPPING.mapping_id)
    metadata.add_text("developed_classes", "121,122,123,124")
    metadata.add_text("preview_emphasis", preview_emphasis)
    metadata.add_text("qualification", "CDL is crop-focused; non-agricultural change is a proxy; no population/economic/causal claim")
    canvas.save(destination, "PNG", optimize=True, pnginfo=metadata)
    return destination
