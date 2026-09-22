"""Register an API-key refresh hook so managed-proxy LLMs recover from a 401.

The enterprise/SaaS control plane mints the managed LiteLLM proxy key and can
rotate or heal it after a conversation's LLM snapshot was taken. When that
happens the sandbox agent keeps sending the stale key and its first LLM call
fails with ``401 token_not_found_in_db`` — the "first conversation fails once,
then self-heals" behaviour in OpenHands/software-agent-sdk#5189.

The SDK ships an opt-in mitigation:
:meth:`openhands.sdk.llm.LLM.set_api_key_refresh_hook` re-resolves the key once
and retries a single time on an authentication error. The hook is a Python
callable, so it cannot be serialized and shipped into the sandbox with the
LLM — it must be registered here, in the process where the LLM actually runs.

This module wires that hook to a re-resolution source supplied by the control
plane. It is a **no-op unless** the control plane sets
``OH_LLM_API_KEY_REFRESH_URL`` in the sandbox environment, so conversations that
do not opt in keep their exact current behaviour.

What the control plane must expose (see ``register_managed_llm_key_refresh``):

* ``OH_LLM_API_KEY_REFRESH_URL`` — a URL the sandbox can GET to obtain the
  *current* managed key as the plain-text response body. This is the only
  required piece; without it the feature is off.
* ``OH_LLM_API_KEY_REFRESH_HEADERS`` — optional JSON object of request headers
  used to authenticate the refresh call (e.g. the sandbox session key). Header
  values may reference environment variables with ``$VAR`` / ``${VAR}`` syntax,
  expanded from the sandbox environment; this lets the control plane point a
  header at ``${OH_SESSION_API_KEYS_0}`` without knowing the per-sandbox key when
  it builds the environment (the remote runtime assigns that key inside the
  sandbox).
* ``OH_LLM_API_KEY_REFRESH_BASE_URLS`` — optional comma-separated allow-list of
  LLM ``base_url`` values the managed key applies to. Strongly recommended so
  the hook is registered only on managed-proxy LLMs and never on a user's
  BYOK LLM (whose 401 must not be retried with the managed key).
"""

from __future__ import annotations

import json
import os

from openhands.sdk.agent import AgentBase
from openhands.sdk.llm import LLM
from openhands.sdk.logger import get_logger
from openhands.sdk.secret import LookupSecret


logger = get_logger(__name__)

REFRESH_URL_ENV = "OH_LLM_API_KEY_REFRESH_URL"
REFRESH_HEADERS_ENV = "OH_LLM_API_KEY_REFRESH_HEADERS"
REFRESH_BASE_URLS_ENV = "OH_LLM_API_KEY_REFRESH_BASE_URLS"


def _load_headers(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("%s is not valid JSON; ignoring", REFRESH_HEADERS_ENV)
        return {}
    if not isinstance(data, dict):
        logger.warning("%s must be a JSON object; ignoring", REFRESH_HEADERS_ENV)
        return {}
    # Values may reference environment variables (``$VAR`` / ``${VAR}``), expanded
    # from this process's environment. This lets the control plane point a header
    # at a credential it cannot know when it builds the env — notably the sandbox
    # session key in ``OH_SESSION_API_KEYS_0``, which is assigned per sandbox and,
    # for remote runtimes, only known inside the sandbox itself.
    return {str(k): os.path.expandvars(str(v)) for k, v in data.items()}


def _managed_base_urls(raw: str | None) -> set[str] | None:
    if not raw:
        return None
    return {u.strip().rstrip("/") for u in raw.split(",") if u.strip()}


def _llm_is_in_scope(llm: LLM, managed_base_urls: set[str] | None) -> bool:
    # Subscription auth isn't a refreshable API key (the SDK resolver skips it too).
    if llm.auth_type != "api_key":
        return False
    if managed_base_urls is None:
        # Without an allow-list, every api_key LLM matches — including BYOK keys.
        return True
    base_url = (llm.base_url or "").rstrip("/")
    return base_url in managed_base_urls


def register_managed_llm_key_refresh(agent: AgentBase) -> int:
    """Register a refresh-on-401 hook on the agent's managed-proxy LLMs.

    Iterates the agent's LLMs (``agent.get_all_llms()`` yields the live
    instances the run loop uses) and, for each in-scope LLM, registers a hook
    that re-resolves the current managed key from ``OH_LLM_API_KEY_REFRESH_URL``.

    Returns the number of LLMs a hook was registered on (``0`` means the feature
    is off or no LLM matched). Safe to call unconditionally: it does nothing
    unless the control plane opted in via ``OH_LLM_API_KEY_REFRESH_URL``.
    """
    url = os.environ.get(REFRESH_URL_ENV)
    if not url:
        return 0

    headers = _load_headers(os.environ.get(REFRESH_HEADERS_ENV))
    managed_base_urls = _managed_base_urls(os.environ.get(REFRESH_BASE_URLS_ENV))

    def _refresh() -> str | None:
        # Fresh LookupSecret per call so a rotated key isn't served from a cached one.
        # Never raise: a failed refresh must leave the original 401 to surface.
        try:
            value = LookupSecret(url=url, headers=headers).get_value()
        except Exception:
            logger.warning(
                "Managed LLM key refresh failed; surfacing the original auth error",
                exc_info=True,
            )
            return None
        value = (value or "").strip()
        return value or None

    count = 0
    for llm in agent.get_all_llms():
        if _llm_is_in_scope(llm, managed_base_urls):
            llm.set_api_key_refresh_hook(_refresh)
            count += 1

    if count:
        logger.info("Registered managed LLM key refresh hook on %d LLM(s)", count)
    else:
        logger.debug(
            "%s is set but no LLM matched the managed-key scope; no hook registered",
            REFRESH_URL_ENV,
        )
    return count
