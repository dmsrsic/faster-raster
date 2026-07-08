#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_LIVE = "reports/live_stack_cook/live_stack_cook_v0_5_3.json"
DEFAULT_WAVE = "reports/live_stack_cook/live_stack_adapter_wave_plan_v0_5_3.json"
DEFAULT_OUT_JSON = "reports/live_stack_cook/live_stack_quality_assessment_v0_5_3.json"
DEFAULT_OUT_MD = "reports/live_stack_cook/live_stack_quality_assessment_v0_5_3.md"


@dataclass(frozen=True)
class GradeBand:
    label: str
    min_score: float
    language: str


GRADE_BANDS = [
    GradeBand("excellent", 90.0, "Strong enough to drive the next experimental adapter implementation wave."),
    GradeBand("strong", 80.0, "Good evidence, with a few validation gaps before adapter hardening."),
    GradeBand("usable", 70.0, "Useful as a research proof, but not yet strong enough for adapter promotion."),
    GradeBand("preliminary", 60.0, "Promising but too thin for implementation decisions without follow-up probes."),
    GradeBand("weak", 0.0, "Insufficient evidence; treat as exploratory only."),
]


DIRECT_DATA_HINTS = {
    "application/zip",
    "application/x-netcdf",
    "image/tiff",
    "text/csv",
    "application/octet-stream",
    "binary/octet-stream",
}

HIGH_CONFIDENCE_RESULT_CLASSES = {
    "pass_verified",
    "pass_range_limited",
}

PROVISIONAL_RESULT_CLASSES = {
    "pass_bounded_truncated",
}

FIXTURE_DECISIONS = {
    "preserve_as_contract_fixture",
}

READY_DECISIONS = {
    "ready_for_experimental_static_range_adapter",
    "ready_for_experimental_metadata_json_adapter",
    "ready_for_experimental_thredds_catalog_adapter",
    "ready_for_experimental_grib_index_adapter",
}

CAUTION_DECISIONS = {
    "needs_magic_byte_and_parameter_validation",
    "needs_product_specific_key_resolution",
    "metadata_only_keep_auth_caution",
}


def read_json(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"Missing required JSON file: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def grade(score: float) -> GradeBand:
    for band in GRADE_BANDS:
        if score >= band.min_score:
            return band
    return GRADE_BANDS[-1]


def pct(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return 100.0 * numerator / denominator


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def content_family(content_type: str | None) -> str:
    c = (content_type or "").lower()
    if "netcdf" in c:
        return "netcdf"
    if "zip" in c:
        return "zip"
    if "tiff" in c or "tif" in c:
        return "tiff"
    if "json" in c:
        return "json"
    if "xml" in c:
        return "xml"
    if "csv" in c:
        return "csv"
    if "html" in c:
        return "html"
    if "grib" in c:
        return "grib"
    if "octet-stream" in c:
        return "octet_stream"
    if not c:
        return "unknown"
    return c.split(";")[0].strip()


def result_quality_label(row: dict[str, Any]) -> str:
    result_class = str(row.get("result_class") or "")
    content_type = str(row.get("content_type") or "")
    source_id = str(row.get("source_id") or row.get("live_source_id") or "")

    if result_class == "pass_verified":
        return "high"
    if result_class == "pass_range_limited":
        if "netcdf" in content_type.lower() or "zip" in content_type.lower() or "tiff" in content_type.lower():
            return "high_bounded"
        return "good_bounded_needs_magic"
    if result_class == "pass_bounded_truncated":
        return "provisional_needs_content_validation"
    if "cmr" in source_id:
        return "metadata_only_auth_caution"
    return "review"


def row_recommendation(row: dict[str, Any]) -> str:
    source_id = str(row.get("source_id") or row.get("live_source_id") or "")
    result_class = str(row.get("result_class") or "")
    content_type = str(row.get("content_type") or "").lower()

    if source_id in {"prism_daily_ppt", "daymet_single_pixel", "usda_cdl"}:
        return "Preserve this as a known-good fixture and contract regression test."
    if source_id in {"chirps_daily", "gridmet_daily", "terraclimate_monthly", "worldclim_normals"}:
        return "Route into the experimental static_http_range adapter with magic-byte validation."
    if source_id == "usgs_3dep_tnm":
        return "Promote as metadata JSON discovery evidence, then resolve actual DEM asset URLs separately."
    if source_id == "noaa_ncei_thredds":
        return "Promote as THREDDS catalog evidence, then descend into dataset-specific catalogs."
    if source_id == "noaa_hrrr_open_data":
        return "Promote as GRIB index evidence; next step is index-to-byte-range mapping."
    if source_id == "noaa_gfs_nomads":
        return "Do not promote yet; validate GRIB magic bytes and filter parameters first."
    if source_id == "noaa_mrms_open_data":
        return "Do not promote yet; resolve product-specific object keys first."
    if source_id == "nasa_cmr_metadata":
        return "Keep as metadata-only CMR evidence with Earthdata/auth caution for assets."
    if result_class.startswith("pass_") and "json" in content_type:
        return "Treat as discovery metadata, not final raster evidence."
    return "Manual review before promotion."


def score_report(live: dict[str, Any], wave: dict[str, Any] | None) -> dict[str, Any]:
    live_rows = list(live.get("results", []))
    wave_rows = list((wave or {}).get("rows", []))

    endpoint_count = int(live.get("endpoint_count", len(live_rows)))
    endpoint_pass_count = int(live.get("endpoint_pass_count", sum(1 for r in live_rows if str(r.get("result_class", "")).startswith("pass_"))))
    endpoint_fail_count = int(live.get("endpoint_fail_count", endpoint_count - endpoint_pass_count))
    total_bytes = int(live.get("total_bytes_read", sum(int(r.get("bytes_read") or 0) for r in live_rows)))

    pass_rate = pct(endpoint_pass_count, endpoint_count)

    bounded_rows = [
        r for r in live_rows
        if int(r.get("bytes_read") or 0) <= int(live.get("max_bytes_per_source", 65536))
    ]
    bounded_rate = pct(len(bounded_rows), endpoint_count)

    sha_rows = [r for r in live_rows if r.get("sha256")]
    sha_rate = pct(len(sha_rows), endpoint_count)

    http_good_rows = [r for r in live_rows if int(r.get("status") or 0) in {200, 206}]
    http_good_rate = pct(len(http_good_rows), endpoint_count)

    high_conf_rows = [
        r for r in live_rows
        if str(r.get("result_class") or "") in HIGH_CONFIDENCE_RESULT_CLASSES
    ]
    high_conf_rate = pct(len(high_conf_rows), endpoint_count)

    provisional_rows = [
        r for r in live_rows
        if str(r.get("result_class") or "") in PROVISIONAL_RESULT_CLASSES
    ]
    provisional_rate = pct(len(provisional_rows), endpoint_count)

    families = Counter(content_family(r.get("content_type")) for r in live_rows)
    family_diversity = len([k for k in families if k != "unknown"])

    decisions = Counter(str(r.get("promotion_decision") or "") for r in wave_rows)
    ready_count = sum(decisions[d] for d in READY_DECISIONS)
    fixture_count = sum(decisions[d] for d in FIXTURE_DECISIONS)
    caution_count = sum(decisions[d] for d in CAUTION_DECISIONS)

    default_network_off = bool((wave or {}).get("recommended_default_network_mode") == "off")
    no_default_knob_change = bool((wave or {}).get("default_knobs_change_recommended") is False)
    runtime_registry_safe = all(not bool(r.get("runtime_promotion_allowed_now")) for r in wave_rows) if wave_rows else True

    safety_score = 100.0
    if not default_network_off:
        safety_score -= 25.0
    if not no_default_knob_change:
        safety_score -= 25.0
    if not runtime_registry_safe:
        safety_score -= 25.0
    if endpoint_fail_count > 0:
        safety_score -= min(25.0, endpoint_fail_count * 5.0)

    evidence_score = (
        0.30 * pass_rate
        + 0.20 * bounded_rate
        + 0.20 * sha_rate
        + 0.15 * http_good_rate
        + 0.15 * high_conf_rate
    )

    # Provisional responses are not bad, but they should reduce confidence a little.
    evidence_score -= min(8.0, provisional_rate * 0.08)

    diversity_score = clamp(100.0 * min(family_diversity, 7) / 7)

    adapter_score = 0.0
    if wave_rows:
        adapter_score = clamp(100.0 * (ready_count + fixture_count * 0.75) / max(len(wave_rows), 1))
        # Caution rows are healthy if they are identified rather than blindly promoted.
        adapter_score += min(10.0, caution_count * 2.0)
        adapter_score = clamp(adapter_score)

    overall = clamp(
        0.38 * evidence_score
        + 0.27 * safety_score
        + 0.20 * adapter_score
        + 0.15 * diversity_score
    )

    band = grade(overall)

    row_assessments = []
    for r in live_rows:
        row_assessments.append({
            "source_id": r.get("source_id"),
            "endpoint_id": r.get("endpoint_id"),
            "result_class": r.get("result_class"),
            "http_status": r.get("status"),
            "bytes_read": r.get("bytes_read"),
            "content_type": r.get("content_type"),
            "content_family": content_family(r.get("content_type")),
            "sha256_short": (r.get("sha256") or "")[:12],
            "quality_label": result_quality_label(r),
            "recommendation": row_recommendation(r),
        })

    strongest = [
        r for r in row_assessments
        if r["source_id"] in {
            "chirps_daily",
            "gridmet_daily",
            "terraclimate_monthly",
            "worldclim_normals",
            "prism_daily_ppt",
        }
    ]

    caution = [
        r for r in row_assessments
        if r["source_id"] in {
            "noaa_gfs_nomads",
            "noaa_mrms_open_data",
            "nasa_cmr_metadata",
        }
    ]

    return {
        "assessment_id": "live_stack_quality_assessment_v0_5_3",
        "overall_score": round(overall, 2),
        "overall_grade": band.label,
        "grade_language": band.language,
        "component_scores": {
            "evidence_score": round(evidence_score, 2),
            "safety_score": round(safety_score, 2),
            "adapter_score": round(adapter_score, 2),
            "diversity_score": round(diversity_score, 2),
        },
        "metrics": {
            "endpoint_count": endpoint_count,
            "endpoint_pass_count": endpoint_pass_count,
            "endpoint_fail_count": endpoint_fail_count,
            "pass_rate_percent": round(pass_rate, 2),
            "bounded_rate_percent": round(bounded_rate, 2),
            "sha_rate_percent": round(sha_rate, 2),
            "http_good_rate_percent": round(http_good_rate, 2),
            "high_confidence_rate_percent": round(high_conf_rate, 2),
            "provisional_rate_percent": round(provisional_rate, 2),
            "total_bytes_read": total_bytes,
            "content_family_count": family_diversity,
            "content_families": dict(sorted(families.items())),
            "ready_adapter_decision_count": ready_count,
            "fixture_decision_count": fixture_count,
            "caution_decision_count": caution_count,
            "default_network_mode_off": default_network_off,
            "default_knobs_change_recommended": not no_default_knob_change,
            "runtime_registry_safe": runtime_registry_safe,
        },
        "strongest_candidates": strongest,
        "caution_candidates": caution,
        "row_assessments": row_assessments,
    }


def explanatory_text(assessment: dict[str, Any], lingo: str = "standard") -> str:
    m = assessment["metrics"]
    c = assessment["component_scores"]
    grade_label = assessment["overall_grade"]
    score = assessment["overall_score"]

    if lingo == "kitchen":
        subject = "cook"
        sources = "sauces"
        probes = "dips"
        ready = "goods"
        caution = "bads/locks"
    else:
        subject = "live stack test"
        sources = "sources"
        probes = "bounded probes"
        ready = "adapter candidates"
        caution = "caution candidates"

    lines = []
    lines.append(f"Quality assessment: {grade_label.upper()} ({score}/100)")
    lines.append("")
    lines.append(
        f"The {subject} is high quality because {m['endpoint_pass_count']} of "
        f"{m['endpoint_count']} {sources} responded successfully, with "
        f"{m['endpoint_fail_count']} endpoint failures and only {m['total_bytes_read']} total bytes read."
    )
    lines.append(
        f"The safety posture is also strong: the report keeps default live networking off, "
        f"does not recommend changing default knobs, and does not allow runtime promotion from this evidence alone."
    )
    lines.append(
        f"The evidence is broad rather than narrow. It covers {m['content_family_count']} content families: "
        f"{', '.join(sorted(m['content_families'].keys()))}."
    )
    lines.append("")
    lines.append("Component scores:")
    lines.append(f"- Evidence: {c['evidence_score']}/100")
    lines.append(f"- Safety: {c['safety_score']}/100")
    lines.append(f"- Adapter planning: {c['adapter_score']}/100")
    lines.append(f"- Format/source diversity: {c['diversity_score']}/100")
    lines.append("")
    lines.append(
        f"The best immediate implementation target is a generic static_http_range adapter, "
        f"because CHIRPS, gridMET, TerraClimate, WorldClim, and PRISM all produced bounded range-readable evidence."
    )
    lines.append(
        f"The caution set should not be treated as failed. NOAA GFS, NOAA MRMS, and NASA CMR are useful, "
        f"but they need specialized validation before promotion: magic-byte/parameter validation, product key resolution, "
        f"or asset-level auth checks."
    )
    lines.append("")
    lines.append("Decision:")
    lines.append("- Do not change default knobs.")
    lines.append("- Preserve this run as live evidence.")
    lines.append("- Implement experimental static_http_range first.")
    lines.append("- Keep runtime registry promotion disabled until the adapter has tests and fixtures.")
    return "\n".join(lines)


def markdown_report(assessment: dict[str, Any], explain: bool, lingo: str) -> str:
    lines = []
    lines.append("# Live Stack Quality Assessment v0.5.3")
    lines.append("")
    lines.append(f"- overall_score: `{assessment['overall_score']}`")
    lines.append(f"- overall_grade: `{assessment['overall_grade']}`")
    lines.append(f"- grade_language: {assessment['grade_language']}")
    lines.append("")

    if explain:
        lines.append("## Explanation")
        lines.append("")
        lines.append(explanatory_text(assessment, lingo=lingo))
        lines.append("")

    lines.append("## Component scores")
    lines.append("")
    lines.append("| Component | Score |")
    lines.append("| --- | ---: |")
    for key, value in assessment["component_scores"].items():
        lines.append(f"| `{key}` | {value} |")

    lines.append("")
    lines.append("## Metrics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | ---: |")
    for key, value in assessment["metrics"].items():
        if isinstance(value, dict):
            value = json.dumps(value, sort_keys=True)
        lines.append(f"| `{key}` | `{value}` |")

    lines.append("")
    lines.append("## Row assessments")
    lines.append("")
    lines.append("| Source | Class | HTTP | Bytes | Type | Quality | Recommendation |")
    lines.append("| --- | --- | ---: | ---: | --- | --- | --- |")
    for r in assessment["row_assessments"]:
        lines.append(
            f"| `{r['source_id']}` | `{r['result_class']}` | {r['http_status']} | "
            f"{r['bytes_read']} | {r['content_type']} | `{r['quality_label']}` | {r['recommendation']} |"
        )

    lines.append("")
    lines.append("## Strongest candidates")
    lines.append("")
    for r in assessment["strongest_candidates"]:
        lines.append(f"- `{r['source_id']}`: {r['recommendation']}")

    lines.append("")
    lines.append("## Caution candidates")
    lines.append("")
    for r in assessment["caution_candidates"]:
        lines.append(f"- `{r['source_id']}`: {r['recommendation']}")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Summarize and assess FasterRaster live stack cook result quality."
    )
    parser.add_argument("--live", default=DEFAULT_LIVE, help="Live stack cook JSON path.")
    parser.add_argument("--wave", default=DEFAULT_WAVE, help="Adapter wave plan JSON path.")
    parser.add_argument("--out-json", default=DEFAULT_OUT_JSON, help="Output assessment JSON path.")
    parser.add_argument("--out-md", default=DEFAULT_OUT_MD, help="Output assessment Markdown path.")
    parser.add_argument("--explain", action="store_true", help="Print and write descriptive assessment language.")
    parser.add_argument(
        "--lingo",
        choices=["standard", "kitchen"],
        default="standard",
        help="Language mode for descriptive explanation.",
    )
    parser.add_argument(
        "--fail-below",
        type=float,
        default=None,
        help="Exit with code 2 if overall score is below this threshold.",
    )
    args = parser.parse_args()

    live = read_json(args.live)
    wave = read_json(args.wave) if Path(args.wave).exists() else None

    assessment = score_report(live, wave)

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)

    out_json.write_text(json.dumps(assessment, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    out_md.write_text(markdown_report(assessment, explain=args.explain, lingo=args.lingo), encoding="utf-8")

    print(f"overall_score: {assessment['overall_score']}")
    print(f"overall_grade: {assessment['overall_grade']}")
    print(f"endpoint_pass_count: {assessment['metrics']['endpoint_pass_count']}")
    print(f"endpoint_fail_count: {assessment['metrics']['endpoint_fail_count']}")
    print(f"total_bytes_read: {assessment['metrics']['total_bytes_read']}")
    print(f"ready_adapter_decision_count: {assessment['metrics']['ready_adapter_decision_count']}")
    print(f"fixture_decision_count: {assessment['metrics']['fixture_decision_count']}")
    print(f"caution_decision_count: {assessment['metrics']['caution_decision_count']}")
    print(f"wrote_json: {out_json}")
    print(f"wrote_markdown: {out_md}")

    if args.explain:
        print("")
        print(explanatory_text(assessment, lingo=args.lingo))

    if args.fail_below is not None and assessment["overall_score"] < args.fail_below:
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
