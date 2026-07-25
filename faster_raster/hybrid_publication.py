from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np
from PIL import Image, ImageDraw, ImageOps
from rasterio.enums import Resampling

from faster_raster.ag_classification_publication import (
    BACKGROUND,
    CARD,
    CLASSIFICATION_PALETTE,
    MUTED,
    TEXT,
    _font,
    _panel,
    _read_aoi_mask,
    _read_naip,
    _read_single,
)
from faster_raster.ag_recipes import AgriculturalRecipeV4


SPECIALIST_COLORS = (
    (0, 188, 212),
    (255, 112, 67),
    (171, 71, 188),
    (255, 202, 40),
    (38, 198, 218),
    (239, 83, 80),
)
UNRESOLVED_COLOR = (255, 255, 255)
DECISION_COLORS = {
    0: BACKGROUND,
    1: (86, 96, 104),
    2: (0, 172, 193),
    3: (255, 255, 255),
}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _hybrid_palette(recipe: AgriculturalRecipeV4) -> dict[int, tuple[int, int, int]]:
    palette = dict(CLASSIFICATION_PALETTE)
    for index, specialist in enumerate(
        recipe.classification.specialists.classes
    ):
        palette[specialist.output_code] = SPECIALIST_COLORS[
            index % len(SPECIALIST_COLORS)
        ]
    palette[recipe.classification.arbitration.unresolved_code] = (
        UNRESOLVED_COLOR
    )
    return palette


def _categorical_image(
    values: np.ndarray,
    palette: Mapping[int, tuple[int, int, int]],
    valid: np.ndarray,
) -> Image.Image:
    rgb = np.zeros((*values.shape, 3), dtype=np.uint8)
    rgb[:] = BACKGROUND
    for code, color in palette.items():
        rgb[values == code] = color
    rgb[~valid] = BACKGROUND
    return Image.fromarray(rgb, "RGB")


def _continuous_image(
    values: np.ndarray,
    valid: np.ndarray,
    *,
    zero_aware: bool,
) -> tuple[Image.Image, dict[str, Any]]:
    finite = valid & np.isfinite(values) & (values > -9990)
    sample = values[finite]
    if sample.size:
        low, high = np.quantile(sample.astype(np.float64), (0.02, 0.98))
        if high <= low:
            low, high = float(sample.min()), float(sample.max())
    else:
        low, high = (-1.0, 1.0) if zero_aware else (0.0, 1.0)
    if zero_aware:
        low = min(float(low), -0.05)
        high = max(float(high), 0.05)
        negative = np.clip(-values / max(abs(low), 1e-6), 0, 1)
        positive = np.clip(values / max(high, 1e-6), 0, 1)
        middle = np.array((235, 234, 218), dtype=np.float32)
        low_color = np.array((69, 117, 180), dtype=np.float32)
        high_color = np.array((27, 158, 119), dtype=np.float32)
        rgb = np.broadcast_to(middle, (*values.shape, 3)).copy()
        below = values < 0
        above = values > 0
        rgb[below] = (
            middle * (1 - negative[below, None])
            + low_color * negative[below, None]
        )
        rgb[above] = (
            middle * (1 - positive[above, None])
            + high_color * positive[above, None]
        )
        palette = "zero_centered_blue_neutral_green"
    else:
        scale = max(float(high - low), 1e-6)
        normalized = np.clip((values - low) / scale, 0, 1)
        low_color = np.array((24, 35, 44), dtype=np.float32)
        high_color = np.array((0, 188, 212), dtype=np.float32)
        rgb = (
            low_color[None, None, :] * (1 - normalized[..., None])
            + high_color[None, None, :] * normalized[..., None]
        )
        palette = "sequential_dark_teal"
    rgb[~finite] = BACKGROUND
    contract = {
        "method": "deterministic_aoi_valid_2_98_percentile_display_stretch",
        "stretch_bounds": [float(low), float(high)],
        "palette": palette,
        "analytical_values_modified": False,
    }
    return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB"), contract


def _score_target_direction(specialist: Any) -> str:
    strategy = specialist.strategy
    if strategy.type == "single_index_threshold":
        direction = strategy.condition.direction
        if direction == "range":
            return "declared range indicates target"
        return f"{direction} values indicate target"
    if strategy.type == "multi_index_weighted_score":
        return f"{strategy.direction} values indicate target"
    if strategy.type == "multi_index_boolean":
        return "higher satisfied-condition fraction indicates target"
    return "higher similarity indicates target"


def render_hybrid_classification_audit(
    destination: Path,
    *,
    naip_path: Path,
    general_result: Mapping[str, Any],
    hybrid_result: Mapping[str, Any],
    recipe: AgriculturalRecipeV4,
    year: int,
    cdl_year: int,
    analysis_aoi_epsg_4326: Mapping[str, Any] | None,
    network_bytes: int,
    reused_bytes: int,
) -> tuple[Path, dict[str, Any]]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    width, height = 3840, 2160
    canvas = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    draw.text(
        (38, 20),
        "NAIP–CDL Index-Guided Hybrid Classification Audit",
        fill=TEXT,
        font=_font(40, bold=True),
    )
    draw.text(
        (40, 70),
        (
            "Broad weak-supervised classes remain separate; specialist "
            "scene-relative rules are explicit and inspectable."
        ),
        fill=MUTED,
        font=_font(20),
    )
    paths = hybrid_result["paths"]
    panel_width = 930
    panel_height = 870
    image_height = panel_height - 54
    natural, _, _, _ = _read_naip(
        naip_path,
        panel_width,
        image_height,
        analysis_aoi_epsg_4326,
    )
    general = _read_single(
        Path(paths["general_classification"]),
        panel_width,
        image_height,
    )
    final = _read_single(
        Path(paths["final_hybrid_classification"]),
        panel_width,
        image_height,
    )
    decision = _read_single(
        Path(paths["hybrid_decision_state"]),
        panel_width,
        image_height,
    )
    valid = _read_aoi_mask(
        Path(paths["general_classification"]),
        panel_width,
        image_height,
        analysis_aoi_epsg_4326,
    )
    valid &= general > 0
    palette = _hybrid_palette(recipe)
    general_image = _categorical_image(
        general,
        CLASSIFICATION_PALETTE,
        valid,
    )
    final_image = _categorical_image(final, palette, valid)
    decision_image = _categorical_image(
        decision,
        DECISION_COLORS,
        valid,
    )
    display_indices = [
        request
        for request in recipe.classification.indices
        if request.display
    ] or [
        request
        for request in recipe.classification.indices
        if request.persist
    ]
    selected_index = display_indices[0]
    index_values = _read_single(
        Path(paths["indices"][selected_index.index_id]),
        panel_width,
        image_height,
        resampling=Resampling.bilinear,
    )
    index_image, index_stretch = _continuous_image(
        index_values,
        valid,
        zero_aware=selected_index.index_id
        in {
            "ndvi",
            "gndvi",
            "vari",
            "green_nir_water_proxy",
            "ndmi",
            "nbr",
        },
    )
    first_specialist = recipe.classification.specialists.classes[0]
    score_target_direction = _score_target_direction(first_specialist)
    score_values = _read_single(
        Path(paths["specialist_scores"][first_specialist.class_id]),
        panel_width,
        image_height,
        resampling=Resampling.bilinear,
    )
    score_image, score_stretch = _continuous_image(
        score_values,
        valid,
        zero_aware=False,
    )
    candidate = _read_single(
        Path(paths["specialist_candidates"][first_specialist.class_id]),
        panel_width,
        image_height,
    )
    candidate_rgb = np.zeros((*candidate.shape, 3), dtype=np.uint8)
    candidate_rgb[:] = BACKGROUND
    candidate_rgb[(candidate == 0) & valid] = (70, 79, 86)
    candidate_rgb[(candidate == 1) & valid] = palette[
        first_specialist.output_code
    ]
    candidate_image = Image.fromarray(candidate_rgb, "RGB")
    summary = Image.new("RGB", (panel_width, image_height), CARD)
    summary_draw = ImageDraw.Draw(summary)
    summary_draw.text(
        (24, 20),
        "Decision receipt",
        fill=(80, 205, 196),
        font=_font(28, bold=True),
    )
    rows = [
        ("Imagery / CDL year", f"{year} / {cdl_year}"),
        (
            "General classes",
            str(recipe.classification.general.requested_class_count),
        ),
        (
            "Specialist classes",
            str(recipe.classification.specialists.requested_class_count),
        ),
        ("Selection mode", recipe.classification.specialists.selection_mode),
        (
            "Registry",
            str(hybrid_result["registry"]["registry_sha256"])[:16],
        ),
        (
            "Unresolved pixels",
            f"{hybrid_result['hybrid_receipt']['unresolved_pixels']:,}",
        ),
        ("Network / reused", f"{network_bytes:,} / {reused_bytes:,} bytes"),
    ]
    y = 78
    for label, value in rows:
        summary_draw.text((26, y), label, fill=MUTED, font=_font(20))
        summary_draw.text((330, y), value, fill=TEXT, font=_font(20))
        y += 42
    y += 16
    limitations = (
        "Index values and specialist scores are scene-relative analytical "
        "evidence, not probabilities or physical causation. Raw NAIP values "
        "are not automatically surface reflectance. Weak-label agreement is "
        "not independent accuracy. Thresholds may not transfer across scenes."
    )
    for line in _wrap(limitations, 66):
        summary_draw.text((26, y), line, fill=TEXT, font=_font(19))
        y += 31

    panels = (
        ("1 · Source NAIP natural color", natural),
        ("2 · Broad general classification · preserved", general_image),
        ("3 · Final hybrid classification", final_image),
        ("4 · Decision receipt and limitations", summary),
        (
            f"5 · Analytical {selected_index.index_id} · display stretch only",
            index_image,
        ),
        (
            f"6 · {first_specialist.label} · {score_target_direction}; not probability",
            score_image,
        ),
        (
            f"7 · {first_specialist.label} · candidate mask",
            candidate_image,
        ),
        ("8 · Decision state · general / specialist / unresolved", decision_image),
    )
    gap_x = 20
    gap_y = 20
    start_y = 126
    for index, (label, image) in enumerate(panels):
        row_index, column_index = divmod(index, 4)
        panel = _panel(image, label, panel_width, panel_height)
        canvas.paste(
            panel,
            (
                30 + column_index * (panel_width + gap_x),
                start_y + row_index * (panel_height + gap_y),
            ),
        )
    footer_y = 2120
    draw.text(
        (40, footer_y),
        (
            "Analytical rasters are unchanged by publication rendering · "
            "specialist priority and eligible parents are recorded in receipts"
        ),
        fill=MUTED,
        font=_font(18),
    )
    canvas.save(destination, format="PNG", optimize=False, compress_level=9)
    if Image.open(destination).size != (3840, 2160):
        raise RuntimeError("hybrid classification preview did not render at 4K")

    labels = {
        0: "unknown_or_invalid",
        **{
            int(item["code"]): str(item["name"])
            for item in general_result.get("mapping", {}).get(
                "output_classes",
                [],
            )
        },
    }
    for specialist in recipe.classification.specialists.classes:
        labels[specialist.output_code] = specialist.label
    labels[recipe.classification.arbitration.unresolved_code] = (
        "unresolved specialist overlap"
    )
    legend = {
        "schema_version": "fasterraster.hybrid-classification-legend/v1",
        "palette": {
            str(code): {
                "label": labels.get(code, f"class_{code}"),
                "rgb": list(color),
                "hex": "#%02x%02x%02x" % color,
            }
            for code, color in sorted(palette.items())
        },
        "decision_state_palette": {
            str(code): list(color)
            for code, color in sorted(DECISION_COLORS.items())
        },
        "general_classification_preserved": True,
        "arbitration": recipe.classification.arbitration.model_dump(
            mode="json"
        ),
        "index_display": {
            "index_id": selected_index.index_id,
            "definition": next(
                item
                for item in hybrid_result["registry"]["indices"]
                if item["index_id"] == selected_index.index_id
            ),
            "stretch": index_stretch,
        },
        "specialist_score_display": {
            "class_id": first_specialist.class_id,
            "stretch": score_stretch,
            "score_is_probability": False,
            "target_direction": score_target_direction,
        },
        "analytical_rasters_modified": False,
    }
    legend_path = destination.parent / "classification_legend.json"
    _write_json(legend_path, legend)
    receipt = {
        "schema_version": "fasterraster.hybrid-publication-receipt/v1",
        "preview": destination.relative_to(destination.parents[2]).as_posix(),
        "legend": legend_path.relative_to(destination.parents[2]).as_posix(),
        "dimensions": [3840, 2160],
        "panels": [label for label, _ in panels],
        "panel_selection_rule": (
            "recipe display indices in declared order, then first specialist "
            "in declared class order"
        ),
        "source_indices": [selected_index.index_id],
        "index_display_stretches": {
            selected_index.index_id: index_stretch
        },
        "specialist_score_display_stretches": {
            first_specialist.class_id: score_stretch
        },
        "specialist_score_target_direction": {
            first_specialist.class_id: score_target_direction
        },
        "class_arbitration": recipe.classification.arbitration.model_dump(
            mode="json"
        ),
        "general_output": Path(
            paths["general_classification"]
        ).name,
        "final_hybrid_output": Path(
            paths["final_hybrid_classification"]
        ).name,
        "analytical_rasters_modified": False,
        "network_bytes": network_bytes,
        "reused_bytes": reused_bytes,
        "analysis_aoi_mask_applied": analysis_aoi_epsg_4326 is not None,
    }
    return destination, receipt


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if current and len(candidate) > width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines
