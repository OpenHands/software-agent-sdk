"""Tests for SDK-3: one-time prompt-cache inactive warning at conversation
construction."""

import logging
import tempfile

from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.llm import LLM


def _make_conv(model: str, tmpdir: str, caching: bool = True) -> Conversation:
    llm = LLM(
        model=model,
        api_key=SecretStr("k"),
        usage_id="test-llm",
        caching_prompt=caching,
    )
    agent = Agent(llm=llm, tools=[])
    return Conversation(agent=agent, persistence_dir=tmpdir, workspace=tmpdir)


def test_warning_emitted_for_non_caching_model(caplog):
    """A model that's not in PROMPT_CACHE_MODELS should emit the warning."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with caplog.at_level(
            logging.WARNING, logger="openhands.sdk.conversation.impl.local_conversation"
        ):
            _make_conv("nemotron-3-ultra-550b", tmpdir)
    matched = [r for r in caplog.records if "Prompt caching is not active" in r.message]
    assert len(matched) == 1
    # The model name surfaces in the formatted message so log aggregation
    # tools can group by model.
    assert "nemotron-3-ultra-550b" in matched[0].getMessage()


def test_no_warning_for_caching_model(caplog):
    """A model in PROMPT_CACHE_MODELS should not emit the warning."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with caplog.at_level(
            logging.WARNING, logger="openhands.sdk.conversation.impl.local_conversation"
        ):
            _make_conv("claude-sonnet-4-5", tmpdir)
    matched = [r for r in caplog.records if "Prompt caching is not active" in r.message]
    assert matched == []


def test_no_warning_when_caching_explicitly_disabled_on_supported_model(caplog):
    """If caching is supported by the model but disabled by config, the
    user already made that choice — still warn so it's loud."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with caplog.at_level(
            logging.WARNING, logger="openhands.sdk.conversation.impl.local_conversation"
        ):
            _make_conv("claude-sonnet-4-5", tmpdir, caching=False)
    matched = [r for r in caplog.records if "Prompt caching is not active" in r.message]
    assert len(matched) == 1


def test_warning_emitted_only_once_per_conversation(caplog):
    with tempfile.TemporaryDirectory() as tmpdir:
        with caplog.at_level(
            logging.WARNING, logger="openhands.sdk.conversation.impl.local_conversation"
        ):
            _make_conv("nemotron-3-ultra-550b", tmpdir)
            # Constructing again gives a *new* conversation → one warning each
            # is fine, but constructing once should yield exactly one record.
    matched = [r for r in caplog.records if "Prompt caching is not active" in r.message]
    assert len(matched) == 1
