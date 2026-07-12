from __future__ import annotations

import io, json, shutil
from pathlib import Path

import pytest
from PIL import Image

from faster_raster import preview_alpha2, preview_contracts, preview_themes
from faster_raster.adapters import capabilities
from faster_raster.adapters.conformance import verify_adapter_conformance

ROOT = Path('/home/dmsrsic/raster-work/faster-raster')

def make_root(tmp_path: Path) -> Path:
    (tmp_path / 'configs').mkdir()
    shutil.copy(ROOT / 'configs/source_allowlist.yaml', tmp_path / 'configs/source_allowlist.yaml')
    return tmp_path

def png_bytes(color: tuple[int, int, int, int], size=(64,64)) -> bytes:
    img = Image.new('RGBA', size, color)
    if color[0] == 10:
        for x in range(size[0]):
            for y in range(size[1]):
                img.putpixel((x,y), ((x*7)%255, (y*5)%255, 90, 255))
    handle = io.BytesIO(); img.save(handle, format='PNG'); return handle.getvalue()

def fake_read_bounded(url, *, entry, max_total_bytes, timeout_seconds):
    colors = {'usgs_naip_imagery': (10,80,20,255), 'usda_cdl_imageserver': (220,200,30,180), 'usgs_3dep_hillshade': (90,90,90,255)}
    data = png_bytes(colors[entry['source_id']])
    return {'data': data, 'bytes_read': len(data), 'content_type': 'image/png', 'sha256': preview_alpha2.sha256_bytes(data), 'url_redacted': url, 'http_status': 200}

def test_theme_registry_hash_deterministic_and_roles():
    first = preview_themes.theme_registry()
    second = preview_themes.theme_registry()
    assert first['preview_theme_registry_sha256'] == second['preview_theme_registry_sha256']
    assert preview_themes.get_theme('aerial_imagery')['render_role'] == 'primary_imagery'
    assert preview_themes.get_theme('semantic_fallback')['may_be_primary'] is False
    assert preview_themes.get_theme('cloud_mask')['diagnostic_only'] is True

def test_theme_order_independent_of_input_order():
    layers = [{'source_id':'fallback','theme':'semantic_fallback'}, {'source_id':'cdl','theme':'landcover_categorical'}, {'source_id':'naip','theme':'aerial_imagery'}, {'source_id':'mask','theme':'cloud_mask'}]
    forward = preview_themes.sort_layers_by_theme(layers)
    reverse = preview_themes.sort_layers_by_theme(list(reversed(layers)))
    assert [x['theme'] for x in forward] == [x['theme'] for x in reverse]
    assert forward[0]['theme'] == 'aerial_imagery'
    assert forward[-1]['theme'] == 'semantic_fallback'

def test_render_contract_hash_deterministic_and_policy_sensitive(tmp_path):
    root = make_root(tmp_path); allowlist = preview_alpha2.load_allowlist(root)
    first = preview_contracts.build_render_contract('example_imagery_first_multipreview', allowlist)
    second = preview_contracts.build_render_contract('example_imagery_first_multipreview', allowlist)
    assert first['preview_render_contract_sha256'] == second['preview_render_contract_sha256']
    changed = preview_contracts.build_render_contract('example_imagery_first_multipreview', allowlist, width=900)
    assert changed['preview_render_contract_sha256'] != first['preview_render_contract_sha256']
    first['generated_at_utc'] = '2099-01-01T00:00:00Z'
    assert preview_contracts.contract_hash(first) == second['preview_render_contract_sha256']

def test_render_profiles_are_explicit_and_radar_not_natural_color():
    assert preview_contracts.RENDER_PROFILES['natural_color']['band_order'] == ['red','green','blue']
    assert preview_contracts.RENDER_PROFILES['color_infrared']['band_order'] == ['nir','red','green']
    assert preview_contracts.RENDER_PROFILES['radar_db_grayscale']['natural_color_claim'] is False
    assert preview_contracts.RENDER_PROFILES['grayscale_single_band']['gamma'] == 1.0

def test_adapter_conformance_and_capability_hashes(tmp_path):
    report = verify_adapter_conformance(root=tmp_path)
    assert report['verification_status'] == 'PASS'
    assert set(report['adapter_capability_hashes']) >= {'arcgis_imageserver_preview','stac_api','ogc_wms_preview'}
    for adapter in capabilities.adapter_capabilities():
        assert adapter['capabilities']['materialize'] is False
        assert adapter['adapter_capability_contract_sha256']

def test_source_allowlist_policy(tmp_path):
    root = make_root(tmp_path)
    report = preview_alpha2.verify_source_allowlist(root=root)
    assert report['verification_status'] == 'PASS'
    assert report['live_verified_source_count'] >= 3
    assert report['classifications']['sentinel_1_radar_scaffold'] == 'future_unverified'
    assert report['classifications']['usgs_naip_imagery'] == 'service_discovered'

def test_unknown_host_rejected():
    with pytest.raises(preview_alpha2.PreviewError) as exc:
        preview_alpha2.ensure_host_allowed('https://example.com/data.png', ['imagery.nationalmap.gov'])
    assert exc.value.failure_class == 'host_not_allowed'

def test_render_requires_preview_and_approval(tmp_path, monkeypatch):
    root = make_root(tmp_path); monkeypatch.chdir(root)
    contract = preview_alpha2.plan_preview('example_imagery_first_multipreview', root=root)
    blocked = preview_alpha2.render_preview('example_imagery_first_multipreview', allow_network=True, allow_preview=False, approve_plan_sha256=contract['preview_render_contract_sha256'], root=root)
    assert blocked['receipt']['operation_status'] == 'failed'
    wrong = preview_alpha2.render_preview('example_imagery_first_multipreview', allow_network=True, allow_preview=True, approve_plan_sha256='0'*64, root=root)
    assert 'preview_contract_mismatch' in wrong['receipt']['failures']

def test_mocked_imagery_first_render_passes_policy(tmp_path, monkeypatch):
    root = make_root(tmp_path); monkeypatch.chdir(root); monkeypatch.setattr(preview_alpha2, 'read_bounded', fake_read_bounded)
    contract = preview_alpha2.plan_preview('example_imagery_first_multipreview', root=root)
    result = preview_alpha2.render_preview('example_imagery_first_multipreview', allow_network=True, allow_preview=True, approve_plan_sha256=contract['preview_render_contract_sha256'], root=root)
    receipt = result['receipt']
    assert result['verification']['preview_verification_status'] == 'PASS'
    assert receipt['actual_imagery_pixel_status'] is True
    assert receipt['dominant_visual_role'] == 'primary_imagery'
    assert receipt['primary_imagery_visible_fraction'] >= 0.60
    assert receipt['overlay_total_alpha_budget'] < receipt['primary_imagery_opacity']
    assert Path(receipt['output_image_logical_path']).exists()
    assert receipt['layers'][0]['source_id'] == 'usgs_naip_imagery'
    assert receipt['layers'][4]['resampling_method'] == 'nearest'

def test_receipt_image_and_layer_tampering_detected(tmp_path, monkeypatch):
    root = make_root(tmp_path); monkeypatch.chdir(root); monkeypatch.setattr(preview_alpha2, 'read_bounded', fake_read_bounded)
    contract = preview_alpha2.plan_preview('example_imagery_first_multipreview', root=root)
    result = preview_alpha2.render_preview('example_imagery_first_multipreview', allow_network=True, allow_preview=True, approve_plan_sha256=contract['preview_render_contract_sha256'], root=root)
    receipt = result['receipt']
    bad = json.loads(json.dumps(receipt)); bad['layers'][1]['opacity'] = 2.0; bad['preview_receipt_contract_sha256'] = preview_alpha2.receipt_hash(bad)
    assert preview_alpha2.verify_preview(bad, contract=contract, root=root)['preview_verification_status'] == 'FAIL'
    path = root / receipt['output_image_logical_path']; path.write_bytes(path.read_bytes() + b'tamper')
    assert preview_alpha2.verify_preview(receipt, contract=contract, root=root)['preview_verification_status'] == 'FAIL'

def test_source_selection_prefers_real_pixels_over_future_unverified(tmp_path):
    root = make_root(tmp_path); allowlist = preview_alpha2.load_allowlist(root); contract = preview_contracts.build_render_contract('example_imagery_first_multipreview', allowlist)
    selection = preview_alpha2.source_selection_receipt(contract, preview_alpha2.allowlist_entries(allowlist))
    assert selection['selected_item'] == 'usgs_naip_imagery'
    rejected = {c['candidate_id']: c['rejection_reason'] for c in selection['candidates']}
    assert rejected['sentinel_1_radar_scaffold'] == 'source_future_unverified'
    assert selection['source_selection_contract_sha256']


def fake_read_with_transparent_cdl(url, *, entry, max_total_bytes, timeout_seconds):
    colors = {
        'usgs_naip_imagery': (10,80,20,255),
        'usda_cdl_imageserver': (0,0,0,0),
        'usgs_3dep_hillshade': (90,90,90,255),
    }
    data = png_bytes(colors[entry['source_id']])
    return {'data': data, 'bytes_read': len(data), 'content_type': 'image/png', 'sha256': preview_alpha2.sha256_bytes(data), 'url_redacted': url, 'http_status': 200}


def fake_read_with_partial_cdl(url, *, entry, max_total_bytes, timeout_seconds):
    if entry['source_id'] == 'usda_cdl_imageserver':
        img = Image.new('RGBA', (64,64), (0,0,0,0))
        for x in range(32):
            for y in range(64):
                img.putpixel((x,y), (220,200,30,180))
        handle = io.BytesIO(); img.save(handle, format='PNG'); data = handle.getvalue()
    else:
        data = png_bytes((10,80,20,255) if entry['source_id'] == 'usgs_naip_imagery' else (90,90,90,255))
    return {'data': data, 'bytes_read': len(data), 'content_type': 'image/png', 'sha256': preview_alpha2.sha256_bytes(data), 'url_redacted': url, 'http_status': 200}


def rehash_receipt(receipt):
    receipt['preview_receipt_contract_sha256'] = ''
    receipt['preview_receipt_contract_sha256'] = preview_alpha2.receipt_hash(receipt)
    return receipt


def write_selection(root, receipt, selection):
    selection['source_selection_contract_sha256'] = preview_alpha2.source_selection_contract_hash(selection)
    selection['source_selection_receipt_sha256'] = preview_alpha2.source_selection_receipt_hash(selection)
    path = root / receipt['source_selection_receipt_path']
    path.write_text(json.dumps(selection, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def bound_render(root, monkeypatch, reader=fake_read_bounded):
    monkeypatch.chdir(root); monkeypatch.setattr(preview_alpha2, 'read_bounded', reader)
    contract = preview_alpha2.plan_preview('example_imagery_first_multipreview', root=root)
    result = preview_alpha2.render_preview('example_imagery_first_multipreview', allow_network=True, allow_preview=True, approve_plan_sha256=contract['preview_render_contract_sha256'], root=root)
    return contract, result


def test_preview_receipt_binds_source_selection_receipt_and_contract(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    contract, result = bound_render(root, monkeypatch)
    receipt = result['receipt']
    selection = result['source_selection']
    assert receipt['source_selection_receipt_path']
    assert receipt['source_selection_receipt_sha256'] == selection['source_selection_receipt_sha256']
    assert receipt['source_selection_contract_sha256'] == selection['source_selection_contract_sha256']
    assert result['verification']['source_selection_receipt_status'] == 'PASS'
    assert result['verification']['source_selection_contract_status'] == 'PASS'
    assert result['verification']['selected_source_consistency_status'] == 'PASS'


def test_source_selection_receipt_tampering_fails_preview_verification(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    contract, result = bound_render(root, monkeypatch)
    receipt = result['receipt']
    path = root / receipt['source_selection_receipt_path']
    selection = json.loads(path.read_text())
    selection['selected_asset'] = 'tampered asset'
    path.write_text(json.dumps(selection, indent=2, sort_keys=True) + '\n')
    verification = preview_alpha2.verify_preview(receipt, contract=contract, root=root)
    assert verification['preview_verification_status'] == 'FAIL'
    assert verification['source_selection_receipt_status'] == 'FAIL'


def test_source_selection_contract_tampering_fails_preview_verification(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    contract, result = bound_render(root, monkeypatch)
    receipt = result['receipt']
    path = root / receipt['source_selection_receipt_path']
    selection = json.loads(path.read_text())
    selection['source_selection_contract_sha256'] = '0' * 64
    selection['source_selection_receipt_sha256'] = preview_alpha2.source_selection_receipt_hash(selection)
    path.write_text(json.dumps(selection, indent=2, sort_keys=True) + '\n')
    receipt['source_selection_receipt_sha256'] = selection['source_selection_receipt_sha256']
    rehash_receipt(receipt)
    verification = preview_alpha2.verify_preview(receipt, contract=contract, root=root)
    assert verification['preview_verification_status'] == 'FAIL'
    assert verification['source_selection_contract_status'] == 'FAIL'


def test_selected_source_mismatch_fails_preview_verification(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    contract, result = bound_render(root, monkeypatch)
    receipt = result['receipt']
    selection = dict(result['source_selection'])
    selection['selected_item'] = 'usgs_3dep_hillshade'
    selection['selected_adapter_id'] = 'arcgis_imageserver_preview'
    write_selection(root, receipt, selection)
    receipt['source_selection_receipt_sha256'] = selection['source_selection_receipt_sha256']
    receipt['source_selection_contract_sha256'] = selection['source_selection_contract_sha256']
    rehash_receipt(receipt)
    verification = preview_alpha2.verify_preview(receipt, contract=contract, root=root)
    assert verification['preview_verification_status'] == 'FAIL'
    assert verification['selected_source_consistency_status'] == 'FAIL'


def test_selected_adapter_mismatch_fails_preview_verification(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    contract, result = bound_render(root, monkeypatch)
    receipt = result['receipt']
    selection = dict(result['source_selection'])
    selection['selected_adapter_id'] = 'stac_api'
    write_selection(root, receipt, selection)
    receipt['source_selection_receipt_sha256'] = selection['source_selection_receipt_sha256']
    receipt['source_selection_contract_sha256'] = selection['source_selection_contract_sha256']
    rehash_receipt(receipt)
    verification = preview_alpha2.verify_preview(receipt, contract=contract, root=root)
    assert verification['preview_verification_status'] == 'FAIL'
    assert verification['selected_source_consistency_status'] == 'FAIL'


def test_source_selection_score_depends_on_verified_binding():
    from faster_raster.system_grade import preview_source_selection_score_from_verification
    assert preview_source_selection_score_from_verification({'source_selection_receipt_status': 'PASS', 'source_selection_contract_status': 'PASS', 'selected_source_consistency_status': 'PASS'}) == 100
    assert preview_source_selection_score_from_verification({'source_selection_receipt_status': 'FAIL', 'source_selection_contract_status': 'PASS', 'selected_source_consistency_status': 'PASS'}) == 75


def test_fully_transparent_layer_is_audited_but_not_rendered(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    contract, result = bound_render(root, monkeypatch, fake_read_with_transparent_cdl)
    receipt = result['receipt']
    cdl = next(layer for layer in receipt['layers'] if layer['source_id'] == 'usda_cdl_imageserver')
    assert cdl['layer_status'] == 'no_visible_pixels'
    assert cdl['real_pixel_status'] is False
    assert cdl['nontransparent_fraction'] == 0.0
    assert cdl['unique_sample_colors'] == 0
    assert cdl['rendered_into_composite'] is False
    assert cdl['exclusion_reason'] == 'no_visible_pixels'
    assert cdl['thumbnail_status'] == 'diagnostic_no_visible_pixels'
    assert receipt['overlay_count'] == 1
    assert receipt['overlay_total_alpha_budget'] == 0.18
    assert receipt['visible_thematic_coverage_fraction'] == 0.0
    assert receipt['primary_imagery_visible_fraction'] == 0.937
    assert result['verification']['preview_verification_status'] == 'PASS'


def test_no_visible_pixels_thumbnail_is_not_solid_black():
    thumb = preview_alpha2.diagnostic_thumbnail('usda_cdl_imageserver', 'NO VISIBLE PIXELS', bytes_read=123)
    raw = thumb.tobytes()
    pixels = [tuple(raw[i:i+3]) for i in range(0, len(raw), 3)]
    assert len(set(pixels)) > 2
    assert set(pixels) != {(0, 0, 0)}


def test_partially_transparent_layer_remains_real_pixels(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    contract, result = bound_render(root, monkeypatch, fake_read_with_partial_cdl)
    cdl = next(layer for layer in result['receipt']['layers'] if layer['source_id'] == 'usda_cdl_imageserver')
    assert cdl['layer_status'] == 'real_pixels_rendered'
    assert cdl['real_pixel_status'] is True
    assert cdl['nontransparent_fraction'] > 0
    assert result['receipt']['overlay_count'] == 2


def test_opaque_real_raster_remains_real_pixels(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    contract, result = bound_render(root, monkeypatch, fake_read_bounded)
    naip = next(layer for layer in result['receipt']['layers'] if layer['source_id'] == 'usgs_naip_imagery')
    assert naip['layer_status'] == 'real_pixels_rendered'
    assert naip['real_pixel_status'] is True
    assert naip['nontransparent_fraction'] == 1.0
    assert result['receipt']['primary_imagery_visible_fraction'] == 0.832
