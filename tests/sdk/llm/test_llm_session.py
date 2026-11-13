"""Tests for LLMSession: where an LLM's usage metrics and telemetry live."""

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse, Usage
from pydantic import SecretStr

from openhands.sdk.llm import (
    LLM,
    LLMCallContext,
    LLMRegistry,
    LLMSession,
    Message,
    TextContent,
)
from openhands.sdk.llm.call_context import llm_call_context_scope


PROMPT_TOKENS = 100


def _llm(usage_id: str = "agent", model: str = "gpt-4o", **kwargs) -> LLM:
    return LLM(model=model, api_key=SecretStr("k"), usage_id=usage_id, **kwargs)


def _context(registry: LLMRegistry) -> LLMCallContext:
    return LLMCallContext().for_conversation("conv", sessions=registry.sessions)


def _call(llm: LLM, call_context: LLMCallContext | None = None) -> None:
    llm.completion(
        [Message(role="user", content=[TextContent(text="hi")])],
        call_context=call_context,
    )


def _prompt_tokens(session: LLMSession) -> int:
    usage = session.metrics.accumulated_token_usage
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
                    message=LiteLLMMessage(role="assistant", content="ok"),
                )
            ],
            model="gpt-4o",
            usage=Usage(
                prompt_tokens=PROMPT_TOKENS, completion_tokens=1, total_tokens=101
            ),
        )

    with patch("openhands.sdk.llm.llm.litellm_completion", side_effect=respond):
        yield


# ── The LLM's own session ──────────────────────────────────────────────


def test_llm_metrics_and_telemetry_are_its_session():
    llm = _llm()

    assert llm.metrics is llm.session.metrics
    assert llm.telemetry is llm.session.telemetry
    assert llm.session_for() is llm.session

    _call(llm)
    assert _prompt_tokens(llm.session) == PROMPT_TOKENS


def test_copy_keeps_the_session_unless_the_usage_id_changes():
    llm = _llm()

    assert llm.model_copy(update={"stream": False}).session is llm.session

    other_role = llm.model_copy(update={"usage_id": "condenser"})
    assert other_role.session is not llm.session
    _call(other_role)
    assert _prompt_tokens(other_role.session) == PROMPT_TOKENS
    assert _prompt_tokens(llm.session) == 0


def test_equal_llms_stay_equal():
    """Sessions compare by value, so LLM equality is unchanged."""
    assert _llm() == _llm()
    used = _llm()
    used.metrics.add_cost(1.0)
    assert used != _llm()


# ── Sessions of a registry ─────────────────────────────────────────────


def test_registry_takes_over_the_session_of_the_first_llm_of_a_role():
    registry = LLMRegistry()
    llm = _llm()
    llm.metrics.add_cost(1.0)  # usage from before the conversation is kept

    registry.add(llm)

    assert registry.sessions["agent"] is llm.session
    assert llm.metrics.accumulated_cost == 1.0
    _call(llm, _context(registry))
    assert _prompt_tokens(registry.sessions["agent"]) == PROMPT_TOKENS


def test_session_stays_with_the_role_when_the_llm_is_replaced():
    registry = LLMRegistry()
    first = _llm()
    registry.add(first)
    session = registry.sessions["agent"]
    _call(first, _context(registry))

    registry.remove("agent")
    assert registry.sessions["agent"] is session  # kept while the slot is empty
    second = _llm(model="gpt-4o-mini")
    registry.add(second)

    assert registry.sessions["agent"] is session
    assert second.metrics is session.metrics
    assert session.telemetry.model_name == "gpt-4o-mini"
    _call(second, _context(registry))
    assert _prompt_tokens(session) == 2 * PROMPT_TOKENS


def test_call_records_in_the_session_of_its_role_whichever_object_calls():
    """A copy that never went through ``add()`` still counts for its role."""
    registry = LLMRegistry()
    registry.add(_llm(usage_id="condenser"))
    unregistered = _llm(model="gpt-4o-mini").model_copy(
        update={"usage_id": "condenser"}
    )

    _call(unregistered, _context(registry))

    session = registry.sessions["condenser"]
    assert _prompt_tokens(session) == PROMPT_TOKENS
    assert session.telemetry.model_name == "gpt-4o-mini"
    # ...and on the object that made the call.
    assert _prompt_tokens(unregistered.session) == PROMPT_TOKENS


def test_call_without_a_registered_role_records_on_the_llm_itself():
    registry = LLMRegistry()
    registry.add(_llm())
    helper = _llm(usage_id="helper")

    _call(helper, _context(registry))

    assert "helper" not in registry.sessions
    assert _prompt_tokens(helper.session) == PROMPT_TOKENS
    assert _prompt_tokens(registry.sessions["agent"]) == 0


def test_registered_llm_records_in_its_conversation_without_a_context():
    registry = LLMRegistry()
    llm = _llm()
    registry.add(llm)

    _call(llm)

    assert _prompt_tokens(registry.sessions["agent"]) == PROMPT_TOKENS


def test_scoped_context_selects_the_session_like_an_explicit_one():
    first, second = LLMRegistry(), LLMRegistry()
    llm = _llm()
    first.add(llm)
    second.add(llm)

    with llm_call_context_scope(_context(first)):
        _call(llm)

    assert _prompt_tokens(first.sessions["agent"]) == PROMPT_TOKENS
    assert _prompt_tokens(second.sessions["agent"]) == 0


def test_one_llm_in_two_registries_gets_a_session_in_each():
    first, second = LLMRegistry(), LLMRegistry()
    llm = _llm()
    first.add(llm)
    _call(llm, _context(first))

    second.add(llm)
    _call(llm, _context(second))
    _call(llm, _context(second))
    _call(llm, _context(first))

    assert first.sessions["agent"] is not second.sessions["agent"]
    assert _prompt_tokens(first.sessions["agent"]) == 2 * PROMPT_TOKENS
    assert _prompt_tokens(second.sessions["agent"]) == 2 * PROMPT_TOKENS
    # The object itself keeps counting everything it did.
    assert _prompt_tokens(llm.session) == 4 * PROMPT_TOKENS


def test_child_conversation_context_does_not_inherit_sessions():
    registry = LLMRegistry()
    parent = _context(registry)

    assert parent.for_conversation("child").sessions is None
    assert parent == LLMCallContext(prompt_cache_key="conv", session_id="conv")


# ── Registry-wide callbacks ────────────────────────────────────────────


def test_registry_stats_callback_covers_present_and_future_sessions():
    registry = LLMRegistry()
    agent_llm = _llm()
    registry.add(agent_llm)
    fired: list[str] = []
    registry.set_stats_update_callback(lambda: fired.append("stats"))

    _call(agent_llm, _context(registry))
    late = _llm(usage_id="late")
    registry.add(late)
    _call(late, _context(registry))
    registry.remove("agent")
    replacement = _llm(model="gpt-4o-mini")
    registry.add(replacement)
    _call(replacement, _context(registry))

    assert fired == ["stats"] * 3


def test_registry_callbacks_do_not_cross_registries():
    first, second = LLMRegistry(), LLMRegistry()
    llm = _llm()
    fired: list[str] = []
    first.set_stats_update_callback(lambda: fired.append("first"))
    second.set_stats_update_callback(lambda: fired.append("second"))
    first.add(llm)
    second.add(llm)

    _call(llm, _context(first))
    _call(llm, _context(second))

    assert fired == ["first", "second"]


def test_registry_log_callback_names_the_role_and_the_model_called(tmp_path):
    registry = LLMRegistry()
    logs: list[tuple[str, str]] = []
    registry.set_log_completions_callback(
        lambda filename, data, usage_id, model: logs.append((usage_id, model))
    )
    quiet = _llm()
    registry.add(quiet)
    _call(quiet, _context(registry))
    assert logs == []  # log_completions is off for this LLM

    registry.remove("agent")
    logged = _llm(
        model="gpt-4o-mini",
        log_completions=True,
        log_completions_folder=str(tmp_path),
    )
    registry.add(logged)
    _call(logged, _context(registry))

    assert logs == [("agent", "gpt-4o-mini")]
    assert list(tmp_path.iterdir()) == []  # sent to the callback, not to disk


def test_callback_set_on_the_llm_still_fires_in_a_conversation():
    registry = LLMRegistry()
    llm = _llm()
    fired: list[str] = []
    llm.telemetry.set_stats_update_callback(lambda: fired.append("llm"))

    registry.add(llm)
    _call(llm, _context(registry))

    assert fired == ["llm"]
