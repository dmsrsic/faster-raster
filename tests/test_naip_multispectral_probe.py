from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "scripts/probe_naip_multispectral.py"


def test_probe_requires_explicit_live_opt_in(tmp_path):
    output = tmp_path / "probe"
    result = subprocess.run(
        [
            sys.executable,
            str(PROBE),
            "--bbox=-112.05,33.40,-112.0499,33.4001",
            "--year",
            "2023",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "--live is required" in result.stderr
    assert not output.exists()


def test_probe_help_is_offline_and_documents_ten_mb_ceiling():
    result = subprocess.run(
        [sys.executable, str(PROBE), "--help"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "--maximum-bytes" in result.stdout
    assert "raw bands 0,1,2,3" in result.stdout
