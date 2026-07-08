from __future__ import annotations
import os
from pathlib import Path
from typing import Any
import yaml

DEFAULT_LINGO_PATH = Path('configs/cli_lingo.yaml')
MODES = {'standard', 'mixed', 'kitchen'}


def load_lingo(path: Path = DEFAULT_LINGO_PATH) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or 'terms' not in data:
        raise ValueError('cli lingo file must contain terms')
    return data


def resolve_mode(mode: str | None = None) -> str:
    selected = mode or os.environ.get('FASTERRASTER_LINGO') or 'mixed'
    if selected not in MODES:
        raise ValueError(f'unsupported lingo mode: {selected}')
    return selected


def title(key: str, mode: str | None = None, lingo: dict[str, Any] | None = None) -> str:
    data = lingo or load_lingo()
    resolved = resolve_mode(mode)
    return data.get('titles', {}).get(key, {}).get(resolved) or data.get('titles', {}).get(key, {}).get('standard') or key


def glossary(mode: str | None = None) -> list[dict[str, str]]:
    data = load_lingo()
    return [{'term': k, 'meaning': v} for k, v in data['terms'].items()]


def glossary_text() -> str:
    lines = ['Kitchen Mode glossary', '']
    for row in glossary():
        lines.append(f"{row['term']} = {row['meaning']}")
    return '\n'.join(lines) + '\n'
