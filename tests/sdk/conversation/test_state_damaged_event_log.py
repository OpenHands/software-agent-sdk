"""A damaged event log must not make a persisted conversation unloadable.

Losing one event file (a partially synced store, an overeager cleanup tool, or
the secret-masking rewrite from #6160) used to strand the whole conversation:
the scan stopped at the gap, the persisted leaf resolved to an unknown id, and
every event route failed with 500 "Unknown event_id" after a restart (#6174).

Now every surviving event is indexed, a lost ancestor is bridged so the branch
keeps its system prompt, a lost HEAD is moved once on load, and a lost system
prompt is re-created as the root of the surviving history.
"""

import os
import uuid

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event import Event, SystemPromptEvent
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.io import LocalFileStore
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.workspace import LocalWorkspace


def _agent() -> Agent:
    return Agent(llm=LLM(model="test-model", usage_id="test-usage"), tools=[])


def _user_event(text: str, event_id: str | None = None) -> MessageEvent:
    event = MessageEvent(
        llm_message=Message(role="user", content=[TextContent(text=text)]),
        source="user",
    )
    return event.model_copy(update={"id": event_id}) if event_id else event


def _init_state(state: ConversationState) -> None:
    """Run ``Agent.init_state`` the way a (resumed) conversation does."""

    def on_event(event: Event) -> None:
        state.append_event(event)

    _agent().init_state(state, on_event=on_event)


def _open(tmp_path, cid, agent: Agent | None = None) -> ConversationState:
    return ConversationState.create(
        id=cid,
        agent=agent,
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
        file_store=LocalFileStore(str(tmp_path)),
    )


def _create_state(tmp_path, *, system_prompt: bool = True) -> ConversationState:
    """[SystemPromptEvent, event 0, event 1, event 2], or just the three events."""
    state = _open(tmp_path, uuid.uuid4(), _agent())
    if system_prompt:
        _init_state(state)
    for i in range(3):
        state.append_event(_user_event(f"event {i}"))
    return state


def _delete_event_file(tmp_path, disk_index: int) -> None:
    events_dir = os.path.join(str(tmp_path), "events")
    prefix = f"event-{disk_index:05d}-"
    name = next(n for n in os.listdir(events_dir) if n.startswith(prefix))
    os.remove(os.path.join(events_dir, name))


def _delete_all_event_files(tmp_path) -> None:
    events_dir = os.path.join(str(tmp_path), "events")
    for name in os.listdir(events_dir):
        if name.startswith("event-"):
            os.remove(os.path.join(events_dir, name))


def test_missing_middle_event_file_keeps_the_system_prompt_in_view(tmp_path):
    state = _create_state(tmp_path)
    ids = [e.id for e in state.events]

    _delete_event_file(tmp_path, 2)  # event 1
    reloaded = _open(tmp_path, state.id)

    assert len(reloaded.events) == 3
    for evt_id in [ids[0], ids[1], ids[3]]:
        assert reloaded.events[reloaded.events.get_index(evt_id)].id == evt_id
    # The branch is bridged over the hole, so the LLM still gets the prompt
    # and the history before the lost event.
    assert [e.id for e in reloaded.view.events] == [ids[0], ids[1], ids[3]]
    assert isinstance(reloaded.view.events[0], SystemPromptEvent)


def test_missing_leaf_event_file_moves_head_once(tmp_path, caplog):
    state = _create_state(tmp_path)
    ids = [e.id for e in state.events]

    _delete_event_file(tmp_path, 3)  # the leaf, event 2
    with caplog.at_level("WARNING"):
        reloaded = _open(tmp_path, state.id)

    assert reloaded.leaf_event_id == ids[2]
    assert [e.id for e in reloaded.view.events] == ids[:3]
    assert f"moved HEAD to '{ids[2]}'" in caplog.text
    assert reloaded.append_event(_user_event("after recovery")) == 3

    # The move was persisted: the next load has nothing left to repair.
    caplog.clear()
    with caplog.at_level("WARNING"):
        again = _open(tmp_path, state.id)
    assert "moved HEAD" not in caplog.text
    assert again.leaf_event_id == reloaded.leaf_event_id


def test_every_event_file_lost_still_accepts_events(tmp_path):
    state = _create_state(tmp_path)

    _delete_all_event_files(tmp_path)
    reloaded = _open(tmp_path, state.id)

    assert len(reloaded.events) == 0
    assert reloaded.leaf_event_id is None
    _init_state(reloaded)
    assert isinstance(reloaded.events[0], SystemPromptEvent)
    assert reloaded.append_event(_user_event("after recovery")) == 1


def test_missing_system_prompt_file_is_recreated_as_the_root(tmp_path):
    state = _create_state(tmp_path)
    ids = [e.id for e in state.events]

    _delete_event_file(tmp_path, 0)  # the SystemPromptEvent
    reloaded = _open(tmp_path, state.id)
    _init_state(reloaded)

    # Re-created under its old id, so the surviving events hang off it again,
    # and HEAD stayed on the last event instead of moving onto the prompt.
    view = reloaded.view.events
    assert isinstance(view[0], SystemPromptEvent)
    assert [e.id for e in view] == ids
    assert reloaded.leaf_event_id == ids[3]

    # A later resume finds the re-created prompt and adds nothing.
    again = _open(tmp_path, state.id)
    _init_state(again)
    assert len(again.events) == len(reloaded.events)
    assert [e.id for e in again.view.events] == ids


def test_legacy_log_with_a_gap_reloads_after_an_append(tmp_path):
    """Legacy events (no parent_id) chain by index; a new event behind the gap
    must not sort before the last survivor and close a cycle on reload."""
    state = _open(tmp_path, uuid.uuid4(), _agent())
    for i, c in enumerate("abc"):
        state._events.append(_user_event(f"legacy {i}", f"{c * 8}-0000-4000-8000-0"))
    _delete_event_file(tmp_path, 1)

    reloaded = _open(tmp_path, state.id)
    reloaded.append_event(_user_event("new", "11111111-0000-4000-8000-0"))
    again = _open(tmp_path, state.id)

    assert [e.id[:1] for e in again.view.events] == ["a", "c", "1"]


def test_intact_log_loads_unchanged(tmp_path):
    state = _create_state(tmp_path, system_prompt=False)

    reloaded = _open(tmp_path, state.id)

    assert len(reloaded.events) == 3
    assert [e.id for e in reloaded.view.events] == [e.id for e in reloaded.events]
