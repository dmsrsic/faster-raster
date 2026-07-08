from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from faster_raster.user_toggles import effective_toggles, load_user_toggles, validate_user_toggles

GOOD_PROVENANCE = {'official_primary', 'official_cloud_mirror', 'institutional_mirror'}
LOW_COMPLEXITY_MODES = {'static_https', 'parameterized_rest', 'arcgis_imageserver'}


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding='utf-8'))


def endpoint_present(entry: dict[str, Any]) -> bool:
    return bool(entry.get('endpoint_or_catalog_url')) and entry.get('endpoint_or_catalog_url') not in {'unknown', 'needs_official_verification'}


def candidate_status(entry: dict[str, Any]) -> str:
    if entry.get('credential_requirement') != 'none':
        return 'blocked_by_credentials'
    if not endpoint_present(entry):
        return 'blocked_by_endpoint_uncertainty'
    if not entry.get('bounded_probe_appropriate'):
        return 'blocked_by_unbounded_probe_policy'
    return 'ready_for_dry_review'


def score_entry(entry: dict[str, Any]) -> int:
    score = 0
    if entry.get('credential_requirement') == 'none': score += 40
    if entry.get('provenance_class') in GOOD_PROVENANCE: score += 25
    if entry.get('bounded_probe_appropriate'): score += 20
    if endpoint_present(entry): score += 15
    if entry.get('access_mode') in LOW_COMPLEXITY_MODES: score += 10
    if entry.get('access_pattern_category') == 'mirror_candidate': score -= 25
    if entry.get('promotion_status') == 'blocked_by_adapter': score += 5
    return score


def build_queue(atlas: dict[str, Any], unlocks: dict[str, Any], toggles: dict[str, Any]) -> list[dict[str, Any]]:
    effective = effective_toggles(toggles)
    scope = effective['source_scope']
    unlock_map = {row['source_id']: row for row in unlocks.get('unlock_plan', [])}
    rows = []
    for entry in atlas.get('sources', []):
        if entry.get('trust_level') == 'verified_live':
            continue
        if scope['no_auth_only'] and entry.get('credential_requirement') != 'none':
            continue
        if scope['official_or_institutional_only'] and entry.get('provenance_class') not in GOOD_PROVENANCE:
            continue
        if not scope['allow_mirror_candidates'] and entry.get('access_pattern_category') == 'mirror_candidate':
            continue
        unlock = unlock_map.get(entry['source_id'], {})
        status = candidate_status(entry)
        unlock_bonus = max(0, 100 - list(unlock_map).index(entry['source_id'])) if entry['source_id'] in unlock_map else 0
        if unlock.get('class') == 'adapter_next':
            unlock_bonus += 10
        rows.append({
            'source_id': entry['source_id'],
            'display_name': entry['display_name'],
            'provider': entry['provider'],
            'access_mode': entry['access_mode'],
            'provenance_class': entry['provenance_class'],
            'credential_requirement': entry['credential_requirement'],
            'bounded_probe_appropriate': entry['bounded_probe_appropriate'],
            'endpoint_present': endpoint_present(entry),
            'cook_status': status,
            'score': score_entry(entry) + unlock_bonus,
            'unlock_class': unlock.get('class'),
            'recommended_action': unlock.get('recommended_action') or 'verify endpoint and adapter policy',
        })
    rows.sort(key=lambda row: (-row['score'], row['source_id']))
    return rows[: int(scope.get('max_sources_per_run', 5))]


def write_reports(rows: list[dict[str, Any]], out: Path, markdown: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    markdown.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({'cook_queue': rows}, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    lines = ['# No-Auth Cook Queue', '', '| Rank | Sauce | Status | Score | Action |', '| ---: | --- | --- | ---: | --- |']
    for idx, row in enumerate(rows, 1):
        lines.append(f"| {idx} | `{row['source_id']}` | `{row['cook_status']}` | {row['score']} | {row['recommended_action']} |")
    markdown.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--atlas', required=True)
    parser.add_argument('--unlocks', required=True)
    parser.add_argument('--toggles', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--markdown', required=True)
    args = parser.parse_args()
    toggles = load_user_toggles(Path(args.toggles))
    errors = validate_user_toggles(toggles)
    if errors:
        raise SystemExit('invalid toggles: ' + '; '.join(errors))
    rows = build_queue(load_yaml(Path(args.atlas)), json.loads(Path(args.unlocks).read_text()), toggles)
    write_reports(rows, Path(args.out), Path(args.markdown))
    print(json.dumps({'queue_count': len(rows), 'top': rows[:5]}, sort_keys=True))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
