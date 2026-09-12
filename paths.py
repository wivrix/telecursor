"""Resolve Telecursor home, config, and runtime directories."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from platform_util import user_config_home


@lru_cache
def package_dir() -> Path:
    """Directory containing this source tree / installed modules."""
    return Path(__file__).resolve().parent


@lru_cache
def app_home() -> Path:
    """
    Writable data directory for .env, logs, and PID files.

    Resolution order:
      1. $TELECURSOR_HOME / %TELECURSOR_HOME%
      2. Source checkout (directory with pyproject.toml) — for editable/dev use
      3. Platform config dir:
           Windows → %APPDATA%\\telecursor
           macOS   → ~/Library/Application Support/telecursor
           Linux   → ~/.config/telecursor
    """
    override = os.environ.get("TELECURSOR_HOME", "").strip()
    if override:
        path = Path(override).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    pkg = package_dir()
    if (pkg / "pyproject.toml").is_file() and os.access(pkg, os.W_OK):
        return pkg

    path = user_config_home()
    path.mkdir(parents=True, exist_ok=True)
    return path


def env_path() -> Path:
    return app_home() / ".env"


def runtime_dir() -> Path:
    path = app_home() / ".runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_temp_upload_dir() -> Path:
    return app_home() / "temp_uploads"


def clear_path_caches() -> None:
    package_dir.cache_clear()
    app_home.cache_clear()
