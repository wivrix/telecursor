"""Cursor account / agent health helpers (usage limits, models, auth)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

from platform_util import agent_bin_search_paths, cursor_auth_candidates


@dataclass
class AgentHealth:
    installed: bool
    path: str | None
    authenticated: bool
    email: str | None
    subscription: str | None
    error: str | None = None


# Short TTL so /health and prompt preflight share work without going stale.
_HEALTH_CACHE_TTL_SEC = 45.0
_health_cache: dict[str, tuple[float, "AgentHealth"]] = {}


def resolve_agent_bin(configured: str | Path | None = None) -> Path | None:
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    for name in ("agent", "cursor-agent", "agent.exe", "cursor-agent.exe", "agent.cmd"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    candidates.extend(agent_bin_search_paths())

    seen: set[Path] = set()
    for path in candidates:
        try:
            resolved = path.expanduser()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if not resolved.exists():
            continue
        # On Windows, X_OK is not a reliable executable bit
        if os.name == "nt" or os.access(resolved, os.X_OK):
            try:
                return resolved.resolve()
            except OSError:
                return resolved
    return None


def load_cursor_access_token(api_key: str | None = None) -> str | None:
    if api_key and api_key.strip():
        return api_key.strip()
    env_key = os.environ.get("CURSOR_API_KEY", "").strip()
    if env_key:
        return env_key
    for path in cursor_auth_candidates():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        token = data.get("accessToken") or data.get("token")
        if isinstance(token, str) and token.strip():
            return token.strip()
    return None


def _http_json(
    method: str,
    url: str,
    token: str,
    *,
    body: bytes | None = None,
    connect: bool = False,
    timeout: float = 20.0,
) -> Any:
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "telecursor-bot/1.0",
        "Accept": "application/json",
    }
    if body is not None or method.upper() == "POST":
        headers["Content-Type"] = "application/json"
    if connect:
        headers["Connect-Protocol-Version"] = "1"
    req = Request(url, data=body, headers=headers, method=method.upper())
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed Cursor API hosts
        raw = resp.read()
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def fetch_usage_sync(api_key: str | None = None) -> dict[str, Any]:
    """
    Fetch remaining usage from Cursor Dashboard APIs.

    Returns a normalized dict suitable for Telegram display.
    """
    token = load_cursor_access_token(api_key)
    if not token:
        return {
            "ok": False,
            "error": (
                "Not authenticated. Run `agent login` on this machine "
                "or set CURSOR_API_KEY in .env."
            ),
        }

    try:
        period = _http_json(
            "POST",
            "https://api2.cursor.sh/aiserver.v1.DashboardService/GetCurrentPeriodUsage",
            token,
            body=b"{}",
            connect=True,
        )
        profile = _http_json(
            "GET",
            "https://api2.cursor.sh/auth/full_stripe_profile",
            token,
        )
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        return {"ok": False, "error": f"Usage API error HTTP {exc.code}: {detail or exc.reason}"}
    except URLError as exc:
        return {"ok": False, "error": f"Network error contacting Cursor API: {exc.reason}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Failed to fetch usage: {exc}"}

    plan = period.get("planUsage") or {}
    total_pct = plan.get("totalPercentUsed")
    auto_pct = plan.get("autoPercentUsed")
    api_pct = plan.get("apiPercentUsed")
    remaining_total = None if total_pct is None else max(0.0, 100.0 - float(total_pct))
    remaining_auto = None if auto_pct is None else max(0.0, 100.0 - float(auto_pct))
    remaining_api = None if api_pct is None else max(0.0, 100.0 - float(api_pct))

    cycle_end = _ms_to_iso(period.get("billingCycleEnd"))
    cycle_start = _ms_to_iso(period.get("billingCycleStart"))

    return {
        "ok": True,
        "membership": profile.get("membershipType") or profile.get("individualMembershipType"),
        "subscription_status": profile.get("subscriptionStatus"),
        "display_message": period.get("displayMessage"),
        "auto_message": period.get("autoModelSelectedDisplayMessage"),
        "api_message": period.get("namedModelSelectedDisplayMessage"),
        "total_percent_used": total_pct,
        "auto_percent_used": auto_pct,
        "api_percent_used": api_pct,
        "remaining_total_percent": remaining_total,
        "remaining_auto_percent": remaining_auto,
        "remaining_api_percent": remaining_api,
        "plan_limit": plan.get("limit"),
        "included_spend": plan.get("includedSpend"),
        "total_spend": plan.get("totalSpend"),
        "billing_cycle_start": cycle_start,
        "billing_cycle_end": cycle_end,
        "raw_period": period,
        "raw_profile": profile,
    }


async def fetch_usage(api_key: str | None = None) -> dict[str, Any]:
    return await asyncio.to_thread(fetch_usage_sync, api_key)


def format_usage_message(usage: dict[str, Any]) -> str:
    if not usage.get("ok"):
        return f"❌ Could not load usage.\n{usage.get('error', 'Unknown error')}"

    def _md(value: Any) -> str:
        text = str(value)
        for ch in ("\\", "`", "*", "_", "["):
            text = text.replace(ch, f"\\{ch}")
        return text

    lines = ["📊 *Cursor usage / remaining limit*", ""]
    if usage.get("membership"):
        lines.append(
            f"Plan: `{usage['membership']}`"
            + (
                f" ({usage['subscription_status']})"
                if usage.get("subscription_status")
                else ""
            )
        )
    if usage.get("remaining_total_percent") is not None:
        lines.append(
            f"Total remaining: *{usage['remaining_total_percent']:.1f}%* "
            f"(used {float(usage.get('total_percent_used') or 0):.1f}%)"
        )
    if usage.get("remaining_auto_percent") is not None:
        lines.append(
            f"Auto / included remaining: *{usage['remaining_auto_percent']:.1f}%*"
        )
    if usage.get("remaining_api_percent") is not None:
        lines.append(
            f"API / named-model remaining: *{usage['remaining_api_percent']:.1f}%*"
        )
    if usage.get("auto_message"):
        lines.append(f"\n_{_md(usage['auto_message'])}_")
    if usage.get("api_message"):
        lines.append(f"_{_md(usage['api_message'])}_")
    if usage.get("display_message"):
        lines.append(f"\n⚠️ {_md(usage['display_message'])}")
    if usage.get("billing_cycle_end"):
        lines.append(f"\nBilling cycle ends: `{usage['billing_cycle_end']}`")
    lines.append("\nDashboard: https://cursor.com/dashboard?tab=usage")
    return "\n".join(lines)


def _ms_to_iso(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        ms = int(str(value))
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
    except (TypeError, ValueError, OSError):
        return str(value)


async def run_agent_json(agent_bin: Path, *args: str, timeout: float = 45.0) -> dict[str, Any]:
    proc = await asyncio.create_subprocess_exec(
        str(agent_bin),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "NO_OPEN_BROWSER": "1", "CI": "1"},
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(f"Timed out running: {agent_bin} {' '.join(args)}")
    text = stdout.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0 and not text:
        err = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(err or f"agent exited {proc.returncode}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON from agent: {text[:200]}") from exc


async def check_agent_health(
    agent_bin: str | Path | None = None,
    *,
    force_refresh: bool = False,
) -> AgentHealth:
    cache_key = str(agent_bin) if agent_bin is not None else ""
    now = asyncio.get_running_loop().time()
    if not force_refresh:
        cached = _health_cache.get(cache_key)
        if cached is not None:
            cached_at, health = cached
            if now - cached_at < _HEALTH_CACHE_TTL_SEC:
                return health

    path = resolve_agent_bin(agent_bin)
    if path is None:
        health = AgentHealth(
            installed=False,
            path=None,
            authenticated=False,
            email=None,
            subscription=None,
            error=(
                "cursor-agent / agent CLI not found. Install it "
                "(https://cursor.com/docs/cli) or set AGENT_BIN in setup."
            ),
        )
        _health_cache[cache_key] = (now, health)
        return health
    try:
        about = await run_agent_json(path, "about", "--format", "json")
        who = await run_agent_json(path, "whoami", "--format", "json")
    except Exception as exc:  # noqa: BLE001
        health = AgentHealth(
            installed=True,
            path=str(path),
            authenticated=False,
            email=None,
            subscription=None,
            error=str(exc),
        )
        _health_cache[cache_key] = (now, health)
        return health
    authenticated = bool(who.get("isAuthenticated"))
    email = None
    user = who.get("userInfo") or {}
    if isinstance(user, dict):
        email = user.get("email")
    email = email or about.get("userEmail")
    health = AgentHealth(
        installed=True,
        path=str(path),
        authenticated=authenticated,
        email=email,
        subscription=about.get("subscriptionTier") or about.get("subscriptionTier"),
        error=None if authenticated else "Agent is installed but not logged in. Run: agent login",
    )
    _health_cache[cache_key] = (now, health)
    return health


async def list_models(agent_bin: str | Path | None = None) -> list[tuple[str, str]]:
    """Return [(model_id, display_name), ...] from `agent models` text output."""
    path = resolve_agent_bin(agent_bin)
    if path is None:
        raise FileNotFoundError("agent binary not found")
    proc = await asyncio.create_subprocess_exec(
        str(path),
        "models",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "NO_OPEN_BROWSER": "1", "CI": "1"},
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=45.0)
    text = stdout.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode("utf-8", errors="replace") or "models failed")
    models: list[tuple[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("available"):
            continue
        if " - " in line:
            mid, _, name = line.partition(" - ")
            models.append((mid.strip(), name.strip()))
        else:
            models.append((line, line))
    return models


# Patterns for mapping agent failures to user-facing messages
ERROR_PATTERNS: list[tuple[str, str]] = [
    (
        r"usage limit|hit your (usage )?limit|limit exceeded|out of (usage|credits)|quota",
        "⚠️ Cursor usage limit exceeded. Check /limit or https://cursor.com/dashboard?tab=usage",
    ),
    (
        r"rate.?limit|too many requests|429",
        "⚠️ Rate limited by Cursor. Wait a bit and try again, or check /limit.",
    ),
    (
        r"not (logged in|authenticated)|unauthorized|401|invalid.?api.?key|authentication",
        "🔐 Agent not authenticated. On the server run `agent login` or set CURSOR_API_KEY.",
    ),
    (
        r"model .+ not (found|available)|unknown model",
        "❌ That model is not available for this account. Use /models to pick another.",
    ),
    (
        r"ENOENT|no such file|command not found|not found",
        "❌ Agent binary missing or path invalid. Re-run `python main.py setup`.",
    ),
    (
        r"network|ECONNRESET|ETIMEDOUT|ENOTFOUND|dns|connect",
        "🌐 Network error talking to Cursor. Check connectivity and retry.",
    ),
]


def classify_agent_error(
    *,
    returncode: int,
    stdout: str,
    stderr: str,
    timed_out: bool = False,
    cancelled: bool = False,
    missing_binary: bool = False,
) -> str | None:
    """Return a friendly error message, or None if the run looks successful."""
    if missing_binary:
        return (
            "❌ cursor-agent is not installed (or AGENT_BIN is wrong).\n"
            "Install the CLI, then: `python main.py config --agent-bin /path/to/agent`"
        )
    if cancelled:
        return "🛑 Run cancelled."
    if timed_out:
        return "⌛ Agent timed out. Try a shorter prompt or /cancel next time and retry."
    if returncode == 0:
        return None

    blob = f"{stdout}\n{stderr}".lower()
    import re

    for pattern, message in ERROR_PATTERNS:
        if re.search(pattern, blob, re.IGNORECASE):
            return message

    snippet = (stderr or stdout).strip()
    if len(snippet) > 500:
        snippet = snippet[:500] + "…"
    if snippet:
        return f"❌ Agent failed (exit {returncode}):\n{snippet}"
    return f"❌ Agent failed with exit code {returncode}."
