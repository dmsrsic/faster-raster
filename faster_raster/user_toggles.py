from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

DEFAULT_TOGGLES = Path('configs/user_toggles.example.yaml')
def _normalize_scalar(value):
    return value


def _normalize_toggles(data):
    if isinstance(data, dict):
        return {key: _normalize_toggles(value) for key, value in data.items()}
    if isinstance(data, list):
        return [_normalize_toggles(value) for value in data]
    return _normalize_scalar(data)


SECRET_RE = re.compile(r'(?i)(token|password|secret|apikey|api_key|bearer)[:=][A-Za-z0-9_./+=-]{8,}')


def load_user_toggles(path: Path = DEFAULT_TOGGLES) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('user toggles must be a mapping')
    return _normalize_toggles(data)


def effective_toggles(data: dict[str, Any]) -> dict[str, Any]:
    return {
        'lingo_mode': data['lingo_mode']['default'],
        'network_mode': data['network_mode']['default'],
        'source_scope': dict(data['source_scope']),
        'dip_limits': dict(data['dip_limits']),
        'promotion_policy': {
            'mode': data['promotion_policy']['default'],
            'forbid_runtime_registry_edit': data['promotion_policy']['forbid_runtime_registry_edit'],
        },
        'safety': dict(data['safety']),
        'output': dict(data['output']),
    }


def walk_values(value: Any, prefix: str = ''):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from walk_values(item, f'{prefix}.{key}' if prefix else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_values(item, f'{prefix}[{index}]')
    else:
        yield prefix, value


def validate_user_toggles(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = ['lingo_mode', 'network_mode', 'source_scope', 'dip_limits', 'promotion_policy', 'safety', 'output']
    for key in required:
        if key not in data:
            errors.append(f'missing required section: {key}')
    for section in ['lingo_mode', 'network_mode']:
        if section in data:
            default = data[section].get('default')
            allowed = data[section].get('allowed', [])
            if default not in allowed:
                errors.append(f'{section}.default must be in allowed')
    if 'promotion_policy' in data:
        default = data['promotion_policy'].get('default')
        allowed = data['promotion_policy'].get('allowed', [])
        if default not in allowed:
            errors.append('promotion_policy.default must be in allowed')
        if data['promotion_policy'].get('forbid_runtime_registry_edit') is not True:
            errors.append('promotion_policy must forbid runtime registry edits')
    if 'safety' in data:
        for key in ['require_allow_network_for_live', 'forbid_secret_values', 'forbid_unbounded_downloads', 'forbid_extraction', 'fail_closed_on_unknown_endpoint']:
            if data['safety'].get(key) is not True:
                errors.append(f'safety.{key} must be true')
    if 'dip_limits' in data:
        if int(data['dip_limits'].get('max_bytes_per_source', 0)) <= 0:
            errors.append('dip_limits.max_bytes_per_source must be positive')
        if data['dip_limits'].get('allow_binary_preview') is not False:
            errors.append('dip_limits.allow_binary_preview must be false')
    for path, value in walk_values(data):
        if isinstance(value, str) and SECRET_RE.search(value):
            errors.append(f'raw secret-like value rejected at {path}')
    return errors


def write_effective_reports(data: dict[str, Any], out_json: Path, out_md: Path) -> dict[str, Any]:
    effective = effective_toggles(data)
    errors = validate_user_toggles(data)
    report = {'status': 'PASS' if not errors else 'FAIL', 'errors': errors, 'effective_toggles': effective}
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    lines = ['# User Toggles Effective', '', f"- Status: `{report['status']}`", f"- Lingo mode: `{effective['lingo_mode']}`", f"- Network mode: `{effective['network_mode']}`", f"- No-auth only: `{effective['source_scope']['no_auth_only']}`", f"- Max bytes/source: `{effective['dip_limits']['max_bytes_per_source']}`", f"- Promotion policy: `{effective['promotion_policy']['mode']}`"]
    out_md.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return report
