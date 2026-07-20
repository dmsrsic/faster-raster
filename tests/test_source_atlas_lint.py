from __future__ import annotations

from pathlib import Path

import yaml

from scripts import lint_source_atlas as lint

ROOT = Path(__file__).resolve().parent.parent
ATLAS = ROOT / "research" / "source_atlas_v0_4.yaml"


def test_source_atlas_parses_and_has_25_entries():
    atlas = lint.load_atlas(ATLAS)
    assert len(atlas['sources']) >= 25


def test_source_atlas_lints_clean():
    atlas = lint.load_atlas(ATLAS)
    assert lint.lint_atlas(atlas) == []


def test_linter_catches_credential_without_profile():
    atlas = lint.load_atlas(ATLAS)
    atlas['sources'][0]['credential_requirement'] = 'earthdata_login'
    atlas['sources'][0]['credential_profile_id'] = None
    findings = lint.lint_atlas(atlas)
    assert any('credentialed source missing credential_profile_id' in f['message'] for f in findings)


def test_linter_catches_direct_url_without_endpoint():
    atlas = lint.load_atlas(ATLAS)
    atlas['sources'][0]['deterministic_url_generation_direct'] = True
    atlas['sources'][0]['endpoint_or_catalog_url'] = None
    findings = lint.lint_atlas(atlas)
    assert any('direct URL generation requires endpoint' in f['message'] for f in findings)
