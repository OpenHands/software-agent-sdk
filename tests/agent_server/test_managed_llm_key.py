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

from litellm.exceptions import AuthenticationError
from pydantic import SecretStr

from openhands.agent_server.managed_llm_key import (
    REFRESH_BASE_URLS_ENV,
    REFRESH_HEADERS_ENV,
    REFRESH_URL_ENV,
    register_managed_llm_key_refresh,
)
from openhands.sdk import LLM, Agent
from openhands.sdk.secret import (
    register_local_secret_resolver,
    unregister_local_secret_resolver,
)


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


def _llm(usage_id: str, base_url: str | None, auth_type: str = "api_key") -> LLM:
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

    assert register_managed_llm_key_refresh(agent) == 0
    # Hook untouched: a 401 does not resolve a refreshed key.
    assert llm._resolve_refreshed_api_key(_auth_error()) is None


def test_registers_and_feeds_sdk_resolution(monkeypatch):
    monkeypatch.setenv(REFRESH_URL_ENV, REFRESH_URL)
    monkeypatch.setenv(REFRESH_BASE_URLS_ENV, MANAGED_BASE_URL)
    llm = _llm("m", MANAGED_BASE_URL)
    agent = _agent(llm)

    assert register_managed_llm_key_refresh(agent) == 1

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
    assert register_managed_llm_key_refresh(agent) == 0
    with _served_key(REFRESH_URL, "fresh_key"):
        assert byok._resolve_refreshed_api_key(_auth_error()) is None


def test_skips_subscription_auth(monkeypatch):
    monkeypatch.setenv(REFRESH_URL_ENV, REFRESH_URL)
    monkeypatch.delenv(REFRESH_BASE_URLS_ENV, raising=False)
    sub = _llm("sub", MANAGED_BASE_URL, auth_type="subscription")
    agent = _agent(sub)

    assert register_managed_llm_key_refresh(agent) == 0


def test_without_allowlist_applies_to_all_api_key_llms(monkeypatch):
    monkeypatch.setenv(REFRESH_URL_ENV, REFRESH_URL)
    monkeypatch.delenv(REFRESH_BASE_URLS_ENV, raising=False)
    llm = _llm("m", MANAGED_BASE_URL)
    agent = _agent(llm)

    assert register_managed_llm_key_refresh(agent) == 1


def test_hook_failure_surfaces_original_error(monkeypatch):
    # Point at a fast-failing address (connection refused) with no local
    # resolver: the hook must swallow the error and return None so the SDK
    # surfaces the original 401 rather than masking it with a new error.
    monkeypatch.setenv(REFRESH_URL_ENV, "http://127.0.0.1:1/managed-llm-key")
    monkeypatch.delenv(REFRESH_BASE_URLS_ENV, raising=False)
    llm = _llm("m", MANAGED_BASE_URL)
    agent = _agent(llm)
    assert register_managed_llm_key_refresh(agent) == 1

    assert llm._resolve_refreshed_api_key(_auth_error()) is None


def test_empty_served_key_does_not_retry(monkeypatch):
    monkeypatch.setenv(REFRESH_URL_ENV, REFRESH_URL)
    monkeypatch.delenv(REFRESH_BASE_URLS_ENV, raising=False)
    llm = _llm("m", MANAGED_BASE_URL)
    agent = _agent(llm)
    assert register_managed_llm_key_refresh(agent) == 1

    with _served_key(REFRESH_URL, "   "):
        # Whitespace-only body strips to empty -> no usable key -> no retry.
        assert llm._resolve_refreshed_api_key(_auth_error()) is None


def test_bad_headers_env_is_ignored(monkeypatch):
    monkeypatch.setenv(REFRESH_URL_ENV, REFRESH_URL)
    monkeypatch.setenv(REFRESH_HEADERS_ENV, "not-json")
    monkeypatch.delenv(REFRESH_BASE_URLS_ENV, raising=False)
    llm = _llm("m", MANAGED_BASE_URL)
    agent = _agent(llm)

    # Malformed headers must not break registration; the hook still resolves.
    assert register_managed_llm_key_refresh(agent) == 1
    with _served_key(REFRESH_URL, "fresh_key"):
        refreshed = llm._resolve_refreshed_api_key(_auth_error())
    assert refreshed is not None
    assert refreshed.get_secret_value() == "fresh_key"
