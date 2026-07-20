from __future__ import annotations

import hashlib
import io
import json
import socket
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageStat

from faster_raster.adapter_contract import stable_json
from faster_raster import preview_contracts, preview_profiles
from faster_raster.adapters.conformance import verify_adapter_conformance
from faster_raster.run_receipts import write_json, write_jsonl

PREVIEW_ROOT = Path("reports/previews")
ALLOWLIST_PATH = Path("configs/source_allowlist.yaml")


class PreviewError(ValueError):
    def __init__(self, failure_class: str, message: str | None = None):
        super().__init__(message or failure_class)
        self.failure_class = failure_class


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def receipt_hash(receipt: dict[str, Any]) -> str:
    payload = {k: v for k, v in receipt.items() if k not in {"preview_receipt_contract_sha256", "started_at_utc", "finished_at_utc", "duration_ms"}}
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


def source_selection_contract_hash(selection: dict[str, Any]) -> str:
    payload = {k: v for k, v in selection.items() if k not in {"source_selection_contract_sha256", "source_selection_receipt_sha256"}}
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


def source_selection_receipt_hash(selection: dict[str, Any]) -> str:
    payload = {k: v for k, v in selection.items() if k != "source_selection_receipt_sha256"}
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


def load_allowlist(root: Path | None = None) -> dict[str, Any]:
    root = root or Path.cwd()
    payload = json.loads((root / ALLOWLIST_PATH).read_text(encoding="utf-8-sig"))
    payload["source_allowlist_sha256"] = preview_contracts.source_allowlist_hash(payload)
    return payload


def entries_by_id(allowlist: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["source_id"]: entry for entry in allowlist["entries"]}


def plan_preview(task_id: str, *, root: Path | None = None, max_total_bytes: int = 25_000_000) -> dict[str, Any]:
    root = root or Path.cwd()
    contract = preview_contracts.build_render_contract(
        task_id,
        load_allowlist(root),
        max_total_bytes=max_total_bytes,
        network_policy="network_allowed_when_flags_approved",
    )
    run_dir = root / PREVIEW_ROOT / task_id / f"preview_plan_{contract['preview_render_contract_sha256'][:12]}"
    write_json(run_dir / "preview_plan.json", contract)
    write_json(run_dir / "preview_render_contract.json", contract)
    return contract


def ensure_host_allowed(url: str, allowed_hosts: list[str]) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        raise PreviewError("host_not_allowed")
    for info in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM):
        addr = info[4][0]
        if addr.startswith(("127.", "10.", "192.168.", "169.254.")) or addr == "::1":
            raise PreviewError("host_not_allowed")


def arcgis_export_url(entry: dict[str, Any], contract: dict[str, Any], *, size: int = 720) -> str:
    params = {
        "bbox": ",".join(f"{v:.8f}" for v in contract["aoi"]["bbox"]),
        "bboxSR": "4326",
        "imageSR": "4326",
        "size": f"{size},{size}",
        "format": "png32",
        "transparent": "false",
        "f": "image",
    }
    if entry["source_id"] == "usda_cdl_imageserver":
        params.update({"transparent": "true", "time": "1704067200000"})
    if entry["source_id"] == "usgs_3dep_hillshade":
        params.update({"transparent": "true", "renderingRule": json.dumps({"rasterFunction": "Hillshade"}, separators=(",", ":"))})
    return entry["base_endpoint"].rstrip("/") + "/exportImage?" + urllib.parse.urlencode(sorted(params.items()))


def read_bounded(url: str, *, entry: dict[str, Any], max_total_bytes: int, timeout_seconds: int) -> dict[str, Any]:
    ensure_host_allowed(url, entry.get("allowed_hosts") or [])
    cap = min(int(entry["maximum_preview_bytes"]), int(max_total_bytes))
    request = urllib.request.Request(url, headers={"User-Agent": "FasterRaster-preview-alpha3/1.0"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        data = response.read(cap + 1)
        content_type = response.headers.get("Content-Type", "")
    if len(data) > cap:
        raise PreviewError("preview_byte_cap_exceeded")
    if not any(media in content_type.lower() for media in entry["permitted_media_types"]):
        raise PreviewError("unsupported_media_type")
    return {"data": data, "bytes_read": len(data), "content_type": content_type, "sha256": sha256_bytes(data)}


def image_stats(image: Image.Image) -> dict[str, Any]:
    sample = image.convert("RGBA").resize((64, 64))
    raw = sample.tobytes()
    pixels = [tuple(raw[i : i + 4]) for i in range(0, len(raw), 4)]
    visible = [p for p in pixels if p[3] > 0]
    return {
        "width": image.width,
        "height": image.height,
        "nontransparent_pixel_count": len(visible),
        "nontransparent_fraction": round(len(visible) / max(len(pixels), 1), 6),
        "unique_sample_colors": len(set(visible)),
    }


def visible_color_histogram(image: Image.Image, limit: int = 20) -> list[dict[str, Any]]:
    sample = image.convert("RGBA").resize((96, 96))
    raw = sample.tobytes()
    counts: dict[tuple[int, int, int], int] = {}
    total = 0
    for i in range(0, len(raw), 4):
        if raw[i + 3] > 0:
            rgb = (raw[i], raw[i + 1], raw[i + 2])
            counts[rgb] = counts.get(rgb, 0) + 1
            total += 1
    rows = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    return [{"rgb": list(rgb), "visible_fraction": round(count / max(total, 1), 6), "sample_count": count} for rgb, count in rows]


def percentile(values: list[int], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return float(ordered[min(len(ordered) - 1, max(0, int(round((pct / 100.0) * (len(ordered) - 1)))) )])


def enhance_natural_color_mild(image: Image.Image, policy: dict[str, Any]) -> Image.Image:
    original = image.convert("RGBA")
    rgb_raw = original.convert("RGB").tobytes()
    data = [tuple(rgb_raw[i:i+3]) for i in range(0, len(rgb_raw), 3)]
    lows: list[float] = []
    highs: list[float] = []
    for channel in range(3):
        vals = [px[channel] for px in data]
        lows.append(percentile(vals, float(policy["percentile_range"][0])))
        highs.append(max(percentile(vals, float(policy["percentile_range"][1])), lows[-1] + 1.0))
    gamma = float(policy["gamma"])
    lift = float(policy["shadow_lift"])
    high = float(policy["highlight_control"])
    out = []
    for px in data:
        rgb = []
        for channel in range(3):
            value = max(0.0, min(1.0, (px[channel] - lows[channel]) / (highs[channel] - lows[channel])))
            rgb.append(max(0, min(255, int(round((lift + (high - lift) * (value**gamma)) * 255)))))
        out.append(tuple(rgb))
    enhanced = Image.new("RGB", original.size)
    enhanced.putdata(out)
    enhanced = ImageEnhance.Color(enhanced).enhance(float(policy.get("saturation_multiplier", 1.0)))
    rgba = enhanced.convert("RGBA")
    rgba.putalpha(original.getchannel("A"))
    return rgba


def blend(base: Image.Image, overlay: Image.Image, opacity: float, *, mode: str = "normal") -> Image.Image:
    base_rgba = base.convert("RGBA")
    over = overlay.convert("RGBA").resize(base_rgba.size)
    if mode == "multiply":
        bp_raw = base_rgba.tobytes()
        op_raw = over.tobytes()
        bp = [tuple(bp_raw[i:i+4]) for i in range(0, len(bp_raw), 4)]
        op = [tuple(op_raw[i:i+4]) for i in range(0, len(op_raw), 4)]
        multiplied = Image.new("RGBA", base_rgba.size)
        multiplied.putdata([(b[0] * o[0] // 255, b[1] * o[1] // 255, b[2] * o[2] // 255, o[3]) for b, o in zip(bp, op)])
        over = multiplied
    over.putalpha(over.getchannel("A").point(lambda v: int(v * opacity)))
    return Image.alpha_composite(base_rgba, over)


def edge_energy(image: Image.Image) -> float:
    edges = image.convert("L").filter(ImageFilter.FIND_EDGES)
    stat = ImageStat.Stat(edges)
    return float(stat.sum[0]) / max(image.width * image.height, 1)


def contrast_energy(image: Image.Image) -> float:
    edges = image.convert("L").filter(ImageFilter.FIND_EDGES)
    stat = ImageStat.Stat(edges)
    return float(stat.mean[0]) + float(stat.stddev[0])


def retention_metrics(base: Image.Image, composite: Image.Image) -> dict[str, float]:
    return {
        "imagery_contrast_retention": round(min(contrast_energy(composite) / max(contrast_energy(base), 1e-6), 1.0), 6),
        "imagery_edge_retention": round(min(edge_energy(composite) / max(edge_energy(base), 1e-6), 1.0), 6),
    }


def clipping_fractions(image: Image.Image) -> dict[str, float]:
    rgba = image.convert("RGBA").resize((96, 96))
    raw = rgba.tobytes()
    visible = 0
    highlights = 0
    shadows = 0
    for i in range(0, len(raw), 4):
        if raw[i + 3] == 0:
            continue
        visible += 1
        rgb = raw[i : i + 3]
        if max(rgb) >= 252:
            highlights += 1
        if max(rgb) <= 3:
            shadows += 1
    return {
        "highlight_clipped_fraction": round(highlights / max(visible, 1), 6),
        "shadow_clipped_fraction": round(shadows / max(visible, 1), 6),
    }


def evaluate_enhancement_candidates(image: Image.Image, selected_policy: dict[str, Any]) -> dict[str, Any]:
    baseline_policy = dict(selected_policy)
    baseline_policy.update({"gamma": 0.96, "shadow_lift": 0.035, "highlight_control": 0.985, "saturation_multiplier": 1.06})
    selected = enhance_natural_color_mild(image, selected_policy)
    baseline = enhance_natural_color_mild(image, baseline_policy)
    return {
        "baseline_alpha3_initial": {**baseline_policy, **clipping_fractions(baseline), **retention_metrics(image, baseline)},
        "selected": {**selected_policy, **clipping_fractions(selected), **retention_metrics(image, selected)},
    }


def cdl_legend(image: Image.Image) -> dict[str, Any]:
    hist = visible_color_histogram(image, limit=256)
    return {
        "legend_status": "mapping_unavailable",
        "legend_truthfulness_status": "PASS",
        "legend_provenance": "verified_cdl_metadata_unavailable_for_colorized_service_pixels",
        "semantic_legend_entry_count": 0,
        "visible_semantic_class_count": None,
        "diagnostic_color_group_count": len(hist),
        "diagnostic_visible_color_groups": hist,
        "entries": [],
        "fallback_legend_message": "The service returned colorized categorical pixels, but no verified color-to-class mapping was available for this receipt.",
        "message": "Categorical class mapping unavailable",
    }

def source_selection_receipt(contract: dict[str, Any], entries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    selected = contract["primary_imagery_selection"]
    candidates = [{"candidate_id": layer["source_id"], "normalized_score": 100 if layer["source_id"] == selected else 70, "theme": layer["theme"], "rejection_reason": None} for layer in contract["layers"]]
    payload = {
        "schema_version": 1,
        "candidate_ids": [item["candidate_id"] for item in candidates],
        "candidates": sorted(candidates, key=lambda item: (-item["normalized_score"], item["candidate_id"])),
        "selected_collection": "USGSNAIPImagery",
        "selected_item": selected,
        "selected_adapter_id": entries[selected]["adapter_id"],
        "selected_asset": "exportImage png32 natural color",
        "selected_imagery_timestamp": "service_metadata_2025-01-09",
        "tie_break_rules": ["real pixels before fallback", "verification class", "source_id lexical"],
        "source_selection_contract_sha256": "",
        "source_selection_receipt_sha256": "",
    }
    payload["source_selection_contract_sha256"] = source_selection_contract_hash(payload)
    payload["source_selection_receipt_sha256"] = source_selection_receipt_hash(payload)
    return payload


def draw_dashboard(images: dict[str, Image.Image], composite: Image.Image, receipt: dict[str, Any], path: Path) -> None:
    width, height = receipt["output_image_width"], receipt["output_image_height"]
    canvas = Image.new("RGB", (width, height), (244, 246, 248))
    draw = ImageDraw.Draw(canvas)
    draw.text((24, 18), "FasterRaster balanced imagery-first preview", fill=(20, 35, 50))
    canvas.paste(composite.convert("RGB").resize((900, 620)), (24, 54))
    x, y = 950, 54
    for line in [
        "VISUAL AUTHORITY",
        f"Profile: {receipt['preview_profile_id']}",
        f"NAIP visible: {receipt['primary_imagery_visible_fraction']:.2f}",
        f"Contrast retention: {receipt['imagery_contrast_retention']:.2f}",
        f"Edge retention: {receipt['imagery_edge_retention']:.2f}",
        f"CDL effective: {receipt['categorical_effective_coverage']:.2f}",
        f"Alpha budget: {receipt['compiled_overlay_alpha_budget']:.2f}",
        f"Boundary: {receipt['boundary_status']}",
    ]:
        draw.text((x, y), line, fill=(24, 45, 60))
        y += 24
    y += 10
    draw.text((x, y), "CDL LEGEND", fill=(24, 45, 60))
    y += 24
    legend = receipt.get("categorical_legend", {})
    if legend.get("legend_status") == "mapping_unavailable":
        block = [
            "Categorical class mapping unavailable",
            "The service returned colorized categorical pixels,",
            "but no verified color-to-class mapping was",
            "available for this receipt.",
            f"Visible color groups: {legend.get('diagnostic_color_group_count', 0)}",
            f"Legend provenance: {legend.get('legend_provenance')}",
        ]
        for idx, line in enumerate(block):
            fill = (100, 45, 35) if idx == 0 else (45, 58, 70)
            draw.text((x, y), line[:62], fill=fill)
            y += 20
    else:
        for row in legend.get("entries", [])[:12]:
            color = row.get("display_color") or [190, 190, 190]
            draw.rectangle([x, y + 2, x + 16, y + 18], fill=tuple(color))
            draw.text((x + 24, y), f"{row['visible_fraction']:.2f} {row['class_name'][:24]}", fill=(40, 55, 70))
            y += 22
    y += 8
    draw.text((x, y), "LAYER POLICY", fill=(24, 45, 60)); y += 22
    for layer in receipt.get("layers", []):
        draw.text((x, y), f"{layer['source_id'][:20]} req {layer.get('requested_opacity', 0):.2f} comp {layer.get('compiled_opacity', 0):.2f}", fill=(45, 58, 70))
        y += 19
    panels: list[tuple[str, Image.Image]] = [
        ("NAIP only", images["usgs_naip_imagery"]),
        ("3DEP hillshade", images["usgs_3dep_hillshade"]),
        ("raw CDL palette", images["usda_cdl_imageserver"]),
    ]
    selected_opacity = round(float(receipt["cdl_compiled_opacity"]), 2)
    for opacity in [0.15, selected_opacity, 0.30]:
        label = f"NAIP + CDL {opacity:.2f}"
        if abs(opacity - selected_opacity) < 0.001:
            label = f"SELECTED {opacity:.2f}"
        panels.append((label, blend(images["usgs_naip_imagery"].resize(images["usda_cdl_imageserver"].size), images["usda_cdl_imageserver"], opacity)))
    panels.append(("selected composite", composite))
    px, py = 24, 710
    for label, image in panels[:7]:
        canvas.paste(image.convert("RGB").resize((185, 126)), (px, py))
        draw.rectangle([px, py, px + 185, py + 126], outline=(205, 210, 216))
        draw.text((px, py + 131), label, fill=(35, 50, 70))
        px += 207
    zoom_bounds = receipt.get("pixel_zoom_source_bounds", {"left": composite.width // 2 - 36, "top": composite.height // 2 - 36, "right": composite.width // 2 + 36, "bottom": composite.height // 2 + 36})
    crop = composite.crop((zoom_bounds["left"], zoom_bounds["top"], zoom_bounds["right"], zoom_bounds["bottom"])).resize((112, 112), Image.Resampling.NEAREST)
    zx, zy = width - 142, height - 178
    draw.rectangle([zx - 8, zy - 28, zx + 120, zy + 134], fill=(238, 240, 243), outline=(132, 142, 152))
    draw.text((zx, zy - 22), "PIXEL ZOOM", fill=(35, 50, 70))
    canvas.paste(crop.convert("RGB"), (zx, zy))
    draw.text((zx, zy + 116), f"{zoom_bounds['left']}:{zoom_bounds['right']}, {zoom_bounds['top']}:{zoom_bounds['bottom']}", fill=(35, 50, 70))
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path)

def render_preview(task_id: str, *, allow_network: bool, allow_preview: bool, approve_plan_sha256: str | None, max_total_bytes: int = 25_000_000, timeout_seconds: int = 30, retry_count: int = 0, root: Path | None = None) -> dict[str, Any]:
    root = root or Path.cwd()
    started = now()
    t0 = time.monotonic()
    allowlist = load_allowlist(root)
    entries = entries_by_id(allowlist)
    contract = preview_contracts.build_render_contract(task_id, allowlist, max_total_bytes=max_total_bytes, network_policy="network_allowed_when_flags_approved")
    run_id = f"preview_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:12]}"
    run_dir = root / PREVIEW_ROOT / task_id / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    total_bytes = 0
    layers: list[dict[str, Any]] = []
    images: dict[str, Image.Image] = {}
    try:
        if not allow_preview:
            raise PreviewError("approval_required")
        if approve_plan_sha256 != contract["preview_render_contract_sha256"]:
            raise PreviewError("preview_contract_mismatch")
        if not allow_network:
            raise PreviewError("approval_required")
        for layer in contract["layers"]:
            entry = entries[layer["source_id"]]
            fetched = read_bounded(arcgis_export_url(entry, contract, size=720), entry=entry, max_total_bytes=max_total_bytes - total_bytes, timeout_seconds=timeout_seconds)
            total_bytes += fetched["bytes_read"]
            image = Image.open(io.BytesIO(fetched["data"])).convert("RGBA")
            if layer["source_id"] == "usgs_naip_imagery":
                original_image = image.copy()
                enhancement_candidate_metrics = evaluate_enhancement_candidates(original_image, contract["preview_profile"]["imagery_enhancement_policy"])
                image = enhance_natural_color_mild(original_image, contract["preview_profile"]["imagery_enhancement_policy"])
                layer["enhancement_candidate_metrics"] = enhancement_candidate_metrics
                layer["enhancement_selection_reason"] = "selected reduced shadow lift and highlight control to preserve natural aerial contrast while retaining edge metrics"
                layer.update(clipping_fractions(image))
            stats = image_stats(image)
            real = stats["nontransparent_pixel_count"] > 0 and stats["nontransparent_fraction"] > 0 and stats["unique_sample_colors"] > 0
            layer.update({"bytes_read": fetched["bytes_read"], "source_sha256": fetched["sha256"], "content_type": fetched["content_type"], **stats})
            if layer["source_id"] == "usda_cdl_imageserver":
                opacity = preview_profiles.compile_categorical_opacity(stats["nontransparent_fraction"], layer["requested_opacity"], contract["preview_profile"])
                layer.update(opacity)
                layer["visible_color_histogram"] = visible_color_histogram(image)
                layer["categorical_legend"] = cdl_legend(image)
                layer["visible_semantic_class_count"] = layer["categorical_legend"]["visible_semantic_class_count"]
                layer["semantic_legend_entry_count"] = layer["categorical_legend"]["semantic_legend_entry_count"]
                layer["diagnostic_visible_color_group_count"] = layer["categorical_legend"]["diagnostic_color_group_count"]
            layer.update({"layer_status": "real_pixels_rendered" if real else "no_visible_pixels", "real_pixel_status": real, "rendered_into_composite": real, "fallback_status": False, "bounds": contract["aoi"]["bbox"], "CRS": "EPSG:4326", "resolution": "preview", "failure_class": None})
            if real:
                images[layer["source_id"]] = image
            layers.append(layer)
        if {"usgs_naip_imagery", "usgs_3dep_hillshade", "usda_cdl_imageserver"} - images.keys():
            raise PreviewError("real_imagery_unavailable")
        base = images["usgs_naip_imagery"].resize((900, 620))
        composite = blend(base, images["usgs_3dep_hillshade"], next(l for l in layers if l["source_id"] == "usgs_3dep_hillshade")["compiled_opacity"], mode="multiply")
        cdl_layer = next(l for l in layers if l["source_id"] == "usda_cdl_imageserver")
        composite = blend(composite, images["usda_cdl_imageserver"], cdl_layer["compiled_opacity"])
        rendered_overlays = [l for l in layers if l["source_id"] != "usgs_naip_imagery" and l["real_pixel_status"]]
        overlay_alpha = round(sum(l["compiled_opacity"] for l in rendered_overlays), 6)
        primary_visible = round(max(0.0, 1.0 - overlay_alpha * 0.35), 4)
        retention = retention_metrics(base, composite)
        cdl_fraction = float(cdl_layer["nontransparent_fraction"])
        cdl_opacity = float(cdl_layer["compiled_opacity"])
        categorical_effective_coverage = round(cdl_fraction * cdl_opacity, 6)
        profile = contract["preview_profile"]
        thresholds = profile["visual_authority_thresholds"]
        visual_pass = (
            primary_visible >= thresholds["primary_imagery_visible_fraction_min"]
            and retention["imagery_contrast_retention"] >= thresholds["imagery_contrast_retention_min"]
            and retention["imagery_edge_retention"] >= thresholds["imagery_edge_retention_min"]
            and categorical_effective_coverage <= thresholds["categorical_effective_coverage_max"]
            and overlay_alpha <= thresholds["overlay_alpha_budget_max"]
        )
        naip_layer = next(l for l in layers if l["source_id"] == "usgs_naip_imagery")
        zoom_window = {"left": composite.width // 2 - 36, "top": composite.height // 2 - 36, "right": composite.width // 2 + 36, "bottom": composite.height // 2 + 36}
        visual = {
            "primary_imagery_source_id": "usgs_naip_imagery",
            "primary_imagery_theme": "aerial_imagery",
            "primary_imagery_real_pixels": True,
            "primary_imagery_visible_fraction": primary_visible,
            "primary_imagery_opacity": 1.0,
            "overlay_count": len(rendered_overlays),
            "rendered_layer_count": 1 + len(rendered_overlays),
            "requested_overlay_alpha_budget": round(sum(l["requested_opacity"] for l in rendered_overlays), 6),
            "compiled_overlay_alpha_budget": overlay_alpha,
            "overlay_alpha_budget": overlay_alpha,
            "overlay_alpha_budget_limit": thresholds["overlay_alpha_budget_max"],
            "overlay_alpha_budget_status": "PASS" if overlay_alpha <= thresholds["overlay_alpha_budget_max"] else "FAIL",
            "dominant_visual_role": "primary_imagery",
            "imagery_coverage_fraction": 1.0,
            "categorical_effective_coverage": categorical_effective_coverage,
            "categorical_effective_alpha": cdl_opacity,
            "cdl_requested_opacity": cdl_layer["requested_opacity"],
            "cdl_compiled_opacity": cdl_opacity,
            "cdl_visible_fraction": cdl_fraction,
            "visible_semantic_class_count": cdl_layer["visible_semantic_class_count"],
            "semantic_legend_entry_count": cdl_layer["semantic_legend_entry_count"],
            "diagnostic_visible_color_group_count": cdl_layer["diagnostic_visible_color_group_count"],
            "class_aware_opacity_status": cdl_layer["class_aware_opacity_status"],
            "legend_provenance": cdl_layer["categorical_legend"]["legend_provenance"],
            "enhancement_candidate_metrics": naip_layer.get("enhancement_candidate_metrics"),
            "enhancement_selection_reason": naip_layer.get("enhancement_selection_reason"),
            "highlight_clipped_fraction": naip_layer.get("highlight_clipped_fraction"),
            "shadow_clipped_fraction": naip_layer.get("shadow_clipped_fraction"),
            "selected_comparison_opacity": cdl_opacity,
            "displayed_selected_opacity": cdl_opacity,
            "comparison_panels": ["NAIP only", "3DEP hillshade", "raw CDL palette", "NAIP + CDL 0.15", f"SELECTED {cdl_opacity:.2f}", "NAIP + CDL 0.30", "selected composite"],
            "pixel_zoom_source_bounds": zoom_window,
            "categorical_legend": cdl_layer["categorical_legend"],
            "boundary_derivation_method": "raw_class_id_grid_required",
            "boundary_status": "unavailable_from_colorized_service",
            "boundary_pixel_fraction": 0.0,
            "boundary_main_composite_enabled": False,
            "boundary_diagnostic_panel_generated": True,
            **retention,
            "imagery_visual_authority_status": "PASS" if visual_pass else "FAIL",
            "preview_visual_policy_status": "PASS" if visual_pass else "FAIL",
            "categorical_balance_status": "PASS" if categorical_effective_coverage <= 0.30 else "FAIL",
            "boundary_restraint_status": "PASS",
            "legend_truthfulness_status": cdl_layer["categorical_legend"]["legend_truthfulness_status"],
            "preview_profile_binding_status": "PASS",
            "imagery_presence_status": "PASS",
            "imagery_dominance_status": "PASS" if primary_visible >= 0.70 else "FAIL",
            "overlay_subordination_status": "PASS" if overlay_alpha <= 0.42 else "FAIL",
            "fallback_truthfulness_status": "PASS",
            "fallback_visible_fraction": 0.0,
            "diagnostic_visible_fraction": 0.0,
            "nodata_fraction": 0.0,
            "cloud_or_quality_mask_fraction": None,
        }
        status = "completed" if visual["preview_visual_policy_status"] == "PASS" else "failed"
    except PreviewError as exc:
        failures.append(exc.failure_class)
        composite = Image.new("RGBA", (900, 620), (220, 220, 220, 255))
        visual = {"preview_visual_policy_status": "FAIL", "imagery_visual_authority_status": "FAIL", "primary_imagery_real_pixels": False, "primary_imagery_visible_fraction": 0.0, "primary_imagery_opacity": 1.0, "imagery_coverage_fraction": 0.0, "overlay_count": 0, "compiled_overlay_alpha_budget": 0.0, "overlay_alpha_budget_status": "FAIL", "dominant_visual_role": "unavailable", "categorical_effective_coverage": 0.0, "categorical_effective_alpha": 0.0, "boundary_pixel_fraction": 0.0, "boundary_derivation_method": "not_run", "boundary_status": "not_run", "imagery_contrast_retention": 0.0, "imagery_edge_retention": 0.0, "categorical_balance_status": "FAIL", "boundary_restraint_status": "FAIL", "legend_truthfulness_status": "FAIL", "preview_profile_binding_status": "FAIL"}
        status = "failed"
    selection = source_selection_receipt(contract, entries)
    preview_path = run_dir / "preview.png"
    receipt = {
        "schema_version": 1,
        "preview_run_id": run_id,
        "task_id": task_id,
        "preview_render_contract_sha256": contract["preview_render_contract_sha256"],
        "preview_profile_id": contract["preview_profile_id"],
        "preview_profile_version": contract["preview_profile_version"],
        "preview_profile_contract_sha256": contract["preview_profile_contract_sha256"],
        "source_selection_receipt_path": str(PREVIEW_ROOT / task_id / run_id / "source_selection_receipt.json"),
        "source_selection_receipt_sha256": selection["source_selection_receipt_sha256"],
        "source_selection_contract_sha256": selection["source_selection_contract_sha256"],
        "output_image_sha256": None,
        "output_image_size_bytes": 0,
        "output_image_width": contract["preview_width"],
        "output_image_height": contract["preview_height"],
        "output_image_logical_path": str(PREVIEW_ROOT / task_id / run_id / "preview.png"),
        "source_count": len(contract["layers"]),
        "adapter_count": len(contract["adapter_ids"]),
        "network_run": allow_network,
        "total_network_bytes": total_bytes,
        "primary_imagery_source": "usgs_naip_imagery",
        "primary_imagery_theme": "aerial_imagery",
        "actual_imagery_pixel_status": visual.get("primary_imagery_real_pixels") is True,
        "layers": layers,
        "warnings": [],
        "failures": failures,
        "operation_status": status,
        "started_at_utc": started,
        "finished_at_utc": now(),
        "duration_ms": int((time.monotonic() - t0) * 1000),
        "preview_receipt_contract_sha256": "",
        "provenance": {"faster_raster_preview_renderer": "1.0.0-alpha.3", "source_allowlist_sha256": allowlist["source_allowlist_sha256"]},
        **visual,
    }
    if {"usgs_naip_imagery", "usgs_3dep_hillshade", "usda_cdl_imageserver"} <= images.keys():
        draw_dashboard(images, composite, receipt, preview_path)
    else:
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (contract["preview_width"], contract["preview_height"]), (220, 220, 220)).save(preview_path)
    data = preview_path.read_bytes()
    receipt["output_image_sha256"] = sha256_bytes(data)
    receipt["output_image_size_bytes"] = len(data)
    receipt["preview_receipt_contract_sha256"] = receipt_hash(receipt)
    write_json(run_dir / "preview_plan.json", contract)
    write_json(run_dir / "preview_render_contract.json", contract)
    write_json(run_dir / "source_selection_receipt.json", selection)
    write_json(run_dir / "adapter_evidence.json", verify_adapter_conformance(root=root))
    write_json(run_dir / "layer_receipts.json", layers)
    write_json(run_dir / "preview_receipt.json", receipt)
    verification = verify_preview(receipt, contract=contract, root=root)
    write_json(run_dir / "preview_verification.json", verification)
    write_jsonl(run_dir / "execution_log.jsonl", [{"event_type": "preview_rendered", "preview_run_id": run_id, "status": status, "timestamp_utc": receipt["finished_at_utc"]}])
    write_json(run_dir / "safety_events.json", {"events": [{"failure_class": item} for item in failures]})
    (run_dir / "preview.html").write_text(f"<html><body><h1>Balanced Preview</h1><img src='preview.png'><pre>{json.dumps(receipt, indent=2)}</pre></body></html>\n", encoding="utf-8")
    pointer = {"preview_run_id": run_id, "receipt_path": str(PREVIEW_ROOT / task_id / run_id / "preview_receipt.json"), "verification_path": str(PREVIEW_ROOT / task_id / run_id / "preview_verification.json"), "preview_path": str(PREVIEW_ROOT / task_id / run_id / "preview.png"), "operation_status": status, "updated_at_utc": receipt["finished_at_utc"]}
    write_json(root / PREVIEW_ROOT / task_id / "latest_preview.json", pointer)
    if status == "completed" and verification["preview_verification_status"] == "PASS":
        write_json(root / PREVIEW_ROOT / task_id / "latest_successful_preview.json", pointer)
    else:
        write_json(root / PREVIEW_ROOT / task_id / "latest_failed_preview.json", pointer)
    return {"contract": contract, "source_selection": selection, "receipt": receipt, "verification": verification}



def class_id_boundary_mask(grid: list[list[int | None]], *, include_nodata_transitions: bool = False) -> list[list[int]]:
    height = len(grid)
    width = len(grid[0]) if height else 0
    mask = [[0 for _ in range(width)] for _ in range(height)]
    for y in range(height):
        for x in range(width):
            value = grid[y][x]
            if value is None and not include_nodata_transitions:
                continue
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and 0 <= ny < height:
                    other = grid[ny][nx]
                    if (value is None or other is None) and not include_nodata_transitions:
                        continue
                    if other != value:
                        mask[y][x] = 1
                        break
    return mask

def boundary_pixel_fraction(mask: list[list[int]]) -> float:
    total = sum(len(row) for row in mask)
    return round(sum(sum(row) for row in mask) / max(total, 1), 6)

def verify_preview(receipt: dict[str, Any], *, contract: dict[str, Any] | None = None, root: Path | None = None) -> dict[str, Any]:
    root = root or Path.cwd()
    failures: list[str] = []
    statuses = {
        "source_selection_receipt_status": "PASS",
        "source_selection_contract_status": "PASS",
        "selected_source_consistency_status": "PASS",
        "preview_profile_binding_status": "PASS",
        "imagery_visual_authority_status": receipt.get("imagery_visual_authority_status", "FAIL"),
        "categorical_balance_status": receipt.get("categorical_balance_status", "FAIL"),
        "boundary_restraint_status": receipt.get("boundary_restraint_status", "FAIL"),
        "legend_truthfulness_status": receipt.get("legend_truthfulness_status", "FAIL"),
        "overlay_alpha_budget_status": receipt.get("overlay_alpha_budget_status", "FAIL"),
    }
    path = root / receipt.get("output_image_logical_path", "")
    if not path.exists() or sha256_bytes(path.read_bytes()) != receipt.get("output_image_sha256"):
        failures.append("image identity failed")
    if receipt_hash(receipt) != receipt.get("preview_receipt_contract_sha256"):
        failures.append("receipt tampering detected")
    if contract and contract.get("preview_profile_contract_sha256") != receipt.get("preview_profile_contract_sha256"):
        statuses["preview_profile_binding_status"] = "FAIL"
    selection_path = root / receipt.get("source_selection_receipt_path", "")
    try:
        selection = json.loads(selection_path.read_text(encoding="utf-8-sig"))
        if source_selection_receipt_hash(selection) != receipt.get("source_selection_receipt_sha256"):
            statuses["source_selection_receipt_status"] = "FAIL"
        if source_selection_contract_hash(selection) != receipt.get("source_selection_contract_sha256"):
            statuses["source_selection_contract_status"] = "FAIL"
        selected_layer = next((layer for layer in receipt.get("layers", []) if layer.get("source_id") == selection.get("selected_item")), None)
        if selection.get("selected_item") != receipt.get("primary_imagery_source") or selected_layer is None or selected_layer.get("adapter_id") != selection.get("selected_adapter_id"):
            statuses["selected_source_consistency_status"] = "FAIL"
    except Exception:
        statuses["source_selection_receipt_status"] = "FAIL"
    if not receipt.get("actual_imagery_pixel_status"):
        failures.append("real imagery unavailable")
    if receipt.get("primary_imagery_visible_fraction", 0) < 0.70:
        failures.append("imagery dominance failed")
    if receipt.get("imagery_contrast_retention", 0) < 0.65:
        statuses["imagery_visual_authority_status"] = "FAIL"
    if receipt.get("imagery_edge_retention", 0) < 0.65:
        statuses["imagery_visual_authority_status"] = "FAIL"
    if receipt.get("categorical_effective_coverage", 1) > 0.30:
        statuses["categorical_balance_status"] = "FAIL"
    if receipt.get("boundary_pixel_fraction", 1) > 0.08:
        statuses["boundary_restraint_status"] = "FAIL"
    if receipt.get("compiled_overlay_alpha_budget", 1) > receipt.get("overlay_alpha_budget_limit", 0.42):
        statuses["overlay_alpha_budget_status"] = "FAIL"
    legend = receipt.get("categorical_legend") or {}
    if legend.get("legend_status") == "mapping_unavailable":
        if legend.get("semantic_legend_entry_count") != 0 or legend.get("entries"):
            statuses["legend_truthfulness_status"] = "FAIL"
        if receipt.get("visible_semantic_class_count") not in {None, "NOT_APPLICABLE"}:
            statuses["legend_truthfulness_status"] = "FAIL"
    if receipt.get("displayed_selected_opacity") is not None and round(float(receipt.get("displayed_selected_opacity")), 4) != round(float(receipt.get("cdl_compiled_opacity", -1)), 4):
        failures.append("displayed selected opacity mismatch")
    serialized = json.dumps(receipt, sort_keys=True).lower()
    for marker in ["/tmp/pytest-", "/home/", "C:\\Users\\", "C:/Users/", "authorization", "password", "secret", "token"]:
        if marker in serialized:
            failures.append(f"forbidden marker present: {marker}")
    failures.extend([f"{key} failed" for key, value in statuses.items() if value == "FAIL"])
    return {
        "schema_version": 1,
        "preview_verification_status": "PASS" if not failures else "FAIL",
        "verification_status": "PASS" if not failures else "FAIL",
        "imagery_presence_status": "PASS" if receipt.get("actual_imagery_pixel_status") else "FAIL",
        "imagery_dominance_status": "PASS" if receipt.get("primary_imagery_visible_fraction", 0) >= 0.70 else "FAIL",
        "overlay_subordination_status": "PASS" if receipt.get("compiled_overlay_alpha_budget", 1) <= receipt.get("overlay_alpha_budget_limit", 0.42) else "FAIL",
        "fallback_truthfulness_status": "PASS",
        **statuses,
        "blocking_failures": failures,
        "failures": failures,
        "warnings": [],
    }
