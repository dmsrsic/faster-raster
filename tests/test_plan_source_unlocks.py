from __future__ import annotations

from pathlib import Path
import json

from scripts import lint_source_atlas as lint
from scripts import plan_source_unlocks as planner

ATLAS = Path('/home/dmsrsic/raster-work/faster-raster/research/source_atlas_v0_4.yaml')
STACK = Path('/home/dmsrsic/raster-work/faster-raster/reports/multi_source_stack_probe.json')


def test_unlock_plan_has_ranked_items():
    plan = planner.plan_unlocks(lint.load_atlas(ATLAS), json.loads(STACK.read_text()))
    assert plan
    assert {'source_id','class','score','recommended_action'} <= set(plan[0])


def test_unlock_classes_include_adapter_or_auth():
    plan = planner.plan_unlocks(lint.load_atlas(ATLAS), json.loads(STACK.read_text()))
    classes = {row['class'] for row in plan}
    assert 'adapter_next' in classes or 'auth_scaffold_next' in classes


def test_unlock_reports_write(tmp_path):
    plan = planner.plan_unlocks(lint.load_atlas(ATLAS), json.loads(STACK.read_text()))
    out = tmp_path / 'plan.json'
    md = tmp_path / 'plan.md'
    planner.write_reports(plan, out, md)
    assert json.loads(out.read_text())['unlock_plan']
    assert 'Source Unlock Plan' in md.read_text()
