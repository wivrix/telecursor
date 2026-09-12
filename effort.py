"""Model effort helpers for Cursor Agent parameterized --model args."""

from __future__ import annotations

import re

# Cursor CLI supports: 'model[context=1m,effort=high,fast=false]'
EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")
_CLEAR = frozenset({"", "auto", "default", "none", "off"})

_BRACKET_RE = re.compile(r"^(.+?)(?:\[(.*)\])?\s*$")


def normalize_effort(value: str | None) -> str | None:
    """Return a canonical effort level, or None to clear / use model default."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in _CLEAR:
        return None
    # Accept common aliases
    aliases = {
        "med": "medium",
        "extra": "xhigh",
        "extra-high": "xhigh",
        "extrahigh": "xhigh",
        "xl": "xhigh",
        "x-high": "xhigh",
    }
    text = aliases.get(text, text)
    if text not in EFFORT_LEVELS:
        raise ValueError(
            f"Invalid effort `{value}`. Choose one of: {', '.join(EFFORT_LEVELS)}, or auto"
        )
    return text


def compose_model_arg(model: str | None, effort: str | None) -> str | None:
    """
    Build the --model value, optionally injecting effort= into bracket params.

    When model is None (auto), effort cannot be applied via the CLI — returns None.
    """
    if not model:
        return None
    if not effort:
        return model

    match = _BRACKET_RE.match(model.strip())
    if not match:
        return model
    base = match.group(1).strip()
    existing = match.group(2) or ""

    params: dict[str, str] = {}
    if existing.strip():
        for part in existing.split(","):
            part = part.strip()
            if not part:
                continue
            if "=" in part:
                key, val = part.split("=", 1)
                params[key.strip()] = val.strip()
            else:
                params[part] = "true"
    params["effort"] = effort
    inner = ",".join(f"{k}={v}" for k, v in params.items())
    return f"{base}[{inner}]"
