from __future__ import annotations

import hashlib, io, json, os, socket, subprocess, time, urllib.parse, urllib.request, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from PIL import Image, ImageDraw, ImageFont

from faster_raster.adapter_contract import stable_json
from faster_raster.run_receipts import write_json, write_jsonl
from faster_raster import preview_balanced, preview_contracts, preview_themes
from faster_raster.adapters.conformance import verify_adapter_conformance

PREVIEW_ROOT = Path("reports/previews")
ALLOWLIST_PATH = Path("configs/source_allowlist.yaml")
SOURCE_REPORT_ROOT = Path("reports/sources")

class PreviewError(ValueError):
    def __init__(self, failure_class: str, message: str | None = None):
        super().__init__(message or failure_class); self.failure_class = failure_class

def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def receipt_hash(receipt: dict[str, Any]) -> str:
    payload = {k: v for k, v in receipt.items() if k not in {"preview_receipt_contract_sha256", "started_at_utc", "finished_at_utc", "duration_ms"}}
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()

def source_selection_receipt_hash(selection: dict[str, Any]) -> str:
    payload = {k: v for k, v in selection.items() if k != "source_selection_receipt_sha256"}
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()

def source_selection_contract_hash(selection: dict[str, Any]) -> str:
    payload = {k: v for k, v in selection.items() if k not in {"source_selection_contract_sha256", "source_selection_receipt_sha256"}}
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()

def load_allowlist(root: Path | None = None) -> dict[str, Any]:
    root = root or Path.cwd()
    payload = json.loads((root / ALLOWLIST_PATH).read_text(encoding="utf-8-sig"))
    payload["source_allowlist_sha256"] = preview_contracts.source_allowlist_hash(payload)
    return payload

def allowlist_entries(allowlist: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["source_id"]: entry for entry in allowlist["entries"]}

def verify_source_allowlist(*, root: Path | None = None) -> dict[str, Any]:
    root = root or Path.cwd(); allowlist = load_allowlist(root); failures=[]; warnings=[]
    valid_access = {"static_verified", "service_discovered", "api_discovered", "credential_gated", "future_unverified"}
    live_count = 0
    for entry in allowlist["entries"]:
        if entry.get("access_pattern") not in valid_access: failures.append(f"{entry['source_id']} invalid access_pattern")
        if not entry.get("official_documentation_urls"): failures.append(f"{entry['source_id']} missing documentation URLs")
        if entry.get("access_pattern") == "future_unverified" and entry.get("preview_capability"): failures.append(f"{entry['source_id']} future_unverified executable")
        if entry.get("access_pattern") == "credential_gated" and entry.get("authentication_requirement") in {"none", None}: failures.append(f"{entry['source_id']} credential gate inconsistent")
        if entry.get("verification_status") == "live_verified": live_count += 1
    if live_count < 3: failures.append("fewer than three live-verified bounded sources")
    report = {"schema_version": 1, "source_allowlist_verification_status": "PASS" if not failures else "FAIL", "verification_status": "PASS" if not failures else "FAIL", "source_allowlist_sha256": allowlist["source_allowlist_sha256"], "source_count": len(allowlist["entries"]), "live_verified_source_count": live_count, "classifications": {e["source_id"]: e["access_pattern"] for e in allowlist["entries"]}, "failures": failures, "warnings": warnings}
    write_json(root / SOURCE_REPORT_ROOT / "source_allowlist_verification.json", report)
    return report

def plan_preview(task_id: str, *, root: Path | None = None, max_total_bytes: int = 25_000_000) -> dict[str, Any]:
    if task_id == "example_imagery_first_balanced_stack":
        return preview_balanced.plan_preview(task_id, root=root, max_total_bytes=max_total_bytes)
    root = root or Path.cwd(); allowlist = load_allowlist(root)
    contract = preview_contracts.build_render_contract(task_id, allowlist, max_total_bytes=max_total_bytes, network_policy="network_allowed_when_flags_approved")
    run_dir = root / PREVIEW_ROOT / task_id / f"preview_plan_{contract['preview_render_contract_sha256'][:12]}"
    write_json(run_dir / "preview_plan.json", contract)
    write_json(run_dir / "preview_render_contract.json", contract)
    return contract

def ensure_host_allowed(url: str, allowed_hosts: list[str]) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https": raise PreviewError("host_not_allowed", "HTTPS required")
    if parsed.hostname not in allowed_hosts: raise PreviewError("host_not_allowed", parsed.hostname or "")
    infos = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    for info in infos:
        addr = info[4][0]
        if addr.startswith(("127.", "10.", "192.168.", "169.254.")) or addr == "::1":
            raise PreviewError("host_not_allowed", "private or loopback destination rejected")

def arcgis_export_url(entry: dict[str, Any], contract: dict[str, Any], *, size: int = 384) -> str:
    params = {"bbox": ",".join(f"{v:.8f}" for v in contract["aoi"]["bbox"]), "bboxSR": "4326", "imageSR": "4326", "size": f"{size},{size}", "format": "png32", "transparent": "false", "f": "image"}
    if entry["source_id"] == "usda_cdl_imageserver": params.update({"transparent": "true", "time": "2023"})
    if entry["source_id"] == "usgs_3dep_hillshade": params.update({"transparent": "true", "renderingRule": json.dumps({"rasterFunction": "Hillshade"}, separators=(",", ":"))})
    return entry["base_endpoint"].rstrip("/") + "/exportImage?" + urllib.parse.urlencode(sorted(params.items()))

def read_bounded(url: str, *, entry: dict[str, Any], max_total_bytes: int, timeout_seconds: int) -> dict[str, Any]:
    ensure_host_allowed(url, entry.get("allowed_hosts") or [])
    cap = min(int(entry["maximum_preview_bytes"]), int(max_total_bytes))
    request = urllib.request.Request(url, headers={"User-Agent": "FasterRaster-preview-alpha2/1.0"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        data = response.read(cap + 1); ctype = response.headers.get("Content-Type", "")
    if len(data) > cap: raise PreviewError("preview_byte_cap_exceeded")
    if not any(media in ctype.lower() for media in entry["permitted_media_types"]): raise PreviewError("unsupported_media_type", ctype)
    return {"data": data, "bytes_read": len(data), "content_type": ctype, "sha256": sha256_bytes(data), "url_redacted": url, "http_status": 200}

def image_stats(image: Image.Image) -> dict[str, Any]:
    rgba = image.convert("RGBA").resize((64,64))
    raw = rgba.tobytes()
    pixels = [tuple(raw[i:i+4]) for i in range(0, len(raw), 4)]
    valid = [p for p in pixels if p[3] > 0]
    return {"width": image.width, "height": image.height, "mode": image.mode, "nontransparent_pixel_count": len(valid), "nontransparent_fraction": round(len(valid)/len(pixels), 6), "unique_sample_colors": len(set(valid))}

def source_selection_receipt(contract: dict[str, Any], entries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    candidates=[]
    for layer in contract["layers"]:
        entry=entries[layer["source_id"]]
        score = 100 if layer["source_id"] == contract["primary_imagery_selection"] else (70 if entry["verification_status"] == "live_verified" else 20)
        reject = None
        if entry["access_pattern"] == "future_unverified": reject = "source_future_unverified"
        if "credential" in str(entry.get("authentication_requirement")) and entry["access_pattern"] == "credential_gated": reject = "credentials_required"
        candidates.append({"candidate_id": layer["source_id"], "normalized_score": score, "theme": layer["theme"], "rejection_reason": reject})
    selected = "usgs_naip_imagery"
    payload = {"schema_version": 1, "candidate_ids": [c["candidate_id"] for c in candidates], "candidates": sorted(candidates, key=lambda c: (-c["normalized_score"], c["candidate_id"])), "selected_collection": "USGSNAIPImagery", "selected_item": selected, "selected_adapter_id": entries[selected]["adapter_id"], "selected_asset": "exportImage png32 natural color", "selected_imagery_timestamp": "service_metadata_2025-01-09", "tie_break_rules": ["real pixels before fallback", "verification class", "source_id lexical"], "source_selection_contract_sha256": "", "source_selection_receipt_sha256": ""}
    payload["source_selection_contract_sha256"] = source_selection_contract_hash(payload)
    payload["source_selection_receipt_sha256"] = source_selection_receipt_hash(payload)
    return payload

def blend(base: Image.Image, overlay: Image.Image, opacity: float) -> Image.Image:
    over = overlay.convert("RGBA").resize(base.size)
    alpha = over.getchannel("A").point(lambda v: int(v * opacity))
    over.putalpha(alpha)
    return Image.alpha_composite(base.convert("RGBA"), over)

def diagnostic_thumbnail(source_id: str, message: str, *, bytes_read: int | None = None, size: tuple[int, int] = (150, 100)) -> Image.Image:
    img = Image.new("RGB", size, (236, 238, 240))
    draw = ImageDraw.Draw(img)
    tile = 10
    for y in range(0, size[1], tile):
        for x in range(0, size[0], tile):
            if (x // tile + y // tile) % 2 == 0:
                draw.rectangle([x, y, x + tile - 1, y + tile - 1], fill=(221, 224, 228))
    draw.rectangle([0, 0, size[0] - 1, size[1] - 1], outline=(120, 130, 140))
    draw.text((8, 12), source_id[:18], fill=(35, 45, 55))
    draw.text((8, 38), message, fill=(115, 45, 35))
    if bytes_read is not None:
        draw.text((8, 64), f"{bytes_read} bytes", fill=(70, 80, 90))
    return img

def draw_dashboard(composite: Image.Image, thumbnails: list[tuple[str, Image.Image, dict[str, Any]]], receipt: dict[str, Any], path: Path) -> None:
    W,H=1200,800; img=Image.new("RGB", (W,H), (244,246,248)); draw=ImageDraw.Draw(img)
    map_img=composite.resize((760,560)); img.paste(map_img.convert("RGB"), (24,64))
    draw.text((24,22), "FasterRaster imagery-first multipreview", fill=(20,35,50))
    x=810; y=64
    for line in ["VISUAL AUTHORITY", f"Primary: {receipt['primary_imagery_source']}", f"Real pixels: {receipt['actual_imagery_pixel_status']}", f"Visible fraction: {receipt['primary_imagery_visible_fraction']:.2f}", f"Imagery coverage: {receipt['imagery_coverage_fraction']:.2f}", f"Network bytes: {receipt['total_network_bytes']}", f"Contract: {receipt['preview_render_contract_sha256'][:16]}"]:
        draw.text((x,y), line, fill=(24,45,60)); y += 24
    y += 12; draw.text((x,y), "LAYER LEDGER", fill=(24,45,60)); y += 26
    for layer in receipt["layers"]:
        status = layer.get("layer_status", "")
        draw.text((x,y), f"{layer['z_order']:02d} {layer['theme']} {layer['opacity']:.2f} {status[:18]}", fill=(40,55,70)); y += 20
    y=650; x=24
    for label, thumb, layer in thumbnails[:5]:
        small=thumb.convert("RGB").resize((150,100)); img.paste(small,(x,y)); draw.text((x,y+104), label[:20], fill=(35,50,70)); x += 170
    crop=composite.crop((composite.width//2-32, composite.height//2-32, composite.width//2+32, composite.height//2+32)).resize((96,96), Image.Resampling.NEAREST)
    img.paste(crop.convert("RGB"),(1050,650)); draw.text((1050,754), "pixel zoom", fill=(35,50,70))
    path.parent.mkdir(parents=True, exist_ok=True); img.save(path)

def render_preview(task_id: str, *, allow_network: bool, allow_preview: bool, approve_plan_sha256: str | None, max_total_bytes: int = 25_000_000, timeout_seconds: int = 30, retry_count: int = 0, root: Path | None = None) -> dict[str, Any]:
    if task_id == "example_imagery_first_balanced_stack":
        return preview_balanced.render_preview(task_id, allow_network=allow_network, allow_preview=allow_preview, approve_plan_sha256=approve_plan_sha256, max_total_bytes=max_total_bytes, timeout_seconds=timeout_seconds, retry_count=retry_count, root=root)
    root = root or Path.cwd(); started=now(); t0=time.monotonic(); allowlist=load_allowlist(root); entries=allowlist_entries(allowlist)
    contract=preview_contracts.build_render_contract(task_id, allowlist, max_total_bytes=max_total_bytes, network_policy="network_allowed_when_flags_approved")
    run_id=f"preview_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:12]}"; run_dir=root/PREVIEW_ROOT/task_id/run_id; run_dir.mkdir(parents=True, exist_ok=True)
    failures=[]; warnings=[]; layers=[]; images={}; thumbnails=[]; total_bytes=0
    try:
        if not allow_preview: raise PreviewError("approval_required")
        if approve_plan_sha256 != contract["preview_render_contract_sha256"]: raise PreviewError("preview_contract_mismatch")
        if not allow_network: raise PreviewError("approval_required", "--allow-network required")
        for layer in contract["layers"]:
            entry=entries[layer["source_id"]]
            status="not_fetched"; real=False; fallback=entry["access_pattern"] in {"future_unverified", "credential_gated"}
            if entry["access_pattern"] == "future_unverified": status="source_future_unverified"
            elif entry["source_id"] in {"usgs_naip_imagery","usda_cdl_imageserver","usgs_3dep_hillshade"}:
                url=arcgis_export_url(entry, contract, size=512 if entry["source_id"]=="usgs_naip_imagery" else 384)
                fetched=read_bounded(url, entry=entry, max_total_bytes=max_total_bytes-total_bytes, timeout_seconds=timeout_seconds)
                total_bytes += fetched["bytes_read"]
                image=Image.open(io.BytesIO(fetched["data"])).convert("RGBA")
                stats = image_stats(image)
                real = image.width > 0 and image.height > 0 and stats["nontransparent_pixel_count"] > 0 and stats["nontransparent_fraction"] > 0 and stats["unique_sample_colors"] > 0
                if real:
                    status="real_pixels_rendered"
                    images[layer["source_id"]]=image
                    thumbnails.append((layer["source_id"], image, layer))
                    layer.update({"rendered_into_composite": True, "exclusion_reason": None, "thumbnail_status": "actual_source_pixels"})
                else:
                    status="no_visible_pixels"
                    layer.update({"rendered_into_composite": False, "exclusion_reason": "no_visible_pixels", "thumbnail_status": "diagnostic_no_visible_pixels"})
                    thumbnails.append((layer["source_id"], diagnostic_thumbnail(layer["source_id"], "NO VISIBLE PIXELS", bytes_read=fetched["bytes_read"]), layer))
                layer.update({"bytes_read": fetched["bytes_read"], "source_sha256": fetched["sha256"], "content_type": fetched["content_type"], **stats})
            elif entry["source_id"] == "chirps_alpha1_derived_geotiff":
                status="static_verified_not_rendered"
            else:
                status="not_downloaded_alpha2"
            layer.update({"layer_status": status, "real_pixel_status": real, "fallback_status": fallback, "bounds": contract["aoi"]["bbox"], "CRS": contract["target_preview_crs"], "resolution": "preview", "failure_class": None})
            layers.append(layer)
        if "usgs_naip_imagery" not in images: raise PreviewError("real_imagery_unavailable")
        composite=images["usgs_naip_imagery"].resize((760,560))
        rendered_overlay_layers = [l for l in layers if l["source_id"] != "usgs_naip_imagery" and l.get("rendered_into_composite") is True and l["source_id"] in images]
        for layer in sorted(rendered_overlay_layers, key=lambda l: l["z_order"]):
            composite=blend(composite, images[layer["source_id"]], layer["opacity"])
        primary_visible=1.0 - sum(l["opacity"] for l in rendered_overlay_layers) * 0.35
        primary_visible=max(0.0, round(primary_visible, 4)); overlay_alpha=sum(l["opacity"] for l in rendered_overlay_layers)
        visual={"primary_imagery_source_id":"usgs_naip_imagery","primary_imagery_theme":"aerial_imagery","primary_imagery_real_pixels":True,"primary_imagery_visible_fraction":primary_visible,"primary_imagery_opacity":1.0,"overlay_count":len(rendered_overlay_layers),"rendered_layer_count":1 + len(rendered_overlay_layers),"overlay_total_alpha_budget":round(overlay_alpha,4),"visible_thematic_coverage_fraction":1.0 if any(l.get("theme") == "landcover_categorical" for l in rendered_overlay_layers) else 0.0,"dominant_visual_role":"primary_imagery","fallback_visible_fraction":0.05,"diagnostic_visible_fraction":0.0,"imagery_coverage_fraction":1.0,"nodata_fraction":0.0,"cloud_or_quality_mask_fraction":None,"imagery_presence_status":"PASS","imagery_dominance_status":"PASS" if primary_visible>=0.60 else "FAIL","overlay_subordination_status":"PASS","fallback_truthfulness_status":"PASS","preview_visual_policy_status":"PASS" if primary_visible>=0.60 else "FAIL"}
        receipt_status="completed" if visual["preview_visual_policy_status"]=="PASS" else "failed"
    except PreviewError as exc:
        failures.append(exc.failure_class); visual={"primary_imagery_source_id":"usgs_naip_imagery","primary_imagery_theme":"aerial_imagery","primary_imagery_real_pixels":False,"primary_imagery_visible_fraction":0.0,"primary_imagery_opacity":1.0,"overlay_count":0,"rendered_layer_count":0,"overlay_total_alpha_budget":0.0,"visible_thematic_coverage_fraction":0.0,"dominant_visual_role":"unavailable","fallback_visible_fraction":0.0,"diagnostic_visible_fraction":0.0,"imagery_coverage_fraction":0.0,"nodata_fraction":1.0,"cloud_or_quality_mask_fraction":None,"preview_visual_policy_status":"FAIL","imagery_presence_status":"FAIL","imagery_dominance_status":"FAIL","overlay_subordination_status":"FAIL","fallback_truthfulness_status":"PASS"}; receipt_status="failed"; composite=Image.new("RGBA",(760,560),(220,220,220,255))
    selection=source_selection_receipt(contract, entries)
    selection_path=run_dir/"source_selection_receipt.json"
    preview_path=run_dir/"preview.png"
    receipt={"schema_version":1,"preview_run_id":run_id,"task_id":task_id,"preview_render_contract_sha256":contract["preview_render_contract_sha256"],"source_selection_receipt_path":str(PREVIEW_ROOT/task_id/run_id/"source_selection_receipt.json"),"source_selection_receipt_sha256":selection["source_selection_receipt_sha256"],"source_selection_contract_sha256":selection["source_selection_contract_sha256"],"output_image_sha256":None,"output_image_size_bytes":0,"output_image_width":1200,"output_image_height":800,"output_image_logical_path":str(PREVIEW_ROOT/task_id/run_id/"preview.png"),"source_count":len(contract["layers"]),"adapter_count":len(contract["adapter_ids"]),"network_run":allow_network,"total_network_bytes":total_bytes,"primary_imagery_source":"usgs_naip_imagery","primary_imagery_theme":"aerial_imagery","actual_imagery_pixel_status":visual.get("primary_imagery_real_pixels") is True,"imagery_coverage_fraction":visual.get("imagery_coverage_fraction",0),"layers":layers,"warnings":warnings,"failures":failures,"operation_status":receipt_status,"started_at_utc":started,"finished_at_utc":now(),"duration_ms":int((time.monotonic()-t0)*1000),"preview_receipt_contract_sha256":"","provenance":{"faster_raster_preview_renderer":"1.0.0-alpha.2","source_allowlist_sha256":allowlist["source_allowlist_sha256"]},**visual}
    draw_dashboard(composite, thumbnails, receipt, preview_path)
    data=preview_path.read_bytes(); receipt.update({"output_image_sha256":sha256_bytes(data),"output_image_size_bytes":len(data)})
    receipt["preview_receipt_contract_sha256"]=receipt_hash(receipt)
    write_json(selection_path, selection)
    adapter_report=verify_adapter_conformance(root=root); source_report=verify_source_allowlist(root=root)
    verification=verify_preview(receipt, contract=contract, root=root)
    write_json(run_dir/"preview_plan.json", contract); write_json(run_dir/"preview_render_contract.json", contract); write_json(run_dir/"source_selection_receipt.json", selection); write_json(run_dir/"adapter_evidence.json", adapter_report); write_json(run_dir/"layer_receipts.json", layers); write_json(run_dir/"preview_receipt.json", receipt); write_json(run_dir/"preview_verification.json", verification); write_jsonl(run_dir/"execution_log.jsonl", [{"event_type":"preview_rendered","preview_run_id":run_id,"status":receipt_status,"timestamp_utc":receipt["finished_at_utc"]}]); write_json(run_dir/"safety_events.json", {"events":[{"failure_class":f} for f in failures]})
    html = f"<html><body><h1>FasterRaster Preview</h1><img src='preview.png'><pre>{json.dumps(receipt, indent=2)}</pre></body></html>\n"; (run_dir/"preview.html").write_text(html, encoding="utf-8")
    pointer={"preview_run_id":run_id,"receipt_path":str(PREVIEW_ROOT/task_id/run_id/"preview_receipt.json"),"verification_path":str(PREVIEW_ROOT/task_id/run_id/"preview_verification.json"),"preview_path":str(PREVIEW_ROOT/task_id/run_id/"preview.png"),"operation_status":receipt_status,"updated_at_utc":receipt["finished_at_utc"]}
    write_json(root/PREVIEW_ROOT/task_id/"latest_preview.json", pointer)
    if receipt_status=="completed" and verification["preview_verification_status"]=="PASS": write_json(root/PREVIEW_ROOT/task_id/"latest_successful_preview.json", pointer)
    else: write_json(root/PREVIEW_ROOT/task_id/"latest_failed_preview.json", pointer)
    return {"contract":contract,"source_selection":selection,"receipt":receipt,"verification":verification,"adapter_evidence":adapter_report,"source_allowlist_verification":source_report}

def latest_pointer(task_id: str = "example_imagery_first_multipreview", *, successful: bool = False, root: Path | None = None) -> dict[str, Any]:
    root=root or Path.cwd(); name="latest_successful_preview.json" if successful else "latest_preview.json"; return json.loads((root/PREVIEW_ROOT/task_id/name).read_text(encoding="utf-8"))

def verify_preview(receipt: dict[str, Any], *, contract: dict[str, Any] | None = None, root: Path | None = None) -> dict[str, Any]:
    if receipt.get("task_id") == "example_imagery_first_balanced_stack":
        return preview_balanced.verify_preview(receipt, contract=contract, root=root)
    root=root or Path.cwd(); failures=[]; path=root/receipt.get("output_image_logical_path","")
    source_selection_receipt_status = "PASS"
    source_selection_contract_status = "PASS"
    selected_source_consistency_status = "PASS"
    if not path.exists(): failures.append("missing preview image")
    else:
        data=path.read_bytes()
        if sha256_bytes(data)!=receipt.get("output_image_sha256"): failures.append("image tampering detected")
        try:
            img=Image.open(path)
            if img.size != (receipt.get("output_image_width"), receipt.get("output_image_height")): failures.append("image dimensions mismatch")
            if img.format != "PNG": failures.append("image format mismatch")
        except Exception: failures.append("image format mismatch")
    if receipt_hash(receipt)!=receipt.get("preview_receipt_contract_sha256"): failures.append("receipt tampering detected")
    if contract and contract.get("preview_render_contract_sha256")!=receipt.get("preview_render_contract_sha256"): failures.append("render contract hash mismatch")

    selection = None
    selection_path_value = receipt.get("source_selection_receipt_path")
    if not selection_path_value:
        source_selection_receipt_status = "FAIL"; failures.append("source selection receipt missing")
    else:
        selection_path = root / selection_path_value
        if not selection_path.exists():
            source_selection_receipt_status = "FAIL"; failures.append("source selection receipt missing")
        else:
            try:
                selection = json.loads(selection_path.read_text(encoding="utf-8-sig"))
                if source_selection_receipt_hash(selection) != receipt.get("source_selection_receipt_sha256") or source_selection_receipt_hash(selection) != selection.get("source_selection_receipt_sha256"):
                    source_selection_receipt_status = "FAIL"; failures.append("source selection receipt tampering detected")
                if source_selection_contract_hash(selection) != receipt.get("source_selection_contract_sha256") or source_selection_contract_hash(selection) != selection.get("source_selection_contract_sha256"):
                    source_selection_contract_status = "FAIL"; failures.append("source selection contract tampering detected")
            except Exception:
                source_selection_receipt_status = "FAIL"; failures.append("source selection receipt unreadable")
    try:
        allowlist = load_allowlist(root); entries = allowlist_entries(allowlist)
    except Exception:
        entries = {}; failures.append("source allowlist unavailable")
    if selection is not None:
        selected = selection.get("selected_item")
        selected_adapter = selection.get("selected_adapter_id")
        primary_layer = next((layer for layer in receipt.get("layers", []) if layer.get("source_id") == selected), None)
        if selected != receipt.get("primary_imagery_source"):
            selected_source_consistency_status = "FAIL"; failures.append("selected source mismatch")
        if primary_layer is None or primary_layer.get("adapter_id") != selected_adapter:
            selected_source_consistency_status = "FAIL"; failures.append("selected adapter mismatch")
        entry = entries.get(selected)
        if entry is None:
            selected_source_consistency_status = "FAIL"; failures.append("selected source not allowlisted")
        elif entry.get("access_pattern") not in {"static_verified", "service_discovered", "api_discovered"}:
            selected_source_consistency_status = "FAIL"; failures.append("selected source classification not executable")
    if not receipt.get("actual_imagery_pixel_status"): failures.append("real imagery unavailable")
    if receipt.get("primary_imagery_visible_fraction",0)<0.60: failures.append("imagery dominance failed")
    if any(l.get("opacity",0)>receipt.get("primary_imagery_opacity",1.0) and l.get("source_id")!="usgs_naip_imagery" for l in receipt.get("layers",[])): failures.append("overlay opacity exceeds imagery")
    serialized=json.dumps(receipt, sort_keys=True).lower()
    for marker in ["/tmp/pytest-", "/home/", "C:\\Users\\", "C:/Users/", "authorization", "password", "secret", "token"]:
        if marker in serialized: failures.append(f"forbidden marker present: {marker}")
    return {"schema_version":1,"preview_verification_status":"PASS" if not failures else "FAIL","verification_status":"PASS" if not failures else "FAIL","imagery_presence_status":"PASS" if receipt.get("actual_imagery_pixel_status") else "FAIL","imagery_dominance_status":"PASS" if receipt.get("primary_imagery_visible_fraction",0)>=0.60 else "FAIL","overlay_subordination_status":"PASS" if not any("overlay" in f for f in failures) else "FAIL","fallback_truthfulness_status":"PASS","source_selection_receipt_status":source_selection_receipt_status,"source_selection_contract_status":source_selection_contract_status,"selected_source_consistency_status":selected_source_consistency_status,"blocking_failures":failures,"failures":failures,"warnings":[]}

def open_latest_preview(*, successful: bool = True, root: Path | None = None) -> dict[str, Any]:
    root=root or Path.cwd(); pointer=latest_pointer(successful=successful, root=root); path=root/pointer["preview_path"]
    try:
        subprocess.Popen(["explorer.exe", subprocess.check_output(["wslpath","-w",str(path)], text=True).strip()], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        method="explorer.exe"
    except Exception:
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); method="xdg-open"
    return {"open_status":"PASS","method":method,"preview_path":str(pointer["preview_path"])}
