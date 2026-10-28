"""Conversation affinity for direct and proxied OpenRouter requests."""

import asyncio

import pytest

from openhands.sdk.llm import LLM, LLMCallContext
from openhands.sdk.llm.call_context import llm_call_context_scope
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.llm.options.chat_options import select_chat_options
from openhands.sdk.llm.options.responses_options import select_responses_options


@pytest.fixture(params=["chat", "responses"])
def select_options(request):
    if request.param == "chat":
        return lambda llm, **kwargs: select_chat_options(llm, has_tools=False, **kwargs)
    return lambda llm, **kwargs: select_responses_options(
        llm, include=None, store=None, **kwargs
    )


@pytest.mark.parametrize(
    ("model", "base_url", "enabled", "expected"),
    [
        ("litellm_proxy/router-alias", "https://proxy.example.test", None, False),
        ("litellm_proxy/router-alias", "https://proxy.example.test", True, True),
        ("litellm_proxy/router-alias", "https://proxy.example.test", False, False),
        ("openrouter/provider/model", None, None, True),
        ("openrouter/provider/model", None, False, False),
        ("provider/model", "https://openrouter.ai/api/v1", None, True),
        ("openai/gpt-4o-mini", None, None, False),
    ],
)
def test_session_body_policy(select_options, model, base_url, enabled, expected):
    llm = LLM(
        model=model,
        base_url=base_url,
        send_session_id_in_body=enabled,
    )
    options = select_options(
        llm, user_kwargs={}, call_context=LLMCallContext(session_id="conversation-A")
    )

    assert options.get("extra_body", {}).get("session_id") == (
        "conversation-A" if expected else None
    )
    assert options["extra_headers"]["x-litellm-session-id"] == "conversation-A"


@pytest.mark.parametrize("override_source", ["llm", "call"])
def test_explicit_body_session_id_wins(select_options, override_source):
    body = {"session_id": "explicit", "provider": {"order": ["provider-A"]}}
    llm = LLM(
        model="litellm_proxy/router-alias",
        send_session_id_in_body=True,
        litellm_extra_body=body if override_source == "llm" else {},
    )
    kwargs = {"extra_body": body} if override_source == "call" else {}
    options = select_options(
        llm, user_kwargs=kwargs, call_context=LLMCallContext(session_id="conversation-A")
    )

    assert options["extra_body"] == body
    assert options["extra_headers"]["x-litellm-session-id"] == "conversation-A"


def test_session_body_requires_conversation_context(select_options):
    llm = LLM(model="litellm_proxy/router-alias", send_session_id_in_body=True)
    options = select_options(llm, user_kwargs={})

    assert "session_id" not in options.get("extra_body", {})


@pytest.mark.asyncio
async def test_shared_llm_keeps_body_sessions_isolated(select_options):
    body = {"provider": {"order": ["provider-A"]}}
    llm = LLM(
        model="litellm_proxy/router-alias",
        send_session_id_in_body=True,
        litellm_extra_body=body,
    )

    async def turns(session_id):
        observed = []
        with llm_call_context_scope(LLMCallContext(session_id=session_id)):
            for _ in range(2):
                await asyncio.sleep(0)
                observed.append(select_options(llm, user_kwargs={})["extra_body"])
        return observed

    first, second = await asyncio.gather(turns("first"), turns("second"))

    assert [item["session_id"] for item in first] == ["first", "first"]
    assert [item["session_id"] for item in second] == ["second", "second"]
    assert all(item["provider"] == body["provider"] for item in first + second)
    assert llm.litellm_extra_body == body
    assert "session_id" not in select_options(llm, user_kwargs={}).get("extra_body", {})


def test_profile_round_trip_preserves_session_body_policy(tmp_path, select_options):
    store = LLMProfileStore(base_dir=tmp_path)
    store.save(
        "proxied-router",
        LLM(model="litellm_proxy/router-alias", send_session_id_in_body=True),
    )
    loaded = store.load("proxied-router")
    options = select_options(
        loaded, user_kwargs={}, call_context=LLMCallContext(session_id="after-load")
    )

    assert options["extra_body"]["session_id"] == "after-load"
