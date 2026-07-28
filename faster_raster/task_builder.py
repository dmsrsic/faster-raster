from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

TASKS_DIR = Path('tasks')
TASK_REPORTS_DIR = Path('reports/task_builder')
TASK_PREVIEWS_DIR = Path('reports/task_previews')
TASK_ID_RE = re.compile(r'^[a-z0-9][a-z0-9_-]*$')
SECRET_RE = re.compile(r'(?i)(token|password|secret|api[_-]?key|bearer)\s*[:=]\s*\S+')


def parse_bbox(text: str) -> list[float]:
    parts = [part.strip() for part in text.split(',')]
    if len(parts) != 4:
        raise ValueError('bbox must contain four comma-separated numeric values')
    return [float(part) for part in parts]


def parse_years(text: str | None) -> list[int]:
    if not text:
        return []
    return [int(part.strip()) for part in text.split(',') if part.strip()]


def parse_dates(text: str | None) -> list[str]:
    if not text:
        return []
    return [part.strip() for part in text.split(',') if part.strip()]


def task_path(task_id: str) -> Path:
    return TASKS_DIR / f'{task_id}.yaml'


def default_task(task_id: str, name: str, bbox: list[float], bbox_crs: str, target_crs: str, years: list[int], themes: list[str], sources: list[str], description: str | None = None, *, resolution_m: int | float = 30, dates: list[str] | None = None) -> dict[str, Any]:
    return {
        'task_id': task_id,
        'name': name,
        'description': description or 'Local planning task for semantic raster stack preview.',
        'aoi': {'bbox': bbox, 'bbox_crs': bbox_crs},
        'target_grid': {'crs': target_crs, 'resolution_m': resolution_m},
        'time': {'years': years, 'dates': dates or []},
        'themes': themes,
        'sources': sources,
        'preview': {'color_scheme': 'default', 'open_after_create': False},
        'notes': [],
    }


def deterministic_yaml(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False)


def save_task(task: dict[str, Any]) -> Path:
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    path = task_path(task['task_id'])
    path.write_text(deterministic_yaml(task), encoding='utf-8')
    return path


def load_task(task_id: str) -> dict[str, Any]:
    path = task_path(task_id)
    if not path.exists():
        raise FileNotFoundError(f'task not found: {task_id}')
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError(f'task file must be a mapping: {path}')
    return data


def walk_values(value: Any):
    if isinstance(value, dict):
        for item in value.values():
            yield from walk_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_values(item)
    else:
        yield value


def validate_task(task: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    task_id = str(task.get('task_id') or '')
    if not task_id or not TASK_ID_RE.match(task_id):
        errors.append('task_id must be a nonempty slug')
    if not task.get('name'):
        errors.append('name is required')
    bbox = (task.get('aoi') or {}).get('bbox')
    if not isinstance(bbox, list) or len(bbox) != 4 or not all(isinstance(x, (int, float)) for x in bbox):
        errors.append('aoi.bbox must have four numeric values')
    elif not (bbox[0] < bbox[2] and bbox[1] < bbox[3]):
        errors.append('aoi.bbox min values must be less than max values')
    if not (task.get('aoi') or {}).get('bbox_crs'):
        errors.append('aoi.bbox_crs is required')
    grid = task.get('target_grid') or {}
    if not grid.get('crs'):
        errors.append('target_grid.crs is required')
    if 'resolution_m' in grid and grid.get('resolution_m') is not None and float(grid['resolution_m']) <= 0:
        errors.append('target_grid.resolution_m must be positive')
    years = (task.get('time') or {}).get('years') or []
    if years != sorted(set(years)) or not all(isinstance(year, int) for year in years):
        errors.append('time.years must be sorted unique integers')
    dates = (task.get('time') or {}).get('dates') or []
    if not all(isinstance(date, str) for date in dates):
        errors.append('time.dates must be strings')
    if not task.get('sources') and not task.get('themes'):
        errors.append('at least one source or theme is required')
    for value in walk_values(task):
        if isinstance(value, str) and SECRET_RE.search(value):
            errors.append('task contains a secret-looking value')
            break
    return errors


def list_tasks() -> list[dict[str, Any]]:
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for path in sorted(TASKS_DIR.glob('*.yaml')):
        try:
            task = yaml.safe_load(path.read_text(encoding='utf-8'))
            rows.append({'task_id': task.get('task_id', path.stem), 'name': task.get('name', ''), 'path': str(path), 'sources': len(task.get('sources') or []), 'themes': len(task.get('themes') or [])})
        except Exception:
            rows.append({'task_id': path.stem, 'name': '<invalid>', 'path': str(path), 'sources': 0, 'themes': 0})
    return rows


def task_summary(task: dict[str, Any]) -> dict[str, Any]:
    from faster_raster.stack_preview import build_preview_summary
    return build_preview_summary(task)


def write_task_reports(task: dict[str, Any]) -> dict[str, Any]:
    summary = task_summary(task)
    errors = validate_task(task)
    report = {
        'task_id': task['task_id'],
        'name': task.get('name'),
        'path': str(task_path(task['task_id'])),
        'source_count': len(task.get('sources') or []),
        'theme_count': len(task.get('themes') or []),
        'bbox': (task.get('aoi') or {}).get('bbox'),
        'bbox_crs': (task.get('aoi') or {}).get('bbox_crs'),
        'target_crs': (task.get('target_grid') or {}).get('crs'),
        'years': (task.get('time') or {}).get('years') or [],
        'dates': (task.get('time') or {}).get('dates') or [],
        'validation_status': 'PASS' if not errors else 'FAIL',
        'validation_errors': errors,
        'warnings': summary['warnings'],
        'output_artifacts': summary['output_artifacts'],
    }
    TASK_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = TASK_REPORTS_DIR / f"{task['task_id']}_task.json"
    md_path = TASK_REPORTS_DIR / f"{task['task_id']}_task.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    lines = [
        f"# Task {task['task_id']}", '',
        f"- Name: {task.get('name')}",
        f"- Validation status: `{report['validation_status']}`",
        f"- AOI bbox: `{report['bbox']}`",
        f"- AOI CRS: `{report['bbox_crs']}`",
        f"- Target CRS: `{report['target_crs']}`",
        f"- Years: `{report['years']}`",
        f"- Dates: `{report['dates']}`",
        f"- Themes: `{', '.join(task.get('themes') or [])}`",
        '', '## Sources',
    ]
    for layer in summary['layers']:
        lines.append(f"- `{layer['source_id']}`: `{layer['status']}`")
    if summary['warnings']:
        lines.extend(['', '## Warnings'])
        lines.extend(f"- {warning}" for warning in summary['warnings'])
    lines.extend(['', '## Next commands', f"```bash\nfaster-raster task validate {task['task_id']} --plain\nfaster-raster task preview {task['task_id']} --plain\n```"])
    md_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return report


def create_preview(task: dict[str, Any], open_after_create: bool = False) -> dict[str, Any]:
    from faster_raster.stack_preview import create_preview as _create_preview
    return _create_preview(task, open_after_create=open_after_create)


def open_preview(path: Path) -> str:
    from faster_raster.stack_preview import open_preview as _open_preview
    return _open_preview(path)


def render_task_plain(summary: dict[str, Any]) -> str:
    lines = [
        f"task_id: {summary['task_id']}",
        f"name: {summary.get('name')}",
        f"bbox: {summary['aoi']['bbox']}",
        f"bbox_crs: {summary['aoi']['bbox_crs']}",
        f"target_crs: {summary['target_grid']['crs']}",
        f"resolution_m: {summary['target_grid'].get('resolution_m')}",
        f"years: {summary['time'].get('years', [])}",
        f"dates: {summary['time'].get('dates', [])}",
        f"themes: {', '.join(summary['themes'])}",
        f"static_range_adapter_available: {summary.get('static_range_adapter_available', False)}",
        f"static_range_wave1_available_sources: {summary.get('static_range_wave1_available_sources', [])}",
        f"static_range_wave1_fixture_sources: {summary.get('static_range_wave1_fixture_sources', [])}",
        f"static_range_wave1_missing_sources: {summary.get('static_range_wave1_missing_sources', [])}",
        'layers:',
    ]
    for layer in summary['layers']:
        lines.append(f"  - {layer['source_id']} status={layer['status']} color={layer['color']}")
    if summary['warnings']:
        lines.append('warnings:')
        lines.extend(f"  - {warning}" for warning in summary['warnings'])
    return '\n'.join(lines) + '\n'
