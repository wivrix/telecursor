"""Unit tests for agent stream-json helpers (no real agent binary)."""

from __future__ import annotations

from agent_runner import (
    AgentRunner,
    extract_text_from_event,
    looks_like_approval_event,
    looks_like_approval_prompt,
)


def test_extract_skips_thinking_and_tools() -> None:
    assert extract_text_from_event({"type": "thinking", "text": "secret"}) == ""
    assert extract_text_from_event({"type": "tool_call", "text": "rm -rf"}) == ""
    assert extract_text_from_event({"type": "system", "subtype": "init"}) == ""


def test_extract_assistant_and_deltas() -> None:
    assert (
        extract_text_from_event(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Hello"}]},
            }
        )
        == "Hello"
    )
    assert extract_text_from_event({"type": "text_delta", "delta": " world"}) == " world"
    # Final result duplicates streamed text — intentionally ignored
    assert extract_text_from_event({"type": "result", "result": "Hello world"}) == ""


def test_dedupe_assistant_text() -> None:
    runner = AgentRunner.__new__(AgentRunner)
    runner._assistant_emitted = ""
    assert runner._dedupe_assistant_text("Hel") == "Hel"
    assert runner._dedupe_assistant_text("Hello") == "lo"
    assert runner._dedupe_assistant_text("Hello") == ""
    assert runner._assistant_emitted == "Hello"


def test_approval_heuristics() -> None:
    assert looks_like_approval_prompt("Allow this command? (y/n)")
    assert looks_like_approval_prompt("Type y to confirm")
    assert not looks_like_approval_prompt("Here is the analysis of your code.")
    assert looks_like_approval_event({"type": "approval_request"})
    assert looks_like_approval_event({"requires_approval": True})
    assert looks_like_approval_event({"tool_call": {"needs_approval": True}})
    assert not looks_like_approval_event({"type": "assistant", "text": "hi"})


def test_summarize_and_track_file_edits() -> None:
    from agent_runner import summarize_tool_call_event

    started = {
        "type": "tool_call",
        "subtype": "started",
        "tool_call": {
            "editToolCall": {"args": {"path": "/home/u/proj/app.py"}},
        },
    }
    name, path, summary = summarize_tool_call_event(started)
    assert name == "editToolCall"
    assert path == "/home/u/proj/app.py"
    assert summary and "edit" in summary and "app.py" in summary

    completed_write = {
        "type": "tool_call",
        "subtype": "completed",
        "tool_call": {
            "writeToolCall": {
                "args": {"path": "/tmp/out.txt"},
                "result": {"success": {"path": "/tmp/out.txt", "linesCreated": 3}},
            }
        },
    }
    runner = AgentRunner.__new__(AgentRunner)
    runner._edited_files = set()
    runner._tools_started = 0
    runner._tools_completed = 0
    runner._latest_log = ""
    runner._reply_chars = 0
    runner._track_progress_event(started)
    runner._track_progress_event(completed_write)
    snap = runner.progress_snapshot()
    assert snap["tools_started"] == 1
    assert snap["tools_completed"] == 1
    assert snap["files_edited"] == 1
    assert "/tmp/out.txt" in snap["edited_paths"]
    assert snap["latest_log"].startswith("✅")
