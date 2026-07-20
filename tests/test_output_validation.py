from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from faster_raster.cli import app
from faster_raster.manifest import read_manifest, write_manifest
from faster_raster.output_validation import validate_harmonization, validate_manifest
from faster_raster.harmonization_planner import read_harmonization_plan, write_harmonization_plan

runner = CliRunner()
ROOT = Path(__file__).resolve().parent.parent
PROJECT = ROOT / "tests" / "fixtures" / "ohio_cdl_edges"
MANIFEST = PROJECT / 'manifests' / 'acquisition_manifest.jsonl'
PLAN = PROJECT / 'plans' / 'harmonization_plan.json'


def test_valid_current_ohio_manifest_passes():
    report = validate_manifest(MANIFEST)
    assert report['status'] == 'PASS'
    assert report['row_count'] == 2
    assert report['error_count'] == 0


def test_valid_current_ohio_harmonization_passes_with_manifest():
    report = validate_harmonization(PLAN, MANIFEST)
    assert report['status'] == 'PASS'
    assert report['input_count'] == 2
    assert report['manifest_row_count'] == 2


def test_malformed_jsonl_fails(tmp_path):
    path = tmp_path / 'bad.jsonl'
    path.write_text('{"request_id": "ok"}\n{bad json}\n', encoding='utf-8')
    report = validate_manifest(path)
    assert report['status'] == 'FAIL'
    assert any('malformed JSONL' in error for error in report['errors'])


def test_duplicate_request_id_fails(tmp_path):
    rows = read_manifest(MANIFEST)
    rows[1]['request_id'] = rows[0]['request_id']
    path = tmp_path / 'manifest.jsonl'
    write_manifest(rows, path)
    report = validate_manifest(path)
    assert report['status'] == 'FAIL'
    assert any('duplicate request_id' in error for error in report['errors'])


def test_missing_url_fails(tmp_path):
    rows = read_manifest(MANIFEST)
    rows[0].pop('url')
    path = tmp_path / 'manifest.jsonl'
    write_manifest(rows, path)
    report = validate_manifest(path)
    assert report['status'] == 'FAIL'
    assert any('missing required field url' in error for error in report['errors'])


def test_invalid_crs_field_fails(tmp_path):
    rows = read_manifest(MANIFEST)
    rows[0]['target_grid_crs'] = '5070'
    path = tmp_path / 'manifest.jsonl'
    write_manifest(rows, path)
    report = validate_manifest(path)
    assert report['status'] == 'FAIL'
    assert any('target_grid_crs' in error and 'EPSG' in error for error in report['errors'])


def test_categorical_bilinear_rejected_in_manifest(tmp_path):
    rows = read_manifest(MANIFEST)
    rows[0]['resampling'] = 'bilinear'
    path = tmp_path / 'manifest.jsonl'
    write_manifest(rows, path)
    report = validate_manifest(path)
    assert report['status'] == 'FAIL'
    assert any('categorical raster cannot use bilinear' in error for error in report['errors'])


def test_manifest_to_plan_mismatch_fails(tmp_path):
    plan = read_harmonization_plan(PLAN)
    plan['inputs'] = plan['inputs'][:-1]
    plan_path = tmp_path / 'plan.json'
    write_harmonization_plan(plan, plan_path)
    report = validate_harmonization(plan_path, MANIFEST)
    assert report['status'] == 'FAIL'
    assert any('manifest request_id missing from harmonization plan' in error for error in report['errors'])


def test_cli_validate_manifest_json_output():
    result = runner.invoke(app, ['validate-manifest', str(MANIFEST), '--json'])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload['status'] == 'PASS'
    assert payload['row_count'] == 2


def test_cli_validate_harmonization_json_output():
    result = runner.invoke(app, ['validate-harmonization', str(PLAN), '--manifest', str(MANIFEST), '--json'])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload['status'] == 'PASS'
    assert payload['input_count'] == 2


def test_cli_nonzero_exit_on_invalid_manifest(tmp_path):
    path = tmp_path / 'bad.jsonl'
    path.write_text('{bad json}\n', encoding='utf-8')
    result = runner.invoke(app, ['validate-manifest', str(path)])
    assert result.exit_code != 0
    assert 'Manifest validation: FAIL' in result.output


def test_cli_nonzero_exit_on_invalid_harmonization(tmp_path):
    path = tmp_path / 'bad.json'
    path.write_text('{bad json}', encoding='utf-8')
    result = runner.invoke(app, ['validate-harmonization', str(path)])
    assert result.exit_code != 0
    assert 'malformed JSON' in result.output


def test_output_validators_do_not_use_network(monkeypatch):
    def fail_network(*args, **kwargs):
        raise AssertionError('network access attempted')

    monkeypatch.setattr('urllib.request.urlopen', fail_network)
    assert validate_manifest(MANIFEST)['status'] == 'PASS'
    assert validate_harmonization(PLAN, MANIFEST)['status'] == 'PASS'
