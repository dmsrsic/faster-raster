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
from faster_raster.ag_classification import require_confidence_provenance
from faster_raster.ag_recipes import AgriculturalRecipeV4
from faster_raster.preview_templates import require_audit_evidence


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
    confidence_provenance = require_confidence_provenance(
        general_result.get("confidence_provenance"),
        uncertainty_reported=True,
    )
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
    display_labels = {
        0: "unknown / uncertain",
        **{
            int(item["code"]): str(item["name"])
            for item in general_result.get("mapping", {}).get(
                "output_classes",
                [],
            )
        },
    }
    for specialist in recipe.classification.specialists.classes:
        display_labels[specialist.output_code] = specialist.label
    display_labels[
        recipe.classification.arbitration.unresolved_code
    ] = "unresolved specialist overlap"
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
        (
            "Confidence threshold",
            f"{confidence_provenance['confidence_threshold']:.2f} "
            f"({confidence_provenance['threshold_source']})",
        ),
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
    y += 8
    summary_draw.text(
        (26, y),
        "Broad + specialist color legend",
        fill=(80, 205, 196),
        font=_font(22, bold=True),
    )
    y += 32
    for index, (code, label) in enumerate(
        sorted(display_labels.items())
    ):
        column = index % 2
        row = index // 2
        legend_x = 26 + column * 445
        legend_y = y + row * 28
        summary_draw.rectangle(
            (
                legend_x,
                legend_y + 3,
                legend_x + 16,
                legend_y + 19,
            ),
            fill=palette[code],
            outline=(225, 231, 233),
        )
        summary_draw.text(
            (legend_x + 24, legend_y),
            f"{code}: {label}",
            fill=TEXT,
            font=_font(18),
        )
    y += ((len(display_labels) + 1) // 2) * 28 + 4
    summary_draw.text(
        (26, y),
        (
            "Decision states: gray retains the broad class; teal applies a "
            "specialist; white marks unresolved overlap."
        ),
        fill=TEXT,
        font=_font(18),
    )

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
            f"6 · {first_specialist.label} score · not probability",
            score_image,
        ),
        (
            f"7 · {first_specialist.label} · candidate mask",
            candidate_image,
        ),
        ("8 · Decision state · general / specialist / unresolved", decision_image),
    )
    provenance_footer = (
        "Analytical rasters are unchanged by rendering; broad and specialist "
        "colors, threshold provenance, and arbitration are receipt-bound."
    )
    audit_contract = require_audit_evidence(
        "ag_hybrid_classification_audit_v1",
        panel_titles=[label for label, _ in panels],
        legends_present={
            "broad_classes",
            "specialist_classes",
            "decision_states",
        },
        explanations_present={
            "unknown_uncertain",
            "confidence_threshold",
            "retained_general_vs_specialist",
        },
        class_codes=display_labels,
        supported_class_codes=palette,
        confidence_provenance=confidence_provenance,
        provenance_footer=provenance_footer,
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
        provenance_footer,
        fill=MUTED,
        font=_font(18),
    )
    canvas.save(destination, format="PNG", optimize=False, compress_level=9)
    if Image.open(destination).size != (3840, 2160):
        raise RuntimeError("hybrid classification preview did not render at 4K")
    derivative_contract = audit_contract[
        "documentation_derivative"
    ]
    documentation_destination = destination.with_name(
        destination.stem.replace("_4k", "") + "_docs.png"
    )
    canvas.resize(
        (
            int(derivative_contract["width"]),
            int(derivative_contract["height"]),
        ),
        Image.Resampling.LANCZOS,
    ).save(
        documentation_destination,
        format="PNG",
        optimize=False,
        compress_level=9,
    )

    labels = display_labels
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
        "preview_template_id": audit_contract["template_id"],
        "preview_template_schema_version": audit_contract[
            "template_schema_version"
        ],
        "preview_template_contract_sha256": audit_contract[
            "template_sha256"
        ],
        "minimum_font_size": audit_contract["minimum_font_size"],
        "provenance_footer": provenance_footer,
    }
    legend_path = destination.parent / "classification_legend.json"
    _write_json(legend_path, legend)
    receipt = {
        "schema_version": "fasterraster.hybrid-publication-receipt/v1",
        "preview": destination.relative_to(destination.parents[2]).as_posix(),
        "legend": legend_path.relative_to(destination.parents[2]).as_posix(),
        "dimensions": [3840, 2160],
        "documentation_derivative": (
            documentation_destination.relative_to(
                destination.parents[2]
            ).as_posix()
        ),
        "documentation_dimensions": [
            int(derivative_contract["width"]),
            int(derivative_contract["height"]),
        ],
        "preview_template_id": audit_contract["template_id"],
        "preview_template_schema_version": audit_contract[
            "template_schema_version"
        ],
        "preview_template_contract_sha256": audit_contract[
            "template_sha256"
        ],
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
        **confidence_provenance,
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
