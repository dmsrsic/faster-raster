from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from faster_raster.user_toggles import load_user_toggles, validate_user_toggles


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding='utf-8'))


def find_source(atlas: dict[str, Any], source_id: str) -> dict[str, Any]:
    for entry in atlas.get('sources', []):
        if entry.get('source_id') == source_id:
            return entry
    raise ValueError(f'unknown source_id: {source_id}')


def latest_probe(source_id: str, reports_dir: Path) -> dict[str, Any] | None:
    path = reports_dir / f'atlas_probe_{source_id}.json'
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return None


def expected_adapter(entry: dict[str, Any]) -> str:
    mode = entry.get('access_mode')
    if mode in {'static_https', 'mirror_https'}: return 'generic_https_template'
    if mode == 'arcgis_imageserver': return 'arcgis_imageserver'
    if mode in {'opendap', 'thredds_catalog', 'thredds_ncss'}: return 'metadata_probe_adapter_future'
    if mode == 'grib_filter': return 'grib_filter_metadata_adapter_future'
    if mode in {'stac_api', 'cmr_api', 'odata_api'}: return f'{mode}_adapter_future'
    return 'adapter_research_required'


def decision(entry: dict[str, Any], probe: dict[str, Any] | None) -> str:
    if entry.get('credential_requirement') != 'none':
        return 'not_ready'
    if not entry.get('endpoint_or_catalog_url'):
        return 'not_ready'
    if probe and (probe.get('probe') or {}).get('result_class') in {'pass_verified', 'pass_partial_content_verified'}:
        return 'ready_for_experimental_adapter'
    return 'not_ready'


def build_proposal(source_id: str, atlas: dict[str, Any], toggles: dict[str, Any], reports_dir: Path) -> dict[str, Any]:
    source = find_source(atlas, source_id)
    probe = latest_probe(source_id, reports_dir)
    dec = decision(source, probe)
    return {
        'source_id': source_id,
        'current_status': source.get('promotion_status'),
        'promotion_decision': dec,
        'required_evidence': ['verified bounded metadata/capability probe', 'deterministic URL or asset resolution contract', 'golden fixture', 'offline tests'],
        'endpoint_status': 'present' if source.get('endpoint_or_catalog_url') else 'missing_or_unknown',
        'credential_status': source.get('credential_requirement'),
        'probe_evidence': probe,
        'expected_adapter_type': expected_adapter(source),
        'files_that_would_need_changes': ['configs/source_registry.yaml', 'faster_raster/adapters/', 'tests/golden/', 'docs/ADAPTERS.md'],
        'tests_required': ['capability validation', 'URL/metadata determinism', 'golden byte stability', 'no-network unit test'],
        'forbidden_edits_performed': [],
        'proposal_only': True,
        'runtime_registry_edit_forbidden': toggles['promotion_policy']['forbid_runtime_registry_edit'],
    }


def write_reports(proposal: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{proposal['source_id']}_promotion_proposal.json"
    md_path = out_dir / f"{proposal['source_id']}_promotion_proposal.md"
    json_path.write_text(json.dumps(proposal, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    lines = ['# Adapter Promotion Proposal', '', f"- Source: `{proposal['source_id']}`", f"- Decision: `{proposal['promotion_decision']}`", f"- Endpoint status: `{proposal['endpoint_status']}`", f"- Credential status: `{proposal['credential_status']}`", f"- Expected adapter: `{proposal['expected_adapter_type']}`", '', 'No runtime registry files were edited.']
    md_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-id', required=True)
    parser.add_argument('--atlas', required=True)
    parser.add_argument('--toggles', required=True)
    parser.add_argument('--reports-dir', default='reports')
    parser.add_argument('--out-dir', required=True)
    args = parser.parse_args()
    toggles = load_user_toggles(Path(args.toggles))
    errors = validate_user_toggles(toggles)
    if errors:
        raise SystemExit('invalid toggles: ' + '; '.join(errors))
    proposal = build_proposal(args.source_id, load_yaml(Path(args.atlas)), toggles, Path(args.reports_dir))
    write_reports(proposal, Path(args.out_dir))
    print(json.dumps({'source_id': args.source_id, 'promotion_decision': proposal['promotion_decision']}, sort_keys=True))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
