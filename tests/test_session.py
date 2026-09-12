"""Tests for session persistence."""

from __future__ import annotations

from pathlib import Path

from session import AgentMode, RunMode, SessionStore


def test_session_round_trip_persistence(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    workspace = tmp_path / "ws"
    workspace.mkdir()

    store = SessionStore(
        default_mode=AgentMode.SAFE,
        default_workspace=workspace,
        default_model=None,
        default_effort=None,
        default_project_id="demo",
        path=state,
    )
    chat_id = 42
    store.set_mode(chat_id, AgentMode.YOLO)
    store.set_run_mode(chat_id, RunMode.PLAN)
    store.set_project(chat_id, project_id="demo", workspace=workspace)
    store.set_model(chat_id, "composer-2.5")
    store.set_effort(chat_id, "high")
    store.set_agent_session(chat_id, "sess-abc-123")

    assert state.is_file()

    restored = SessionStore(
        default_mode=AgentMode.SAFE,
        default_workspace=workspace,
        path=state,
    )
    session = restored.get(chat_id)
    assert session.mode is AgentMode.YOLO
    assert session.run_mode is RunMode.PLAN
    assert session.project_id == "demo"
    assert session.workspace == workspace.resolve()
    assert session.model == "composer-2.5"
    assert session.effort == "high"
    assert session.agent_session_id == "sess-abc-123"
    # Queues / runners are never restored
    assert session.queued_count == 0
    assert session.active_runner is None
