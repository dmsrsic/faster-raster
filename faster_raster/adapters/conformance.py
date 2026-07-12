from __future__ import annotations

from pathlib import Path
from typing import Any
from faster_raster.adapters.capabilities import CAPABILITY_NAMES, adapter_capability_catalog
from faster_raster.run_receipts import write_json

def verify_adapter_conformance(*, root: Path | None = None) -> dict[str, Any]:
    catalog = adapter_capability_catalog()
    failures: list[str] = []
    for adapter in catalog["adapters"]:
        caps = adapter.get("capabilities") or {}
        missing = [name for name in CAPABILITY_NAMES if name not in caps]
        if missing:
            failures.append(f"{adapter['adapter_id']} missing capabilities: {','.join(missing)}")
        if not adapter.get("adapter_capability_contract_sha256"):
            failures.append(f"{adapter['adapter_id']} missing capability hash")
        if caps.get("materialize") is True:
            failures.append(f"{adapter['adapter_id']} silently claims materialize")
    report = {"schema_version": 1, "adapter_conformance_status": "PASS" if not failures else "FAIL", "verification_status": "PASS" if not failures else "FAIL", "adapter_count": catalog["adapter_count"], "adapter_capability_hashes": {item["adapter_id"]: item["adapter_capability_contract_sha256"] for item in catalog["adapters"]}, "failures": failures, "warnings": []}
    if root is not None:
        base = root / "reports" / "adapters"
        write_json(base / "adapter_capability_catalog.json", catalog)
        write_json(base / "adapter_conformance.json", report)
    return report
