"""Property tests for the LLMSummarizingCondenser contract.

For any history that opens with a SystemPromptEvent and any condenser settings,
a condensation the condenser returns forgets at least one event, never forgets
the system prompt and never places the summary in front of it. A hard context
reset keeps the system prompt as well.
"""

from unittest.mock import patch

from hypothesis import given, strategies as st

from openhands.sdk.context.condenser.base import NoCondensationAvailableException
from openhands.sdk.context.condenser.llm_summarizing_condenser import (
    LLMSummarizingCondenser,
)
from openhands.sdk.context.view import View
from openhands.sdk.event.base import Event
from openhands.sdk.event.condenser import Condensation, CondensationRequest
from openhands.sdk.event.llm_convertible import SystemPromptEvent
from openhands.sdk.llm import (
    LLM,
    LLMResponse,
    Message,
    MetricsSnapshot,
    TextContent,
)
from tests.conftest import create_mock_litellm_response
from tests.sdk.context.view.conftest import (
    create_action_event,
    create_observation_event,
    message_event,
)


SUMMARY_RESPONSE = LLMResponse(
    message=Message(role="assistant", content=[TextContent(text="summary")]),
    metrics=MetricsSnapshot(
        model_name="gpt-4o",
        accumulated_cost=0.0,
        max_budget_per_task=None,
        accumulated_token_usage=None,
    ),
    raw_response=create_mock_litellm_response(content="summary"),
)

HISTORY_BODIES = st.lists(
    st.sampled_from(["message", "tool_call", "tool_call_with_thinking"]), max_size=30
)
KEEP_FIRST = st.integers(min_value=0, max_value=3)
EXTRA_MAX_SIZE = st.integers(min_value=0, max_value=6)


def _condenser(keep_first: int, extra_max_size: int) -> LLMSummarizingCondenser:
    # 2 * keep_first + 4 is the smallest max_size the condenser accepts.
    return LLMSummarizingCondenser(
        llm=LLM(model="gpt-4o", usage_id="condenser"),
        keep_first=keep_first,
        max_size=2 * keep_first + 4 + extra_max_size,
    )


def _view(
    system_prompt: SystemPromptEvent, body: list[str], condensation_requested: bool
) -> View:
    events: list[Event] = [system_prompt]
    for n, kind in enumerate(body):
        if kind == "message":
            events.append(message_event(f"message {n}"))
        else:
            thinking = "thinking" if kind == "tool_call_with_thinking" else None
            events.append(
                create_action_event(f"response_{n}", f"call_{n}", thinking=thinking)
            )
            events.append(create_observation_event(f"call_{n}"))
    if condensation_requested:
        events.append(CondensationRequest())
    return View.from_events(events)


def _system_prompt() -> SystemPromptEvent:
    return SystemPromptEvent(system_prompt=TextContent(text="system"), tools=[])


@given(
    keep_first=KEEP_FIRST,
    extra_max_size=EXTRA_MAX_SIZE,
    body=HISTORY_BODIES,
    condensation_requested=st.booleans(),
)
def test_condensation_forgets_events_but_never_the_system_prompt(
    keep_first: int,
    extra_max_size: int,
    body: list[str],
    condensation_requested: bool,
) -> None:
    condenser = _condenser(keep_first, extra_max_size)
    system_prompt = _system_prompt()
    view = _view(system_prompt, body, condensation_requested)

    try:
        with patch.object(LLM, "generate", return_value=SUMMARY_RESPONSE):
            result = condenser.condense(view)
    except NoCondensationAvailableException:
        return

    if isinstance(result, Condensation):
        assert result.forgotten_event_ids
        assert system_prompt.id not in result.forgotten_event_ids
        assert result.summary_offset is None or result.summary_offset > 0


@given(keep_first=KEEP_FIRST, extra_max_size=EXTRA_MAX_SIZE, body=HISTORY_BODIES)
def test_hard_context_reset_keeps_the_system_prompt(
    keep_first: int, extra_max_size: int, body: list[str]
) -> None:
    condenser = _condenser(keep_first, extra_max_size)
    system_prompt = _system_prompt()
    view = _view(system_prompt, body, condensation_requested=False)

    with patch.object(LLM, "generate", return_value=SUMMARY_RESPONSE):
        reset = condenser.hard_context_reset(view)

    if reset is not None:
        assert reset.forgotten_event_ids
        assert system_prompt.id not in reset.forgotten_event_ids
        assert reset.summary_offset is None or reset.summary_offset > 0
