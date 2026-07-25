from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Sequence

from faster_raster.ag_geography import validate_bbox


class StudyTemplateError(ValueError):
    pass


@dataclass(frozen=True)
class StudyTemplate:
    template_id: str
    summary: str
    workflow: str
    default_years: tuple[int, ...]
    network_policy: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


TEMPLATES = {
    item.template_id: item
    for item in (
        StudyTemplate(
            "human-development-cdl",
            "Live USDA CDL mapped-development proxy with optional endpoint NAIP context.",
            "human_development_change",
            (2008, 2016, 2021),
            "explicit bounded network acquisition with reusable evidence",
        ),
        StudyTemplate(
            "human-development-cdl-reuse",
            "Strict zero-network replay of a compatible finalized CDL study.",
            "human_development_change",
            (2008, 2016, 2021),
            "reuse only; network prohibited",
        ),
        StudyTemplate(
            "ag-cdl-naip",
            "Existing agricultural CDL and NAIP irrigation-field capability.",
            "irrigation_field_structure",
            (2023,),
            "bounded acquisition or compatible reuse",
        ),
        StudyTemplate(
            "ag-naip-classification",
            "Raw four-band NAIP surface classification with same-year CDL weak supervision.",
            "naip_cdl_classification_audit",
            (2023,),
            "bounded acquisition or compatible reuse; classifier extra required",
        ),
        StudyTemplate(
            "generic-cog",
            "Compile-only generic HTTPS/COG manifest scaffold using a supported recipe shell.",
            "crop_class_area_inventory",
            (2023,),
            "offline scaffold; no download is performed by generation",
        ),
    )
}

DEFAULT_BBOX = (-116.41, 43.54, -116.38, 43.57)


def list_study_templates() -> list[dict[str, object]]:
    return [TEMPLATES[key].as_dict() for key in sorted(TEMPLATES)]


def get_study_template(template_id: str) -> StudyTemplate:
    try:
        return TEMPLATES[template_id]
    except KeyError as exc:
        raise StudyTemplateError(
            f"unknown template {template_id!r}; choose one of: {', '.join(sorted(TEMPLATES))}"
        ) from exc


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.")
    if not safe:
        raise StudyTemplateError("study name must contain a letter or number")
    return safe


def _number(value: float) -> str:
    return f"{float(value):.8f}".rstrip("0").rstrip(".")


def _human_development(
    template_id: str,
    name: str,
    bbox: tuple[float, float, float, float],
    years: tuple[int, ...],
) -> str:
    if len(years) < 2:
        raise StudyTemplateError("human-development templates require at least two years")
    if years != tuple(sorted(set(years))):
        raise StudyTemplateError("years must be unique and ordered from earliest to latest")
    reuse_only = template_id == "human-development-cdl-reuse"
    reuse = "only" if reuse_only else "auto"
    allow_network = "false" if reuse_only else "true"
    context_year = years[-1]
    epoch_lines = "\n".join(f"  - year: {year}" for year in years)
    network_note = (
        "This strict replay requires compatible verified assets in finalized handoffs and makes no network requests."
        if reuse_only
        else "Network use is explicit, bounded, exact-year, and recorded; repeat runs can use verified handoffs."
    )
    return f"""---
schema_version: fasterraster.work/v2
name: {name}
workflow: human_development_change

area:
  bbox:
    - {_number(bbox[0])}
    - {_number(bbox[1])}
    - {_number(bbox[2])}
    - {_number(bbox[3])}

epochs:
{epoch_lines}

sources:
  policy: service_discovered
  source_id: usda_nass_cdl_imageserver
  mapping_id: usda_cdl_development_proxy_v1
  context_imagery_source_id: usgs_naip_imageserver
  context_year: {context_year}

data:
  reuse: {reuse}
  allow_network: {allow_network}

processing:
  target_crs: EPSG:5070
  resolution_m: 30
  window_size: 512
  service_tile_size: 2048

limits:
  maximum_download_mb: 25

outputs:
  preview: true
  include_context_imagery: true
  open_when_complete: false
  preview_emphasis: development
---

# {name.replace("-", " ").title()}

This study measures mapped development-proxy state and change from USDA Cropland
Data Layer classes 121-124. CDL is crop-focused: apparent non-agricultural
change can reflect mapped change, classification differences, production
differences, or a combination. It does not establish population, construction
date, occupancy, economics, or causality.

{network_note}

Edit the bounding box, epoch years, reuse policy, context year, byte ceiling,
and preview emphasis in the YAML front matter. Notes below the front matter are
for your methods, citations, observations, and interpretation.
"""


def _agricultural(name: str, bbox: tuple[float, float, float, float], year: int) -> str:
    return f"""---
schema_version: fasterraster.work/v1
name: {name}
workflow: irrigation-field-structure

area:
  bbox:
    - {_number(bbox[0])}
    - {_number(bbox[1])}
    - {_number(bbox[2])}
    - {_number(bbox[3])}

time:
  start: {year}-04-01
  end: {year}-10-31
  crop_year: {year}

sources:
  policy: auto

data:
  reuse: auto
  allow_network: true

processing:
  resolution_m: 1.2
  service_tile_size: 2048
  maximum_parallel_tasks: 2

limits:
  maximum_download_mb: 250

outputs:
  preview: true
  open_when_complete: false
---

# {name.replace("-", " ").title()}

This uses the existing agricultural CDL/NAIP irrigation-field recipe. It does
not introduce a new agricultural analysis. Treat CDL as crop-class evidence and
NAIP as high-resolution visual context; record interpretation and limitations
here.
"""


def _classification(
    name: str,
    bbox: tuple[float, float, float, float],
    year: int,
) -> str:
    return f"""---
schema_version: fasterraster.work/v1
name: {name}
workflow: naip-cdl-classification-audit

area:
  bbox:
    - {_number(bbox[0])}
    - {_number(bbox[1])}
    - {_number(bbox[2])}
    - {_number(bbox[3])}

time:
  start: {year}-01-01
  end: {year}-12-31
  crop_year: {year}

sources:
  policy: auto

data:
  reuse: auto
  allow_network: true

processing:
  resolution_m: 1.2
  service_tile_size: 1800
  maximum_parallel_tasks: 1

limits:
  maximum_download_mb: 500

outputs:
  preview: true
  open_when_complete: false
---

# {name.replace("-", " ").title()}

This study performs single-date high-resolution NAIP spectral surface
classification weakly supervised by same-year USDA CDL superclasses. Spatial
holdout metrics measure agreement with weak labels, not independent
ground-truth accuracy.

It does not establish crop species truth from one NAIP acquisition,
authoritative land cover, cadastral or parcel boundaries, ownership,
construction dates, occupancy, population or economic activity, irrigation
status, crop yield, causal land-use change, independent accuracy, or historical
change from one date.

The classifier consumes unstretched red, green, blue, near-infrared, NDVI,
GNDVI, VARI, excess-green, brightness, and saturation predictors derived
locally from the raw four-band NAIP asset. CDL supplies weak supervision and
comparison evidence only.
"""


def _generic_cog(name: str, bbox: tuple[float, float, float, float], year: int) -> str:
    return f"""---
schema_version: fasterraster.work/v1
name: {name}
workflow: crop-class-area-inventory

area:
  bbox:
    - {_number(bbox[0])}
    - {_number(bbox[1])}
    - {_number(bbox[2])}
    - {_number(bbox[3])}

time:
  start: {year}-01-01
  end: {year}-12-31
  crop_year: {year}

sources:
  policy: pinned
  crop_classes: https://example.invalid/replace-with-a-bounded-categorical-cog.tif

data:
  reuse: only
  allow_network: false

processing:
  resolution_m: 30

limits:
  maximum_download_mb: 25

outputs:
  preview: true
  open_when_complete: false
---

# {name.replace("-", " ").title()}

This is an offline, compile-oriented scaffold for FasterRaster's existing
generic HTTPS-template and COG manifest contracts. Replace the invalid example
URL with an approved bounded categorical COG before planning. Generation and
validation never contact the URL. This template does not claim a general COG
download engine or arbitrary raster algebra.
"""


def render_study_template(
    template_id: str,
    *,
    name: str | None = None,
    bbox: Sequence[float] | None = None,
    years: Sequence[int] | None = None,
) -> str:
    template = get_study_template(template_id)
    safe_name = _safe_name(name or template_id)
    selected_bbox = validate_bbox(tuple(float(value) for value in (bbox or DEFAULT_BBOX)))
    selected_years = tuple(int(value) for value in (years or template.default_years))
    if not selected_years:
        raise StudyTemplateError("at least one year is required")
    if any(year < 1985 or year > 2200 for year in selected_years):
        raise StudyTemplateError("years must be between 1985 and 2200")
    if template_id.startswith("human-development-cdl"):
        return _human_development(template_id, safe_name, selected_bbox, selected_years)
    if len(selected_years) != 1:
        raise StudyTemplateError(f"{template_id} accepts exactly one year")
    if template_id == "ag-cdl-naip":
        return _agricultural(safe_name, selected_bbox, selected_years[0])
    if template_id == "ag-naip-classification":
        return _classification(safe_name, selected_bbox, selected_years[0])
    return _generic_cog(safe_name, selected_bbox, selected_years[0])


def show_study_template(template_id: str) -> str:
    return render_study_template(template_id)
