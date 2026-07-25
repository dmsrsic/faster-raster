from __future__ import annotations

from typing import Any, Mapping


class RawNaipEvidenceError(ValueError):
    """Raised when an acquired NAIP asset lacks the raw-band request contract."""


def validate_raw_naip_acquisition_evidence(
    manifest: Mapping[str, Any],
    *,
    requested_year: int,
) -> dict[str, Any]:
    """Fail closed unless acquisition proves a same-year, unrendered 4-band request."""
    errors: list[str] = []
    naip = manifest.get("naip")
    naip_evidence = naip if isinstance(naip, Mapping) else {}
    if naip_evidence.get("requested_year") != requested_year:
        errors.append("requested_year_mismatch")
    if int(naip_evidence.get("catalog_match_count") or 0) < 1:
        errors.append("same_year_naip_catalog_evidence_missing")

    raw_layers = [
        layer
        for layer in manifest.get("layers", [])
        if isinstance(layer, Mapping)
        and layer.get("name") == "naip_multispectral"
    ]
    if len(raw_layers) != 1:
        errors.append(f"raw_layer_count_{len(raw_layers)}_is_not_1")
    elif raw_layers[0].get("band_ids") != [0, 1, 2, 3]:
        errors.append("raw_layer_band_ids_are_not_0_1_2_3")

    export_requests = [
        request
        for request in manifest.get("requests", [])
        if isinstance(request, Mapping)
        and str(request.get("label", "")).startswith("naip_multispectral_")
    ]
    if not export_requests:
        errors.append("raw_export_request_receipts_missing")
    for request in export_requests:
        parameters = request.get("parameters")
        values = parameters if isinstance(parameters, Mapping) else {}
        if values.get("bandIds") != "0,1,2,3":
            errors.append("raw_export_request_band_ids_invalid")
        if "renderingRule" in values:
            errors.append("raw_export_request_contains_rendering_rule")
        if "mosaicRule" not in values:
            errors.append("raw_export_request_mosaic_evidence_missing")

    if errors:
        raise RawNaipEvidenceError(
            "raw four-band NAIP acquisition evidence failed: "
            + ", ".join(sorted(set(errors)))
        )
    return {
        "status": "PASS",
        "requested_year": requested_year,
        "same_year_catalog_match_count": int(
            naip_evidence["catalog_match_count"]
        ),
        "band_ids": [0, 1, 2, 3],
        "rendering_rule": None,
        "mosaic_rule_verified": True,
        "export_request_count": len(export_requests),
    }
