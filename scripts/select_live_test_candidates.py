from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from faster_raster.user_toggles import effective_toggles, load_user_toggles, validate_user_toggles


def load_pack(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding='utf-8'))


def classify(entry: dict[str, Any], toggles: dict[str, Any]) -> str:
    limits = effective_toggles(toggles)['dip_limits']
    if entry['credential_requirement'] != 'none':
        return 'skip_for_now'
    if entry['max_bytes_recommended'] > limits['max_bytes_per_source']:
        return 'skip_for_now'
    if not entry.get('known_endpoint_or_catalog_url'):
        if entry['endpoint_status'] in {'blocked_by_adapter_design', 'verified_catalog_candidate'}:
            return 'adapter_needed'
        return 'docs_ready_endpoint_needed'
    if entry['recommended_probe_type'] in {'metadata_http_dip', 'catalog_http_dip'}:
        return 'live_ready_metadata'
    if entry['recommended_probe_type'] == 'range_byte_dip':
        return 'live_ready_range'
    return 'skip_for_now'


def select_candidates(pack: dict[str, Any], toggles: dict[str, Any], *, limit: int = 3) -> list[dict[str, Any]]:
    rows = []
    for entry in pack['endpoint_readiness']:
        result = classify(entry, toggles)
        rows.append({
            'source_id': entry['source_id'],
            'candidate_result_class': result,
            'score': entry['quality_candidate_score'],
            'endpoint_status': entry['endpoint_status'],
            'recommended_probe_type': entry['recommended_probe_type'],
            'live_test_safety': entry['live_test_safety'],
            'max_bytes_recommended': entry['max_bytes_recommended'],
            'next_exact_action': entry['next_exact_action'],
        })
    priority = {'live_ready_metadata': 0, 'live_ready_range': 1, 'docs_ready_endpoint_needed': 2, 'adapter_needed': 3, 'skip_for_now': 4}
    rows.sort(key=lambda row: (priority[row['candidate_result_class']], -row['score'], row['source_id']))
    return rows[:limit]


def write_reports(rows: list[dict[str, Any]], out: Path, markdown: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    markdown.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({'live_test_candidates': rows}, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    lines = ['# Live Test Candidates v0.5.3', '', '| Rank | Source | Class | Score | Next action |', '| ---: | --- | --- | ---: | --- |']
    for idx, row in enumerate(rows, 1):
        lines.append(f"| {idx} | `{row['source_id']}` | `{row['candidate_result_class']}` | {row['score']} | {row['next_exact_action']} |")
    markdown.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--pack', required=True)
    parser.add_argument('--toggles', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--markdown', required=True)
    args = parser.parse_args()
    toggles = load_user_toggles(Path(args.toggles))
    errors = validate_user_toggles(toggles)
    if errors:
        raise SystemExit('invalid toggles: ' + '; '.join(errors))
    rows = select_candidates(load_pack(Path(args.pack)), toggles)
    write_reports(rows, Path(args.out), Path(args.markdown))
    print(json.dumps({'candidate_count': len(rows), 'top': rows}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
