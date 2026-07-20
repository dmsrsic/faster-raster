from __future__ import annotations

from pathlib import Path
import json

from scripts import render_source_stack_report as render
from scripts import lint_source_atlas as lint

ROOT = Path(__file__).resolve().parent.parent
ATLAS = ROOT / "research" / "source_atlas_v0_4.yaml"
STACK = ROOT / "reports" / "multi_source_stack_probe.json"


def test_build_matrix_shape():
    rows = render.build_matrix(json.loads(STACK.read_text()), lint.load_atlas(ATLAS))
    assert len(rows) >= 25
    assert {'source_id','display_name','provider','next_unlock_step'} <= set(rows[0])


def test_grouping_and_outputs(tmp_path):
    rows = render.build_matrix(json.loads(STACK.read_text()), lint.load_atlas(ATLAS))
    out_json = tmp_path / 'matrix.json'
    out_csv = tmp_path / 'matrix.csv'
    md = tmp_path / 'matrix.md'
    render.write_outputs(rows, out_json, out_csv, md)
    assert json.loads(out_json.read_text())['rows']
    assert 'source_id' in out_csv.read_text().splitlines()[0]
    assert 'Verified now' in md.read_text()
