from __future__ import annotations

import json
import zlib
from datetime import datetime, timezone
import struct
import subprocess
from pathlib import Path
from typing import Any

from faster_raster import cli_models as models
from faster_raster.task_builder import TASK_REPORTS_DIR, TASK_PREVIEWS_DIR, task_path

LAYER_COLORS = [
    [72, 136, 204],
    [82, 168, 83],
    [236, 174, 73],
    [190, 93, 93],
    [139, 108, 190],
    [80, 170, 170],
]
STATUS_COLORS = {
    'verified_now': [47, 143, 86],
    'reused_existing_result': [66, 133, 190],
    'credential_gated': [214, 159, 46],
    'adapter_needed': [63, 169, 187],
    'mirror_candidate': [178, 93, 174],
    'future_unverified': [130, 130, 130],
    'blocked': [190, 73, 73],
    'failed_probe': [190, 73, 73],
    'skipped_policy': [150, 150, 150],
    'unknown': [120, 120, 120],
}

FONT = {
    'A':['01110','10001','10001','11111','10001','10001','10001'], 'B':['11110','10001','10001','11110','10001','10001','11110'],
    'C':['01111','10000','10000','10000','10000','10000','01111'], 'D':['11110','10001','10001','10001','10001','10001','11110'],
    'E':['11111','10000','10000','11110','10000','10000','11111'], 'F':['11111','10000','10000','11110','10000','10000','10000'],
    'G':['01111','10000','10000','10011','10001','10001','01111'], 'H':['10001','10001','10001','11111','10001','10001','10001'],
    'I':['11111','00100','00100','00100','00100','00100','11111'], 'J':['11111','00010','00010','00010','00010','10010','01100'],
    'K':['10001','10010','10100','11000','10100','10010','10001'], 'L':['10000','10000','10000','10000','10000','10000','11111'],
    'M':['10001','11011','10101','10101','10001','10001','10001'], 'N':['10001','11001','10101','10011','10001','10001','10001'],
    'O':['01110','10001','10001','10001','10001','10001','01110'], 'P':['11110','10001','10001','11110','10000','10000','10000'],
    'Q':['01110','10001','10001','10001','10101','10010','01101'], 'R':['11110','10001','10001','11110','10100','10010','10001'],
    'S':['01111','10000','10000','01110','00001','00001','11110'], 'T':['11111','00100','00100','00100','00100','00100','00100'],
    'U':['10001','10001','10001','10001','10001','10001','01110'], 'V':['10001','10001','10001','10001','10001','01010','00100'],
    'W':['10001','10001','10001','10101','10101','10101','01010'], 'X':['10001','10001','01010','00100','01010','10001','10001'],
    'Y':['10001','10001','01010','00100','00100','00100','00100'], 'Z':['11111','00001','00010','00100','01000','10000','11111'],
    '0':['01110','10001','10011','10101','11001','10001','01110'], '1':['00100','01100','00100','00100','00100','00100','01110'],
    '2':['01110','10001','00001','00010','00100','01000','11111'], '3':['11110','00001','00001','01110','00001','00001','11110'],
    '4':['00010','00110','01010','10010','11111','00010','00010'], '5':['11111','10000','10000','11110','00001','00001','11110'],
    '6':['01110','10000','10000','11110','10001','10001','01110'], '7':['11111','00001','00010','00100','01000','01000','01000'],
    '8':['01110','10001','10001','01110','10001','10001','01110'], '9':['01110','10001','10001','01111','00001','00001','01110'],
    ' ':['00000','00000','00000','00000','00000','00000','00000'], '-':['00000','00000','00000','11111','00000','00000','00000'],
    '_':['00000','00000','00000','00000','00000','00000','11111'], ':':['00000','00100','00100','00000','00100','00100','00000'],
    '.':['00000','00000','00000','00000','00000','01100','01100'], '/':['00001','00010','00010','00100','01000','01000','10000'],
    ',':['00000','00000','00000','00000','00100','00100','01000'], '[':['01110','01000','01000','01000','01000','01000','01110'],
    ']':['01110','00010','00010','00010','00010','00010','01110'], '+':['00000','00100','00100','11111','00100','00100','00000'],
}


def atlas_status(source_id: str) -> dict[str, Any]:
    try:
        sources = models.load_sources(models.DEFAULT_ATLAS)
        matrix = models.load_matrix(models.DEFAULT_MATRIX) if models.DEFAULT_MATRIX.exists() else []
        source = models.source_by_id(sources, source_id)
        status = models.source_status(source, models.matrix_by_source(matrix).get(source_id))
        return {'status': status, 'provider': source.get('provider'), 'display_name': source.get('display_name')}
    except Exception:
        return {'status': 'unknown', 'provider': 'unknown', 'display_name': source_id}


def build_preview_summary(task: dict[str, Any]) -> dict[str, Any]:
    from faster_raster.adapters.static_http_range import static_range_availability

    layers = []
    for idx, source_id in enumerate(task.get('sources') or []):
        meta = atlas_status(source_id)
        layers.append({
            'source_id': source_id,
            'status': meta['status'],
            'provider': meta['provider'],
            'display_name': meta['display_name'],
            'color': LAYER_COLORS[idx % len(LAYER_COLORS)],
        })
    warnings = []
    for layer in layers:
        if layer['status'] not in {'verified_now', 'reused_existing_result'}:
            warnings.append(f"{layer['source_id']} is {layer['status']}")
    availability = static_range_availability(task.get('sources') or [])
    return {
        'task_id': task['task_id'],
        'name': task.get('name'),
        'aoi': task.get('aoi'),
        'target_grid': task.get('target_grid'),
        'time': task.get('time'),
        'themes': task.get('themes') or [],
        'layers': layers,
        'warnings': warnings,
        'source_count': len(task.get('sources') or []),
        'theme_count': len(task.get('themes') or []),
        'network_needed': False,
        **availability,
        'output_artifacts': {
            'task_yaml': str(task_path(task['task_id'])),
            'task_json': str(TASK_REPORTS_DIR / f"{task['task_id']}_task.json"),
            'task_md': str(TASK_REPORTS_DIR / f"{task['task_id']}_task.md"),
            'preview_png': str(TASK_PREVIEWS_DIR / f"{task['task_id']}_stack_preview.png"),
            'preview_json': str(TASK_PREVIEWS_DIR / f"{task['task_id']}_stack_preview.json"),
            'preview_md': str(TASK_PREVIEWS_DIR / f"{task['task_id']}_stack_preview.md"),
        },
    }


def task_summary(task: dict[str, Any]) -> dict[str, Any]:
    return build_preview_summary(task)


def _blank(width: int, height: int, color: list[int]) -> bytearray:
    return bytearray(color * width * height)


def _set_px(img: bytearray, width: int, height: int, x: int, y: int, color: list[int]) -> None:
    if 0 <= x < width and 0 <= y < height:
        i = (y * width + x) * 3
        img[i:i+3] = bytes(color)


def _rect(img: bytearray, width: int, height: int, x: int, y: int, w: int, h: int, color: list[int]) -> None:
    for yy in range(y, y + h):
        for xx in range(x, x + w):
            _set_px(img, width, height, xx, yy, color)


def _border(img: bytearray, width: int, height: int, x: int, y: int, w: int, h: int, color: list[int], t: int = 2) -> None:
    _rect(img, width, height, x, y, w, t, color)
    _rect(img, width, height, x, y + h - t, w, t, color)
    _rect(img, width, height, x, y, t, h, color)
    _rect(img, width, height, x + w - t, y, t, h, color)


def _text(img: bytearray, width: int, height: int, x: int, y: int, text: str, color: list[int], scale: int = 2) -> None:
    cx = x
    for ch in text.upper():
        glyph = FONT.get(ch, FONT[' '])
        for gy, row in enumerate(glyph):
            for gx, bit in enumerate(row):
                if bit == '1':
                    _rect(img, width, height, cx + gx * scale, y + gy * scale, scale, scale, color)
        cx += 6 * scale
        if cx > width - 10:
            return


def _write_png(path: Path, width: int, height: int, img: bytearray) -> None:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    raw = b''.join(b'\x00' + bytes(img[y * width * 3:(y + 1) * width * 3]) for y in range(height))
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b'')
    path.write_bytes(png)


def create_preview(task: dict[str, Any], open_after_create: bool = False) -> dict[str, Any]:
    summary = build_preview_summary(task)
    from faster_raster.task_builder import validate_task
    errors = validate_task(task)
    if errors:
        raise ValueError('invalid task: ' + '; '.join(errors))
    TASK_PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    png_path = TASK_PREVIEWS_DIR / f"{task['task_id']}_stack_preview.png"
    json_path = TASK_PREVIEWS_DIR / f"{task['task_id']}_stack_preview.json"
    md_path = TASK_PREVIEWS_DIR / f"{task['task_id']}_stack_preview.md"
    width, height = 1100, 720
    img = _blank(width, height, [248, 250, 252])
    _rect(img, width, height, 0, 0, width, 70, [35, 52, 72])
    _text(img, width, height, 28, 22, f"FASTER RASTER STACK PREVIEW {task['task_id']}", [255, 255, 255], 2)
    _border(img, width, height, 44, 115, 420, 420, [57, 85, 120], 3)
    _rect(img, width, height, 84, 160, 340, 300, [223, 232, 240])
    _border(img, width, height, 145, 215, 205, 135, [66, 133, 190], 4)
    _text(img, width, height, 105, 480, 'STUDY AOI FRAME', [35, 52, 72], 2)
    y = 112
    lines = [
        f"NAME: {task.get('name', '')}",
        f"BBOX: {task['aoi']['bbox']}",
        f"BBOX CRS: {task['aoi']['bbox_crs']}",
        f"TARGET CRS: {task['target_grid']['crs']}",
        f"RESOLUTION M: {task['target_grid'].get('resolution_m')}",
        f"YEARS: {task.get('time', {}).get('years', [])}",
        f"THEMES: {', '.join(task.get('themes') or [])}",
    ]
    for line in lines:
        _text(img, width, height, 500, y, line[:72], [35, 52, 72], 2)
        y += 34
    y += 14
    _text(img, width, height, 500, y, 'LAYERS', [35, 52, 72], 2)
    y += 34
    for idx, layer in enumerate(summary['layers']):
        color = layer['color']
        status_color = STATUS_COLORS.get(layer['status'], STATUS_COLORS['unknown'])
        _rect(img, width, height, 500, y, 24, 24, color)
        _rect(img, width, height, 530, y, 24, 24, status_color)
        _text(img, width, height, 565, y + 3, f"{layer['source_id']} {layer['status']}", [35, 52, 72], 2)
        y += 38
    if summary['warnings']:
        y += 8
        _text(img, width, height, 500, y, 'WARNINGS LOCKS PROVISIONAL', [170, 80, 50], 2)
        y += 34
        for warning in summary['warnings'][:4]:
            _text(img, width, height, 500, y, warning[:66], [170, 80, 50], 2)
            y += 30
    generated_at_utc = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    _text(img, width, height, 44, 646, 'LEGEND VERIFIED_NOW VERIFIED_EVIDENCE ADAPTER_NEEDED PROVISIONAL LOCKED PLANNED', [57, 85, 120], 1)
    _text(img, width, height, 44, 678, f'NETWORK: FALSE  UTC: {generated_at_utc}  ARTIFACT: {png_path.name}', [35, 52, 72], 1)
    _write_png(png_path, width, height, img)
    preview_report = {
        'task_id': task['task_id'],
        'generated_at_utc': generated_at_utc,
        'png_path': str(png_path),
        'md_path': str(md_path),
        'source_count': summary['source_count'],
        'theme_count': summary['theme_count'],
        'bbox': task['aoi']['bbox'],
        'bbox_crs': task['aoi']['bbox_crs'],
        'target_crs': task['target_grid']['crs'],
        'resolution_m': task['target_grid'].get('resolution_m'),
        'years': task.get('time', {}).get('years', []),
        'dates': task.get('time', {}).get('dates', []),
        'layers': summary['layers'],
        'warnings': summary['warnings'],
        'network_run': False,
        'static_range_adapter_available': summary['static_range_adapter_available'],
        'static_range_wave1_available_sources': summary['static_range_wave1_available_sources'],
        'static_range_wave1_fixture_sources': summary.get('static_range_wave1_fixture_sources', []),
        'static_range_wave1_missing_sources': summary['static_range_wave1_missing_sources'],
        'preview_json': str(json_path),
        'preview_md': str(md_path),
        'preview_png': str(png_path),
    }
    json_path.write_text(json.dumps(preview_report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    md_lines = [f"# Stack Preview {task['task_id']}", '', f"- PNG: `{png_path}`", f"- Network run: `False`", f"- Generated UTC: `{generated_at_utc}`", f"- Layers: `{len(summary['layers'])}`", '', '## Layer table']
    md_lines.insert(md_lines.index('## Layer table') + 1, '| Source | Status | Color |')
    md_lines.insert(md_lines.index('## Layer table') + 2, '| --- | --- | --- |')
    for layer in summary['layers']:
        md_lines.append(f"| `{layer['source_id']}` | `{layer['status']}` | `{layer['color']}` |")
    if summary['warnings']:
        md_lines.extend(['', '## Warnings'])
        md_lines.extend(f"- {warning}" for warning in summary['warnings'])
    md_path.write_text('\n'.join(md_lines) + '\n', encoding='utf-8')
    if open_after_create:
        open_preview(png_path)
    return preview_report


def open_preview(path: Path) -> str:
    for cmd in ['wslview', 'xdg-open']:
        try:
            found = subprocess.run(['bash', '-lc', f'command -v {cmd}'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False)
            if found.returncode == 0:
                subprocess.Popen([cmd, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return f'opened with {cmd}'
        except Exception:
            continue
    return f'open skipped; PNG path: {path}'


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
        'layers:',
    ]
    for layer in summary['layers']:
        lines.append(f"  - {layer['source_id']} status={layer['status']} color={layer['color']}")
    if summary['warnings']:
        lines.append('warnings:')
        lines.extend(f"  - {warning}" for warning in summary['warnings'])
    return '\n'.join(lines) + '\n'
