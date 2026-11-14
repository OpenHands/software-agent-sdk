"""Property tests for EventLog persistence.

An EventLog behaves like an append-only list of event ids, even when two logs
share one events directory (as two processes would), either log is reopened at
any point, and unrelated files appear in the directory. Every event file name
the writer produces also parses back with the reader's pattern.
"""

from hypothesis import given, settings, strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    rule,
    run_state_machine_as_test,
)

from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.conversation.persistence_const import (
    EVENT_FILE_PATTERN,
    EVENT_NAME_RE,
    EVENTS_DIR,
)
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.io.memory import InMemoryFileStore
from openhands.sdk.llm import Message, TextContent


# Dot files are the log's own sidecars (lock, length marker), so they are not
# "unrelated" files.
STRAY_FILE_NAMES = (
    st.tuples(
        st.sampled_from(["", "event-", "event-00001-", "notes-"]),
        st.text(alphabet="abcxyz0123456789-_", max_size=10),
        st.sampled_from(["", ".json", ".json.bak", ".tmp"]),
    )
    .map("".join)
    .filter(
        lambda name: (
            name != "" and not name.startswith(".") and not EVENT_NAME_RE.match(name)
        )
    )
)


def _message_event() -> MessageEvent:
    return MessageEvent(
        llm_message=Message(role="user", content=[TextContent(text="x")]),
        source="user",
    )


class EventLogMatchesList(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.fs = InMemoryFileStore()
        self.logs = [EventLog(self.fs), EventLog(self.fs)]
        self.expected_ids: list[str] = []

    @rule(writer=st.sampled_from([0, 1]))
    def append(self, writer: int) -> None:
        event = _message_event()
        seq = self.logs[writer].append(event)
        self.expected_ids.append(event.id)
        assert seq == len(self.expected_ids) - 1
        assert [e.id for e in self.logs[writer]] == self.expected_ids

    @rule(writer=st.sampled_from([0, 1]))
    def reopen(self, writer: int) -> None:
        self.logs[writer] = EventLog(self.fs)

    @rule(name=STRAY_FILE_NAMES)
    def unrelated_file_appears(self, name: str) -> None:
        self.fs.write(f"{EVENTS_DIR}/{name}", "{}")

    @invariant()
    def fresh_log_reads_expected_ids(self) -> None:
        assert [e.id for e in EventLog(self.fs)] == self.expected_ids


def test_event_log_matches_list_model() -> None:
    run_state_machine_as_test(
        EventLogMatchesList, settings=settings(stateful_step_count=12)
    )


@given(idx=st.integers(min_value=0, max_value=10**9), event_id=st.uuids().map(str))
def test_event_file_name_parses_back_with_reader_pattern(
    idx: int, event_id: str
) -> None:
    match = EVENT_NAME_RE.match(EVENT_FILE_PATTERN.format(idx=idx, event_id=event_id))
    assert match is not None
    assert (int(match.group("idx")), match.group("event_id")) == (idx, event_id)
