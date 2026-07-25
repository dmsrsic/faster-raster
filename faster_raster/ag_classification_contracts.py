from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from faster_raster.development_sources import CDL_CLASS_LABELS, USDA_CDL_SOURCE_ID


CDL_SURFACE_SUPERCLASSES_MAPPING_ID = "cdl_surface_superclasses_v1"
CLASSIFICATION_SCIENTIFIC_CLAIM = (
    "Single-date high-resolution NAIP spectral surface classification weakly "
    "supervised by same-year USDA CDL superclasses. Spatial holdout metrics "
    "measure agreement with weak labels, not independent ground-truth accuracy."
)


def classification_scientific_claim(
    imagery_year: int,
    cdl_year: int,
) -> str:
    if imagery_year == cdl_year:
        return CLASSIFICATION_SCIENTIFIC_CLAIM
    return (
        "Single-date high-resolution NAIP spectral surface classification "
        f"using {imagery_year} imagery, weakly supervised by {cdl_year} USDA "
        "CDL superclasses. The sources are temporally mismatched; spatial "
        "holdout metrics measure cross-year agreement with weak labels, not "
        "independent ground-truth accuracy or conditions in the originally "
        "requested imagery year."
    )
CLASSIFICATION_UNSUPPORTED_CLAIMS = (
    "crop species truth from one NAIP acquisition",
    "authoritative land-cover replacement",
    "cadastral or parcel boundaries",
    "confirmed field ownership",
    "construction dates",
    "occupancy",
    "population or economic activity",
    "irrigation status",
    "crop yield",
    "causal land-use change",
    "independent accuracy assessment",
    "historical change from a single-date classification",
)


@dataclass(frozen=True)
class SurfaceClass:
    code: int
    name: str
    cdl_codes: tuple[int, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "cdl_codes": list(self.cdl_codes),
        }


@dataclass(frozen=True)
class ClassificationMapping:
    mapping_id: str
    contract_version: str
    source_id: str
    source_semantic_type: str
    scientific_claim: str
    output_classes: tuple[SurfaceClass, ...]
    excluded_valid_codes: tuple[int, ...]
    invalid_codes: tuple[int, ...]
    unknown_value_policy: str

    def __post_init__(self) -> None:
        output_codes = [item.code for item in self.output_classes]
        if output_codes != [0, 1, 2, 3, 4, 5, 6]:
            raise ValueError("classification output classes must be ordered 0 through 6")
        memberships: list[int] = []
        for item in self.output_classes:
            if item.code == 0 and item.cdl_codes:
                raise ValueError("output class 0 cannot contain weak-label codes")
            memberships.extend(item.cdl_codes)
        if len(memberships) != len(set(memberships)):
            raise ValueError("classification weak-label codes overlap")
        if any(code in memberships for code in self.invalid_codes):
            raise ValueError("invalid CDL codes cannot enter classification mapping")
        if set(memberships) & set(self.excluded_valid_codes):
            raise ValueError("mapped and excluded CDL codes overlap")
        declared = set(CDL_CLASS_LABELS)
        accounted = set(memberships) | set(self.excluded_valid_codes)
        if accounted != declared:
            missing = sorted(declared - accounted)
            extra = sorted(accounted - declared)
            raise ValueError(
                f"classification mapping must account for every CDL class; "
                f"missing={missing}, extra={extra}"
            )

    @property
    def class_labels(self) -> Mapping[int, str]:
        return {item.code: item.name for item in self.output_classes}

    @property
    def mapped_cdl_codes(self) -> tuple[int, ...]:
        return tuple(
            sorted(
                code
                for item in self.output_classes
                if item.code != 0
                for code in item.cdl_codes
            )
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "fasterraster.classification-mapping/v1",
            "mapping_id": self.mapping_id,
            "contract_version": self.contract_version,
            "source_id": self.source_id,
            "source_semantic_type": self.source_semantic_type,
            "scientific_claim": self.scientific_claim,
            "output_classes": [item.as_dict() for item in self.output_classes],
            "excluded_valid_codes": list(self.excluded_valid_codes),
            "excluded_valid_labels": {
                str(code): CDL_CLASS_LABELS[code]
                for code in self.excluded_valid_codes
            },
            "invalid_codes": list(self.invalid_codes),
            "unknown_value_policy": self.unknown_value_policy,
        }

    @property
    def sha256(self) -> str:
        payload = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# Reviewed crop and managed-vegetation CDL classes. This is intentionally an
# explicit code set; classification must never rely on a numeric range shortcut.
_CROPLAND_CODES = (
    1, 2, 3, 4, 5, 6, 10, 11, 12, 13, 14,
    21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37,
    38, 39, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55,
    56, 57, 58, 59, 60, 66, 67, 68, 69, 70, 71, 72, 74, 75, 76, 77,
    204, 205, 206, 207, 208, 209, 210, 211, 212, 213, 214, 215, 216,
    217, 218, 219, 220, 221, 222, 223, 224, 225, 226, 227, 228, 229,
    230, 231, 232, 233, 234, 235, 236, 237, 238, 239, 240, 241, 242,
    243, 244, 245, 246, 247, 248, 249, 250, 254,
)


CDL_SURFACE_SUPERCLASSES = ClassificationMapping(
    mapping_id=CDL_SURFACE_SUPERCLASSES_MAPPING_ID,
    contract_version="1",
    source_id=USDA_CDL_SOURCE_ID,
    source_semantic_type="same_year_crop_focused_weak_supervision",
    scientific_claim=CLASSIFICATION_SCIENTIFIC_CLAIM,
    output_classes=(
        SurfaceClass(0, "unknown_or_uncertain", ()),
        SurfaceClass(1, "cropland", _CROPLAND_CODES),
        SurfaceClass(2, "fallow_or_barren", (61, 65, 131)),
        SurfaceClass(3, "developed_open_or_low", (82, 121, 122)),
        SurfaceClass(4, "developed_medium_or_high", (123, 124)),
        SurfaceClass(
            5,
            "noncrop_vegetation",
            (63, 64, 141, 142, 143, 152, 176),
        ),
        SurfaceClass(6, "water", (83, 111)),
    ),
    excluded_valid_codes=(87, 88, 92, 112, 190, 195),
    invalid_codes=(0, 81, 255),
    unknown_value_policy="map_to_output_class_0_and_exclude_from_training",
)


CLASSIFICATION_MAPPINGS = {
    CDL_SURFACE_SUPERCLASSES.mapping_id: CDL_SURFACE_SUPERCLASSES,
}


def classification_mapping(mapping_id: str) -> ClassificationMapping:
    try:
        return CLASSIFICATION_MAPPINGS[mapping_id]
    except KeyError as exc:
        raise ValueError(f"unknown classification mapping: {mapping_id}") from exc
