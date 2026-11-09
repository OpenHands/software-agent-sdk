"""A conversation's LLM usage and callbacks belong to it, per usage_id.

The usage metrics and telemetry callbacks used to sit on the ``LLM`` object, so
they went wherever that object went: into a second conversation that reused
the LLM, and away from the conversation when the LLM was switched.
"""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse, Usage
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.conversation.impl.local_conversation import (
    ASK_AGENT_LLM_USAGE_ID,
    LocalConversation,
)
from openhands.sdk.llm import LLM, Message, TextContent


PROMPT_TOKENS = 100


def _llm(model: str = "gpt-4o") -> LLM:
    return LLM(model=model, api_key=SecretStr("k"), usage_id="agent")


def _conversation(tmp_path: Path, llm: LLM, name: str = "conv") -> LocalConversation:
    condenser = LLMSummarizingCondenser(
        llm=llm.model_copy(update={"usage_id": "condenser"}), max_size=80
    )
    return LocalConversation(
        agent=Agent(llm=llm, tools=[], condenser=condenser),
        workspace=tmp_path / name,
        visualizer=None,
    )


def _run(conversation: LocalConversation, text: str = "hi") -> None:
    conversation.send_message(text)
    conversation.run()


def _prompt_tokens(conversation: LocalConversation, usage_id: str = "agent") -> int:
    usage = conversation.conversation_stats.usage_to_metrics[
        usage_id
    ].accumulated_token_usage
    assert usage is not None
    return usage.prompt_tokens


@pytest.fixture(autouse=True)
def fake_completion() -> Iterator[None]:
    def respond(*args, **kwargs) -> ModelResponse:
        return ModelResponse(
            id="resp",
            choices=[
                Choices(
                    index=0,
                    finish_reason="stop",
                    message=LiteLLMMessage(role="assistant", content="done"),
                )
            ],
            model="gpt-4o",
            usage=Usage(
                prompt_tokens=PROMPT_TOKENS, completion_tokens=1, total_tokens=101
            ),
        )

    with patch("openhands.sdk.llm.llm.litellm_completion", side_effect=respond):
        yield


def test_conversations_sharing_an_llm_keep_their_usage_apart(tmp_path):
    llm = _llm()
    first = _conversation(tmp_path, llm, "first")
    second = _conversation(tmp_path, llm, "second")

    _run(first)
    _run(second)
    _run(second)

    assert _prompt_tokens(first) == PROMPT_TOKENS
    assert _prompt_tokens(second) == 2 * PROMPT_TOKENS
    # The LLM object still reports everything that went through it.
    assert llm.metrics.accumulated_token_usage is not None
    assert llm.metrics.accumulated_token_usage.prompt_tokens == 3 * PROMPT_TOKENS


def test_conversations_sharing_an_llm_keep_their_callbacks_apart(tmp_path):
    llm = _llm()
    first = _conversation(tmp_path, llm, "first")
    second = _conversation(tmp_path, llm, "second")
    fired: list[str] = []
    first.llm_registry.set_stats_update_callback(lambda: fired.append("first"))
    second.llm_registry.set_stats_update_callback(lambda: fired.append("second"))

    _run(first)
    _run(second)

    assert fired == ["first", "second"]


def test_single_conversation_reports_through_the_llm_object(tmp_path):
    """The common case is unchanged: ``llm.metrics`` is the conversation's ledger."""
    llm = _llm()
    conversation = _conversation(tmp_path, llm)

    _run(conversation)

    assert conversation.conversation_stats.usage_to_metrics["agent"] is llm.metrics
    assert _prompt_tokens(conversation) == PROMPT_TOKENS


def test_stats_callback_keeps_firing_after_switch_llm(tmp_path):
    conversation = _conversation(tmp_path, _llm())
    fired: list[str] = []
    conversation.llm_registry.set_stats_update_callback(lambda: fired.append("stats"))
    _run(conversation)

    conversation.switch_llm(_llm("gpt-4o-mini"))
    _run(conversation, "after the switch")

    assert fired == ["stats", "stats"]
    assert _prompt_tokens(conversation) == 2 * PROMPT_TOKENS


def test_condenser_usage_is_counted_after_switch_llm(tmp_path):
    conversation = _conversation(tmp_path, _llm())
    _run(conversation)
    assert _prompt_tokens(conversation, "condenser") == 0

    conversation.switch_llm(_llm("gpt-4o-mini"))
    condenser = conversation.agent.condenser
    assert isinstance(condenser, LLMSummarizingCondenser)
    assert condenser.llm.model == "gpt-4o-mini"
    condenser.llm.completion(
        [Message(role="user", content=[TextContent(text="summarise")])],
        call_context=conversation.get_llm_call_context(),
    )

    assert _prompt_tokens(conversation, "condenser") == PROMPT_TOKENS


def test_ask_agent_usage_is_counted_after_every_switch(tmp_path):
    conversation = _conversation(tmp_path, _llm())
    _run(conversation)

    counted = []
    for model in ("gpt-4o", "gpt-4o-mini", "gpt-4o"):
        conversation.switch_llm(_llm(model))
        conversation.ask_agent("what are you doing?")
        counted.append(_prompt_tokens(conversation, ASK_AGENT_LLM_USAGE_ID))

    assert counted == [PROMPT_TOKENS, 2 * PROMPT_TOKENS, 3 * PROMPT_TOKENS]


def test_resumed_conversation_continues_from_its_persisted_usage(tmp_path):
    persistence = tmp_path / "persist"
    workspace = tmp_path / "ws"
    first = LocalConversation(
        agent=Agent(llm=_llm(), tools=[]),
        workspace=workspace,
        persistence_dir=persistence,
        visualizer=None,
    )
    _run(first)
    conversation_id = first.id
    first.close()

    llm = _llm()
    resumed = LocalConversation(
        agent=Agent(llm=llm, tools=[]),
        workspace=workspace,
        persistence_dir=persistence,
        conversation_id=conversation_id,
        visualizer=None,
    )
    _run(resumed, "again")

    assert _prompt_tokens(resumed) == 2 * PROMPT_TOKENS
    assert llm.metrics is resumed.conversation_stats.usage_to_metrics["agent"]
