"""A damaged event log must not make a persisted conversation unloadable.

Losing one event file (a partially synced store, an overeager cleanup tool, or
the secret-masking rewrite from #6160) used to strand the whole conversation:
the scan stopped at the gap, the persisted leaf resolved to an unknown id, and
every event route failed with 500 "Unknown event_id" after a restart (#6174).

Now the surviving events are indexed densely, the branch walk truncates at the
hole, and an unknown persisted leaf falls back to the last surviving event.
"""

import os
import uuid

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.io import LocalFileStore
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.workspace import LocalWorkspace


def _agent() -> Agent:
    return Agent(llm=LLM(model="test-model", usage_id="test-usage"), tools=[])


def _user_event(text: str) -> MessageEvent:
    return MessageEvent(
        llm_message=Message(role="user", content=[TextContent(text=text)]),
        source="user",
    )


def _create_state(tmp_path):
    state = ConversationState.create(
        id=uuid.uuid4(),
        agent=_agent(),
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
        file_store=LocalFileStore(str(tmp_path)),
    )
    for i in range(3):
        state.append_event(_user_event(f"event {i}"))
    return state


def _delete_event_file(events_dir: str, disk_index: int) -> None:
    name = next(n for n in sorted(os.listdir(events_dir)) if n.startswith(f"event-{disk_index:05d}-"))
    os.remove(os.path.join(events_dir, name))


def _reload(tmp_path, cid):
    return ConversationState.create(
        id=cid,
        agent=None,
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
        file_store=LocalFileStore(str(tmp_path)),
    )


def test_missing_middle_event_file_keeps_conversation_loadable(tmp_path):
    state = _create_state(tmp_path)
    events_dir = os.path.join(str(tmp_path), "events")

    # Lose the middle event, as a partial sync or cleanup tool would.
    _delete_event_file(events_dir, 1)

    reloaded = _reload(tmp_path, state.id)

    # Restore succeeded and every surviving event is indexed and readable.
    assert len(reloaded.events) == 2
    view_ids = [e.id for e in reloaded.view.events]
    assert len(view_ids) == 1  # branch truncated at the hole
    for evt_id in [e.id for e in reloaded.events]:
        idx = reloaded.events.get_index(evt_id)
        assert reloaded.events[idx].id == evt_id


def test_missing_leaf_event_file_falls_back_to_surviving_head(tmp_path):
    state = _create_state(tmp_path)
    events_dir = os.path.join(str(tmp_path), "events")

    # Lose the leaf itself: the persisted HEAD points at a missing event.
    _delete_event_file(events_dir, 2)

    reloaded = _reload(tmp_path, state.id)

    # Restore fell back to the last surviving event instead of raising
    # KeyError on the stored leaf.
    assert len(reloaded.events) == 2
    view_ids = [e.id for e in reloaded.view.events]
    assert len(view_ids) == 2
    assert reloaded._resolve_active_leaf() == view_ids[-1]

    # The conversation accepts new events again.
    seq = reloaded.append_event(_user_event("after recovery"))
    assert seq == 2
    assert len(reloaded.events) == 3


def test_intact_log_loads_unchanged(tmp_path):
    state = _create_state(tmp_path)

    reloaded = _reload(tmp_path, state.id)

    assert len(reloaded.events) == 3
    assert [e.id for e in reloaded.view.events] == [
        e.id for e in reloaded.events
    ]
