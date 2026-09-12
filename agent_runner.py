"""Async subprocess runner for the cursor-agent CLI."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import signal
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import Settings
from effort import compose_model_arg
from platform_util import IS_WINDOWS, pid_is_alive

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Safe-mode approval heuristics
# ---------------------------------------------------------------------------
# Cursor Agent may ask for confirmation via:
#   1) Structured stream-json events (type/subtype / requires_approval flags)
#   2) Free-text (y/n) prompts on stdout or stderr
#
# These regexes are best-effort for (2). False positives only pause for a
# Telegram Approve/Reject; false negatives may leave the agent waiting on
# stdin until the run is cancelled. Prefer structured events when available.
# Patterns are intentionally narrow to avoid treating normal assistant text
# as an approval prompt.
_APPROVAL_PATTERNS = [
    re.compile(r"\(y/n\)", re.IGNORECASE),
    re.compile(r"\[y/n\]", re.IGNORECASE),
    re.compile(r"\(yes/no\)", re.IGNORECASE),
    re.compile(r"\[yes/no\]", re.IGNORECASE),
    re.compile(r"\(y/N\)"),
    re.compile(r"\[Y/n\]"),
    re.compile(r"allow\s+(this|the)\s+(command|action|tool)", re.IGNORECASE),
    re.compile(r"do you want to (continue|proceed|allow|run)", re.IGNORECASE),
    re.compile(r"approve\s+(this|the)\s+", re.IGNORECASE),
    re.compile(r"waiting for (approval|confirmation)", re.IGNORECASE),
    re.compile(r"press enter to continue", re.IGNORECASE),
    re.compile(r"run\s+this\s+command\?", re.IGNORECASE),
    re.compile(r"confirm\s+(to\s+)?(continue|proceed|run|execute)", re.IGNORECASE),
    re.compile(r"type\s+['\"]?y['\"]?\s+to\s+", re.IGNORECASE),
]

# Max characters of the redacted shell-form command logged at start.
_LOG_CMD_MAX = 240

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
    session_id: str | None = None


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
    effort: str | None = None  # injected as model[effort=…] when model is set
    run_mode: str = "agent"  # agent | plan | ask
    resume_session_id: str | None = None  # continue prior agent chat when set
    attachment_paths: list[Path] = field(default_factory=list)
    on_text: Callable[[str], Awaitable[None]] | None = None
    on_stderr: Callable[[str], Awaitable[None]] | None = None
    on_approval_request: ApprovalCallback | None = None
    timeout_seconds: float | None = None
    stall_seconds: float | None = None

    _process: asyncio.subprocess.Process | None = field(default=None, init=False, repr=False)
    _approval_token: str | None = field(default=None, init=False, repr=False)
    _waiting_approval: bool = field(default=False, init=False, repr=False)
    _approval_tasks: set[asyncio.Task[None]] = field(
        default_factory=set, init=False, repr=False
    )
    _stdout_buf: list[str] = field(default_factory=list, init=False, repr=False)
    _stderr_buf: list[str] = field(default_factory=list, init=False, repr=False)
    _partial_line: str = field(default="", init=False, repr=False)
    _cancelled: bool = field(default=False, init=False, repr=False)
    _session_id: str | None = field(default=None, init=False, repr=False)
    _assistant_emitted: str = field(default="", init=False, repr=False)
    # Live progress for /status (current run only)
    _edited_files: set[str] = field(default_factory=set, init=False, repr=False)
    _tools_started: int = field(default=0, init=False, repr=False)
    _tools_completed: int = field(default=0, init=False, repr=False)
    _latest_log: str = field(default="", init=False, repr=False)
    _reply_chars: int = field(default=0, init=False, repr=False)
    _last_activity: float = field(default=0.0, init=False, repr=False)
    _process_group: bool = field(default=False, init=False, repr=False)

    def effective_model(self) -> str | None:
        """Session model + optional effort bracket; None means agent default (auto)."""
        return compose_model_arg(self.model, self.effort)

    def progress_snapshot(self) -> dict[str, Any]:
        """In-progress metrics for Telegram /status."""
        files = sorted(self._edited_files)
        proc = getattr(self, "_process", None)
        running = proc is not None and getattr(proc, "returncode", None) is None
        return {
            "running": running,
            "waiting_approval": bool(getattr(self, "_waiting_approval", False)),
            "tools_started": self._tools_started,
            "tools_completed": self._tools_completed,
            "files_edited": len(files),
            "edited_paths": files,
            "reply_chars": self._reply_chars,
            "latest_log": self._latest_log,
        }

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
        mode = (self.run_mode or "agent").strip().lower()
        if mode in {"plan", "ask"}:
            argv.extend(["--mode", mode])
        model = self.effective_model()
        if model:
            argv.extend(["--model", model])
        if self.resume_session_id:
            argv.extend(["--resume", self.resume_session_id])
        # Positional prompt last
        argv.append(final_prompt)
        return argv

    def build_shell_command(self, final_prompt: str, *, for_log: bool = False) -> str:
        """
        Shell-form command with shlex.quote (debugging / logs only).

        When for_log=True, the positional prompt is replaced with
        ``<prompt N chars>`` so user text never hits logger.info.
        """
        argv = self.build_argv(final_prompt)
        if for_log and argv:
            prompt = argv[-1]
            argv = [*argv[:-1], f"<prompt {len(prompt)} chars>"]
        rendered = " ".join(shlex.quote(part) for part in argv)
        if for_log and len(rendered) > _LOG_CMD_MAX:
            return rendered[:_LOG_CMD_MAX] + "…"
        return rendered

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

        timeout = self.timeout_seconds
        if timeout is None:
            timeout = float(getattr(self.settings, "agent_timeout_seconds", 1800.0) or 1800.0)
        stall = self.stall_seconds
        if stall is None:
            stall = float(getattr(self.settings, "agent_stall_seconds", 600.0) or 600.0)

        logger.info(
            "Starting agent workspace=%s force=%s mode=%s model=%s effort=%s "
            "resume=%s timeout=%ss stall=%ss cmd=%s",
            self.workspace,
            self.force,
            self.run_mode or "agent",
            self.effective_model() or "auto",
            self.effort or "auto",
            self.resume_session_id or "-",
            int(timeout),
            int(stall),
            self.build_shell_command(final_prompt, for_log=True),
        )

        env = os.environ.copy()
        if self.settings.cursor_api_key:
            env["CURSOR_API_KEY"] = self.settings.cursor_api_key
        env.setdefault("NO_OPEN_BROWSER", "1")
        env.setdefault("CI", "1")

        started_at = time.monotonic()
        timed_out = False
        stalled = False
        try:
            try:
                popen_kwargs: dict[str, Any] = {}
                if not IS_WINDOWS:
                    # New session so cancel can signal the whole agent process tree
                    popen_kwargs["start_new_session"] = True
                    self._process_group = True
                self._process = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(self.workspace.resolve()),
                    env=env,
                    limit=1024 * 1024,
                    **popen_kwargs,
                )
            except FileNotFoundError:
                return RunResult(
                    returncode=127,
                    stdout_text="",
                    stderr_text=f"Agent binary not found: {argv[0]}",
                    missing_binary=True,
                )

            assert self._process.stdout and self._process.stderr
            self._touch_activity()

            reader = asyncio.create_task(self._read_stdout(self._process.stdout))
            err_reader = asyncio.create_task(self._read_stderr(self._process.stderr))

            try:
                timed_out, stalled = await self._watch_process(
                    timeout_seconds=timeout,
                    stall_seconds=stall,
                )
                if timed_out or stalled:
                    reason = "timed out" if timed_out else "stalled (no output)"
                    logger.error(
                        "Agent %s after %.0fs (timeout=%ss stall=%ss); killing",
                        reason,
                        time.monotonic() - started_at,
                        int(timeout),
                        int(stall),
                    )
                    await self.cancel()
                    # Give wait a moment after kill
                    with suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(self._process.wait(), timeout=8.0)
            finally:
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        asyncio.gather(reader, err_reader, return_exceptions=True),
                        timeout=5.0,
                    )

            returncode = self._process.returncode if self._process.returncode is not None else -1
            elapsed = time.monotonic() - started_at
            logger.info(
                "Agent finished rc=%s elapsed=%.1fs timed_out=%s stalled=%s "
                "cancelled=%s tools=%s/%s files_edited=%s",
                returncode,
                elapsed,
                timed_out or stalled,
                stalled,
                self._cancelled and not (timed_out or stalled),
                self._tools_completed,
                self._tools_started,
                len(self._edited_files),
            )
            return RunResult(
                returncode=returncode,
                stdout_text="".join(self._stdout_buf),
                stderr_text="".join(self._stderr_buf),
                timed_out=timed_out or stalled,
                cancelled=self._cancelled and not (timed_out or stalled),
                session_id=self._session_id or self.resume_session_id,
            )
        finally:
            await self._cancel_approval_tasks()
            await self._cleanup_attachments()

    async def _watch_process(
        self,
        *,
        timeout_seconds: float,
        stall_seconds: float,
    ) -> tuple[bool, bool]:
        """
        Wait for the agent subprocess with hard timeout + stall detection.

        Also recovers when the OS PID is gone but asyncio wait() has not
        completed (orphaned / reaped child), which previously left the queue stuck.
        Returns (timed_out, stalled).
        """
        proc = self._process
        assert proc is not None
        started = time.monotonic()
        poll = 2.0
        while True:
            try:
                await asyncio.wait_for(proc.wait(), timeout=poll)
                return False, False
            except asyncio.TimeoutError:
                now = time.monotonic()
                if proc.returncode is not None:
                    return False, False
                pid = proc.pid
                if pid and not pid_is_alive(pid):
                    logger.error(
                        "Agent PID %s is dead but wait() did not finish; forcing cleanup",
                        pid,
                    )
                    self._cancelled = True
                    with suppress(ProcessLookupError, OSError):
                        proc.kill()
                    # Do not block on wait() again — it may never resolve if the
                    # transport is wedged. Readers are drained by the caller.
                    if proc.returncode is None:
                        # Best-effort marker for RunResult
                        with suppress(Exception):
                            object.__setattr__(proc, "returncode", -9)
                    return False, False
                if timeout_seconds > 0 and (now - started) >= timeout_seconds:
                    return True, False
                # While Telegram approval is pending, do not count as a stall —
                # the agent is legitimately idle waiting for the user.
                if self._waiting_approval:
                    self._touch_activity()
                last = self._last_activity or started
                if stall_seconds > 0 and (now - last) >= stall_seconds:
                    return False, True

    def _touch_activity(self) -> None:
        self._last_activity = time.monotonic()

    async def cancel(self) -> None:
        self._cancelled = True
        await self._cancel_approval_tasks()
        proc = self._process
        if proc is None or proc.returncode is not None:
            return
        pid = proc.pid
        if self._process_group and pid and not IS_WINDOWS:
            with suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(pid, signal.SIGTERM)
        else:
            with suppress(ProcessLookupError):
                proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            if self._process_group and pid and not IS_WINDOWS:
                with suppress(ProcessLookupError, PermissionError, OSError):
                    os.killpg(pid, signal.SIGKILL)
            else:
                with suppress(ProcessLookupError):
                    proc.kill()
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=3.0)

    async def _cancel_approval_tasks(self) -> None:
        tasks = list(self._approval_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._approval_tasks.clear()
        self._waiting_approval = False
        self._approval_token = None

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
            self._touch_activity()
            await self._handle_stdout_line(line.rstrip("\n"))

    async def _read_stderr(self, stream: asyncio.StreamReader) -> None:
        while True:
            raw = await stream.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace")
            self._stderr_buf.append(line)
            self._touch_activity()
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
                self._set_latest_log(line.strip())
                await self._emit_text(line + "\n")
                await self._maybe_request_approval(line)
                return
            sid = event.get("session_id")
            if isinstance(sid, str) and sid.strip():
                self._session_id = sid.strip()
            self._track_progress_event(event)
            text = extract_text_from_event(event)
            if text:
                text = self._dedupe_assistant_text(text)
            if text:
                await self._emit_text(text)
            # Tool approval style events (best-effort)
            if looks_like_approval_event(event):
                prompt = extract_approval_prompt(event) or "Agent requests approval to continue."
                await self._maybe_request_approval(prompt, force=True)
            return

        self._set_latest_log(line.strip())
        await self._emit_text(line + "\n")
        await self._maybe_request_approval(line)

    def _set_latest_log(self, text: str) -> None:
        cleaned = " ".join(text.split())
        if not cleaned:
            return
        if len(cleaned) > 200:
            cleaned = cleaned[:197] + "…"
        self._latest_log = cleaned
        self._touch_activity()

    def _track_progress_event(self, event: dict[str, Any]) -> None:
        """Update live counters / latest activity from a stream-json event."""
        etype = str(event.get("type", "")).lower()
        subtype = str(event.get("subtype", "")).lower()

        if etype == "tool_call":
            tool_name, path, summary = summarize_tool_call_event(event)
            if subtype == "started":
                self._tools_started += 1
                label = summary or tool_name or "tool"
                self._set_latest_log(f"🔧 {label}")
            elif subtype == "completed":
                self._tools_completed += 1
                if path and tool_name in {
                    "editToolCall",
                    "writeToolCall",
                    "deleteToolCall",
                }:
                    self._edited_files.add(path)
                label = summary or tool_name or "tool"
                self._set_latest_log(f"✅ {label}")
            return

        if etype in {"assistant", "message", "agent_message", "partial", "text_delta"}:
            text = extract_text_from_event(event)
            if text and text.strip():
                self._set_latest_log(text.strip())
            return

        if etype == "thinking" and subtype in {"delta", "completed", ""}:
            # Prefer not to flood status with thinking; keep a short marker
            if not self._latest_log:
                self._set_latest_log("💭 thinking…")
            return

        if etype == "result":
            self._set_latest_log("🏁 result received")

    def _dedupe_assistant_text(self, text: str) -> str:
        """
        stream-json may emit token deltas then a final full snapshot.
        Keep Telegram output as a single clean reply.
        """
        prev = self._assistant_emitted
        if not text:
            return ""
        prev_s = prev.rstrip()
        text_s = text.rstrip()
        if not text_s:
            return ""
        if text_s == prev_s:
            return ""
        if prev_s and text_s.startswith(prev_s):
            delta = text_s[len(prev_s) :]
            self._assistant_emitted = text_s
            self._reply_chars = len(self._assistant_emitted)
            return delta
        if prev_s and prev_s.startswith(text_s):
            return ""
        # Revised full snapshot that overlaps the previous reply — skip re-emitting
        # the shared body (avoids duplicated paragraphs in Telegram).
        if prev_s and text_s:
            shared = 0
            limit = min(len(prev_s), len(text_s), 120)
            while shared < limit and prev_s[shared] == text_s[shared]:
                shared += 1
            if shared >= 40:
                self._assistant_emitted = text_s
                self._reply_chars = len(self._assistant_emitted)
                if len(text_s) > len(prev_s) and text_s.startswith(prev_s[:shared]):
                    # Only emit the unseen suffix when it clearly extends the prior text
                    suffix = text_s[len(prev_s) :] if text_s.startswith(prev_s) else ""
                    return suffix
                return ""
        self._assistant_emitted = prev_s + text_s
        self._reply_chars = len(self._assistant_emitted)
        return text_s


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
        # Do not block stdout/stderr readers while waiting for Telegram —
        # a blocked reader can fill the pipe and deadlock the agent.
        task = asyncio.create_task(
            self._complete_approval(text.strip()[:500], token),
            name=f"telecursor-approval-{token}",
        )
        self._approval_tasks.add(task)
        task.add_done_callback(self._approval_tasks.discard)

    async def _complete_approval(self, prompt: str, token: str) -> None:
        approved = False
        cancel_exc: asyncio.CancelledError | None = None
        try:
            if self.on_approval_request is not None:
                approved = await self.on_approval_request(prompt, token)
        except asyncio.CancelledError as exc:
            cancel_exc = exc
            approved = False
        except Exception:
            logger.exception("Approval callback failed; rejecting")
            approved = False
        finally:
            if self._approval_token == token:
                self._waiting_approval = False
                self._approval_token = None
        try:
            await self.write_stdin("y" if approved else "n")
        except Exception:
            logger.debug(
                "Could not write approval response to agent stdin", exc_info=True
            )
        if cancel_exc is not None:
            raise cancel_exc


def looks_like_approval_prompt(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    return any(p.search(stripped) for p in _APPROVAL_PATTERNS)


def looks_like_approval_event(event: dict[str, Any]) -> bool:
    """
    Detect structured approval / confirmation events from stream-json.

    Checks common type names, boolean flags, and nested tool payloads.
    """
    etype = str(event.get("type", "")).lower()
    if etype in {
        "approval_request",
        "tool_approval",
        "user_question",
        "confirmation",
        "permission_request",
    }:
        return True
    if event.get("requires_approval") or event.get("needs_approval"):
        return True
    if event.get("awaiting_approval") or event.get("ask_user"):
        return True
    subtype = str(event.get("subtype", "")).lower()
    if "approval" in subtype or "confirm" in subtype or "permission" in subtype:
        return True
    # Nested tool call that explicitly requests confirmation
    for key in ("tool_call", "tool", "request", "permission"):
        nested = event.get(key)
        if isinstance(nested, dict):
            if nested.get("requires_approval") or nested.get("needs_approval"):
                return True
            ntype = str(nested.get("type", "")).lower()
            if "approval" in ntype or "permission" in ntype:
                return True
    return False


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


_FILE_MUTATING_TOOLS = frozenset(
    {"editToolCall", "writeToolCall", "deleteToolCall"}
)
_TOOL_VERB = {
    "editToolCall": "edit",
    "writeToolCall": "write",
    "deleteToolCall": "delete",
    "readToolCall": "read",
    "shellToolCall": "shell",
    "grepToolCall": "grep",
    "globToolCall": "glob",
    "lsToolCall": "ls",
    "todoToolCall": "todo",
}


def _short_path(path: str, *, max_len: int = 60) -> str:
    text = path.strip().replace("\\", "/")
    if len(text) <= max_len:
        return text
    return "…" + text[-(max_len - 1) :]


def summarize_tool_call_event(
    event: dict[str, Any],
) -> tuple[str | None, str | None, str | None]:
    """
    Return (tool_name, file_path, human_summary) for a tool_call event.

    Best-effort across Cursor stream-json shapes (edit/write/read/shell/…).
    """
    tool_blob = event.get("tool_call")
    if not isinstance(tool_blob, dict) or not tool_blob:
        # Fallback: some streams put name/path at top level
        name = event.get("name") or event.get("tool")
        path = event.get("path") or event.get("file_path")
        if isinstance(name, str) or isinstance(path, str):
            tool_name = str(name) if isinstance(name, str) else None
            file_path = str(path) if isinstance(path, str) else None
            verb = _TOOL_VERB.get(tool_name or "", tool_name or "tool")
            summary = f"{verb} {_short_path(file_path)}" if file_path else verb
            return tool_name, file_path, summary
        return None, None, None

    tool_name = next(iter(tool_blob.keys()), None)
    if not isinstance(tool_name, str):
        return None, None, None
    payload = tool_blob.get(tool_name) or {}
    if not isinstance(payload, dict):
        payload = {}

    args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    success = result.get("success") if isinstance(result.get("success"), dict) else {}

    path: str | None = None
    for candidate in (
        args.get("path"),
        args.get("file_path"),
        args.get("target"),
        success.get("path"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            path = candidate.strip()
            break

    verb = _TOOL_VERB.get(tool_name, tool_name)
    if tool_name == "shellToolCall":
        cmd = args.get("command") or args.get("cmd")
        if isinstance(cmd, str) and cmd.strip():
            cmd_short = " ".join(cmd.split())
            if len(cmd_short) > 80:
                cmd_short = cmd_short[:77] + "…"
            return tool_name, path, f"{verb} {cmd_short}"

    if path:
        return tool_name, path, f"{verb} {_short_path(path)}"
    return tool_name, None, verb


def extract_text_from_event(event: dict[str, Any]) -> str:
    """
    Best-effort extraction of human-visible assistant text from stream-json.

    Skips thinking, tool noise, and system init — Telegram shows only the reply.
    """
    etype = str(event.get("type", "")).lower()
    subtype = str(event.get("subtype", "")).lower()

    # Internal / non-user-facing event types
    if etype in {
        "thinking",
        "tool_call",
        "tool_result",
        "system",
        "user",
        "status",
        "heartbeat",
    }:
        return ""
    if subtype in {"init", "started", "completed"} and etype not in {
        "assistant",
        "message",
        "result",
        "partial",
        "text_delta",
        "assistant_delta",
        "content_block_delta",
    }:
        return ""

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

    if "delta" in event and isinstance(event["delta"], str) and etype != "thinking":
        return event["delta"]
    if "partial_output" in event and isinstance(event["partial_output"], str):
        return event["partial_output"]

    # Full assistant message objects — prefer message content over result summary
    if etype in {"assistant", "message", "agent_message"}:
        msg = event.get("message") or event
        return _content_to_text(msg)

    # Final result often duplicates assistant text; only use if nothing else streamed
    # Callers already stream deltas; emitting result again doubles the reply.
    if etype == "result":
        return ""

    # Generic fallbacks (avoid tool/system payloads)
    for key in ("text", "content", "output"):
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
