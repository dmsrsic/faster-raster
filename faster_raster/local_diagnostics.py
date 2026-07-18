from __future__ import annotations

import hashlib
import os
import platform
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from faster_raster.local_paths import LocalPaths, ensure_local_directories


REQUIRED_GDAL_COMMANDS = ("gdalinfo", "gdal_translate", "gdalbuildvrt", "gdalwarp")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def detect_wsl(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    if env.get("WSL_DISTRO_NAME") or env.get("WSL_INTEROP"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        return False


def available_memory_bytes() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        pages = os.sysconf("SC_AVPHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int(pages) * int(page_size)
    except (AttributeError, OSError, ValueError):
        return None


def detect_preview_opener(
    *,
    which: Callable[[str], str | None] = shutil.which,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    wsl = detect_wsl(environ)
    if wsl and which("explorer.exe") and which("wslpath"):
        return {"available": True, "kind": "wsl_explorer", "command": "explorer.exe"}
    if which("xdg-open"):
        return {"available": True, "kind": "linux_xdg", "command": "xdg-open"}
    return {"available": False, "kind": "none", "command": None}


def _safe_run(
    command: Sequence[str],
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> subprocess.CompletedProcess[str]:
    return runner(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def _driver_inventory(output: str) -> list[str]:
    drivers: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("Supported Formats"):
            continue
        name = stripped.split(" - ", 1)[0].split()[0]
        if name and name.replace("_", "").isalnum():
            drivers.append(name)
    return sorted(set(drivers))


def _writable_check(directory: Path) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".fr-write-test-", dir=directory)
    path = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(b"fasterraster\n")
            stream.flush()
            os.fsync(stream.fileno())
        return {"writable": path.read_bytes() == b"fasterraster\n", "path": str(directory)}
    finally:
        path.unlink(missing_ok=True)


def recommend_execution(cpu_count: int, memory_bytes: int | None, cache_free: int) -> dict[str, Any]:
    memory_gib = memory_bytes / (1024**3) if memory_bytes is not None else None
    cpu_bound = max(1, cpu_count // 2)
    memory_bound = max(1, int(memory_gib // 4)) if memory_gib is not None else 1
    parallel_candidates = {
        "half_cpu_threads": cpu_bound,
        "four_gib_per_task": memory_bound,
        "hard_safety_cap": 8,
    }
    parallel = min(parallel_candidates.values())
    parallel_limit = sorted(
        name for name, value in parallel_candidates.items() if value == parallel
    )
    tile_candidates = {
        "low_memory": 512,
        "moderate_memory": 1024,
        "higher_memory": 1800,
    }
    if (memory_gib or 0) >= 12:
        tile, tile_limit = tile_candidates["higher_memory"], "higher_memory"
    elif (memory_gib or 0) >= 6:
        tile, tile_limit = tile_candidates["moderate_memory"], "moderate_memory"
    else:
        tile, tile_limit = tile_candidates["low_memory"], "low_memory"
    disk_fraction = int(cache_free * 0.05)
    byte_candidates = {
        "minimum_floor": 50_000_000,
        "five_percent_cache_free": disk_fraction,
        "hard_safety_cap": 2_000_000_000,
    }
    byte_ceiling = min(
        byte_candidates["hard_safety_cap"],
        max(byte_candidates["minimum_floor"], disk_fraction),
    )
    byte_limit = (
        "minimum_floor"
        if disk_fraction < byte_candidates["minimum_floor"]
        else "hard_safety_cap"
        if disk_fraction > byte_candidates["hard_safety_cap"]
        else "five_percent_cache_free"
    )
    risky = memory_gib is not None and memory_gib < 6
    evidence = f"{cpu_count} CPU threads"
    if memory_gib is not None:
        evidence += f" and {memory_gib:.2f} GiB available memory"
    return {
        "heuristic_version": "beta-gate-1.1",
        "applied": False,
        "observed_facts": {
            "cpu_threads": cpu_count,
            "available_memory_bytes": memory_bytes,
            "available_memory_gib": round(memory_gib, 3) if memory_gib is not None else None,
            "cache_free_bytes": cache_free,
        },
        "intermediate_candidates": {
            "maximum_parallel_tasks": parallel_candidates,
            "service_tile_size": tile_candidates,
            "default_byte_ceiling": byte_candidates,
        },
        "limiting_factor": {
            "maximum_parallel_tasks": parallel_limit,
            "service_tile_size": tile_limit,
            "default_byte_ceiling": byte_limit,
        },
        "safety_note": (
            "Recommendations are conservative heuristics, not performance, memory, "
            "runtime, or successful-completion guarantees."
        ),
        "maximum_parallel_tasks": {
            "value": parallel,
            "reason": evidence + "; selected the smallest CPU, memory, and safety candidate",
        },
        "service_tile_size": {
            "value": tile,
            "reason": f"selected the {tile_limit.replace('_memory', '')} memory tier",
        },
        "default_byte_ceiling": {
            "value": byte_ceiling,
            "reason": "selected from the disk-fraction candidate with floor and safety cap",
        },
        "native_resolution_risky": {
            "value": risky,
            "reason": "native-resolution work may be memory-intensive" if risky else "no low-memory warning detected",
        },
        "workflow_hint": {
            "value": "preview_or_overview" if risky else "bounded_native_or_overview",
            "reason": "heuristic workflow guidance only",
        },
    }


def run_doctor(
    paths: LocalPaths,
    *,
    offline: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    environ: Mapping[str, str] | None = None,
    now: Callable[[], datetime] = utc_now,
) -> dict[str, Any]:
    started = time.monotonic()
    ensure_local_directories(paths)
    probe_parent = paths.temporary_root / "doctor"
    probe_parent.mkdir(parents=True, exist_ok=True)
    probe_dir = Path(tempfile.mkdtemp(prefix="probe-", dir=probe_parent))
    checks: dict[str, Any] = {}
    warnings: list[str] = []
    failures: list[str] = []
    drivers: list[str] = []
    gdal_version: str | None = None
    try:
        commands: dict[str, Any] = {}
        for command in REQUIRED_GDAL_COMMANDS:
            location = which(command)
            commands[command] = {"available": bool(location), "location": location}
            if not location:
                failures.append(f"missing required GDAL command: {command}")
        checks["gdal_commands"] = commands

        if commands["gdalinfo"]["available"]:
            version_result = _safe_run(["gdalinfo", "--version"], runner)
            if version_result.returncode == 0:
                gdal_version = version_result.stdout.strip() or version_result.stderr.strip()
            else:
                failures.append("gdalinfo --version failed")
            formats_result = _safe_run(["gdalinfo", "--formats"], runner)
            if formats_result.returncode == 0:
                drivers = _driver_inventory(formats_result.stdout)
            else:
                failures.append("unable to enumerate GDAL raster drivers")

        for name, directory in (
            ("configuration", paths.config_home),
            ("cache", paths.cache_home),
            ("temporary", paths.temporary_root),
        ):
            try:
                checks[f"writable_{name}"] = _writable_check(directory)
            except OSError as exc:
                checks[f"writable_{name}"] = {"writable": False, "path": str(directory), "error": str(exc)}
                failures.append(f"{name} directory is not writable")

        tiny = probe_dir / "tiny.bin"
        tiny.write_bytes(b"FasterRaster local fixture\n")
        checksum = hashlib.sha256(tiny.read_bytes()).hexdigest()
        checks["temporary_file"] = {"status": "PASS", "sha256": checksum}
        tiny.unlink()

        raster_check: dict[str, Any]
        if commands["gdal_translate"]["available"] and commands["gdalinfo"]["available"]:
            pgm = probe_dir / "tiny.pgm"
            raster = probe_dir / "tiny.tif"
            pgm.write_bytes(b"P5\n2 2\n255\n\x00\x40\x80\xff")
            translate = _safe_run(["gdal_translate", "-q", "-of", "GTiff", str(pgm), str(raster)], runner)
            inspect = _safe_run(["gdalinfo", "-json", str(raster)], runner) if translate.returncode == 0 else None
            if translate.returncode == 0 and inspect is not None and inspect.returncode == 0 and raster.is_file():
                raster_check = {
                    "status": "PASS",
                    "bytes": raster.stat().st_size,
                    "sha256": hashlib.sha256(raster.read_bytes()).hexdigest(),
                }
            else:
                raster_check = {"status": "FAIL", "reason": "tiny raster create/read check failed"}
                failures.append("unable to create and inspect a tiny local raster")
        else:
            raster_check = {"status": "FAIL", "reason": "required GDAL commands are missing"}
        checks["tiny_raster"] = raster_check

        try:
            context = ssl.create_default_context()
            https = {"available": True, "certificate_validation": context.verify_mode == ssl.CERT_REQUIRED}
        except Exception as exc:
            https = {"available": False, "certificate_validation": False, "error": str(exc)}
            failures.append("HTTPS certificate validation is unavailable")
        checks["https"] = https
        checks["vsicurl"] = {
            "available": bool(gdal_version and https["available"]),
            "evidence": "GDAL and local TLS support detected; no network request was made",
        }
        opener = detect_preview_opener(which=which, environ=environ)
        checks["preview_opener"] = opener
        if not opener["available"]:
            warnings.append("no supported local preview opener was detected")

        cpu = os.cpu_count() or 1
        memory = available_memory_bytes()
        temp_disk = shutil.disk_usage(paths.temporary_root)
        cache_disk = shutil.disk_usage(paths.cache_home)
        resources = {
            "cpu_count": cpu,
            "available_memory_bytes": memory,
            "temporary_disk_free_bytes": temp_disk.free,
            "cache_disk_free_bytes": cache_disk.free,
        }
        recommendations = recommend_execution(cpu, memory, cache_disk.free)
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)
        try:
            if not any(probe_parent.iterdir()):
                probe_parent.rmdir()
        except OSError:
            pass

    status = "FAIL" if failures else "WARN" if warnings else "PASS"
    timestamp = now().isoformat()
    return {
        "schema_version": "fasterraster.doctor/v1",
        "status": status,
        "checked_at": timestamp,
        "offline": offline,
        "machine": {
            "operating_system": platform.system(),
            "platform_release": platform.release(),
            "architecture": platform.machine(),
            "is_wsl": detect_wsl(environ),
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
        },
        "gdal": {"version": gdal_version, "drivers": drivers},
        "checks": checks,
        "resources": resources,
        "recommendations": recommendations,
        "warnings": warnings,
        "failures": failures,
        "duration_seconds": round(time.monotonic() - started, 3),
        "temporary_artifacts_removed": not probe_dir.exists(),
        "network_requests": 0,
    }
