from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class LocalPaths:
    """All generated local state lives outside the repository by default."""

    config_home: Path
    state_home: Path
    cache_home: Path
    temporary_root: Path
    user_config: Path
    capability_profile: Path
    project_config: Path | None


def _application_home(
    environ: Mapping[str, str],
    override: str,
    xdg_name: str,
    fallback: Path,
) -> Path:
    if environ.get(override):
        return Path(environ[override]).expanduser()
    if environ.get(xdg_name):
        return Path(environ[xdg_name]).expanduser() / "fasterraster"
    return fallback / "fasterraster"


def resolve_local_paths(
    project_root: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> LocalPaths:
    env = os.environ if environ is None else environ
    user_home = Path.home() if home is None else home
    config_home = _application_home(
        env,
        "FASTERRASTER_CONFIG_HOME",
        "XDG_CONFIG_HOME",
        user_home / ".config",
    )
    state_home = _application_home(
        env,
        "FASTERRASTER_STATE_HOME",
        "XDG_STATE_HOME",
        user_home / ".local" / "state",
    )
    cache_home = _application_home(
        env,
        "FASTERRASTER_CACHE_HOME",
        "XDG_CACHE_HOME",
        user_home / ".cache",
    )
    temporary_root = Path(
        env.get("FASTERRASTER_TEMP_HOME", str(Path(tempfile.gettempdir()) / "fasterraster"))
    ).expanduser()
    project_config = (
        project_root.resolve() / ".fasterraster" / "config.toml"
        if project_root is not None
        else None
    )
    profile_name = env.get("FASTERRASTER_PROFILE", "default")
    return LocalPaths(
        config_home=config_home,
        state_home=state_home,
        cache_home=cache_home,
        temporary_root=temporary_root,
        user_config=config_home / "config.toml",
        capability_profile=state_home / "capabilities" / f"{profile_name}.json",
        project_config=project_config,
    )


def ensure_local_directories(paths: LocalPaths) -> None:
    for directory in (
        paths.config_home,
        paths.state_home,
        paths.cache_home,
        paths.temporary_root,
        paths.capability_profile.parent,
    ):
        directory.mkdir(parents=True, exist_ok=True)
