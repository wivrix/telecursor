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
