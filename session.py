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
    SAFE = "safe"
    YOLO = "yolo"


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
    model: str | None = None  # None => auto / CLI default
    run_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    active_runner: Any | None = None
    pending_approvals: dict[str, asyncio.Future[bool]] = field(default_factory=dict)
    # FIFO queue of jobs; worker drains this one-at-a-time
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
    def is_busy(self) -> bool:
        return self.active_runner is not None or self.current_job is not None

    @property
    def queued_count(self) -> int:
        return sum(1 for job in self.jobs if not job.cancelled)

    def queue_snapshot(self) -> list[QueuedJob]:
        return [job for job in self.jobs if not job.cancelled]


class SessionStore:
    def __init__(
        self,
        default_mode: AgentMode,
        default_workspace: Path,
        default_model: str | None = None,
    ) -> None:
        self._default_mode = default_mode
        self._default_workspace = default_workspace
        self._default_model = default_model
        self._sessions: dict[int, ChatSession] = {}

    def get(self, chat_id: int) -> ChatSession:
        if chat_id not in self._sessions:
            self._sessions[chat_id] = ChatSession(
                chat_id=chat_id,
                mode=self._default_mode,
                workspace=self._default_workspace,
                model=self._default_model,
            )
        return self._sessions[chat_id]

    def set_mode(self, chat_id: int, mode: AgentMode) -> ChatSession:
        session = self.get(chat_id)
        session.mode = mode
        return session

    def set_workspace(self, chat_id: int, workspace: Path) -> ChatSession:
        session = self.get(chat_id)
        session.workspace = workspace
        return session

    def set_model(self, chat_id: int, model: str | None) -> ChatSession:
        session = self.get(chat_id)
        session.model = model
        return session
