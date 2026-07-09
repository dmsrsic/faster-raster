#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from PIL import Image
except Exception:
    Image = None

OUT_DIR = Path("reports/task_previews/cdl_manual_audit")
OUT_DIR.mkdir(parents=True, exist_ok=True)

BASE = "https://pdi.scinet.usda.gov/image/rest/services/CDL_WM/ImageServer"
MAX_BYTES = 2_500_000
TIMEOUT = 25
SIZE = "512,512"

BBOXES = {
    "task_original": [-83.20, 39.80, -83.19, 39.81],
    "task_expand10": [-83.245, 39.755, -83.145, 39.855],
    "iowa_known_ag": [-93.70, 41.95, -93.55, 42.10],
    "illinois_known_ag": [-89.20, 40.00, -89.05, 40.15],
}

def qurl(path: str, params: dict[str, Any]) -> str:
    return path + "?" + urllib.parse.urlencode(params, doseq=True, safe=",:{}[]\" ")

def bounded_get(url: str) -> tuple[int | None, str | None, bytes, str | None]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "FasterRaster-manual-cdl-audit/0.5.7",
            "Accept": "*/*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            status = getattr(r, "status", None)
            ctype = r.headers.get("content-type")
            chunks = []
            total = 0
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_BYTES:
                    break
            return status, ctype, b"".join(chunks)[:MAX_BYTES], None
    except Exception as e:
        return None, None, b"", repr(e)

def image_diag(blob: bytes, name: str) -> dict[str, Any]:
    d: dict[str, Any] = {
        "bytes": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest() if blob else None,
        "is_image": False,
    }
    if not blob or Image is None:
        return d
    p = OUT_DIR / f"{name}_{d['sha256'][:16]}.png"
    p.write_bytes(blob)
    d["cache_path"] = str(p)
    try:
        im = Image.open(p)
        rgba = im.convert("RGBA")
        colors = rgba.getcolors(maxcolors=1_000_000)
        unique = None if colors is None else len(colors)
        top = sorted(colors or [], reverse=True)[:8]
        total_px = rgba.size[0] * rgba.size[1]
        dominant_fraction = (top[0][0] / total_px) if top else None
        nontransparent = 0
        if colors:
            nontransparent = sum(count for count, color in colors if color[3] != 0)
        d.update({
            "is_image": True,
            "format": im.format,
            "mode": im.mode,
            "width": im.size[0],
            "height": im.size[1],
            "unique_colors": unique,
            "top_colors": [{"count": c, "rgba": list(v)} for c, v in top],
            "dominant_fraction": dominant_fraction,
            "nontransparent_pixels": nontransparent,
            "transparent_fraction": 1 - (nontransparent / total_px),
            "meaningful_image": bool(unique and unique > 2 and nontransparent > 0),
        })
    except Exception as e:
        d["image_error"] = repr(e)
    return d

def parse_sample(blob: bytes) -> dict[str, Any]:
    try:
        js = json.loads(blob.decode("utf-8", errors="replace"))
    except Exception as e:
        return {"json_ok": False, "error": repr(e), "text": blob[:500].decode("utf-8", errors="replace")}

    text = json.dumps(js, sort_keys=True)[:2000]
    vals = []

    def walk(x: Any, path: str = "") -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                lk = str(k).lower()
                if lk in {"value", "pixelvalue", "rastervalue", "classvalue", "classname", "name"}:
                    vals.append({"path": path + "/" + str(k), "value": v})
                walk(v, path + "/" + str(k))
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, path + f"[{i}]")

    walk(js)
    meaningful = []
    for v in vals:
        s = str(v["value"])
        if s and s.lower() not in {"nodata", "none", "null", "pixel", "0"}:
            meaningful.append(v)

    return {
        "json_ok": True,
        "keys": list(js.keys()) if isinstance(js, dict) else None,
        "values_found": vals[:40],
        "meaningful_values": meaningful[:40],
        "meaningful_count": len(meaningful),
        "snippet": text,
    }

def expand_bbox(b, factor):
    xmin, ymin, xmax, ymax = b
    cx = (xmin + xmax) / 2
    cy = (ymin + ymax) / 2
    w = (xmax - xmin) * factor
    h = (ymax - ymin) * factor
    return [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]

def centroid(b):
    return [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]

def epoch_ms(date: str) -> int:
    # YYYY-MM-DD UTC
    y, m, d = map(int, date.split("-"))
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)

time_variants = {
    "no_time": {},
    "time_year_string": {"time": "2023"},
    "time_mid_2023_epoch": {"time": str(epoch_ms("2023-07-01"))},
    "time_2023_interval": {"time": f"{epoch_ms('2023-01-01')},{epoch_ms('2023-12-31')}"},
    "mosaic_year_eq_2023": {
        "mosaicRule": json.dumps({
            "mosaicMethod": "esriMosaicAttribute",
            "where": "Year = 2023",
            "sortField": "Year",
            "sortValue": "2023",
        })
    },
    "mosaic_year_string_2023": {
        "mosaicRule": json.dumps({
            "mosaicMethod": "esriMosaicAttribute",
            "where": "Year = '2023'",
            "sortField": "Year",
            "sortValue": "2023",
        })
    },
}

results: dict[str, Any] = {
    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    "base": BASE,
    "max_bytes": MAX_BYTES,
    "bbox_candidates": BBOXES,
    "metadata": {},
    "export_probes": [],
    "identify_probes": [],
    "best_export_candidates": [],
    "best_identify_candidates": [],
}

# Metadata
meta_url = qurl(BASE, {"f": "json"})
st, ct, blob, err = bounded_get(meta_url)
meta_entry = {"url": meta_url, "http_status": st, "content_type": ct, "bytes": len(blob), "error": err}
try:
    meta = json.loads(blob.decode("utf-8", errors="replace"))
    meta_entry["json_ok"] = True
    meta_entry["name"] = meta.get("name")
    meta_entry["type"] = meta.get("type")
    meta_entry["capabilities"] = meta.get("capabilities")
    meta_entry["currentVersion"] = meta.get("currentVersion")
    meta_entry["spatialReference"] = meta.get("spatialReference")
    meta_entry["extent"] = meta.get("extent")
    meta_entry["timeInfo"] = meta.get("timeInfo")
    meta_entry["fields"] = meta.get("fields")
    meta_entry["maxImageWidth"] = meta.get("maxImageWidth")
    meta_entry["maxImageHeight"] = meta.get("maxImageHeight")
except Exception as e:
    meta_entry["json_ok"] = False
    meta_entry["parse_error"] = repr(e)
    meta_entry["text"] = blob[:1000].decode("utf-8", errors="replace")
results["metadata"] = meta_entry

# Export probes
for bbox_name, bbox in BBOXES.items():
    bbox_s = ",".join(f"{x:.8f}" for x in bbox)
    for tname, tparams in time_variants.items():
        for fmt in ["png32", "png"]:
            params = {
                "f": "image",
                "bbox": bbox_s,
                "bboxSR": "4326",
                "imageSR": "4326",
                "size": SIZE,
                "format": fmt,
                "transparent": "false",
            }
            params.update(tparams)
            url = qurl(BASE + "/exportImage", params)
            st, ct, blob, err = bounded_get(url)
            diag = image_diag(blob, f"export_{bbox_name}_{tname}_{fmt}")
            rec = {
                "probe_type": "exportImage",
                "bbox_name": bbox_name,
                "time_variant": tname,
                "format": fmt,
                "url": url,
                "http_status": st,
                "content_type": ct,
                "error": err,
                "diag": diag,
            }
            results["export_probes"].append(rec)

# Identify probes at centroid only first, enough for tonight
for bbox_name, bbox in BBOXES.items():
    cx, cy = centroid(bbox)
    map_extent = ",".join(f"{x:.8f}" for x in bbox)
    geom = f"{cx:.8f},{cy:.8f}"
    for tname, tparams in time_variants.items():
        params = {
            "f": "json",
            "geometry": geom,
            "geometryType": "esriGeometryPoint",
            "sr": "4326",
            "mapExtent": map_extent,
            "imageDisplay": "512,512,96",
            "tolerance": "3",
            "returnGeometry": "false",
            "returnCatalogItems": "true",
        }
        params.update(tparams)
        url = qurl(BASE + "/identify", params)
        st, ct, blob, err = bounded_get(url)
        parsed = parse_sample(blob)
        rec = {
            "probe_type": "identify",
            "bbox_name": bbox_name,
            "time_variant": tname,
            "point": [cx, cy],
            "url": url,
            "http_status": st,
            "content_type": ct,
            "bytes": len(blob),
            "error": err,
            "parsed": parsed,
        }
        results["identify_probes"].append(rec)

# Ranking
exports = []
for r in results["export_probes"]:
    d = r["diag"]
    score = 0
    if d.get("meaningful_image"):
        score += 100
    score += int(d.get("unique_colors") or 0)
    score += min(10, int((d.get("nontransparent_pixels") or 0) / 1000))
    exports.append((score, r))
results["best_export_candidates"] = [
    {
        "score": s,
        "bbox_name": r["bbox_name"],
        "time_variant": r["time_variant"],
        "format": r["format"],
        "http_status": r["http_status"],
        "content_type": r["content_type"],
        "bytes": r["diag"].get("bytes"),
        "unique_colors": r["diag"].get("unique_colors"),
        "nontransparent_pixels": r["diag"].get("nontransparent_pixels"),
        "dominant_fraction": r["diag"].get("dominant_fraction"),
        "cache_path": r["diag"].get("cache_path"),
        "meaningful_image": r["diag"].get("meaningful_image"),
        "url": r["url"],
    }
    for s, r in sorted(exports, key=lambda x: x[0], reverse=True)[:12]
]

idents = []
for r in results["identify_probes"]:
    parsed = r["parsed"]
    score = parsed.get("meaningful_count") or 0
    idents.append((score, r))
results["best_identify_candidates"] = [
    {
        "score": s,
        "bbox_name": r["bbox_name"],
        "time_variant": r["time_variant"],
        "http_status": r["http_status"],
        "content_type": r["content_type"],
        "bytes": r["bytes"],
        "meaningful_count": r["parsed"].get("meaningful_count"),
        "meaningful_values": r["parsed"].get("meaningful_values"),
        "keys": r["parsed"].get("keys"),
        "url": r["url"],
    }
    for s, r in sorted(idents, key=lambda x: x[0], reverse=True)[:12]
]

out_json = Path("reports/task_previews/manual_cdl_endpoint_audit_now.json")
out_md = Path("reports/task_previews/manual_cdl_endpoint_audit_now.md")
out_json.write_text(json.dumps(results, indent=2, sort_keys=True))

lines = []
lines.append("# Manual CDL Endpoint Audit Now")
lines.append("")
lines.append(f"- Generated UTC: {results['generated_at_utc']}")
lines.append(f"- Base: `{BASE}`")
lines.append(f"- Metadata status: `{meta_entry.get('http_status')}` JSON: `{meta_entry.get('json_ok')}`")
lines.append(f"- Service name: `{meta_entry.get('name')}`")
lines.append(f"- Capabilities: `{meta_entry.get('capabilities')}`")
lines.append(f"- TimeInfo: `{json.dumps(meta_entry.get('timeInfo'), sort_keys=True)[:500]}`")
lines.append("")
lines.append("## Best export candidates")
lines.append("")
lines.append("| score | bbox | time_variant | fmt | http | bytes | unique | nontransparent | meaningful | cache |")
lines.append("|---:|---|---|---|---:|---:|---:|---:|---|---|")
for r in results["best_export_candidates"]:
    lines.append(f"| {r['score']} | {r['bbox_name']} | {r['time_variant']} | {r['format']} | {r['http_status']} | {r['bytes']} | {r['unique_colors']} | {r['nontransparent_pixels']} | {r['meaningful_image']} | `{r.get('cache_path')}` |")
lines.append("")
lines.append("## Best identify candidates")
lines.append("")
lines.append("| score | bbox | time_variant | http | bytes | meaningful_count | values |")
lines.append("|---:|---|---|---:|---:|---:|---|")
for r in results["best_identify_candidates"]:
    vals = json.dumps(r.get("meaningful_values"), sort_keys=True)[:250]
    lines.append(f"| {r['score']} | {r['bbox_name']} | {r['time_variant']} | {r['http_status']} | {r['bytes']} | {r['meaningful_count']} | `{vals}` |")
lines.append("")
lines.append("## Decision")
lines.append("")
best_export = results["best_export_candidates"][0] if results["best_export_candidates"] else {}
best_ident = results["best_identify_candidates"][0] if results["best_identify_candidates"] else {}
if best_export.get("meaningful_image"):
    lines.append(f"Best path: exportImage `{best_export['bbox_name']}` `{best_export['time_variant']}` `{best_export['format']}`.")
elif (best_ident.get("meaningful_count") or 0) > 0:
    lines.append(f"Image renderer is weak, but identify found values for `{best_ident['bbox_name']}` `{best_ident['time_variant']}`.")
else:
    lines.append("No meaningful CDL pixels or sample values found in tested patterns. Likely wrong CDL_WM time/year pattern, endpoint behavior issue, or bbox/service mismatch.")
out_md.write_text("\n".join(lines))

print("WROTE", out_json)
print("WROTE", out_md)
print()
print("BEST EXPORT CANDIDATES")
for r in results["best_export_candidates"][:8]:
    print(r["score"], r["bbox_name"], r["time_variant"], r["format"], "http", r["http_status"], "bytes", r["bytes"], "unique", r["unique_colors"], "nontransparent", r["nontransparent_pixels"], "meaningful", r["meaningful_image"], r.get("cache_path"))
print()
print("BEST IDENTIFY CANDIDATES")
for r in results["best_identify_candidates"][:8]:
    print(r["score"], r["bbox_name"], r["time_variant"], "http", r["http_status"], "bytes", r["bytes"], "meaningful_count", r["meaningful_count"], "values", r["meaningful_values"])
