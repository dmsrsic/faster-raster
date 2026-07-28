from __future__ import annotations

import copy
import hashlib
import json
import sys
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from pydantic import ValidationError

from faster_raster.ag_geography import (
    BBox,
    BBoxValidationError,
    validate_bbox_text,
)
from faster_raster.aoi_geometry import (
    AreaConstructionError,
    ConstructedArea,
    build_point_buffer_area,
    explicit_bbox_area,
)
from faster_raster.temporal_alternatives import alternatives_from_years
from faster_raster.workfiles import TimeSpec, Workfile, WorkfileError, WorkfileSpec


MAXIMUM_INTERACTIVE_ATTEMPTS = 20


class RepairCancelled(RuntimeError):
    pass


class RepairAttemptsExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class RecoverableContractFailure:
    failure_type: str
    logical_asset: str
    source: str
    code: str
    detail: str
    original_requested_value: Any
    compatible_alternatives: tuple[Any, ...]
    evidence: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        result = {
            "schema_version": "fasterraster.recoverable-contract-failure/v1",
            "failure_type": self.failure_type,
            "logical_asset": self.logical_asset,
            "source": self.source,
            "code": self.code,
            "detail": self.detail,
            "original_requested_value": self.original_requested_value,
            "compatible_alternatives": list(self.compatible_alternatives),
            "source_evidence": self.evidence,
        }
        if (
            self.failure_type == "imagery_year_unavailable"
            and str(self.original_requested_value).isdigit()
        ):
            years = [
                int(value)
                for value in self.compatible_alternatives
                if str(value).isdigit()
            ]
            result["temporal_alternatives"] = alternatives_from_years(
                int(self.original_requested_value),
                years,
                source_id="usgs_naip_imageserver",
                provider="USGS",
                product="NAIP",
            )
        return result


def recoverable_failure_from_document(
    document: Mapping[str, Any],
) -> RecoverableContractFailure | None:
    source = str(document.get("source") or "")
    code = str(document.get("code") or "")
    evidence = dict(document.get("evidence") or {})
    if source != "USGS_NAIP":
        return None
    available_years = tuple(
        sorted(
            {
                int(value)
                for value in (
                    evidence.get("available_intersecting_years")
                    or evidence.get("available_years")
                    or []
                )
                if str(value).isdigit()
            }
        )
    )
    requested_imagery_year = (
        evidence.get("requested_year")
        or document.get("requested_imagery_year")
        or document.get("requested_year")
    )
    try:
        comparable_requested_year = int(requested_imagery_year)
    except (TypeError, ValueError):
        comparable_requested_year = None
    year_unavailable_is_supported = (
        code == "requested_year_unavailable"
        and bool(available_years)
        and comparable_requested_year is not None
        and comparable_requested_year not in available_years
    )
    no_intersection_is_year_failure = (
        code == "no_intersecting_imagery"
        and bool(available_years)
        and comparable_requested_year is not None
        and comparable_requested_year not in available_years
    )
    if (
        code == "no_intersecting_imagery"
        and bool(available_years)
        and not no_intersection_is_year_failure
    ):
        return None
    if year_unavailable_is_supported or no_intersection_is_year_failure:
        failure_type = "imagery_year_unavailable"
        requested = requested_imagery_year
        alternatives: tuple[Any, ...] = available_years
    elif (
        code == "date_range_unavailable"
        and bool(evidence.get("available_acquisition_dates"))
    ):
        failure_type = "imagery_date_range_unavailable"
        requested = evidence.get("requested_date_range") or {
            "start": document.get("requested_start"),
            "end": document.get("requested_end"),
        }
        alternatives = tuple(evidence.get("available_acquisition_dates") or ())
    elif code in {"bbox_outside_coverage", "no_intersecting_imagery"}:
        failure_type = "location_unavailable"
        requested = evidence.get("requested_bbox") or document.get(
            "requested_bbox"
        )
        alternatives = ()
    else:
        return None
    return RecoverableContractFailure(
        failure_type=failure_type,
        logical_asset="naip_multispectral",
        source=source,
        code=code,
        detail=str(document.get("detail") or ""),
        original_requested_value=requested,
        compatible_alternatives=alternatives,
        evidence={
            **evidence,
            "failure_document_schema": document.get("schema_version"),
            "metadata_network_bytes": int(document.get("network_bytes") or 0),
        },
    )


@dataclass(frozen=True)
class ClassificationRuntimeRequest:
    request_bbox_epsg_4326: BBox
    imagery_start: date
    imagery_end: date
    imagery_year: int
    cdl_year: int
    analysis_aoi_epsg_4326: dict[str, Any] | None = None
    spatial_construction: dict[str, Any] | None = None

    @classmethod
    def from_workfile(cls, workfile: Workfile) -> "ClassificationRuntimeRequest":
        spec = workfile.spec
        return cls(
            request_bbox_epsg_4326=tuple(spec.area.bbox),
            imagery_start=spec.time.start,
            imagery_end=spec.time.end,
            imagery_year=spec.time.crop_year,
            cdl_year=spec.time.crop_year,
        )

    @property
    def temporal_mismatch(self) -> bool:
        return self.imagery_year != self.cdl_year

    @property
    def acquisition_geometry_differs(self) -> bool:
        return bool(
            (self.spatial_construction or {}).get(
                "acquisition_geometry_differs_from_analysis_aoi"
            )
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_bbox_epsg_4326": list(self.request_bbox_epsg_4326),
            "imagery_timeframe": {
                "start": self.imagery_start.isoformat(),
                "end": self.imagery_end.isoformat(),
            },
            "imagery_year": self.imagery_year,
            "cdl_year": self.cdl_year,
            "analysis_aoi_epsg_4326": self.analysis_aoi_epsg_4326,
            "spatial_construction": self.spatial_construction,
            "temporal_mismatch": self.temporal_mismatch,
            "acquisition_geometry_differs_from_analysis_aoi": (
                self.acquisition_geometry_differs
            ),
        }

    def with_imagery_year(self, year: int) -> "ClassificationRuntimeRequest":
        if not 1900 <= int(year) <= 2200:
            raise ValueError("imagery year must be between 1900 and 2200")
        try:
            start = self.imagery_start.replace(year=int(year))
            end = self.imagery_end.replace(year=int(year))
        except ValueError as exc:
            raise ValueError(
                "the existing imagery month/day range cannot be represented "
                f"in {year}; enter a replacement date range instead"
            ) from exc
        TimeSpec(start=start, end=end, crop_year=int(year))
        return replace(
            self,
            imagery_start=start,
            imagery_end=end,
            imagery_year=int(year),
        )

    def with_imagery_dates(
        self,
        start: date,
        end: date,
    ) -> "ClassificationRuntimeRequest":
        TimeSpec(start=start, end=end, crop_year=self.imagery_year)
        return replace(self, imagery_start=start, imagery_end=end)

    def with_explicit_bbox(self, bbox: BBox) -> "ClassificationRuntimeRequest":
        area = explicit_bbox_area(bbox)
        return replace(
            self,
            request_bbox_epsg_4326=tuple(area["request_bbox_epsg_4326"]),
            analysis_aoi_epsg_4326=area["analysis_aoi_epsg_4326"],
            spatial_construction=area,
        )

    def with_constructed_area(
        self,
        area: ConstructedArea,
    ) -> "ClassificationRuntimeRequest":
        return replace(
            self,
            request_bbox_epsg_4326=area.request_bbox_epsg_4326,
            analysis_aoi_epsg_4326=area.analysis_aoi_epsg_4326,
            spatial_construction=area.as_dict(),
        )


def amended_workfile(
    workfile: Workfile,
    request: ClassificationRuntimeRequest,
) -> Workfile:
    raw = copy.deepcopy(workfile.front_matter)
    raw.setdefault("area", {})["bbox"] = list(request.request_bbox_epsg_4326)
    try:
        spec = WorkfileSpec.model_validate(raw)
        TimeSpec(
            start=request.imagery_start,
            end=request.imagery_end,
            crop_year=request.imagery_year,
        )
    except ValidationError as exc:
        raise WorkfileError(f"invalid in-memory repaired request: {exc}") from exc
    return Workfile(
        path=workfile.path,
        spec=spec,
        prose=workfile.prose,
        front_matter=raw,
    )


def stable_plan_hash(plan: Mapping[str, Any]) -> str:
    stable = {
        "schema_version": plan.get("schema_version"),
        "study_name": plan.get("study_name"),
        "workflow": plan.get("workflow"),
        "rows": plan.get("rows"),
        "asset_plan": plan.get("asset_plan"),
        "maximum_download_bytes": plan.get("maximum_download_bytes"),
        "classification": plan.get("classification"),
        "source_resolution": (plan.get("source_resolution") or {}).get(
            "decisions"
        ),
        "runtime_request": plan.get("runtime_request"),
    }
    return hashlib.sha256(
        json.dumps(
            stable,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


class PromptSession:
    def __init__(
        self,
        *,
        reader: Callable[[str], str] = input,
        writer: Callable[[str], None] = print,
        maximum_invalid_attempts: int = MAXIMUM_INTERACTIVE_ATTEMPTS,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.maximum_invalid_attempts = maximum_invalid_attempts
        self.invalid_attempts = 0

    def write(self, value: str = "") -> None:
        self.writer(value)

    def read(self, prompt: str) -> str:
        try:
            value = self.reader(prompt)
        except EOFError as exc:
            raise RepairCancelled("interactive input ended") from exc
        if value.strip().lower() == "q":
            raise RepairCancelled("repair cancelled")
        return value.strip()

    def invalid(self, message: str) -> None:
        self.invalid_attempts += 1
        self.writer(f"Invalid value: {message}")
        if self.invalid_attempts >= self.maximum_invalid_attempts:
            raise RepairAttemptsExceeded(
                "interactive repair attempt limit reached"
            )

    def confirm(self, prompt: str) -> bool:
        return self.read(prompt).lower() in {"y", "yes"}


def prompt_imagery_year(
    failure: RecoverableContractFailure,
    request: ClassificationRuntimeRequest,
    session: PromptSession,
) -> ClassificationRuntimeRequest:
    years = [
        int(value)
        for value in failure.compatible_alternatives
        if str(value).isdigit()
    ]
    session.write("Source resolution blocked")
    session.write("")
    session.write("Asset: NAIP multispectral imagery")
    session.write(f"Requested year: {request.imagery_year}")
    session.write(f"Reason: {failure.detail}")
    while True:
        session.write("")
        if years:
            session.write("Source-reported compatible years:")
            for index, year in enumerate(years, start=1):
                session.write(f"  [{index}] {year}")
            session.write(f"  [{len(years) + 1}] Enter another year")
        else:
            session.write("No compatible year list was reported.")
            session.write("  [1] Enter another year")
        session.write("  [q] Cancel")
        choice = session.read("Selection: ")
        try:
            index = int(choice)
        except ValueError:
            session.invalid("selection must be a listed number or q")
            continue
        manual_index = len(years) + 1 if years else 1
        if 1 <= index <= len(years):
            candidate = years[index - 1]
        elif index == manual_index:
            raw = session.read("Replacement imagery year: ")
            try:
                candidate = int(raw)
            except ValueError:
                session.invalid("imagery year must be an integer")
                continue
        else:
            session.invalid("selection is outside the listed choices")
            continue
        try:
            return request.with_imagery_year(candidate)
        except (ValueError, ValidationError) as exc:
            session.invalid(str(exc))


def prompt_imagery_dates(
    failure: RecoverableContractFailure,
    request: ClassificationRuntimeRequest,
    session: PromptSession,
) -> ClassificationRuntimeRequest:
    session.write("Source date range blocked")
    session.write("")
    session.write("Asset: NAIP multispectral imagery")
    session.write(
        "Requested range: "
        f"{request.imagery_start.isoformat()} through "
        f"{request.imagery_end.isoformat()}"
    )
    session.write(f"Reason: {failure.detail}")
    while True:
        raw_start = session.read("Replacement start date (YYYY-MM-DD): ")
        try:
            start = date.fromisoformat(raw_start)
        except ValueError:
            session.invalid("start date must use YYYY-MM-DD")
            continue
        if start.year != request.imagery_year:
            session.invalid(
                f"start date must remain in imagery year {request.imagery_year}"
            )
            continue
        while True:
            raw_end = session.read("Replacement end date (YYYY-MM-DD): ")
            try:
                end = date.fromisoformat(raw_end)
            except ValueError:
                session.invalid("end date must use YYYY-MM-DD")
                continue
            try:
                return request.with_imagery_dates(start, end)
            except (ValueError, ValidationError) as exc:
                session.invalid(str(exc))


def _prompt_number(
    session: PromptSession,
    prompt: str,
    label: str,
) -> float:
    while True:
        raw = session.read(prompt)
        try:
            return float(raw)
        except ValueError:
            session.invalid(f"{label} must be numeric")


def _prompt_unit(session: PromptSession) -> str:
    while True:
        session.write("Unit:")
        session.write("  [1] meters")
        session.write("  [2] kilometers")
        session.write("  [3] miles")
        choice = session.read("Selection: ").lower()
        units = {
            "1": "meters",
            "2": "kilometers",
            "3": "miles",
            "meters": "meters",
            "kilometers": "kilometers",
            "miles": "miles",
        }
        if choice in units:
            return units[choice]
        session.invalid("unit must be meters, kilometers, or miles")


def _prompt_shape(session: PromptSession) -> str:
    while True:
        session.write("Shape:")
        session.write("  [1] square")
        session.write("  [2] circle")
        choice = session.read("Selection: ").lower()
        shapes = {
            "1": "square",
            "2": "circle",
            "square": "square",
            "circle": "circle",
        }
        if choice in shapes:
            return shapes[choice]
        session.invalid("shape must be square or circle")


def prompt_location(
    failure: RecoverableContractFailure,
    request: ClassificationRuntimeRequest,
    session: PromptSession,
) -> ClassificationRuntimeRequest:
    session.write("Source coverage blocked")
    session.write("")
    session.write("Current bbox:")
    session.write(
        "  "
        + ", ".join(
            repr(value)
            for value in request.request_bbox_epsg_4326
        )
    )
    session.write(f"Reason: {failure.detail}")
    while True:
        session.write("")
        session.write("Repair location:")
        session.write("  [1] Enter replacement bbox")
        session.write("  [2] Create location from point and buffer")
        session.write("  [q] Cancel")
        choice = session.read("Selection: ")
        if choice == "1":
            raw = session.read(
                "Replacement bbox (west,south,east,north): "
            )
            try:
                candidate = request.with_explicit_bbox(
                    validate_bbox_text(raw)
                )
            except (BBoxValidationError, ValueError) as exc:
                session.invalid(str(exc))
                continue
        elif choice == "2":
            longitude = _prompt_number(
                session,
                "Center longitude (longitude first): ",
                "center longitude",
            )
            latitude = _prompt_number(
                session,
                "Center latitude: ",
                "center latitude",
            )
            while True:
                distance = session.read("Buffer distance: ")
                unit = _prompt_unit(session)
                shape = _prompt_shape(session)
                try:
                    area = build_point_buffer_area(
                        longitude,
                        latitude,
                        distance,
                        unit,
                        shape,
                    )
                    candidate = request.with_constructed_area(area)
                    break
                except AreaConstructionError as exc:
                    session.invalid(str(exc))
        else:
            session.invalid("selection must be 1, 2, or q")
            continue
        area = candidate.spatial_construction or {}
        session.write("")
        session.write("Proposed location:")
        if area.get("center_longitude") is not None:
            session.write(
                "  Center: "
                f"{area['center_longitude']}, {area['center_latitude']}"
            )
            session.write(f"  Shape: {area['shape']}")
            label = "Radius" if area["shape"] == "circle" else "Half-width"
            session.write(
                f"  {label}: {area['entered_buffer_text']} "
                f"{area['entered_distance_unit']}"
            )
        session.write(
            "  Request bbox: "
            + ", ".join(
                repr(value)
                for value in candidate.request_bbox_epsg_4326
            )
        )
        session.write(
            "  Analysis AOI: "
            + (
                f"{area.get('shape')} buffered geometry"
                if area.get("shape") in {"circle", "square"}
                else "explicit bbox"
            )
        )
        if area.get("analysis_aoi_area_square_meters") is not None:
            session.write(
                "  Estimated area: "
                f"{float(area['analysis_aoi_area_square_meters']):,.0f} m²"
            )
        if session.confirm("Use this location? [y/N]: "):
            return candidate


def prompt_repair_candidate(
    failure: RecoverableContractFailure,
    request: ClassificationRuntimeRequest,
    session: PromptSession,
) -> ClassificationRuntimeRequest:
    if failure.failure_type == "imagery_year_unavailable":
        return prompt_imagery_year(failure, request, session)
    if failure.failure_type == "imagery_date_range_unavailable":
        return prompt_imagery_dates(failure, request, session)
    if failure.failure_type == "location_unavailable":
        return prompt_location(failure, request, session)
    raise ValueError(f"unsupported recoverable failure: {failure.failure_type}")


def build_intervention_record(
    *,
    original_request: ClassificationRuntimeRequest,
    resolved_request: ClassificationRuntimeRequest,
    failure: RecoverableContractFailure,
    alternatives_shown: Sequence[Any],
    source_evidence: Mapping[str, Any],
    original_plan_sha256: str,
    resolved_plan_sha256: str,
    confirmation_outcome: str,
) -> dict[str, Any]:
    stable = {
        "schema_version": "fasterraster.contract-intervention/v1",
        "human_intervention_occurred": True,
        "recovery_reason": failure.detail,
        "structured_failure_type": failure.failure_type,
        "source_failure_code": failure.code,
        "affected_logical_asset": failure.logical_asset,
        "source": failure.source,
        "original_request": original_request.as_dict(),
        "resolved_request": resolved_request.as_dict(),
        "alternatives_shown": list(alternatives_shown),
        "selected_replacement": resolved_request.as_dict(),
        "confirmation_outcome": confirmation_outcome,
        "source_evidence_used": dict(source_evidence),
        "original_plan_sha256": original_plan_sha256,
        "resolved_plan_sha256": resolved_plan_sha256,
        "temporal_mismatch": {
            "present": resolved_request.temporal_mismatch,
            "imagery_year": resolved_request.imagery_year,
            "cdl_year": resolved_request.cdl_year,
            "explicitly_accepted": (
                confirmation_outcome == "accepted"
                and resolved_request.temporal_mismatch
            ),
        },
        "spatial_construction": resolved_request.spatial_construction,
    }
    intervention_id = "fri_" + hashlib.sha256(
        json.dumps(
            stable,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()[:20]
    return {
        **stable,
        "intervention_id": intervention_id,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "workfile_write_back": {
            "performed": False,
            "extension_point": "future_explicit_atomic_write_back",
        },
    }


def intervention_reference(
    intervention: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return bounded repair context for derived analytical receipts."""

    if intervention is None:
        return {
            "schema_version": "fasterraster.intervention-reference/v1",
            "human_repair_occurred": False,
            "intervention_id": None,
        }

    def request_reference(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, Mapping):
            return None
        spatial = value.get("spatial_construction")
        spatial = spatial if isinstance(spatial, Mapping) else {}
        return {
            "request_bbox_epsg_4326": value.get(
                "request_bbox_epsg_4326"
            ),
            "imagery_timeframe": value.get("imagery_timeframe"),
            "imagery_year": value.get("imagery_year"),
            "cdl_year": value.get("cdl_year"),
            "temporal_mismatch": value.get("temporal_mismatch"),
            "analysis_aoi_shape": spatial.get("shape"),
            "analysis_aoi_geometry_sha256": spatial.get(
                "geometry_sha256"
            ),
            "acquisition_geometry_differs_from_analysis_aoi": value.get(
                "acquisition_geometry_differs_from_analysis_aoi"
            ),
        }

    return {
        "schema_version": "fasterraster.intervention-reference/v1",
        "human_repair_occurred": True,
        "intervention_id": intervention.get("intervention_id"),
        "structured_failure_type": intervention.get(
            "structured_failure_type"
        ),
        "confirmation_outcome": intervention.get(
            "confirmation_outcome"
        ),
        "original_request": request_reference(
            intervention.get("original_request")
        ),
        "resolved_request": request_reference(
            intervention.get("resolved_request")
        ),
        "temporal_mismatch": intervention.get("temporal_mismatch"),
    }


def terminal_interaction_enabled(
    explicit: bool | None,
    *,
    stdin: Any | None = None,
    stdout: Any | None = None,
    json_output: bool = False,
) -> bool:
    if json_output:
        return False
    if explicit is False:
        return False
    if explicit is True:
        return True
    resolved_stdin = sys.stdin if stdin is None else stdin
    resolved_stdout = sys.stdout if stdout is None else stdout
    return bool(
        getattr(resolved_stdin, "isatty", lambda: False)()
        and getattr(resolved_stdout, "isatty", lambda: False)()
    )
