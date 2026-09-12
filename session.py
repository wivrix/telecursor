"""In-memory per-chat session state with state.json persistence."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from paths import state_path

logger = logging.getLogger(__name__)


class AgentMode(str, Enum):
    """Tool approval mode."""

    SAFE = "safe"
    YOLO = "yolo"


class RunMode(str, Enum):
    """Cursor agent execution mode (--mode)."""

    AGENT = "agent"  # default full agent (no --mode flag)
    PLAN = "plan"
    ASK = "ask"


@dataclass
class QueuedJob:
    """One prompt (with optional attachments) waiting for or running on the agent."""

    id: str
    chat_id: int
    prompt: str
    attachments: list[Path]
    preview: str
    cancelled: bool = False

    @staticmethod
    def create(
        *,
        chat_id: int,
        prompt: str,
        attachments: list[Path],
    ) -> QueuedJob:
        preview = prompt.strip().replace("\n", " ")
        if len(preview) > 80:
            preview = preview[:77] + "…"
        return QueuedJob(
            id=uuid.uuid4().hex[:8],
            chat_id=chat_id,
            prompt=prompt,
            attachments=list(attachments),
            preview=preview or "(attachment)",
        )


@dataclass
class ChatSession:
    chat_id: int
    mode: AgentMode
    workspace: Path
    model: str | None = None
    effort: str | None = None
    run_mode: RunMode = RunMode.AGENT
    project_id: str | None = None
    agent_session_id: str | None = None
    run_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    active_runner: Any | None = None
    pending_approvals: dict[str, asyncio.Future[bool]] = field(default_factory=dict)
    jobs: deque[QueuedJob] = field(default_factory=deque)
    jobs_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    current_job: QueuedJob | None = None
    worker_task: asyncio.Task[None] | None = None

    @property
    def force(self) -> bool:
        return self.mode is AgentMode.YOLO

    @property
    def model_label(self) -> str:
        return self.model or "auto"

    @property
    def effort_label(self) -> str:
        return self.effort or "auto"

    @property
    def run_mode_label(self) -> str:
        return self.run_mode.value

    @property
    def history_label(self) -> str:
        return "on" if self.agent_session_id else "off"

    @property
    def project_label(self) -> str:
        return self.project_id or self.workspace.name

    @property
    def is_busy(self) -> bool:
        return self.active_runner is not None or self.current_job is not None

    @property
    def queued_count(self) -> int:
        return sum(1 for job in self.jobs if not job.cancelled)

    def queue_snapshot(self) -> list[QueuedJob]:
        return [job for job in self.jobs if not job.cancelled]

    def clear_history(self) -> None:
        self.agent_session_id = None

    def to_persistent_dict(self) -> dict[str, Any]:
        return {
            "chat_id": self.chat_id,
            "mode": self.mode.value,
            "run_mode": self.run_mode.value,
            "workspace": str(self.workspace),
            "project_id": self.project_id,
            "model": self.model,
            "effort": self.effort,
            "agent_session_id": self.agent_session_id,
        }


class SessionStore:
    """
    Per-chat sessions with durable fields saved to state.json.

    Persisted: project, workspace, approvals mode, run mode, model, effort,
    agent conversation id.

    Not persisted: live job queues, locks, runners, or pending approvals.
    Queued prompts are in-memory only and are lost if the bot process restarts.
    """

    def __init__(
        self,
        default_mode: AgentMode,
        default_workspace: Path,
        default_model: str | None = None,
        default_effort: str | None = None,
        default_project_id: str | None = None,
        default_run_mode: RunMode = RunMode.AGENT,
        *,
        path: Path | None = None,
    ) -> None:
        self._default_mode = default_mode
        self._default_workspace = default_workspace.resolve()
        self._default_model = default_model
        self._default_effort = default_effort
        self._default_project_id = default_project_id
        self._default_run_mode = default_run_mode
        self._path = path or state_path()
        self._sessions: dict[int, ChatSession] = {}
        self._load()

    def get(self, chat_id: int) -> ChatSession:
        if chat_id not in self._sessions:
            self._sessions[chat_id] = ChatSession(
                chat_id=chat_id,
                mode=self._default_mode,
                workspace=self._default_workspace,
                model=self._default_model,
                effort=self._default_effort,
                project_id=self._default_project_id,
                run_mode=self._default_run_mode,
            )
            self._save()
        return self._sessions[chat_id]

    def set_mode(self, chat_id: int, mode: AgentMode) -> ChatSession:
        session = self.get(chat_id)
        session.mode = mode
        self._save()
        return session

    def set_run_mode(self, chat_id: int, run_mode: RunMode) -> ChatSession:
        session = self.get(chat_id)
        session.run_mode = run_mode
        self._save()
        return session

    def set_workspace(self, chat_id: int, workspace: Path) -> ChatSession:
        session = self.get(chat_id)
        resolved = workspace.resolve()
        if session.workspace != resolved:
            session.workspace = resolved
            session.clear_history()
            self._save()
        return session

    def set_project(
        self,
        chat_id: int,
        *,
        project_id: str,
        workspace: Path,
    ) -> ChatSession:
        session = self.get(chat_id)
        resolved = workspace.resolve()
        changed = session.project_id != project_id or session.workspace != resolved
        session.project_id = project_id
        session.workspace = resolved
        if changed:
            session.clear_history()
        self._save()
        return session

    def set_model(self, chat_id: int, model: str | None) -> ChatSession:
        session = self.get(chat_id)
        session.model = model
        self._save()
        return session

    def set_effort(self, chat_id: int, effort: str | None) -> ChatSession:
        session = self.get(chat_id)
        session.effort = effort
        self._save()
        return session

    def clear_history(self, chat_id: int) -> ChatSession:
        session = self.get(chat_id)
        session.clear_history()
        self._save()
        return session

    def refresh_workspace(self, chat_id: int, workspace: Path) -> ChatSession:
        session = self.get(chat_id)
        session.workspace = workspace.resolve()
        session.clear_history()
        self._save()
        return session

    def set_agent_session(self, chat_id: int, session_id: str | None) -> ChatSession:
        session = self.get(chat_id)
        session.agent_session_id = session_id
        self._save()
        return session

    def _session_from_dict(self, raw: dict[str, Any]) -> ChatSession | None:
        try:
            chat_id = int(raw["chat_id"])
        except (KeyError, TypeError, ValueError):
            return None

        workspace_raw = raw.get("workspace") or str(self._default_workspace)
        try:
            workspace = Path(str(workspace_raw)).expanduser().resolve()
        except OSError:
            workspace = self._default_workspace
        if not workspace.is_dir():
            # Fall back to registered project path if possible
            project_id = raw.get("project_id")
            restored = False
            if project_id:
                try:
                    from projects import get_project

                    proj = get_project(str(project_id))
                    if proj is not None and proj.root.is_dir():
                        workspace = proj.root
                        restored = True
                except Exception:
                    logger.debug("Could not restore project %s", project_id, exc_info=True)
            if not restored:
                workspace = self._default_workspace

        mode_raw = str(raw.get("mode") or self._default_mode.value).lower()
        try:
            mode = AgentMode(mode_raw)
        except ValueError:
            mode = self._default_mode

        run_raw = str(raw.get("run_mode") or self._default_run_mode.value).lower()
        try:
            run_mode = RunMode(run_raw)
        except ValueError:
            run_mode = self._default_run_mode

        model = raw.get("model")
        if model is not None:
            model = str(model).strip() or None
        effort = raw.get("effort")
        if effort is not None:
            effort = str(effort).strip() or None

        project_id = raw.get("project_id")
        if project_id is not None:
            project_id = str(project_id).strip() or None

        agent_session_id = raw.get("agent_session_id")
        if agent_session_id is not None:
            agent_session_id = str(agent_session_id).strip() or None

        return ChatSession(
            chat_id=chat_id,
            mode=mode,
            workspace=workspace,
            model=model if model is not None else self._default_model,
            effort=effort if effort is not None else self._default_effort,
            run_mode=run_mode,
            project_id=project_id if project_id is not None else self._default_project_id,
            agent_session_id=agent_session_id,
        )

    def _load(self) -> None:
        path = self._path
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read %s: %s", path, exc)
            return

        chats = payload.get("chats", {})
        if isinstance(chats, list):
            items = chats
        elif isinstance(chats, dict):
            items = list(chats.values())
        else:
            items = []

        loaded = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            session = self._session_from_dict(item)
            if session is None:
                continue
            self._sessions[session.chat_id] = session
            loaded += 1
        if loaded:
            logger.info("Restored %s chat session(s) from %s", loaded, path)

    def _save(self) -> None:
        path = self._path
        payload = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "chats": {
                str(chat_id): session.to_persistent_dict()
                for chat_id, session in sorted(self._sessions.items())
            },
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = json.dumps(payload, indent=2) + "\n"
            # Atomic replace so a crash mid-write cannot corrupt state.json
            fd, tmp_name = tempfile.mkstemp(
                prefix="state.",
                suffix=".json.tmp",
                dir=str(path.parent),
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(data)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp_name, path)
            except Exception:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except OSError as exc:
            logger.warning("Could not write %s: %s", path, exc)
