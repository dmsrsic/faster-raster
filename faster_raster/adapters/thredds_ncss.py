from __future__ import annotations

from typing import Any


class ThreddsNcssAdapter:
    """Experimental disabled-by-default THREDDS/NCSS request planner.

    This adapter is intentionally not registered in url_planner.ADAPTERS. It provides
    deterministic probe request descriptors for design/testing only and never performs
    network access.
    """

    adapter_name = "thredds_ncss"
    experimental = True
    runtime_enabled = False

    REQUIRED_TOP_LEVEL = {
        "source_id",
        "discovery_mechanism",
        "scenario",
        "probe_sequence",
        "planned_request_fields",
    }
    REQUIRED_SCENARIO = {"variable", "temporal_range", "bbox", "expected_format", "max_bytes"}
    REQUIRED_TEMPORAL_RANGE = {"start", "end"}
    REQUIRED_BBOX = {"values", "crs"}

    def validate_probe_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        for field in sorted(self.REQUIRED_TOP_LEVEL):
            if field not in config:
                errors.append(f"missing required field: {field}")
        if errors:
            return errors
        if config.get("discovery_mechanism") != "THREDDS_NCSS_QUERY":
            errors.append("discovery_mechanism must be THREDDS_NCSS_QUERY")
        if config.get("network_default") != "disabled":
            errors.append("network_default must be disabled")

        scenario = config.get("scenario") or {}
        for field in sorted(self.REQUIRED_SCENARIO):
            if field not in scenario:
                errors.append(f"scenario missing required field: {field}")
        temporal = scenario.get("temporal_range") or {}
        for field in sorted(self.REQUIRED_TEMPORAL_RANGE):
            if field not in temporal:
                errors.append(f"scenario.temporal_range missing required field: {field}")
        bbox = scenario.get("bbox") or {}
        for field in sorted(self.REQUIRED_BBOX):
            if field not in bbox:
                errors.append(f"scenario.bbox missing required field: {field}")
        if "values" in bbox:
            values = bbox["values"]
            if not isinstance(values, list) or len(values) != 4:
                errors.append("scenario.bbox.values must contain four numbers")
            elif not all(isinstance(value, (int, float)) for value in values):
                errors.append("scenario.bbox.values must contain four numbers")
            elif not (values[0] < values[2] and values[1] < values[3]):
                errors.append("scenario.bbox.values must be [west, south, east, north]")
        if scenario.get("max_bytes", 0) <= 0:
            errors.append("scenario.max_bytes must be positive")

        subset_steps = [step for step in config.get("probe_sequence", []) if step.get("mode") == "bounded_subset"]
        if not subset_steps:
            errors.append("probe_sequence must include a bounded_subset step")
        return errors

    def plan_probe_request(self, config: dict[str, Any]) -> dict[str, Any]:
        errors = self.validate_probe_config(config)
        if errors:
            raise ValueError("; ".join(errors))

        scenario = config["scenario"]
        bbox = scenario["bbox"]
        temporal = scenario["temporal_range"]
        request_fields = config["planned_request_fields"]
        subset_step = next(step for step in config["probe_sequence"] if step.get("mode") == "bounded_subset")
        params = dict(sorted((subset_step.get("params") or {}).items()))

        return {
            "request_id": request_fields.get("request_id", f"{config['source_id']}_{scenario['variable']}_{temporal['start']}_probe"),
            "source_id": config["source_id"],
            "adapter": self.adapter_name,
            "experimental": True,
            "runtime_enabled": False,
            "discovery_mechanism": "THREDDS_NCSS_QUERY",
            "access_pattern": config.get("access_pattern"),
            "credential_scope": config.get("credential_scope"),
            "method": request_fields.get("method", "GET"),
            "endpoint": request_fields.get("endpoint", subset_step.get("endpoint")),
            "params": params,
            "variables": [scenario["variable"]],
            "time_range": {
                "start": temporal["start"],
                "end": temporal["end"],
            },
            "bbox": {
                "values": bbox["values"],
                "crs": bbox["crs"],
            },
            "target_grid_crs": request_fields.get("target_grid_crs"),
            "semantic_type": scenario.get("semantic_type", "continuous"),
            "resampling": scenario.get("recommended_resampling", "bilinear"),
            "expected_format": scenario["expected_format"],
            "max_probe_bytes": scenario["max_bytes"],
            "status": "planned_experimental",
            "network_default": "disabled",
            "extraction": scenario.get("extraction", False),
        }
