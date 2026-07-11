from __future__ import annotations

import json
from pathlib import Path

from faster_raster import local_executor, run_receipts

TASK_ID = "example_wave1_climate_stack"


class FakeHeaders(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class FakeResponse:
    def __init__(self, data: bytes, *, status: int = 206, content_type: str = "application/octet-stream", content_range: str | None = "bytes 0-3/100"):
        self.status = status
        self.headers = FakeHeaders({"Content-Type": content_type})
        if content_range:
            self.headers["Content-Range"] = content_range
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def getcode(self):
        return self.status

    def read(self, size=-1):
        return self._data[:size]


def sample_bytes(url: str) -> bytes:
    if url.endswith(".gz"):
        return b"\x1f\x8bmock-gzip-prefix"
    if url.endswith(".zip"):
        return b"PK\x03\x04mock-zip-prefix"
    return b"CDFmock-netcdf-prefix"


def fake_urlopen(request, timeout=0):
    return FakeResponse(sample_bytes(request.full_url))


def deterministic_clock():
    values = [f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z" for i in range(240)]
    iterator = iter(values)
    return lambda: next(iterator)


def test_receipt_hash_verifies_and_tamper_fails(monkeypatch, tmp_path):
    result = local_executor.execute_local(
        TASK_ID,
        allow_network=True,
        timestamp_utc="2026-01-01T00:00:00Z",
        now_fn=deterministic_clock(),
        sleep_fn=lambda seconds: None,
        urlopen=fake_urlopen,
        cache_root=tmp_path / "cache",
    )
    receipt_path = Path(result["receipt_path"])

    verification = run_receipts.verify_run_receipt(
        receipt_path,
        package_path=local_executor.PACKAGE_ROOT / TASK_ID / "execution_package.json",
        manifest_path=local_executor.COMPILE_ROOT / TASK_ID / "acquisition_manifest.jsonl",
        dag_path=local_executor.PACKAGE_ROOT / TASK_ID / "dag.json",
    )
    assert verification["verification_status"] == "PASS"

    tampered = json.loads(receipt_path.read_text())
    tampered["total_bytes_read"] += 1
    receipt_path.write_text(json.dumps(tampered, indent=2, sort_keys=True) + "\n")

    verification = run_receipts.verify_run_receipt(
        receipt_path,
        package_path=local_executor.PACKAGE_ROOT / TASK_ID / "execution_package.json",
        manifest_path=local_executor.COMPILE_ROOT / TASK_ID / "acquisition_manifest.jsonl",
        dag_path=local_executor.PACKAGE_ROOT / TASK_ID / "dag.json",
    )
    assert verification["verification_status"] == "FAIL"


def test_cache_verification_detects_corruption(monkeypatch, tmp_path):
    result = local_executor.execute_local(
        TASK_ID,
        allow_network=True,
        timestamp_utc="2026-01-01T00:00:00Z",
        now_fn=deterministic_clock(),
        sleep_fn=lambda seconds: None,
        urlopen=fake_urlopen,
        cache_root=tmp_path / "cache",
    )
    run_dir = Path(result["receipt_path"]).parent
    cache_index = json.loads((run_dir / "cache_index.json").read_text())

    assert run_receipts.verify_cache_index(cache_index)["verification_status"] == "PASS"
    Path(cache_index["entries"][0]["cache_path"]).write_bytes(b"corrupt")
    assert run_receipts.verify_cache_index(cache_index)["verification_status"] == "FAIL"


def test_absolute_paths_are_excluded_from_receipt_contract_hash():
    receipt = {
        "run_id": "run-a",
        "started_at_utc": "2026-01-01T00:00:00Z",
        "receipt_contract_sha256": None,
        "cache_path": "/tmp/a/cache/file.head1",
        "stable": "value",
    }
    first = run_receipts.compute_receipt_contract_sha256(receipt, Path("/tmp/a"))
    receipt["run_id"] = "run-b"
    receipt["started_at_utc"] = "2026-01-01T00:00:10Z"
    receipt["cache_path"] = "/tmp/b/cache/file.head1"
    second = run_receipts.compute_receipt_contract_sha256(receipt, Path("/tmp/b"))

    assert first == second
