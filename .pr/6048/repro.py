"""One tool loop (thinking only on its first response) that starts inside
keep_first and runs to the end of the view. Only the summarizer transport is
stubbed. Run: PYTHONPATH=. uv run python .pr/6048/repro.py
"""

from unittest.mock import patch

from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse
from pydantic import SecretStr

from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.context.view import View
from openhands.sdk.event import MessageEvent, SystemPromptEvent
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.llm import LLM, Message, TextContent
from tests.sdk.context.view.conftest import (
    create_action_event,
    create_observation_event,
)


events = [
    SystemPromptEvent(system_prompt=TextContent(text="system"), tools=[]),
    MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text="do the task")]),
    ),
]
for i in range(60):
    action = create_action_event(f"r{i}", f"c{i}", thinking="plan" if i == 0 else None)
    obs = create_observation_event(f"c{i}", content=f"out {i}")
    events += [action, obs.model_copy(update={"action_id": action.id})]
view = View.from_events(events)

condenser = LLMSummarizingCondenser(
    llm=LLM(model="gpt-4o", api_key=SecretStr("k"), usage_id="condenser"),
    max_size=80,
    keep_first=4,
)
summary = ModelResponse(
    id="r",
    choices=[
        Choices(
            finish_reason="stop",
            index=0,
            message=LiteLLMMessage(content="loop summary", role="assistant"),
        )
    ],
)
print(f"view: {len(view)} events, max_size=80, keep_first=4")
print(f"manipulation indices: {sorted(view.manipulation_indices)[:6]}")
print(f"requirement: {condenser.condensation_requirement(view)}")
with patch.object(LLM, "_transport_call", return_value=summary) as transport:
    result = condenser.condense(view)
if isinstance(result, Condensation):
    print(
        f"OK condense() -> Condensation: forgot {len(result.forgotten_event_ids)} "
        f"events, summary at offset {result.summary_offset}, "
        f"summarizer calls={transport.call_count}"
    )
else:
    print(
        f"error: condense() returned the view unchanged ({len(result)} events > "
        f"max_size), summarizer calls={transport.call_count}"
    )
