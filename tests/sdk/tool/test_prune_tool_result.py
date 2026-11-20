import uuid
from types import SimpleNamespace

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.event.llm_convertible.observation import ObservationEvent
from openhands.sdk.mcp.definition import MCPToolObservation
from openhands.sdk.tool.builtins import (
    PruneToolResultAction,
    PruneToolResultExecutor,
)
from openhands.sdk.workspace import LocalWorkspace


def _state(tmp_path, mock_llm) -> ConversationState:
    return ConversationState.create(
        id=uuid.uuid4(),
        agent=Agent(llm=mock_llm),
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
    )


def test_prune_tool_result_emits_condensation(tmp_path, mock_llm):
    state = _state(tmp_path, mock_llm)
    obs_event = ObservationEvent(
        observation=MCPToolObservation.from_text(
            text="pages of irrelevant output", tool_name="execute_bash"
        ),
        tool_name="execute_bash",
        tool_call_id="call_42",
        action_id="action_1",
        source="environment",
    )
    state.append_event(obs_event)

    result = PruneToolResultExecutor()(
        PruneToolResultAction(
            tool_call_id="call_42", summary="grep hit target/, irrelevant"
        ),
        SimpleNamespace(state=state),
    )

    condensations = [e for e in state.events if isinstance(e, Condensation)]
    assert len(condensations) == 1
    condensation = condensations[0]
    assert condensation.forgotten_event_ids == {obs_event.id}
    assert condensation.summary == "[pruned] grep hit target/, irrelevant"
    assert "pruned" in result.text.lower()


def test_prune_tool_result_unknown_call_id(tmp_path, mock_llm):
    state = _state(tmp_path, mock_llm)

    result = PruneToolResultExecutor()(
        PruneToolResultAction(tool_call_id="missing", summary="nothing"),
        SimpleNamespace(state=state),
    )

    assert all(not isinstance(e, Condensation) for e in state.events)
    assert "no tool result" in result.text.lower()
