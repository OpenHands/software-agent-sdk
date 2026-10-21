"""Reproduce #6048: force a condensation while the summarizer LLM is down."""

import logging
import tempfile
from unittest.mock import patch

from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.context.view import View
from openhands.sdk.conversation import Conversation
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.llm import LLM

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
for noisy in ("httpx", "LiteLLM", "litellm", "openhands.sdk.conversation", "openhands.sdk.agent"):
    logging.getLogger(noisy).setLevel(logging.ERROR)

agent_llm = LLM(model="gpt-4o-mini", api_key=SecretStr("x"), usage_id="agent")
summarizer = LLM(model="gpt-4o-mini", api_key=SecretStr("x"), usage_id="summarizer")
agent = Agent(
    llm=agent_llm,
    tools=[],
    condenser=LLMSummarizingCondenser(llm=summarizer, max_size=100, keep_first=5),
)
with tempfile.TemporaryDirectory() as tmp:
    conv = Conversation(agent=agent, persistence_dir=tmp, workspace=tmp)
    for i in range(20):
        conv.send_message(f"Message {i}")
    before = len(View.from_events(conv.state.events))

    with patch.object(LLM, "generate", side_effect=RuntimeError("summarizer provider returned 503")):
        try:
            conv.condense()
            print("condense(): returned normally")
        except Exception as e:  # noqa: BLE001
            print(f"condense(): RAISED {type(e).__name__}: {e}")

    after = len(View.from_events(conv.state.events))
    condensations = [e for e in conv.state.events if isinstance(e, Condensation)]
    print(f"view events: before={before} after={after}")
    print(f"Condensation events: {len(condensations)}")
    for c in condensations:
        print(f"  forgotten={len(c.forgotten_event_ids)} summary={c.summary!r}")
    first = View.from_events(conv.state.events).events[0]
    print(f"first event in view: {type(first).__name__}")
