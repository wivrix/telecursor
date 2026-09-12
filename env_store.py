"""Read/write helpers for the Telecursor .env file."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Mapping

from paths import env_path

# Keys we manage via setup / config CLI
MANAGED_KEYS = (
    "BOT_TOKEN",
    "ALLOWED_USERS",
    "ALLOWED_WORKSPACE_PATH",
    "DEFAULT_WORKSPACE_PATH",
    "AGENT_BIN",
    "CURSOR_API_KEY",
    "TEMP_UPLOAD_DIR",
    "STREAM_EDIT_INTERVAL",
    "MAX_CONCURRENT_RUNS_PER_USER",
    "DEFAULT_MODE",
    "AGENT_MODEL",
    "LOG_LEVEL",
)


def get_env_path() -> Path:
    return env_path()


def env_exists() -> bool:
    return env_path().is_file()


def read_env_map(path: Path | None = None) -> dict[str, str]:
    target = path or env_path()
    if not target.is_file():
        return {}
    result: dict[str, str] = {}
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        result[key] = value
    return result


def _format_value(value: str) -> str:
    if re.search(r'[\s#"\'\\]', value) or value == "":
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def write_env_map(values: Mapping[str, str], path: Path | None = None) -> Path:
    """
    Merge `values` into the existing .env (preserving unrelated keys/comments
    where possible). Managed keys are rewritten; unknown keys in `values`
    are appended.
    """
    target = path or env_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    existing_lines: list[str] = []
    if target.is_file():
        existing_lines = target.read_text(encoding="utf-8").splitlines()

    updates = {k: str(v) for k, v in values.items() if v is not None}
    seen: set[str] = set()
    out: list[str] = []

    for raw in existing_lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(raw)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={_format_value(updates[key])}")
            seen.add(key)
        else:
            out.append(raw)

    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={_format_value(value)}")

    if out and out[-1] != "":
        out.append("")

    target.write_text("\n".join(out), encoding="utf-8")
    if os.name != "nt":
        try:
            target.chmod(0o600)
        except OSError:
            pass
    return target


def redact_token(token: str, keep: int = 6) -> str:
    if not token:
        return "(empty)"
    if len(token) <= keep * 2:
        return "*" * len(token)
    return f"{token[:keep]}…{token[-4:]}"
