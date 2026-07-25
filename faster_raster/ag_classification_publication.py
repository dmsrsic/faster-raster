from __future__ import annotations

import json
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
from rasterio.enums import Resampling
from rasterio.features import sieve

from faster_raster.ag_classification import (
    AGREEMENT_STATE_LABELS,
    FEATURE_EPSILON,
)
from faster_raster.ag_classification_contracts import (
    CLASSIFICATION_SCIENTIFIC_CLAIM,
    CDL_SURFACE_SUPERCLASSES,
)
from faster_raster.ag_recipes import AgriculturalRecipeV3


CLASSIFICATION_PALETTE = {
    0: (93, 103, 112),
    1: (230, 171, 2),
    2: (166, 118, 29),
    3: (102, 166, 30),
    4: (231, 41, 138),
    5: (27, 158, 119),
    6: (31, 120, 180),
}
BACKGROUND = (15, 23, 30)
CARD = (24, 35, 44)
TEXT = (236, 242, 244)
MUTED = (166, 181, 190)
ACCENT = (80, 205, 196)
NDVI_NEGATIVE = (132, 75, 42)
NDVI_ZERO = (235, 234, 218)
NDVI_POSITIVE = (26, 135, 77)
DISAGREEMENT_OUTLINE_WIDTH_PIXELS = 1
DISAGREEMENT_HALO_WIDTH_PIXELS = 1


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _stretch_rgb(values: np.ndarray, valid: np.ndarray) -> np.ndarray:
    result = np.zeros(values.shape, dtype=np.uint8)
    for index in range(3):
        band = values[index].astype(np.float32)
        sample = band[valid]
        if sample.size:
            low, high = np.percentile(sample, (2.0, 98.0))
            if high <= low:
                low, high = float(sample.min()), float(sample.max())
            if high > low:
                band = (band - low) * (255.0 / (high - low))
        result[index] = np.clip(band, 0, 255).astype(np.uint8)
    result[:, ~valid] = 0
    return np.moveaxis(result, 0, -1)


def interpret_naip_date_evidence(raw: Any) -> dict[str, Any]:
    result = {
        "raw_naip_evidence": raw,
        "interpreted_naip_date_utc": None,
        "naip_evidence_interpretation": "unparsed",
    }
    if raw is None or raw == []:
        result["naip_evidence_interpretation"] = "no_evidence"
        return result
    values = list(raw) if isinstance(raw, (list, tuple)) else [raw]
    interpreted: list[str] = []
    units: set[str] = set()
    for value in values:
        if isinstance(value, bool):
            return result
        if isinstance(value, (int, np.integer)):
            text = str(int(value))
        elif isinstance(value, str):
            text = value.strip()
        else:
            return result
        if not text.isdigit() or len(text) not in {10, 13}:
            return result
        unit = "milliseconds" if len(text) == 13 else "seconds"
        seconds = int(text) / (1000 if unit == "milliseconds" else 1)
        try:
            interpreted.append(
                datetime.fromtimestamp(seconds, tz=timezone.utc).date().isoformat()
            )
        except (OverflowError, OSError, ValueError):
            return result
        units.add(unit)
    unique_dates = sorted(set(interpreted))
    if len(unique_dates) != 1:
        result["naip_evidence_interpretation"] = "ambiguous_multiple_dates"
        return result
    result["interpreted_naip_date_utc"] = unique_dates[0]
    result["naip_evidence_interpretation"] = (
        f"parsed_unix_epoch_{next(iter(units))}"
        if len(units) == 1
        else "parsed_equivalent_unix_epochs"
    )
    return result


def _render_numeric_ndvi(
    ndvi: np.ndarray,
    valid: np.ndarray,
) -> tuple[Image.Image, dict[str, Any]]:
    finite = valid & np.isfinite(ndvi)
    sample = ndvi[finite]
    if sample.size:
        percentile_low, percentile_high = np.percentile(sample, (2.0, 98.0))
    else:
        percentile_low, percentile_high = -1.0, 1.0
    stretch_low = min(float(percentile_low), -0.05)
    stretch_high = max(float(percentile_high), 0.05)
    negative_weight = np.clip(
        -ndvi / max(abs(stretch_low), float(FEATURE_EPSILON)),
        0.0,
        1.0,
    )
    positive_weight = np.clip(
        ndvi / max(stretch_high, float(FEATURE_EPSILON)),
        0.0,
        1.0,
    )
    rgb = np.empty((*ndvi.shape, 3), dtype=np.float32)
    rgb[:] = NDVI_ZERO
    negative = ndvi < 0
    positive = ndvi > 0
    for channel in range(3):
        rgb[..., channel][negative] = (
            NDVI_ZERO[channel] * (1.0 - negative_weight[negative])
            + NDVI_NEGATIVE[channel] * negative_weight[negative]
        )
        rgb[..., channel][positive] = (
            NDVI_ZERO[channel] * (1.0 - positive_weight[positive])
            + NDVI_POSITIVE[channel] * positive_weight[positive]
        )
    rgb[~finite] = BACKGROUND
    contract = {
        "method": "deterministic_zero_centered_percentile_stretch",
        "percentiles": [2.0, 98.0],
        "stretch_bounds": [stretch_low, stretch_high],
        "numeric_ndvi_modified": False,
        "palette": {
            "negative": list(NDVI_NEGATIVE),
            "near_zero": list(NDVI_ZERO),
            "positive": list(NDVI_POSITIVE),
        },
    }
    return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB"), contract


def _read_naip(
    path: Path,
    width: int,
    height: int,
) -> tuple[Image.Image, Image.Image, Image.Image, dict[str, Any]]:
    with rasterio.open(path) as source:
        bands = source.read(
            (1, 2, 3, 4),
            out_shape=(4, height, width),
            resampling=Resampling.bilinear,
        )
        masks = (
            source.read_masks(
                (1, 2, 3, 4),
                out_shape=(4, height, width),
                resampling=Resampling.nearest,
            )
            > 0
        )
    valid = np.all(masks, axis=0)
    natural = Image.fromarray(_stretch_rgb(bands[[0, 1, 2]], valid), "RGB")
    cir = Image.fromarray(_stretch_rgb(bands[[3, 0, 1]], valid), "RGB")
    red = bands[0].astype(np.float32) / 255.0
    nir = bands[3].astype(np.float32) / 255.0
    ndvi = np.clip(
        (nir - red) / (nir + red + float(FEATURE_EPSILON)),
        -1.0,
        1.0,
    )
    ndvi_image, ndvi_contract = _render_numeric_ndvi(ndvi, valid)
    return natural, cir, ndvi_image, ndvi_contract


def _read_single(
    path: Path,
    width: int,
    height: int,
    *,
    resampling: Resampling = Resampling.nearest,
) -> np.ndarray:
    with rasterio.open(path) as source:
        return source.read(
            1,
            out_shape=(height, width),
            resampling=resampling,
        )


def _classification_image(values: np.ndarray) -> Image.Image:
    rgb = np.zeros((*values.shape, 3), dtype=np.uint8)
    for code, color in CLASSIFICATION_PALETTE.items():
        rgb[values == code] = color
    return Image.fromarray(rgb, "RGB")


def _classification_overlay(values: np.ndarray, alpha: int = 82) -> Image.Image:
    rgba = np.zeros((*values.shape, 4), dtype=np.uint8)
    for code, color in CLASSIFICATION_PALETTE.items():
        if code == 0:
            continue
        selected = values == code
        rgba[selected, :3] = color
        rgba[selected, 3] = alpha
    return Image.fromarray(rgba, "RGBA")


def _disagreement_outline(
    values: np.ndarray,
    minimum_pixels: int,
    *,
    outline_width: int = DISAGREEMENT_OUTLINE_WIDTH_PIXELS,
    halo_width: int = DISAGREEMENT_HALO_WIDTH_PIXELS,
) -> Image.Image:
    disagreement = np.where(values == 3, 1, 0).astype(np.uint8)
    if minimum_pixels > 1 and np.any(disagreement):
        disagreement = sieve(
            disagreement,
            size=minimum_pixels,
            connectivity=8,
        )
    mask = Image.fromarray(
        disagreement * 255,
        "L",
    )
    outline_outer = mask.filter(
        ImageFilter.MaxFilter(2 * max(1, outline_width) + 1)
    )
    halo_outer = mask.filter(
        ImageFilter.MaxFilter(
            2 * max(1, outline_width + halo_width) + 1
        )
    )
    mask_values = np.asarray(mask, dtype=np.int16)
    outline = np.asarray(outline_outer, dtype=np.int16) - mask_values
    halo = np.asarray(halo_outer, dtype=np.int16) - np.asarray(
        outline_outer,
        dtype=np.int16,
    )
    rgba = np.zeros((values.shape[0], values.shape[1], 4), dtype=np.uint8)
    rgba[halo > 0] = (8, 15, 20, 145)
    rgba[outline > 0] = (255, 255, 255, 215)
    return Image.fromarray(rgba, "RGBA")


def _confidence_agreement_image(
    confidence: np.ndarray,
    agreement: np.ndarray,
) -> Image.Image:
    gray = np.clip(confidence.astype(np.float32) * 2.2 + 20, 0, 255)
    rgb = np.stack((gray * 0.55, gray * 0.72, gray), axis=-1).astype(np.uint8)
    rgb[agreement == 1] = (42, 139, 109)
    rgb[agreement == 2] = (113, 117, 121)
    rgb[agreement == 3] = (220, 78, 64)
    rgb[agreement == 0] = BACKGROUND
    return Image.fromarray(rgb, "RGB")


def _panel(
    image: Image.Image,
    label: str,
    width: int,
    height: int,
    *,
    footer: tuple[tuple[str, tuple[int, int, int]], ...] = (),
) -> Image.Image:
    panel = Image.new("RGB", (width, height), CARD)
    draw = ImageDraw.Draw(panel)
    draw.text((18, 12), label, fill=TEXT, font=_font(25, bold=True))
    fitted = ImageOps.fit(
        image,
        (width - 4, height - 54),
        method=Image.Resampling.LANCZOS,
    )
    panel.paste(fitted, (2, 52))
    if footer:
        footer_top = height - 34
        draw.rectangle((2, footer_top, width - 2, height - 2), fill=(13, 22, 29))
        cursor = 12
        footer_font = _font(14)
        for footer_label, color in footer:
            draw.rectangle(
                (cursor, footer_top + 9, cursor + 13, footer_top + 22),
                fill=color,
                outline=(225, 231, 233),
            )
            cursor += 19
            draw.text(
                (cursor, footer_top + 7),
                footer_label,
                fill=TEXT,
                font=footer_font,
            )
            bounds = draw.textbbox((cursor, footer_top + 7), footer_label, font=footer_font)
            cursor = bounds[2] + 14
    return panel


def _metric(value: Any) -> str:
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return "n/a"


def render_classification_audit(
    destination: Path,
    *,
    naip_path: Path,
    classification_result: dict[str, Any],
    recipe: AgriculturalRecipeV3,
    year: int,
    acquisition_evidence: dict[str, Any],
    network_bytes: int,
    reused_bytes: int,
) -> tuple[Path, dict[str, Any]]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    width, height = 3840, 2160
    canvas = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    title_font = _font(42, bold=True)
    label_font = _font(25, bold=True)
    body_font = _font(22)
    small_font = _font(19)
    draw.text(
        (40, 22),
        "NAIP–CDL Weak-Supervised Surface Classification Audit",
        fill=TEXT,
        font=title_font,
    )

    main_width, main_height = 2700, 1370
    natural, _, _, _ = _read_naip(
        naip_path,
        main_width,
        main_height,
    )
    paths = classification_result["paths"]
    predicted = _read_single(
        paths["classification"],
        main_width,
        main_height,
    )
    agreement = _read_single(
        paths["agreement"],
        main_width,
        main_height,
    )
    main = natural.convert("RGBA")
    main.alpha_composite(_classification_overlay(predicted))
    main.alpha_composite(
        _disagreement_outline(
            agreement,
            recipe.classification.sieve_minimum_pixels,
        )
    )
    canvas.paste(main.convert("RGB"), (40, 90))
    draw.rounded_rectangle(
        (40, 90, 2740, 1460),
        radius=8,
        outline=(80, 101, 114),
        width=3,
    )
    draw.rectangle((54, 105, 1005, 151), fill=(13, 22, 29))
    draw.text(
        (68, 112),
        "Natural-color NAIP + muted prediction · thin outline = high-confidence disagreement",
        fill=TEXT,
        font=small_font,
    )

    card_box = (2780, 90, 3800, 1460)
    draw.rounded_rectangle(card_box, radius=12, fill=CARD)
    x, y = 2812, 116
    draw.text((x, y), "Classification receipt", fill=ACCENT, font=label_font)
    y += 48
    metrics = classification_result["metrics"]
    training = classification_result["training_receipt"]
    agreement_summary = classification_result["agreement"]
    source = classification_result["source_validation"]
    classified = sum(
        int(
            classification_result["inference"]["post_sieve_class_counts"][
                str(code)
            ]
        )
        for code in range(1, 7)
    )
    valid = max(1, int(classification_result["inference"]["valid_source_pixels"]))
    uncertain = int(
        classification_result["inference"]["post_sieve_class_counts"]["0"]
    )
    interpreted_evidence = interpret_naip_date_evidence(
        acquisition_evidence.get("acquisition_date_evidence")
    )
    if interpreted_evidence["interpreted_naip_date_utc"]:
        evidence_display = (
            f"{interpreted_evidence['interpreted_naip_date_utc']} UTC"
        )
    elif interpreted_evidence["naip_evidence_interpretation"] == "no_evidence":
        evidence_display = "none · no evidence"
    else:
        evidence_display = (
            f"{interpreted_evidence['raw_naip_evidence']} · unparsed"
        )
    rows = [
        ("Recipe", recipe.recipe_id),
        ("Title", recipe.title),
        (
            "Mapping",
            f"{recipe.classification.mapping_id} v"
            f"{classification_result['mapping']['contract_version']} "
            f"{classification_result['mapping_sha256'][:12]}",
        ),
        ("Source year", str(year)),
        (
            "NAIP evidence",
            evidence_display,
        ),
        ("Four-band verified", f"{source['band_count']} × {source['dtype']} · COG"),
        ("Raw pixel size", " × ".join(f"{value:g} m" for value in source["pixel_size"])),
        (
            "Classification grid",
            f"{source['width']} × {source['height']} raw-NAIP pixels",
        ),
        ("Classifier", recipe.classification.backend),
        ("Seed", str(recipe.classification.random_seed)),
        (
            "Train / holdout",
            f"{training['train_sample_total']:,} / {training['holdout_sample_total']:,}",
        ),
        ("Weak-label agreement", _metric(metrics["overall_agreement"])),
        ("Macro F1", _metric(metrics["macro_f1"])),
        ("Cohen’s kappa", _metric(metrics["cohen_kappa"])),
        ("Confidence threshold", f"{recipe.classification.confidence_threshold:.2f}"),
        ("Classified coverage", f"{classified / valid:.1%}"),
        ("Uncertain fraction", f"{uncertain / valid:.1%}"),
        (
            "High-conf. disagreement",
            f"{agreement_summary['high_confidence_disagreement_fraction']:.1%}",
        ),
        ("Network / reused", f"{network_bytes:,} / {reused_bytes:,} bytes"),
    ]
    for label, value in rows:
        draw.text((x, y), label, fill=MUTED, font=small_font)
        value_lines = textwrap.wrap(str(value), width=42) or [""]
        draw.text((x + 300, y), value_lines[0], fill=TEXT, font=small_font)
        y += 31
        for continuation in value_lines[1:]:
            draw.text((x + 300, y), continuation, fill=TEXT, font=small_font)
            y += 27
    y += 12
    draw.text((x, y), "Scientific limitation", fill=ACCENT, font=label_font)
    y += 38
    limitation = (
        "Spatial holdout metrics measure agreement with same-year CDL weak "
        "labels. They are not independent ground-truth accuracy and this "
        "single-date product is not authoritative land cover, parcel evidence, "
        "irrigation status, yield, or change."
    )
    for line in textwrap.wrap(limitation, width=69):
        draw.text((x, y), line, fill=TEXT, font=small_font)
        y += 27

    panel_y = 1510
    gap = 16
    panel_width = (width - 80 - 4 * gap) // 5
    panel_height = 610
    panel_natural, panel_cir, panel_ndvi, panel_ndvi_contract = _read_naip(
        naip_path,
        panel_width,
        panel_height - 54,
    )
    panel_predicted = _read_single(
        paths["classification"],
        panel_width,
        panel_height - 54,
    )
    panel_confidence = _read_single(
        paths["confidence"],
        panel_width,
        panel_height - 54,
        resampling=Resampling.bilinear,
    )
    panel_agreement = _read_single(
        paths["agreement"],
        panel_width,
        panel_height - 54,
    )
    lower = (
        ("1 · NAIP natural color", panel_natural, ()),
        ("2 · NAIP color infrared", panel_cir, ()),
        (
            "3 · Numeric NDVI · robust 2–98%",
            panel_ndvi,
            (
                ("<0", NDVI_NEGATIVE),
                ("≈0", NDVI_ZERO),
                (">0 vegetation", NDVI_POSITIVE),
            ),
        ),
        (
            "4 · Predicted surface classes · raw",
            _classification_image(panel_predicted),
            (
                ("0 ?", CLASSIFICATION_PALETTE[0]),
                ("1 crop", CLASSIFICATION_PALETTE[1]),
                ("2 fallow", CLASSIFICATION_PALETTE[2]),
                ("3 dev-L", CLASSIFICATION_PALETTE[3]),
                ("4 dev-H", CLASSIFICATION_PALETTE[4]),
                ("5 veg", CLASSIFICATION_PALETTE[5]),
                ("6 water", CLASSIFICATION_PALETTE[6]),
            ),
        ),
        (
            "5 · Confidence / CDL audit",
            _confidence_agreement_image(panel_confidence, panel_agreement),
            (),
        ),
    )
    for index, (label, image, footer) in enumerate(lower):
        panel = _panel(
            image,
            label,
            panel_width,
            panel_height,
            footer=footer,
        )
        canvas.paste(panel, (40 + index * (panel_width + gap), panel_y))

    canvas.save(destination, format="PNG", optimize=False, compress_level=9)
    if Image.open(destination).size != (3840, 2160):
        raise RuntimeError("classification audit preview did not render at 4K")
    legend = {
        "schema_version": "fasterraster.classification-legend/v1",
        "mapping_id": CDL_SURFACE_SUPERCLASSES.mapping_id,
        "mapping_sha256": CDL_SURFACE_SUPERCLASSES.sha256,
        "palette": {
            str(code): {
                "label": CDL_SURFACE_SUPERCLASSES.class_labels[code],
                "rgb": list(color),
                "hex": "#%02x%02x%02x" % color,
            }
            for code, color in CLASSIFICATION_PALETTE.items()
        },
        "prediction_overlay_opacity": 82 / 255,
        "visual_minimum_region_pixels": (
            recipe.classification.sieve_minimum_pixels
        ),
        "visual_minimum_mapping_unit_pixels": (
            recipe.classification.sieve_minimum_pixels
        ),
        "disagreement_rendering_mode": (
            "display_only_outer_outline_with_dark_halo"
        ),
        "outline_width_pixels": DISAGREEMENT_OUTLINE_WIDTH_PIXELS,
        "halo_width_pixels": DISAGREEMENT_HALO_WIDTH_PIXELS,
        "analytical_rasters_modified": False,
        "main_outline": (
            "high-confidence disagreement only; a display-only minimum-region "
            "filter precedes a thin outer outline and subtle dark halo"
        ),
        "ndvi_display": panel_ndvi_contract,
        "predicted_class_display": {
            "source": "raw analytical prediction codes",
            "resampling": "nearest",
            "display_generalization_applied": False,
            "analytical_raster_modified": False,
        },
        "agreement_states": {
            str(code): label for code, label in AGREEMENT_STATE_LABELS.items()
        },
    }
    legend_path = destination.parent / "classification_legend.json"
    _write_json(legend_path, legend)
    publication_receipt = {
        "preview": destination.relative_to(destination.parents[2]).as_posix(),
        "legend": legend_path.relative_to(destination.parents[2]).as_posix(),
        "dimensions": [3840, 2160],
        "panels": [item[0] for item in lower],
        "scientific_claim": CLASSIFICATION_SCIENTIFIC_CLAIM,
        "legacy_universal_cdl_boundary_overlay_used": False,
        "palette": legend["palette"],
        "disagreement_rendering_mode": (
            "display_only_outer_outline_with_dark_halo"
        ),
        "visual_minimum_region_pixels": (
            recipe.classification.sieve_minimum_pixels
        ),
        "visual_minimum_mapping_unit_pixels": (
            recipe.classification.sieve_minimum_pixels
        ),
        "outline_width_pixels": DISAGREEMENT_OUTLINE_WIDTH_PIXELS,
        "halo_width_pixels": DISAGREEMENT_HALO_WIDTH_PIXELS,
        "analytical_rasters_modified": False,
        "ndvi_display": panel_ndvi_contract,
        "predicted_class_display": legend["predicted_class_display"],
        "confidence_threshold": recipe.classification.confidence_threshold,
        **interpreted_evidence,
    }
    return destination, publication_receipt
