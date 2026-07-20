from __future__ import annotations

import copy
from pathlib import Path

import pytest

from faster_raster.schemas import ResearchSpec
from faster_raster.source_registry import load_registry
from faster_raster.validation import load_spec


ROOT = Path(__file__).resolve().parent.parent
PROJECT_SPEC = ROOT / "tests" / "fixtures" / "ohio_cdl_edges" / "research_spec.json"


@pytest.fixture()
def project_spec_path() -> Path:
    return PROJECT_SPEC


@pytest.fixture()
def valid_spec() -> ResearchSpec:
    return load_spec(PROJECT_SPEC)


@pytest.fixture()
def valid_spec_raw(valid_spec: ResearchSpec) -> dict:
    return copy.deepcopy(valid_spec.model_dump())


@pytest.fixture()
def registry():
    return load_registry()
