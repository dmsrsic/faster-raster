from __future__ import annotations

import pytest
from pydantic import ValidationError

from faster_raster.schemas import ResearchSpec
from faster_raster.validation import validate_spec


def test_valid_research_spec_passes(valid_spec, registry):
    assert validate_spec(valid_spec, registry) == []


@pytest.mark.parametrize(
    ("path", "message"),
    [
        (["project", "id"], "project.id"),
        (["aoi", "path"], "aoi.path"),
        (["target_grid", "crs"], "target_grid.crs"),
        (["sources", 0, "registry_key"], "sources.0.registry_key"),
    ],
)
def test_required_schema_fields_fail(valid_spec_raw, path, message):
    cursor = valid_spec_raw
    for part in path[:-1]:
        cursor = cursor[part]
    del cursor[path[-1]]

    with pytest.raises(ValidationError) as excinfo:
        ResearchSpec.model_validate(valid_spec_raw)

    rendered = str(excinfo.value)
    assert message in rendered


@pytest.mark.parametrize("resampling", ["bilinear", "cubic", "lanczos"])
def test_invalid_categorical_resampling_fails(valid_spec_raw, registry, resampling):
    valid_spec_raw["sources"][0]["resampling"] = resampling
    spec = ResearchSpec.model_validate(valid_spec_raw)

    errors = validate_spec(spec, registry)

    assert any(resampling in error for error in errors)


def test_years_are_normalized_deterministically_in_planning(valid_spec_raw, registry, project_spec_path):
    from faster_raster.url_planner import plan_urls

    valid_spec_raw["sources"][0]["years"] = [2024, 2023]
    spec = ResearchSpec.model_validate(valid_spec_raw)

    rows = plan_urls(spec, registry, project_spec_path)

    assert [row["year"] for row in rows] == [2023, 2024]


def test_duplicate_years_fail_schema(valid_spec_raw):
    valid_spec_raw["sources"][0]["years"] = [2023, 2023]

    with pytest.raises(ValidationError) as excinfo:
        ResearchSpec.model_validate(valid_spec_raw)

    assert "years must be unique" in str(excinfo.value)