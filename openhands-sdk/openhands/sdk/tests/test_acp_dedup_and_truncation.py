"""Regression tests for ACP tool call deduplication and content truncation.

Covers:
- RemoteEventsList._add_event_unsafe deduplicates ACPToolCallEvent by tool_call_id
- _serialize_tool_content truncates text blocks to MAX_ACP_CONTENT_CHARS
- _emit_tool_call_event (via _serialize_tool_content) preserves non-text blocks
- Stale index entry is cleaned up and a warning is logged
"""

from __future__ import annotations

import logging
import threading
import unittest

from unittest.mock import MagicMock, patch

from openhands.sdk.agent.acp_agent import MAX_ACP_CONTENT_CHARS, _serialize_tool_content
from openhands.sdk.conversation.impl.remote_conversation import RemoteEventsList
from openhands.sdk.event.acp_tool_call import ACPToolCallEvent


def _make_tool_call_event(tool_call_id: str, raw_output: str = "") -> ACPToolCallEvent:
    return ACPToolCallEvent(
        tool_call_id=tool_call_id,
        title="test tool",
        raw_output=raw_output,
    )


def _make_events_list() -> RemoteEventsList:
    """Return a RemoteEventsList with _do_full_sync stubbed out."""
    with patch.object(RemoteEventsList, "_do_full_sync"):
        client = MagicMock()
        return RemoteEventsList(client=client, conversation_id="conv-1")


class TestACPToolCallDeduplication(unittest.TestCase):
    def setUp(self) -> None:
        self.events = _make_events_list()

    def _add(self, event: ACPToolCallEvent) -> None:
        with self.events._lock:
            self.events._add_event_unsafe(event)

    def test_first_event_is_added(self) -> None:
        ev = _make_tool_call_event("tc-1", "output-1")
        self._add(ev)
        self.assertEqual(len(self.events._cached_events), 1)
        self.assertIn(ev.id, self.events._cached_event_ids)

    def test_subsequent_events_replace_not_append(self) -> None:
        ev1 = _make_tool_call_event("tc-1", "output-1")
        ev2 = _make_tool_call_event("tc-1", "output-1-updated")
        ev3 = _make_tool_call_event("tc-1", "output-1-final")
        self._add(ev1)
        self._add(ev2)
        self._add(ev3)

        self.assertEqual(len(self.events._cached_events), 1)
        last = self.events._cached_events[0]
        assert isinstance(last, ACPToolCallEvent)
        self.assertEqual(last.raw_output, "output-1-final")
        self.assertNotIn(ev1.id, self.events._cached_event_ids)
        self.assertNotIn(ev2.id, self.events._cached_event_ids)
        self.assertIn(ev3.id, self.events._cached_event_ids)

    def test_different_tool_call_ids_are_kept_separately(self) -> None:
        ev_a = _make_tool_call_event("tc-a", "a-output")
        ev_b = _make_tool_call_event("tc-b", "b-output")
        self._add(ev_a)
        self._add(ev_b)

        self.assertEqual(len(self.events._cached_events), 2)
        ids = {
            e.tool_call_id
            for e in self.events._cached_events
            if isinstance(e, ACPToolCallEvent)
        }
        self.assertEqual(ids, {"tc-a", "tc-b"})

    def test_index_stays_consistent_after_replacement(self) -> None:
        ev1 = _make_tool_call_event("tc-1", "v1")
        ev2 = _make_tool_call_event("tc-1", "v2")
        self._add(ev1)
        self._add(ev2)

        self.assertEqual(self.events._acp_tool_call_id_to_event_id["tc-1"], ev2.id)

    def test_stale_index_entry_is_cleaned_up_with_warning(self) -> None:
        ev1 = _make_tool_call_event("tc-1", "v1")
        self._add(ev1)

        # Manually corrupt state: remove ev1 from _cached_events but leave index intact
        self.events._cached_events.clear()
        self.events._cached_event_ids.discard(ev1.id)

        ev2 = _make_tool_call_event("tc-1", "v2")
        with self.assertLogs("openhands.sdk", level=logging.WARNING) as log_ctx:
            self._add(ev2)

        self.assertTrue(
            any("Stale" in line for line in log_ctx.output),
            "Expected a stale-index warning to be logged",
        )
        # ev2 should be inserted normally after cleanup
        self.assertEqual(len(self.events._cached_events), 1)
        self.assertEqual(self.events._cached_events[0].id, ev2.id)
        self.assertEqual(self.events._acp_tool_call_id_to_event_id["tc-1"], ev2.id)

    def test_thread_safety_concurrent_updates(self) -> None:
        """Multiple threads racing to update the same tool_call_id must not corrupt state."""
        errors: list[Exception] = []

        def updater(i: int) -> None:
            try:
                ev = _make_tool_call_event("tc-shared", f"output-{i}")
                self._add(ev)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=updater, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        # Only one event per tool_call_id should survive
        tc_events = [
            e
            for e in self.events._cached_events
            if isinstance(e, ACPToolCallEvent) and e.tool_call_id == "tc-shared"
        ]
        self.assertEqual(len(tc_events), 1)


class TestSerializeToolContentTruncation(unittest.TestCase):
    def test_short_text_is_not_truncated(self) -> None:
        content = [{"type": "text", "text": "short"}]
        result = _serialize_tool_content(content)
        assert result is not None
        self.assertEqual(result[0]["text"], "short")

    def test_long_text_is_truncated_to_max(self) -> None:
        long_text = "x" * (MAX_ACP_CONTENT_CHARS + 5_000)
        content = [{"type": "text", "text": long_text}]
        result = _serialize_tool_content(content)
        assert result is not None
        self.assertLessEqual(len(result[0]["text"]), MAX_ACP_CONTENT_CHARS + 200)

    def test_non_text_blocks_are_not_modified(self) -> None:
        big_data = "y" * (MAX_ACP_CONTENT_CHARS + 1_000)
        content = [{"type": "image_url", "url": big_data}]
        result = _serialize_tool_content(content)
        assert result is not None
        self.assertEqual(result[0]["url"], big_data)

    def test_none_content_returns_none(self) -> None:
        self.assertIsNone(_serialize_tool_content(None))

    def test_empty_content_returns_none(self) -> None:
        self.assertIsNone(_serialize_tool_content([]))

    def test_mixed_blocks_only_truncates_text(self) -> None:
        long_text = "a" * (MAX_ACP_CONTENT_CHARS + 1_000)
        big_url = "b" * (MAX_ACP_CONTENT_CHARS + 1_000)
        content = [
            {"type": "text", "text": long_text},
            {"type": "image_url", "url": big_url},
        ]
        result = _serialize_tool_content(content)
        assert result is not None
        self.assertLessEqual(len(result[0]["text"]), MAX_ACP_CONTENT_CHARS + 200)
        self.assertEqual(len(result[1]["url"]), MAX_ACP_CONTENT_CHARS + 1_000)

    def test_pydantic_model_content_is_serialized(self) -> None:
        """Content blocks that have model_dump() are serialized before truncation check."""

        class FakeBlock:
            def model_dump(self, **_kwargs: object) -> dict:
                return {"type": "text", "text": "hello"}

        result = _serialize_tool_content([FakeBlock()])
        assert result is not None
        self.assertEqual(result[0]["text"], "hello")


if __name__ == "__main__":
    unittest.main()
