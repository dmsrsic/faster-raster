from __future__ import annotations

import json
import re

from faster_raster import cli_render as render


def test_stable_json_parseable_no_markup():
    text = render.stable_json({'b': 1, 'a': 2})
    assert json.loads(text)['a'] == 2
    assert '[' not in text.splitlines()[1]


def test_plain_table_no_ansi():
    text = render.table_plain(['a'], [['b']])
    assert '[' not in text
    assert render.strip_ansi(text) == text


def test_help_style_contains_statuses():
    text = render.help_style_plain()
    for label in ['verified_now', 'credential_gated', 'adapter_needed', 'skipped_policy']:
        assert label in text
