from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from faster_raster.cli import app
from faster_raster.execution_package import build_execution_package, validate_execution_dag
from faster_raster.harmonization_planner import read_harmonization_plan, write_harmonization_plan
from faster_raster.manifest import read_manifest, write_manifest
from faster_raster.scheduler_export import export_scheduler_package

runner = CliRunner()
PROJECT = Path('/home/dmsrsic/raster-work/projects/ohio_cdl_edges')
MANIFEST = PROJECT / 'manifests' / 'acquisition_manifest.jsonl'
PLAN = PROJECT / 'plans' / 'harmonization_plan.json'
GOLDEN = Path('/home/dmsrsic/raster-work/faster-raster/tests/golden')
PROFILE = Path('/home/dmsrsic/raster-work/faster-raster/configs/execution_profiles/default_hpc.yaml')
STAGES = ['fetch', 'validate_download', 'harmonize', 'inspect_output']


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def assert_package_files(out: Path) -> None:
    assert (out / 'execution_package.json').exists()
    assert (out / 'jobs.jsonl').exists()
    assert (out / 'cache_plan.json').exists()
    assert (out / 'failure_policy.json').exists()
    assert (out / 'execution_summary.md').exists()


def compile_package(tmp_path, manifest=MANIFEST, plan=PLAN, profile=None):
    out = tmp_path / 'package'
    package = build_execution_package(manifest_path=manifest, harmonization_path=plan, out_dir=out, execution_profile=profile)
    return out, package


def test_valid_ohio_cdl_package_generation(tmp_path):
    out, package = compile_package(tmp_path)
    assert package['validation_status']['overall'] == 'PASS'
    assert package['dag_validation']['status'] == 'PASS'
    assert package['request_count'] == 2
    assert package['total_job_count'] == 8
    assert package['adapter_counts'] == {'arcgis_imageserver': 2}
    assert package['stage_counts'] == {stage: 2 for stage in STAGES}
    assert_package_files(out)


def test_valid_generic_https_package_generation(tmp_path):
    manifest = GOLDEN / 'acquisition_manifest_prism_daily_zip.jsonl'
    plan = GOLDEN / 'harmonization_plan_prism_daily_zip.json'
    out, package = compile_package(tmp_path, manifest, plan)
    assert package['adapter_counts'] == {'generic_https_template': 1}
    assert package['total_job_count'] == 4
    assert_package_files(out)


def test_mixed_arcgis_generic_package_generation(tmp_path):
    rows = read_manifest(MANIFEST) + read_manifest(GOLDEN / 'acquisition_manifest_prism_daily_zip.jsonl')
    manifest = tmp_path / 'mixed.jsonl'
    write_manifest(rows, manifest)
    plan = read_harmonization_plan(PLAN)
    generic_plan = read_harmonization_plan(GOLDEN / 'harmonization_plan_prism_daily_zip.json')
    plan['inputs'] = sorted(plan['inputs'] + generic_plan['inputs'], key=lambda item: item['request_id'])
    plan_path = tmp_path / 'mixed_plan.json'
    write_harmonization_plan(plan, plan_path)
    _, package = compile_package(tmp_path, manifest, plan_path)
    assert package['request_count'] == 3
    assert package['total_job_count'] == 12
    assert package['adapter_counts'] == {'arcgis_imageserver': 2, 'generic_https_template': 1}


def test_full_four_stage_dag_generation(tmp_path):
    out, _ = compile_package(tmp_path)
    jobs = read_manifest(out / 'jobs.jsonl')
    for request_id in {job['request_id'] for job in jobs}:
        assert [job['stage'] for job in jobs if job['request_id'] == request_id] == STAGES


def test_dependency_correctness_and_no_cycles(tmp_path):
    out, package = compile_package(tmp_path)
    jobs = read_manifest(out / 'jobs.jsonl')
    report = validate_execution_dag(jobs)
    assert report['status'] == 'PASS'
    assert report['dependency_count'] == 6
    assert package['dependency_count'] == 6


def test_duplicate_job_id_rejection(tmp_path):
    out, _ = compile_package(tmp_path)
    jobs = read_manifest(out / 'jobs.jsonl')
    jobs[1]['job_id'] = jobs[0]['job_id']
    report = validate_execution_dag(jobs)
    assert report['status'] == 'FAIL'
    assert any('duplicate job_id' in error for error in report['errors'])


def test_missing_dependency_rejection(tmp_path):
    out, _ = compile_package(tmp_path)
    jobs = read_manifest(out / 'jobs.jsonl')
    jobs[1]['dependencies'] = ['missing_job']
    report = validate_execution_dag(jobs)
    assert report['status'] == 'FAIL'
    assert any('missing dependency' in error for error in report['errors'])


def test_invalid_stage_rejection(tmp_path):
    out, _ = compile_package(tmp_path)
    jobs = read_manifest(out / 'jobs.jsonl')
    jobs[0]['stage'] = 'bad_stage'
    report = validate_execution_dag(jobs)
    assert report['status'] == 'FAIL'
    assert any('invalid stage name' in error for error in report['errors'])


def test_orphan_harmonization_job_rejection(tmp_path):
    out, _ = compile_package(tmp_path)
    jobs = [job for job in read_manifest(out / 'jobs.jsonl') if job['stage'] != 'validate_download']
    report = validate_execution_dag(jobs)
    assert report['status'] == 'FAIL'
    assert any('orphan harmonization' in error or 'stages are invalid' in error for error in report['errors'])


def test_execution_package_byte_stability(tmp_path):
    left, _ = compile_package(tmp_path / 'a')
    right, _ = compile_package(tmp_path / 'b')
    for filename in ['execution_package.json', 'jobs.jsonl', 'cache_plan.json', 'failure_policy.json', 'execution_summary.md']:
        assert (left / filename).read_bytes() == (right / filename).read_bytes()


def test_cache_key_stability_and_extensions(tmp_path):
    out, _ = compile_package(tmp_path)
    cache_plan = read_json(out / 'cache_plan.json')
    keys = [entry['cache_key'] for entry in cache_plan['entries']]
    assert len(keys) == len(set(keys))
    assert cache_plan['extension_counts'] == {'.tiff': 2}

    prism_out, _ = compile_package(tmp_path / 'prism', GOLDEN / 'acquisition_manifest_prism_daily_zip.jsonl', GOLDEN / 'harmonization_plan_prism_daily_zip.json')
    assert read_json(prism_out / 'cache_plan.json')['extension_counts'] == {'.zip': 1}

    nlcd_out, _ = compile_package(tmp_path / 'nlcd', GOLDEN / 'acquisition_manifest_nlcd_aws_tile.jsonl', GOLDEN / 'harmonization_plan_nlcd_aws_tile.json')
    assert read_json(nlcd_out / 'cache_plan.json')['extension_counts'] == {'.tif': 1}


def test_execution_profile_overrides_applied(tmp_path):
    out, package = compile_package(tmp_path, profile=PROFILE)
    jobs = read_manifest(out / 'jobs.jsonl')
    assert package['execution_profile']['profile_id'] == 'default_hpc'
    assert [job for job in jobs if job['stage'] == 'harmonize'][0]['timeout_seconds'] == 7200
    assert [job for job in jobs if job['stage'] == 'fetch'][0]['resources']['cpus'] == 1


def test_slurm_export_files_created(tmp_path):
    package_dir, _ = compile_package(tmp_path)
    out = tmp_path / 'scheduler_slurm'
    summary = export_scheduler_package(package_dir, 'slurm', out)
    assert (out / 'slurm_array.sh').exists()
    assert (out / 'job_index.tsv').exists()
    assert (out / 'scheduler_summary.json').exists()
    assert (out / 'README.md').exists()
    assert summary['stage_counts'] == {stage: 2 for stage in STAGES}


def test_local_dry_run_export_files_created(tmp_path):
    package_dir, _ = compile_package(tmp_path)
    out = tmp_path / 'scheduler_local'
    summary = export_scheduler_package(package_dir, 'local-dry-run', out)
    assert (out / 'run_local_dry_run.sh').exists()
    assert (out / 'job_index.tsv').exists()
    assert (out / 'scheduler_summary.json').exists()
    assert (out / 'README.md').exists()
    assert summary['job_count'] == 8


def test_scheduler_summary_contains_job_stage_counts(tmp_path):
    package_dir, _ = compile_package(tmp_path)
    out = tmp_path / 'scheduler_slurm'
    export_scheduler_package(package_dir, 'slurm', out)
    summary = read_json(out / 'scheduler_summary.json')
    assert summary['job_count'] == 8
    assert summary['stage_counts'] == {stage: 2 for stage in STAGES}
    assert summary['dependency_count'] == 6


def test_compile_execution_package_no_network(monkeypatch, tmp_path):
    def fail_network(*args, **kwargs):
        raise AssertionError('network access attempted')
    monkeypatch.setattr('urllib.request.urlopen', fail_network)
    package_dir, _ = compile_package(tmp_path)
    export_scheduler_package(package_dir, 'slurm', tmp_path / 'scheduler')


def test_cli_compile_execution_package_json_output(tmp_path):
    out = tmp_path / 'package'
    result = runner.invoke(app, ['compile-execution-package', '--manifest', str(MANIFEST), '--harmonization', str(PLAN), '--out', str(out), '--json'])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload['status'] == 'PASS'
    assert payload['total_job_count'] == 8
    assert 'jobs_sha256' in payload
    assert_package_files(out)


def test_cli_export_scheduler_json_output(tmp_path):
    package_dir, _ = compile_package(tmp_path)
    out = tmp_path / 'scheduler'
    result = runner.invoke(app, ['export-scheduler', '--package', str(package_dir), '--scheduler', 'slurm', '--out', str(out), '--json'])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload['scheduler'] == 'slurm'
    assert payload['job_count'] == 8


def test_invalid_manifest_fails_before_writing_package(tmp_path):
    manifest = tmp_path / 'bad.jsonl'
    manifest.write_text('{bad json}\n', encoding='utf-8')
    out = tmp_path / 'package'
    try:
        build_execution_package(manifest_path=manifest, harmonization_path=PLAN, out_dir=out)
    except ValueError as exc:
        assert 'malformed JSONL' in str(exc)
    else:
        raise AssertionError('expected invalid manifest failure')
    assert not out.exists()


def test_invalid_harmonization_fails_before_writing_package(tmp_path):
    plan = tmp_path / 'bad.json'
    plan.write_text('{bad json}', encoding='utf-8')
    out = tmp_path / 'package'
    try:
        build_execution_package(manifest_path=MANIFEST, harmonization_path=plan, out_dir=out)
    except ValueError as exc:
        assert 'malformed JSON' in str(exc)
    else:
        raise AssertionError('expected invalid harmonization failure')
    assert not out.exists()


def test_duplicate_request_ids_fail_before_writing_package(tmp_path):
    rows = read_manifest(MANIFEST)
    rows[1]['request_id'] = rows[0]['request_id']
    manifest = tmp_path / 'dup.jsonl'
    write_manifest(rows, manifest)
    out = tmp_path / 'package'
    try:
        build_execution_package(manifest_path=manifest, harmonization_path=PLAN, out_dir=out)
    except ValueError as exc:
        assert 'duplicate request_id' in str(exc)
    else:
        raise AssertionError('expected duplicate request_id failure')
    assert not out.exists()


def test_example_job_row_shape(tmp_path):
    out, _ = compile_package(tmp_path)
    job = read_manifest(out / 'jobs.jsonl')[0]
    assert {
        'job_id', 'request_id', 'source_id', 'adapter', 'url', 'expected_input_path',
        'expected_cache_path', 'expected_output_path', 'stage', 'dependencies', 'retry_count',
        'timeout_seconds', 'max_bytes', 'semantic_type', 'resampling', 'target_grid_crs',
        'year', 'thematic_layer', 'tile_id', 'failure_policy_id'
    } <= set(job)
