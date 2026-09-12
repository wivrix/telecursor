"""In-memory per-chat session state, job queue, and agent run bookkeeping."""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


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


class SessionStore:
    def __init__(
        self,
        default_mode: AgentMode,
        default_workspace: Path,
        default_model: str | None = None,
        default_effort: str | None = None,
        default_project_id: str | None = None,
        default_run_mode: RunMode = RunMode.AGENT,
    ) -> None:
        self._default_mode = default_mode
        self._default_workspace = default_workspace
        self._default_model = default_model
        self._default_effort = default_effort
        self._default_project_id = default_project_id
        self._default_run_mode = default_run_mode
        self._sessions: dict[int, ChatSession] = {}

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
        return self._sessions[chat_id]

    def set_mode(self, chat_id: int, mode: AgentMode) -> ChatSession:
        session = self.get(chat_id)
        session.mode = mode
        return session

    def set_run_mode(self, chat_id: int, run_mode: RunMode) -> ChatSession:
        session = self.get(chat_id)
        session.run_mode = run_mode
        return session

    def set_workspace(self, chat_id: int, workspace: Path) -> ChatSession:
        session = self.get(chat_id)
        if session.workspace != workspace:
            session.workspace = workspace
            session.clear_history()
        return session

    def set_project(
        self,
        chat_id: int,
        *,
        project_id: str,
        workspace: Path,
    ) -> ChatSession:
        session = self.get(chat_id)
        changed = session.project_id != project_id or session.workspace != workspace
        session.project_id = project_id
        session.workspace = workspace
        if changed:
            session.clear_history()
        return session

    def set_model(self, chat_id: int, model: str | None) -> ChatSession:
        session = self.get(chat_id)
        session.model = model
        return session

    def set_effort(self, chat_id: int, effort: str | None) -> ChatSession:
        session = self.get(chat_id)
        session.effort = effort
        return session

    def clear_history(self, chat_id: int) -> ChatSession:
        session = self.get(chat_id)
        session.clear_history()
        return session

    def refresh_workspace(self, chat_id: int, workspace: Path) -> ChatSession:
        session = self.get(chat_id)
        session.workspace = workspace
        session.clear_history()
        return session

    def set_agent_session(self, chat_id: int, session_id: str | None) -> ChatSession:
        session = self.get(chat_id)
        session.agent_session_id = session_id
        return session
