from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

from PIL import Image

from faster_raster import preview_alpha2, preview_balanced, preview_contracts, preview_profiles

ROOT = Path('/home/dmsrsic/raster-work/faster-raster')


def make_root(tmp_path: Path) -> Path:
    (tmp_path / 'configs').mkdir()
    shutil.copy(ROOT / 'configs/source_allowlist.yaml', tmp_path / 'configs/source_allowlist.yaml')
    return tmp_path


def png_bytes(image: Image.Image) -> bytes:
    handle = io.BytesIO()
    image.save(handle, format='PNG')
    return handle.getvalue()


def gradient(size=(96, 96)) -> Image.Image:
    img = Image.new('RGBA', size)
    for x in range(size[0]):
        for y in range(size[1]):
            img.putpixel((x, y), ((x * 3 + 30) % 255, (y * 2 + 45) % 255, ((x + y) * 2 + 60) % 255, 255))
    return img


def cdl_fixture(size=(96, 96), coverage='full') -> Image.Image:
    img = Image.new('RGBA', size, (0, 0, 0, 0))
    width = size[0] if coverage == 'full' else (size[0] // 2 if coverage == 'medium' else size[0] // 5)
    colors = [(222, 210, 45, 210), (40, 160, 70, 210), (190, 80, 60, 210), (80, 120, 220, 210)]
    for x in range(width):
        for y in range(size[1]):
            img.putpixel((x, y), colors[(x // 8 + y // 8) % len(colors)])
    return img


def fake_read(url, *, entry, max_total_bytes, timeout_seconds):
    if entry['source_id'] == 'usgs_naip_imagery':
        image = gradient()
    elif entry['source_id'] == 'usgs_3dep_hillshade':
        image = Image.new('RGBA', (96, 96), (120, 120, 120, 255))
    else:
        image = cdl_fixture()
    data = png_bytes(image)
    return {'data': data, 'bytes_read': len(data), 'content_type': 'image/png', 'sha256': preview_balanced.sha256_bytes(data)}


def render_mocked(root: Path, monkeypatch):
    monkeypatch.chdir(root)
    monkeypatch.setattr(preview_balanced, 'read_bounded', fake_read)
    contract = preview_balanced.plan_preview('example_imagery_first_balanced_stack', root=root)
    result = preview_balanced.render_preview('example_imagery_first_balanced_stack', allow_network=True, allow_preview=True, approve_plan_sha256=contract['preview_render_contract_sha256'], root=root)
    return contract, result


def test_default_profile_selected_and_hash_deterministic(tmp_path):
    root = make_root(tmp_path)
    allowlist = preview_balanced.load_allowlist(root)
    first = preview_contracts.build_render_contract('example_imagery_first_balanced_stack', allowlist)
    second = preview_contracts.build_render_contract('example_imagery_first_balanced_stack', allowlist)
    assert first['preview_profile_id'] == 'imagery_first_balanced_v1'
    assert first['preview_profile_contract_sha256'] == second['preview_profile_contract_sha256']
    assert first['preview_render_contract_sha256'] == second['preview_render_contract_sha256']
    profile = preview_profiles.imagery_first_balanced_v1()
    profile['categorical_policy']['requested_opacity'] = 0.25
    assert preview_profiles.profile_contract_hash(profile) != first['preview_profile_contract_sha256']


def test_explicit_profile_override_and_temp_paths_do_not_affect_hash():
    profile = preview_profiles.select_default_profile('anything_balanced_stack', explicit_profile_id='imagery_first_balanced_v1')
    assert profile['profile_id'] == 'imagery_first_balanced_v1'
    profile['temporary_root'] = '/tmp/pytest-example'
    profile.pop('temporary_root')
    assert preview_profiles.profile_contract_hash(profile) == profile['default_profile_contract_sha256']


def test_mild_imagery_enhancement_is_deterministic_and_preserves_source_bytes():
    image = gradient((32, 32))
    before = png_bytes(image)
    policy = preview_profiles.imagery_first_balanced_v1()['imagery_enhancement_policy']
    first = preview_balanced.enhance_natural_color_mild(image, policy)
    second = preview_balanced.enhance_natural_color_mild(image, policy)
    assert preview_balanced.sha256_bytes(png_bytes(first)) == preview_balanced.sha256_bytes(png_bytes(second))
    assert png_bytes(image) == before
    assert 0.94 <= policy['gamma'] <= 0.98


def test_adaptive_categorical_opacity_full_medium_sparse():
    profile = preview_profiles.imagery_first_balanced_v1()
    full = preview_profiles.compile_categorical_opacity(0.95, 0.24, profile)
    medium = preview_profiles.compile_categorical_opacity(0.50, 0.24, profile)
    sparse = preview_profiles.compile_categorical_opacity(0.10, 0.24, profile)
    assert 0.18 <= full['compiled_opacity'] <= 0.24
    assert full['opacity_adjustment_reason'] == 'full_coverage_reduction'
    assert 0.22 <= medium['compiled_opacity'] <= 0.27
    assert sparse['compiled_opacity'] >= 0.24
    assert sparse['compiled_opacity'] <= 0.30


def test_alpha_budget_reduces_lower_priority_and_never_imagery():
    profile = preview_profiles.imagery_first_balanced_v1()
    layers = [
        {'source_id': 'naip', 'render_role': 'primary_imagery', 'requested_opacity': 1.0, 'compiled_opacity': 1.0},
        {'source_id': 'diag', 'render_role': 'diagnostic_overlay', 'requested_opacity': 0.20, 'compiled_opacity': 0.20},
        {'source_id': 'env', 'render_role': 'environmental_context', 'requested_opacity': 0.20, 'compiled_opacity': 0.20},
        {'source_id': 'cdl', 'render_role': 'thematic_overlay', 'requested_opacity': 0.30, 'compiled_opacity': 0.30},
        {'source_id': 'hill', 'render_role': 'terrain_context', 'requested_opacity': 0.12, 'compiled_opacity': 0.12},
    ]
    budget = preview_profiles.compile_overlay_alpha_budget(layers, profile)
    assert budget['compiled_overlay_alpha_budget'] <= 0.42
    compiled = {layer['source_id']: layer for layer in budget['compiled_layers']}
    assert compiled['naip']['compiled_opacity'] == 1.0
    assert compiled['diag']['compiled_opacity'] <= layers[1]['compiled_opacity']


def test_class_id_boundary_mask_correct_and_nodata_policy():
    grid = [[1, 1, 2], [1, 2, 2], [None, 2, 2]]
    mask = preview_balanced.class_id_boundary_mask(grid)
    assert mask[0][0] == 0
    assert mask[0][1] == 1
    assert mask[2][0] == 0
    with_nodata = preview_balanced.class_id_boundary_mask(grid, include_nodata_transitions=True)
    assert with_nodata[2][0] == 1
    assert preview_balanced.boundary_pixel_fraction(mask) <= 1.0


def test_mocked_balanced_stack_passes_visual_authority(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    contract, result = render_mocked(root, monkeypatch)
    receipt = result['receipt']
    assert result['verification']['preview_verification_status'] == 'PASS'
    assert receipt['preview_profile_id'] == 'imagery_first_balanced_v1'
    assert receipt['primary_imagery_visible_fraction'] >= 0.70
    assert receipt['imagery_contrast_retention'] >= 0.65
    assert receipt['imagery_edge_retention'] >= 0.65
    assert receipt['categorical_effective_coverage'] <= 0.30
    assert receipt['boundary_pixel_fraction'] <= 0.08
    assert receipt['compiled_overlay_alpha_budget'] <= 0.42
    assert receipt['dominant_visual_role'] == 'primary_imagery'
    assert receipt['visible_semantic_class_count'] is None
    assert receipt['semantic_legend_entry_count'] == 0
    assert receipt['diagnostic_visible_color_group_count'] >= 2
    assert receipt['categorical_legend']['legend_truthfulness_status'] == 'PASS'
    assert receipt['output_image_width'] == 1560
    assert receipt['output_image_height'] == 980


def test_overly_opaque_categorical_fails_verification(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    contract, result = render_mocked(root, monkeypatch)
    receipt = result['receipt']
    receipt['categorical_effective_coverage'] = 0.40
    receipt['preview_receipt_contract_sha256'] = preview_balanced.receipt_hash(receipt)
    verification = preview_balanced.verify_preview(receipt, contract=contract, root=root)
    assert verification['categorical_balance_status'] == 'FAIL'
    assert verification['preview_verification_status'] == 'FAIL'


def test_dense_boundary_lattice_and_hidden_imagery_fail(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    contract, result = render_mocked(root, monkeypatch)
    receipt = result['receipt']
    receipt['boundary_pixel_fraction'] = 0.20
    receipt['primary_imagery_visible_fraction'] = 0.30
    receipt['preview_receipt_contract_sha256'] = preview_balanced.receipt_hash(receipt)
    verification = preview_balanced.verify_preview(receipt, contract=contract, root=root)
    assert verification['boundary_restraint_status'] == 'FAIL'
    assert verification['imagery_dominance_status'] == 'FAIL'


def test_legend_fallback_has_no_pseudo_class_rows():
    legend = preview_balanced.cdl_legend(cdl_fixture())
    assert legend['legend_truthfulness_status'] == 'PASS'
    assert legend['legend_status'] == 'mapping_unavailable'
    assert legend['semantic_legend_entry_count'] == 0
    assert legend['visible_semantic_class_count'] is None
    assert legend['diagnostic_color_group_count'] >= 2
    assert legend['entries'] == []
    assert 'colorized categorical pixels' in legend['fallback_legend_message']
    assert not any('Colorized CDL class mapping unavailable' in json.dumps(row) for row in legend['diagnostic_visible_color_groups'])


def test_alpha2_preview_still_plans_with_default_task(tmp_path):
    root = make_root(tmp_path)
    allowlist = preview_alpha2.load_allowlist(root)
    contract = preview_contracts.build_render_contract('example_imagery_first_multipreview', allowlist)
    assert contract['task_id'] == 'example_imagery_first_multipreview'
    assert contract['preview_profile_id'] is None


def test_receipt_distinguishes_semantic_classes_from_diagnostic_color_groups(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    contract, result = render_mocked(root, monkeypatch)
    receipt = result['receipt']
    assert receipt['semantic_legend_entry_count'] == 0
    assert receipt['visible_semantic_class_count'] is None
    assert receipt['diagnostic_visible_color_group_count'] > 0
    assert receipt['categorical_legend']['entries'] == []


def test_verifier_rejects_claimed_semantic_classes_without_mapping(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    contract, result = render_mocked(root, monkeypatch)
    receipt = result['receipt']
    receipt['visible_semantic_class_count'] = 3
    receipt['categorical_legend']['entries'] = [{'class_name': 'invented', 'visible_fraction': 1.0}]
    receipt['preview_receipt_contract_sha256'] = preview_balanced.receipt_hash(receipt)
    verification = preview_balanced.verify_preview(receipt, contract=contract, root=root)
    assert verification['legend_truthfulness_status'] == 'FAIL'
    assert verification['preview_verification_status'] == 'FAIL'


def test_verifier_rejects_displayed_selected_opacity_mismatch(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    contract, result = render_mocked(root, monkeypatch)
    receipt = result['receipt']
    receipt['displayed_selected_opacity'] = 0.30
    receipt['preview_receipt_contract_sha256'] = preview_balanced.receipt_hash(receipt)
    verification = preview_balanced.verify_preview(receipt, contract=contract, root=root)
    assert verification['preview_verification_status'] == 'FAIL'
    assert 'displayed selected opacity mismatch' in verification['blocking_failures']


def test_enhancement_candidate_metrics_and_clipping_are_recorded(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    contract, result = render_mocked(root, monkeypatch)
    receipt = result['receipt']
    naip = next(layer for layer in receipt['layers'] if layer['source_id'] == 'usgs_naip_imagery')
    assert 'baseline_alpha3_initial' in receipt['enhancement_candidate_metrics']
    assert 'selected' in receipt['enhancement_candidate_metrics']
    assert receipt['enhancement_selection_reason']
    assert 0 <= receipt['highlight_clipped_fraction'] <= 1
    assert 0 <= receipt['shadow_clipped_fraction'] <= 1
    assert naip['contrast_stretch_policy']['gamma'] == 0.98


def test_dashboard_contract_fields_for_selected_panel_and_zoom(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    contract, result = render_mocked(root, monkeypatch)
    receipt = result['receipt']
    assert 'SELECTED 0.20' in receipt['comparison_panels']
    assert receipt['displayed_selected_opacity'] == receipt['cdl_compiled_opacity']
    bounds = receipt['pixel_zoom_source_bounds']
    assert 0 <= bounds['left'] < bounds['right'] <= 900
    assert 0 <= bounds['top'] < bounds['bottom'] <= 620
    assert receipt['output_image_width'] == 1560
    assert receipt['output_image_height'] == 980
