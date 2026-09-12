"""Async subprocess runner for the cursor-agent CLI."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import Settings

logger = logging.getLogger(__name__)

# Heuristics for interactive CLI confirmation prompts (safe mode).
_APPROVAL_PATTERNS = [
    re.compile(r"\(y/n\)", re.IGNORECASE),
    re.compile(r"\[y/n\]", re.IGNORECASE),
    re.compile(r"\(yes/no\)", re.IGNORECASE),
    re.compile(r"allow\s+(this|the)\s+(command|action|tool)", re.IGNORECASE),
    re.compile(r"do you want to (continue|proceed|allow|run)", re.IGNORECASE),
    re.compile(r"approve\s+(this|the)\s+", re.IGNORECASE),
    re.compile(r"waiting for (approval|confirmation)", re.IGNORECASE),
    re.compile(r"press enter to continue", re.IGNORECASE),
    re.compile(r"run\s+this\s+command\?", re.IGNORECASE),
]

ApprovalCallback = Callable[[str, str], Awaitable[bool]]
# (prompt_text, token) -> approved?


@dataclass
class RunResult:
    returncode: int
    stdout_text: str
    stderr_text: str
    timed_out: bool = False
    cancelled: bool = False
    missing_binary: bool = False


@dataclass
class AgentRunner:
    """
    Manages a single cursor-agent subprocess: argv construction, NDJSON stream
    parsing, safe-mode stdin approvals, and temporary upload cleanup.
    """

    settings: Settings
    workspace: Path
    force: bool
    prompt: str
    model: str | None = None  # overrides settings.agent_model when set
    attachment_paths: list[Path] = field(default_factory=list)
    on_text: Callable[[str], Awaitable[None]] | None = None
    on_stderr: Callable[[str], Awaitable[None]] | None = None
    on_approval_request: ApprovalCallback | None = None
    timeout_seconds: float | None = None

    _process: asyncio.subprocess.Process | None = field(default=None, init=False, repr=False)
    _approval_token: str | None = field(default=None, init=False, repr=False)
    _waiting_approval: bool = field(default=False, init=False, repr=False)
    _stdout_buf: list[str] = field(default_factory=list, init=False, repr=False)
    _stderr_buf: list[str] = field(default_factory=list, init=False, repr=False)
    _partial_line: str = field(default="", init=False, repr=False)
    _cancelled: bool = field(default=False, init=False, repr=False)

    def effective_model(self) -> str | None:
        """Session model wins; None means agent default (auto)."""
        return self.model

    def build_prompt(self) -> str:
        """Compose the final prompt, appending absolute paths for attachments."""
        if not self.attachment_paths:
            return self.prompt
        lines = ["Context files:"]
        for path in self.attachment_paths:
            lines.append(f"  - {path.resolve()}")
        lines.append("")
        lines.append(f"User prompt: {self.prompt}")
        return "\n".join(lines)

    def build_argv(self, final_prompt: str) -> list[str]:
        """
        Build argv for create_subprocess_exec (no shell).

        Prompt is a single argv element — equivalent safety to shlex.quote()
        when using a shell, without introducing a shell injection surface.
        """
        bin_path = str(self.settings.agent_bin)
        argv: list[str] = [
            bin_path,
            "--print",
            "--output-format",
            "stream-json",
            "--stream-partial-output",
            "--workspace",
            str(self.workspace.resolve()),
            "--trust",
        ]
        if self.force:
            argv.append("--force")
        model = self.effective_model()
        if model:
            argv.extend(["--model", model])
        # Positional prompt last
        argv.append(final_prompt)
        return argv

    def build_shell_command(self, final_prompt: str) -> str:
        """Shell-form command with shlex.quote (for logging / debugging only)."""
        return " ".join(shlex.quote(part) for part in self.build_argv(final_prompt))

    async def run(self) -> RunResult:
        final_prompt = self.build_prompt()
        argv = self.build_argv(final_prompt)
        bin_path = Path(argv[0])
        if not bin_path.exists() and shutil.which(argv[0]) is None:
            await self._cleanup_attachments()
            return RunResult(
                returncode=127,
                stdout_text="",
                stderr_text=f"Agent binary not found: {argv[0]}",
                missing_binary=True,
            )

        logger.info(
            "Starting agent workspace=%s force=%s model=%s cmd=%s",
            self.workspace,
            self.force,
            self.effective_model() or "auto",
            self.build_shell_command(final_prompt)[:500],
        )

        env = os.environ.copy()
        if self.settings.cursor_api_key:
            env["CURSOR_API_KEY"] = self.settings.cursor_api_key
        env.setdefault("NO_OPEN_BROWSER", "1")
        env.setdefault("CI", "1")

        try:
            try:
                self._process = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(self.workspace.resolve()),
                    env=env,
                    limit=1024 * 1024,
                )
            except FileNotFoundError:
                return RunResult(
                    returncode=127,
                    stdout_text="",
                    stderr_text=f"Agent binary not found: {argv[0]}",
                    missing_binary=True,
                )

            assert self._process.stdout and self._process.stderr

            reader = asyncio.create_task(self._read_stdout(self._process.stdout))
            err_reader = asyncio.create_task(self._read_stderr(self._process.stderr))

            timed_out = False
            try:
                if self.timeout_seconds:
                    await asyncio.wait_for(
                        self._process.wait(),
                        timeout=self.timeout_seconds,
                    )
                else:
                    await self._process.wait()
            except asyncio.TimeoutError:
                timed_out = True
                logger.error("Agent timed out after %ss", self.timeout_seconds)
                await self.cancel()
            finally:
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        asyncio.gather(reader, err_reader, return_exceptions=True),
                        timeout=5.0,
                    )

            returncode = self._process.returncode if self._process.returncode is not None else -1
            return RunResult(
                returncode=returncode,
                stdout_text="".join(self._stdout_buf),
                stderr_text="".join(self._stderr_buf),
                timed_out=timed_out,
                cancelled=self._cancelled and not timed_out,
            )
        finally:
            await self._cleanup_attachments()

    async def cancel(self) -> None:
        self._cancelled = True
        proc = self._process
        if proc is None or proc.returncode is not None:
            return
        with suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            with suppress(ProcessLookupError):
                proc.kill()
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=3.0)

    async def write_stdin(self, data: str) -> None:
        proc = self._process
        if proc is None or proc.stdin is None or proc.returncode is not None:
            return
        payload = data if data.endswith("\n") else data + "\n"
        proc.stdin.write(payload.encode("utf-8", errors="replace"))
        await proc.stdin.drain()

    async def resolve_approval(self, token: str, approved: bool) -> bool:
        """Called from Telegram callback handler for safe-mode Approve/Reject."""
        if self._approval_token != token or not self._waiting_approval:
            return False
        self._waiting_approval = False
        self._approval_token = None
        # Common CLI confirmations accept y/n
        await self.write_stdin("y" if approved else "n")
        return True

    async def _cleanup_attachments(self) -> None:
        for path in self.attachment_paths:
            try:
                if path.exists() and path.is_file():
                    path.unlink()
                    logger.debug("Removed temp upload %s", path)
            except OSError as exc:
                logger.warning("Failed to remove temp file %s: %s", path, exc)

    async def _read_stdout(self, stream: asyncio.StreamReader) -> None:
        while True:
            raw = await stream.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace")
            self._stdout_buf.append(line)
            await self._handle_stdout_line(line.rstrip("\n"))

    async def _read_stderr(self, stream: asyncio.StreamReader) -> None:
        while True:
            raw = await stream.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace")
            self._stderr_buf.append(line)
            text = line.rstrip("\n")
            if self.on_stderr and text.strip():
                await self.on_stderr(text)
            # Some CLIs print approval prompts on stderr
            await self._maybe_request_approval(text)

    async def _handle_stdout_line(self, line: str) -> None:
        if not line.strip():
            return

        # Prefer NDJSON / stream-json events
        if line.startswith("{"):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                await self._emit_text(line + "\n")
                await self._maybe_request_approval(line)
                return
            text = extract_text_from_event(event)
            if text:
                await self._emit_text(text)
            # Tool approval style events (best-effort)
            if looks_like_approval_event(event):
                prompt = extract_approval_prompt(event) or "Agent requests approval to continue."
                await self._maybe_request_approval(prompt, force=True)
            return

        await self._emit_text(line + "\n")
        await self._maybe_request_approval(line)

    async def _emit_text(self, text: str) -> None:
        if self.on_text and text:
            await self.on_text(text)

    async def _maybe_request_approval(self, text: str, *, force: bool = False) -> None:
        if self.force or self.on_approval_request is None:
            return
        if self._waiting_approval:
            return
        if not force and not looks_like_approval_prompt(text):
            return

        token = uuid.uuid4().hex[:12]
        self._approval_token = token
        self._waiting_approval = True
        try:
            approved = await self.on_approval_request(text.strip()[:500], token)
        except Exception:
            logger.exception("Approval callback failed; rejecting")
            approved = False
        finally:
            self._waiting_approval = False
            self._approval_token = None
        await self.write_stdin("y" if approved else "n")


def looks_like_approval_prompt(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    return any(p.search(stripped) for p in _APPROVAL_PATTERNS)


def looks_like_approval_event(event: dict[str, Any]) -> bool:
    etype = str(event.get("type", "")).lower()
    if etype in {"approval_request", "tool_approval", "user_question", "confirmation"}:
        return True
    if event.get("requires_approval") or event.get("needs_approval"):
        return True
    subtype = str(event.get("subtype", "")).lower()
    return "approval" in subtype or "confirm" in subtype


def extract_approval_prompt(event: dict[str, Any]) -> str | None:
    for key in ("prompt", "message", "question", "description", "text"):
        val = event.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, dict):
            nested = val.get("text") or val.get("content")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    return None


def extract_text_from_event(event: dict[str, Any]) -> str:
    """
    Best-effort extraction of human-visible text from stream-json NDJSON events.

    Cursor agent stream-json shapes vary by version; handle common variants.
    """
    etype = str(event.get("type", "")).lower()

    # Partial delta streaming
    if etype in {"partial", "text_delta", "assistant_delta", "content_block_delta"}:
        for key in ("delta", "text", "content", "partial_output"):
            val = event.get(key)
            if isinstance(val, str):
                return val
            if isinstance(val, dict):
                t = val.get("text") or val.get("content")
                if isinstance(t, str):
                    return t

    if "delta" in event and isinstance(event["delta"], str):
        return event["delta"]
    if "partial_output" in event and isinstance(event["partial_output"], str):
        return event["partial_output"]

    # Full assistant message objects
    if etype in {"assistant", "message", "result", "agent_message"}:
        msg = event.get("message") or event.get("result") or event
        return _content_to_text(msg)

    # Tool / system noise — optionally surface brief notices
    if etype in {"tool_call", "tool_result", "system"}:
        name = event.get("name") or event.get("tool") or etype
        status = event.get("status") or event.get("subtype") or ""
        if status:
            return f"\n🔧 `{name}` {status}\n"
        return ""

    # Generic fallbacks
    for key in ("text", "content", "output", "result"):
        if key in event:
            return _content_to_text(event[key])

    return ""


def _content_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if "text" in value and isinstance(value["text"], str):
            return value["text"]
        content = value.get("content")
        if content is not None:
            return _content_to_text(content)
        return ""
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("text"), str):
                    parts.append(item["text"])
        return "".join(parts)
    return str(value)


async def save_telegram_file(
    destination_dir: Path,
    *,
    file_bytes: bytes,
    suggested_name: str,
) -> Path:
    """Write downloaded Telegram media into a unique temp path."""
    destination_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(suggested_name).name or "upload.bin"
    # Prevent path tricks in the original filename
    safe_name = re.sub(r"[^\w.\-]+", "_", safe_name)[:180] or "upload.bin"
    unique = f"{uuid.uuid4().hex}_{safe_name}"
    path = destination_dir / unique
    path.write_bytes(file_bytes)
    # Restrict permissions on the temp file
    with suppress(OSError):
        os.chmod(path, 0o600)
    return path.resolve()
