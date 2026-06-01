"""ACP authentication-status probe endpoint.

``GET /acp/auth-status?server=<key>`` reports whether the chosen ACP provider's
CLI is already authenticated — by a subscription login (Claude Pro/Max, ChatGPT,
Google) *or* a pre-set API key — so the canvas onboarding can show a
"✓ you're already logged in" banner instead of unconditionally asking for an
API key.

The browser cannot read the keychain or credential files, so detection must run
server-side. We avoid sniffing credentials per-OS/topology and instead drive the
ACP protocol handshake (``initialize`` + ``session/new``) via
:meth:`ACPAgent.probe_auth`: the handshake only succeeds when the CLI can
authenticate, and it sends no prompt, so it spends no model tokens. Because the
probe runs *inside* the agent-server, it reports the truth for wherever the agent
will actually run, with no topology knowledge needed.
"""

from __future__ import annotations

import tempfile
from typing import Annotated, Literal, cast

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from openhands.agent_server._secrets_exposure import get_config
from openhands.agent_server.persistence import (
    PersistedSettings,
    get_secrets_store,
    get_settings_store,
)
from openhands.sdk.agent.acp_agent import (
    _ACP_AUTH_PROBE_TIMEOUT,
    ACPAgent,
    ACPAuthProbeResult,
)
from openhands.sdk.logger import get_logger
from openhands.sdk.settings import ACPAgentSettings
from openhands.sdk.settings.acp_providers import ACP_PROVIDERS
from openhands.sdk.settings.model import ACPServerKind


logger = get_logger(__name__)

acp_auth_router = APIRouter(prefix="/acp", tags=["ACP Auth"])

# Outcome categories the canvas onboarding maps directly onto its
# ``useAcpAuthStatus`` hook:
#   - authenticated   → "✓ already logged in" banner; API-key fields optional
#   - unauthenticated → show the API-key fields (server reachable, not logged in)
#   - unknown         → the probe could not run/complete; fall back to key fields
ACPAuthStatusValue = Literal["authenticated", "unauthenticated", "unknown"]

# Cap how much of a probe failure we echo back. These are transport/spawn errors
# (e.g. a missing ``npx``), not user secrets, but we both scrub known secret
# values out of the message and truncate it defensively before surfacing it.
_MAX_DETAIL_CHARS = 300

# Only scrub env values at least this long, so redaction can't mangle a detail
# string by blanking out short, non-secret tokens (e.g. "1", "true").
_MIN_SECRET_LEN = 8


class ACPAuthStatusResponse(BaseModel):
    """Result of an ACP auth-status probe for one provider."""

    server: str = Field(description="The ACP provider key that was probed.")
    status: ACPAuthStatusValue = Field(
        description=(
            "'authenticated' (session/new succeeded ⇒ logged in by subscription "
            "or API key), 'unauthenticated' (server reachable but not logged "
            "in), or 'unknown' (the probe could not run/complete)."
        )
    )
    auth_methods: list[str] = Field(
        default_factory=list,
        description=(
            "Auth method ids the server advertised at initialize. Informational "
            "only — the menu of how to log in, not a logged-in signal."
        ),
    )
    agent_name: str = Field(
        default="", description="ACP server name reported by the handshake."
    )
    agent_version: str = Field(
        default="", description="ACP server version reported by the handshake."
    )
    detail: str | None = Field(
        default=None,
        description="Populated only when status is 'unknown': why the probe failed.",
    )


def _resolve_acp_settings(server: str, settings: PersistedSettings) -> ACPAgentSettings:
    """Resolve the ACPAgentSettings to probe ``server`` with.

    Reuses the persisted agent settings when they already target ``server`` (so
    a user's custom command / configured key are honored); otherwise falls back
    to a fresh default for that provider.
    """
    persisted = settings.agent_settings
    if isinstance(persisted, ACPAgentSettings) and persisted.acp_server == server:
        return persisted
    # ``server`` is validated against ACP_PROVIDERS before this is called, so it
    # is always a concrete ACPServerKind (never the open-ended "custom").
    return ACPAgentSettings(acp_server=cast(ACPServerKind, server))


@acp_auth_router.get("/auth-status")
async def get_acp_auth_status(
    request: Request,
    server: Annotated[
        str,
        Query(title="ACP provider key to probe (e.g. 'claude-code', 'codex')."),
    ],
) -> ACPAuthStatusResponse:
    """Probe whether ``server``'s ACP CLI is already authenticated.

    Resolves the launch command + environment for ``server`` from the registry,
    the persisted agent settings, and any stored global secrets, then runs a
    prompt-free :meth:`ACPAgent.probe_auth`. Always returns 200 with a ``status``
    of ``authenticated`` / ``unauthenticated`` / ``unknown`` — the probe spinning
    up a subprocess that fails is reported as ``unknown``, not an HTTP error.
    """
    if server not in ACP_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Unknown ACP server {server!r}. Known providers: "
                f"{', '.join(sorted(ACP_PROVIDERS))}."
            ),
        )

    # Some providers (e.g. claude-agent-acp) accept ``session/new`` without
    # validating credentials — auth is enforced later, at prompt time — so the
    # handshake probe can't tell logged-in from logged-out and would
    # false-positive. Report ``unknown`` for those instead of guessing; the
    # canvas then shows the API-key fields rather than a misleading banner.
    if not ACP_PROVIDERS[server].supports_auth_status_probe:
        logger.info("ACP auth-status probe skipped (unsupported provider): %s", server)
        return ACPAuthStatusResponse(
            server=server,
            status="unknown",
            detail=(
                f"{server} does not report login state through the ACP handshake; "
                "its credentials can't be probed server-side."
            ),
        )

    config = get_config(request)
    settings = get_settings_store(config).load() or PersistedSettings()
    acp_settings = _resolve_acp_settings(server, settings)

    try:
        command = acp_settings.resolve_acp_command()
    except ValueError as e:
        # Shouldn't happen for a registry provider, but stay honest if it does.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        )

    # Build the probe environment from stored global secrets (the onboarding-
    # stored keys) overlaid with the provider-specific env derived from agent
    # settings, so the settings take precedence. Note: probe_auth lets every key
    # here override the agent-server's own process env, whereas a real run
    # applies registry/agent_context secrets fill-missing (process env wins) — so
    # for a key defined in *both* a stored secret and the process env, the probe
    # can authenticate with a different value than the conversation would.
    env: dict[str, str] = {}
    stored = get_secrets_store(config).load()
    if stored is not None:
        env.update(stored.get_env_vars())
    env.update(acp_settings.resolve_acp_env())

    client_host = request.client.host if request.client else "unknown"
    logger.info(
        "ACP auth-status probe requested",
        extra={"acp_server": server, "client_host": client_host},
    )

    # ``session/new`` keys persistence by cwd; a throwaway directory keeps the
    # probe from leaving session state in any real workspace.
    try:
        with tempfile.TemporaryDirectory(prefix="acp-auth-probe-") as cwd:
            # Forward the SDK's probe timeout explicitly (single source of
            # truth, env-overridable via ACP_AUTH_PROBE_TIMEOUT) so a slow/hung
            # CLI can't pin a FastAPI threadpool worker past that ceiling.
            result: ACPAuthProbeResult = await run_in_threadpool(
                ACPAgent.probe_auth,
                command,
                env=env,
                cwd=cwd,
                timeout=_ACP_AUTH_PROBE_TIMEOUT,
            )
    except Exception as e:
        # Any failure to even run the handshake (subprocess won't start, the
        # server hangs past the timeout, a protocol error other than
        # auth_required) is reported as 'unknown' so the canvas falls back to
        # the API-key fields rather than falsely claiming "not logged in".
        logger.warning(
            "ACP auth-status probe failed for %s: %s",
            server,
            e,
            exc_info=True,
        )
        # Scrub any secret/env value the error string might have echoed (e.g. a
        # provider rejecting a bad key by quoting it back) before truncating.
        detail = f"{type(e).__name__}: {e}"
        for value in env.values():
            if value and len(value) >= _MIN_SECRET_LEN:
                detail = detail.replace(value, "***")
        return ACPAuthStatusResponse(
            server=server,
            status="unknown",
            detail=detail[:_MAX_DETAIL_CHARS],
        )

    return ACPAuthStatusResponse(
        server=server,
        status="authenticated" if result.authenticated else "unauthenticated",
        auth_methods=result.auth_methods,
        agent_name=result.agent_name,
        agent_version=result.agent_version,
    )
