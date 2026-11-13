"""Tests for wiring the SDK refresh-on-401 hook onto managed-proxy LLMs.

``register_managed_llm_key_refresh`` is the agent-server half of
OpenHands/software-agent-sdk#5189: it registers
:meth:`openhands.sdk.llm.LLM.set_api_key_refresh_hook` on the managed LLMs so a
stale managed key recovers in place. These tests assert the wiring is inert
unless the control plane opts in, that it targets only the managed-proxy LLMs,
and that the registered hook actually feeds the SDK's re-resolution path.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal
from uuid import uuid4

import pytest
from litellm import ModelResponse
from litellm.exceptions import AuthenticationError
from pydantic import SecretStr

from openhands.agent_server.conversation_service import AutoTitleSubscriber
from openhands.agent_server.event_service import EventService
from openhands.agent_server.managed_llm_key import (
    REFRESH_BASE_URLS_ENV,
    REFRESH_HEADERS_ENV,
    REFRESH_URL_ENV,
    _load_headers,
    register_managed_llm_key_refresh,
)
from openhands.agent_server.models import StoredConversation
from openhands.agent_server.persistence import reset_stores
from openhands.sdk import LLM, Agent
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.secret import (
    register_local_secret_resolver,
    unregister_local_secret_resolver,
)
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.workspace import LocalWorkspace


MANAGED_BASE_URL = "https://llm-proxy.example/v1"
BYOK_BASE_URL = "https://api.openai.com/v1"
REFRESH_URL = "https://app.example/api/v1/sandboxes/s1/managed-llm-key"


def _auth_error() -> AuthenticationError:
    return AuthenticationError(
        message=(
            "Invalid proxy server token passed. Unable to find token in cache or "
            "LiteLLM_VerificationTokenTable (token_not_found_in_db)"
        ),
        llm_provider="openhands",
        model="gpt-4o",
    )


def _llm(
    usage_id: str,
    base_url: str | None,
    auth_type: Literal["api_key", "subscription"] = "api_key",
) -> LLM:
    return LLM(
        usage_id=usage_id,
        model="gpt-4o",
        api_key=SecretStr("stale_key"),
        base_url=base_url,
        auth_type=auth_type,
    )


def _agent(llm: LLM) -> Agent:
    return Agent(llm=llm, tools=[])


@contextmanager
def _served_key(url: str, value: str) -> Iterator[None]:
    """Answer ``url`` locally so LookupSecret resolves without real HTTP."""

    def resolver(requested: str) -> str | None:
        return value if requested == url else None

    register_local_secret_resolver(resolver)
    try:
        yield
    finally:
        unregister_local_secret_resolver(resolver)


def test_no_env_is_noop(monkeypatch):
    monkeypatch.delenv(REFRESH_URL_ENV, raising=False)
    llm = _llm("m", MANAGED_BASE_URL)
    agent = _agent(llm)

    assert register_managed_llm_key_refresh(agent.get_all_llms()) == 0
    # Hook untouched: a 401 does not resolve a refreshed key.
    assert llm._resolve_refreshed_api_key(_auth_error()) is None


def test_registers_and_feeds_sdk_resolution(monkeypatch):
    monkeypatch.setenv(REFRESH_URL_ENV, REFRESH_URL)
    monkeypatch.setenv(REFRESH_BASE_URLS_ENV, MANAGED_BASE_URL)
    llm = _llm("m", MANAGED_BASE_URL)
    agent = _agent(llm)

    assert register_managed_llm_key_refresh(agent.get_all_llms()) == 1

    with _served_key(REFRESH_URL, "fresh_key"):
        refreshed = llm._resolve_refreshed_api_key(_auth_error())
    assert refreshed is not None
    assert refreshed.get_secret_value() == "fresh_key"


def test_scopes_to_managed_base_url(monkeypatch):
    monkeypatch.setenv(REFRESH_URL_ENV, REFRESH_URL)
    monkeypatch.setenv(REFRESH_BASE_URLS_ENV, MANAGED_BASE_URL)
    byok = _llm("byok", BYOK_BASE_URL)
    agent = _agent(byok)

    # A BYOK LLM (different base_url) must not receive the managed hook.
    assert register_managed_llm_key_refresh(agent.get_all_llms()) == 0
    with _served_key(REFRESH_URL, "fresh_key"):
        assert byok._resolve_refreshed_api_key(_auth_error()) is None


@pytest.mark.parametrize("persisted", [False, True])
def test_canonical_managed_provider_refreshes(monkeypatch, persisted):
    monkeypatch.setenv(REFRESH_URL_ENV, REFRESH_URL)
    monkeypatch.setenv(REFRESH_BASE_URLS_ENV, "https://llm-proxy.app.all-hands.dev")
    llm = LLM(
        model="openhands/deepseek-v4.1-flash",
        api_key=SecretStr("stale_key"),
        usage_id="managed",
    )
    if persisted:
        llm = LLM.model_validate_json(
            llm.model_dump_json(context={"expose_secrets": True})
        )
    assert llm.base_url is None
    assert register_managed_llm_key_refresh([llm]) == 1
    keys = []

    def complete(**kwargs):
        keys.append(kwargs["api_key"])
        if kwargs["api_key"] == "stale_key":
            raise _auth_error()
        return ModelResponse(
            choices=[{"message": {"role": "assistant", "content": "resumed"}}]
        )

    monkeypatch.setattr("openhands.sdk.llm.llm.litellm_completion", complete)
    with _served_key(REFRESH_URL, "fresh_key"):
        response = llm.completion(
            [Message(role="user", content=[TextContent(text="continue")])]
        )
    assert response.message.content == [TextContent(text="resumed")]
    assert keys == ["stale_key", "fresh_key"]


def test_skips_subscription_auth(monkeypatch):
    monkeypatch.setenv(REFRESH_URL_ENV, REFRESH_URL)
    monkeypatch.setenv(REFRESH_BASE_URLS_ENV, MANAGED_BASE_URL)
    sub = _llm("sub", MANAGED_BASE_URL, auth_type="subscription")
    agent = _agent(sub)

    assert register_managed_llm_key_refresh(agent.get_all_llms()) == 0


def test_without_allowlist_feature_stays_off(monkeypatch):
    # Fail closed: the operator must enumerate the managed proxies explicitly,
    # otherwise the hook would also attach to BYOK LLMs.
    monkeypatch.setenv(REFRESH_URL_ENV, REFRESH_URL)
    monkeypatch.delenv(REFRESH_BASE_URLS_ENV, raising=False)
    llm = _llm("m", MANAGED_BASE_URL)
    agent = _agent(llm)

    assert register_managed_llm_key_refresh(agent.get_all_llms()) == 0
    with _served_key(REFRESH_URL, "fresh_key"):
        assert llm._resolve_refreshed_api_key(_auth_error()) is None


def test_blank_allowlist_feature_stays_off(monkeypatch):
    # A set-but-empty allow-list is as unconfigured as an absent one.
    monkeypatch.setenv(REFRESH_URL_ENV, REFRESH_URL)
    monkeypatch.setenv(REFRESH_BASE_URLS_ENV, " , ")
    llm = _llm("m", MANAGED_BASE_URL)

    assert register_managed_llm_key_refresh([llm]) == 0


def test_hook_failure_surfaces_original_error(monkeypatch):
    # Unreachable address, no local resolver: the fetch fails, and the hook must
    # swallow it (return None) rather than mask the original 401.
    monkeypatch.setenv(REFRESH_URL_ENV, "http://127.0.0.1:1/managed-llm-key")
    monkeypatch.setenv(REFRESH_BASE_URLS_ENV, MANAGED_BASE_URL)
    llm = _llm("m", MANAGED_BASE_URL)
    agent = _agent(llm)
    assert register_managed_llm_key_refresh(agent.get_all_llms()) == 1

    assert llm._resolve_refreshed_api_key(_auth_error()) is None


def test_empty_served_key_does_not_retry(monkeypatch):
    monkeypatch.setenv(REFRESH_URL_ENV, REFRESH_URL)
    monkeypatch.setenv(REFRESH_BASE_URLS_ENV, MANAGED_BASE_URL)
    llm = _llm("m", MANAGED_BASE_URL)
    agent = _agent(llm)
    assert register_managed_llm_key_refresh(agent.get_all_llms()) == 1

    with _served_key(REFRESH_URL, "   "):
        # Whitespace-only body strips to empty -> no usable key -> no retry.
        assert llm._resolve_refreshed_api_key(_auth_error()) is None


def test_base_url_match_ignores_trailing_slash(monkeypatch):
    monkeypatch.setenv(REFRESH_URL_ENV, REFRESH_URL)
    # Allow-list has no trailing slash, LLM base_url does — must still match.
    monkeypatch.setenv(REFRESH_BASE_URLS_ENV, MANAGED_BASE_URL)
    llm = _llm("m", MANAGED_BASE_URL + "/")
    assert register_managed_llm_key_refresh([llm]) == 1


def test_allowlist_entry_trailing_slash_ignored(monkeypatch):
    monkeypatch.setenv(REFRESH_URL_ENV, REFRESH_URL)
    # Allow-list has a trailing slash, LLM base_url does not — must still match.
    monkeypatch.setenv(REFRESH_BASE_URLS_ENV, MANAGED_BASE_URL + "/")
    llm = _llm("m", MANAGED_BASE_URL)
    assert register_managed_llm_key_refresh([llm]) == 1


def test_non_object_headers_env_is_ignored(monkeypatch):
    monkeypatch.setenv(REFRESH_URL_ENV, REFRESH_URL)
    # Valid JSON that isn't an object must be ignored, not crash registration.
    monkeypatch.setenv(REFRESH_HEADERS_ENV, '["a", "b"]')
    monkeypatch.setenv(REFRESH_BASE_URLS_ENV, MANAGED_BASE_URL)
    llm = _llm("m", MANAGED_BASE_URL)

    assert register_managed_llm_key_refresh([llm]) == 1
    with _served_key(REFRESH_URL, "fresh_key"):
        refreshed = llm._resolve_refreshed_api_key(_auth_error())
    assert refreshed is not None
    assert refreshed.get_secret_value() == "fresh_key"


def test_bad_headers_env_is_ignored(monkeypatch):
    monkeypatch.setenv(REFRESH_URL_ENV, REFRESH_URL)
    monkeypatch.setenv(REFRESH_HEADERS_ENV, "not-json")
    monkeypatch.setenv(REFRESH_BASE_URLS_ENV, MANAGED_BASE_URL)
    llm = _llm("m", MANAGED_BASE_URL)
    agent = _agent(llm)

    # Malformed headers must not break registration; the hook still resolves.
    assert register_managed_llm_key_refresh(agent.get_all_llms()) == 1
    with _served_key(REFRESH_URL, "fresh_key"):
        refreshed = llm._resolve_refreshed_api_key(_auth_error())
    assert refreshed is not None
    assert refreshed.get_secret_value() == "fresh_key"


def test_headers_expand_env_references(monkeypatch):
    """Header values may reference env vars so the control plane can point at a
    per-sandbox credential (e.g. the session key) it cannot know up front."""
    monkeypatch.setenv("OH_SESSION_API_KEYS_0", "sess-abc123")
    headers = _load_headers(
        '{"X-Session-API-Key": "${OH_SESSION_API_KEYS_0}", "X-Static": "plain"}'
    )
    assert headers == {"X-Session-API-Key": "sess-abc123", "X-Static": "plain"}


def test_headers_bare_dollar_reference_expands(monkeypatch):
    """The ``$VAR`` form (no braces) expands too."""
    monkeypatch.setenv("OH_SESSION_API_KEYS_0", "sess-xyz")
    assert _load_headers('{"X-Session-API-Key": "$OH_SESSION_API_KEYS_0"}') == {
        "X-Session-API-Key": "sess-xyz"
    }


@pytest.mark.asyncio
async def test_switched_llm_keeps_refresh_hook(monkeypatch, tmp_path):
    """switch_llm on a loaded conversation re-registers the hook (#5890)."""
    monkeypatch.setenv(REFRESH_URL_ENV, REFRESH_URL)
    monkeypatch.setenv(REFRESH_BASE_URLS_ENV, MANAGED_BASE_URL)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    service = EventService(
        stored=StoredConversation(
            id=uuid4(),
            workspace=LocalWorkspace(working_dir=str(workspace)),
            confirmation_policy=NeverConfirm(),
        ),
        agent=_agent(_llm("loaded", MANAGED_BASE_URL)),
        conversations_dir=tmp_path / "conversations",
    )
    await service.start()
    try:
        conversation = service.get_conversation()

        conversation.switch_llm(_llm("switched", MANAGED_BASE_URL))
        switched = conversation.agent.llm
        assert switched.usage_id == "switched"
        with _served_key(REFRESH_URL, "fresh_key"):
            refreshed = switched._resolve_refreshed_api_key(_auth_error())
        assert refreshed is not None
        assert refreshed.get_secret_value() == "fresh_key"

        # A switch to a BYOK base_url still gets no managed hook.
        conversation.switch_llm(_llm("byok", BYOK_BASE_URL))
        byok = conversation.agent.llm
        assert byok.usage_id == "byok"
        with _served_key(REFRESH_URL, "fresh_key"):
            assert byok._resolve_refreshed_api_key(_auth_error()) is None

        # Not opted in: a switch registers nothing.
        monkeypatch.delenv(REFRESH_URL_ENV)
        conversation.switch_llm(_llm("off", MANAGED_BASE_URL))
        off = conversation.agent.llm
        assert off.usage_id == "off"
        with _served_key(REFRESH_URL, "fresh_key"):
            assert off._resolve_refreshed_api_key(_auth_error()) is None
    finally:
        await service.close()


def _resolves_served_key(llm: LLM) -> bool:
    """True when ``llm``'s 401 resolves the served key (hook registered)."""
    with _served_key(REFRESH_URL, "fresh_key"):
        refreshed = llm._resolve_refreshed_api_key(_auth_error())
    return refreshed is not None and refreshed.get_secret_value() == "fresh_key"


@pytest.fixture
def title_profile_store(tmp_path, monkeypatch):
    """Persist title-LLM profiles under ``tmp_path`` for the real profile store."""
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path))
    reset_stores()
    yield LLMProfileStore(base_dir=tmp_path / "profiles")
    reset_stores()


def _stored_conversation(workspace, title_llm_profile: str | None = None):
    return StoredConversation(
        id=uuid4(),
        workspace=LocalWorkspace(working_dir=str(workspace)),
        confirmation_policy=NeverConfirm(),
        title_llm_profile=title_llm_profile,
    )


@pytest.mark.parametrize(
    ("base_url", "opted_in", "expect_hook"),
    [
        (MANAGED_BASE_URL, True, True),
        (BYOK_BASE_URL, True, False),
        (MANAGED_BASE_URL, False, False),
    ],
)
def test_auto_title_llm_refresh_hook(
    monkeypatch, tmp_path, title_profile_store, base_url, opted_in, expect_hook
):
    """The profile-loaded title LLM is hooked iff it is a managed LLM (#5528)."""
    if opted_in:
        monkeypatch.setenv(REFRESH_URL_ENV, REFRESH_URL)
        monkeypatch.setenv(REFRESH_BASE_URLS_ENV, MANAGED_BASE_URL)
    else:
        monkeypatch.delenv(REFRESH_URL_ENV, raising=False)
    title_profile_store.save(
        "title-fast", _llm("title-llm", base_url), include_secrets=True
    )
    service = EventService(
        stored=_stored_conversation(tmp_path, title_llm_profile="title-fast"),
        conversations_dir=tmp_path / "conversations",
    )

    title_llm = AutoTitleSubscriber(service=service)._load_title_llm()

    assert title_llm is not None
    assert title_llm.usage_id == "title-llm"
    assert _resolves_served_key(title_llm) is expect_hook


@pytest.mark.asyncio
async def test_generate_title_llm_refresh_hook(monkeypatch, tmp_path):
    """An LLM passed to ``EventService.generate_title`` gets the hook."""
    monkeypatch.setenv(REFRESH_URL_ENV, REFRESH_URL)
    monkeypatch.setenv(REFRESH_BASE_URLS_ENV, MANAGED_BASE_URL)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    service = EventService(
        stored=_stored_conversation(workspace),
        agent=_agent(_llm("agent", MANAGED_BASE_URL)),
        conversations_dir=tmp_path / "conversations",
    )
    await service.start()
    try:
        titled_with: list[LLM | None] = []

        def fake_generate_title(llm, max_length):
            titled_with.append(llm)
            return "A title"

        monkeypatch.setattr(
            service.get_conversation(), "generate_title", fake_generate_title
        )
        title_llm = _llm("title-llm", MANAGED_BASE_URL)

        assert await service.generate_title(llm=title_llm) == "A title"

        assert titled_with == [title_llm]
        assert _resolves_served_key(title_llm)
    finally:
        await service.close()
