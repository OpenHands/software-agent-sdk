"""Property test: a parallel tool-call batch becomes a single assistant message.

The message events_to_messages builds for the ActionEvents of one LLM response
equals the message of the batch's first action on its own, which carries the
shared thought, reasoning and thinking blocks, with the tool calls of every
action in the batch in order.
"""

from hypothesis import given, strategies as st

from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.event.llm_convertible import ActionEvent
from openhands.sdk.llm import (
    MessageToolCall,
    ReasoningItemModel,
    RedactedThinkingBlock,
    TextContent,
    ThinkingBlock,
)
from openhands.sdk.mcp.definition import MCPToolAction


SHORT_TEXT = st.text(max_size=10)

THOUGHTS = st.lists(st.builds(TextContent, text=SHORT_TEXT), max_size=2)
REASONING_CONTENT = st.none() | SHORT_TEXT
THINKING_BLOCKS = st.lists(
    st.one_of(
        st.builds(
            ThinkingBlock,
            thinking=st.text(min_size=1, max_size=10),
            signature=st.none() | SHORT_TEXT,
        ),
        st.builds(RedactedThinkingBlock, data=st.text(min_size=1, max_size=10)),
    ),
    max_size=2,
)
REASONING_ITEMS = st.none() | st.builds(
    ReasoningItemModel,
    id=st.none() | SHORT_TEXT,
    summary=st.lists(SHORT_TEXT, max_size=2),
    encrypted_content=st.none() | st.text(min_size=1, max_size=8),
)


def _action(
    index: int,
    thought: list[TextContent],
    reasoning_content: str | None,
    thinking_blocks: list[ThinkingBlock | RedactedThinkingBlock],
    responses_reasoning_item: ReasoningItemModel | None,
) -> ActionEvent:
    tool_call_id = f"call_{index}"
    return ActionEvent(
        source="agent",
        thought=thought,
        reasoning_content=reasoning_content,
        thinking_blocks=thinking_blocks,
        responses_reasoning_item=responses_reasoning_item,
        action=MCPToolAction(data={}),
        tool_name="tool",
        tool_call_id=tool_call_id,
        tool_call=MessageToolCall(
            id=tool_call_id, name="tool", arguments="{}", origin="completion"
        ),
        llm_response_id="response",
    )


@given(
    batch_size=st.integers(min_value=1, max_value=4),
    thought=THOUGHTS,
    reasoning_content=REASONING_CONTENT,
    thinking_blocks=THINKING_BLOCKS,
    responses_reasoning_item=REASONING_ITEMS,
)
def test_batch_message_is_first_action_message_with_every_tool_call(
    batch_size: int,
    thought: list[TextContent],
    reasoning_content: str | None,
    thinking_blocks: list[ThinkingBlock | RedactedThinkingBlock],
    responses_reasoning_item: ReasoningItemModel | None,
) -> None:
    first = _action(
        0, thought, reasoning_content, thinking_blocks, responses_reasoning_item
    )
    batch = [first] + [_action(i, [], None, [], None) for i in range(1, batch_size)]
    single = first.to_llm_message()

    [message] = LLMConvertibleEvent.events_to_messages([*batch])

    assert message.tool_calls == [action.tool_call for action in batch]
    assert message.model_copy(update={"tool_calls": single.tool_calls}) == single
