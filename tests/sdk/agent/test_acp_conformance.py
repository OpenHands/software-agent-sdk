"""Live, credential-free conformance probes for the built-in ACP providers.

For each entry in ``ACP_PROVIDERS``, launches ``info.default_command``
verbatim (through ``npx``) with a bogus credential and an isolated data dir,
then asserts what the live server reports back rather than what the registry
assumes it reports. Every historical drift this catches (#3772, #3654,
#4629, #4812) was silent precisely because nothing read state back from the
server — see OpenHands/software-agent-sdk#4830 P0-1/P0-2.

No API key, subscription, or network credential is required: every built-in
provider's handshake (initialize / authenticate / new_session /
set_session_mode / model selection) succeeds with a syntactically-present but
invalid key, which is what makes this suite safe to run unauthenticated in
fork PRs. Needs ``npx`` (Node.js) and network access to the npm registry;
skipped when unavailable.

Deselected from the default run via the ``acp_live`` marker (``addopts = -m
'not stress and not acp_live'`` in pyproject.toml): the SDK's default test
job is a *required* merge check, and an npm-registry blip or upstream ACP
handshake hiccup — a real risk distinct from "registry unreachable," which
the skip above already covers — must not be able to block unrelated PRs. Run
explicitly with ``pytest -m acp_live``; CI runs it in the separate, non-
required ``acp-live-tests`` job, gated on changes to ACP registry/catalog
files (see ``.github/workflows/tests.yml``).
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from acp.client.connection import ClientSideConnection
from acp.exceptions import RequestError as ACPRequestError

from openhands.sdk.agent.acp_agent import (
    ACPAgent,
    _apply_acp_model,
    _select_auth_method,
)
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.settings.acp_install_catalog import ACP_INSTALL_CATALOG
from openhands.sdk.settings.acp_providers import ACP_PROVIDERS
from openhands.sdk.utils.async_executor import AsyncExecutor
from openhands.sdk.workspace.local import LocalWorkspace


def _npm_registry_reachable(timeout: float = 3.0) -> bool:
    try:
        socket.create_connection(("registry.npmjs.org", 443), timeout=timeout).close()
        return True
    except OSError:
        return False


requires_npx = pytest.mark.skipif(
    shutil.which("npx") is None or not _npm_registry_reachable(),
    reason="npx (Node.js) not available, or npm registry unreachable",
)

pytestmark = pytest.mark.acp_live

# Syntactically valid-looking but non-functional credentials. Enough for each
# provider's auth-method selection to pick an env-var-backed method and clear
# the handshake; real inference would reject them, but no turn is ever sent.
_BOGUS_KEY = "sk-acp-conformance-probe-0000000000000000000000000000"

# Per-call deadline for the model-acceptance probe's set_config_option /
# set_session_model round-trips — these are metadata writes, not inference
# calls, so they should return in well under a second even cold.
_MODEL_CALL_TIMEOUT = 15.0


def _node_version() -> tuple[int, ...] | None:
    """The running ``node``'s version as a comparable tuple, or None."""
    node = shutil.which("node")
    if node is None:
        return None
    try:
        raw = subprocess.run(
            [node, "--version"], capture_output=True, text=True, timeout=30
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        return tuple(int(part) for part in raw.lstrip("v").split(".")[:3])
    except ValueError:
        return None


def _skip_if_node_below_floor(provider_key: str) -> None:
    """Skip rather than fail when the host Node is below what this provider's
    packages declare.

    Not a soft-pedal: the CLI's own dependencies break in ways that surface as
    an unrelated-looking protocol error several calls later (pi's engine dies
    on `webidl.util.markAsUncloneable`, and the adapter then reports
    "Cannot call write after a stream was destroyed" from `session/new`), so a
    plain failure here would read as a conformance regression rather than an
    unmet prerequisite. The image's own floor is asserted separately, against
    the Dockerfile pin, in tests/cross/test_agent_server_build_metadata.py.
    """
    floor = ACP_INSTALL_CATALOG[provider_key].min_node_version
    if floor is None:
        return
    running = _node_version()
    required = tuple(int(part) for part in floor.split("."))
    if running is not None and running < required:
        pytest.skip(
            f"provider {provider_key!r} declares node >={floor}; this host runs "
            f"v{'.'.join(map(str, running))}"
        )


def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Give the probe its own HOME and bogus keys so it never touches host
    credentials (subscription logins, real API keys) or a real proxy."""
    isolated_home = tmp_path / "home"
    isolated_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(isolated_home))
    for var in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "GEMINI_API_KEY",
        # kimi-code's own KIMI_API_KEY is read only from inside
        # $KIMI_CODE_HOME/config.toml, never from the environment, so it
        # cannot clear the auth gate. KIMI_MODEL_NAME is the trigger for the
        # CLI's env-only provider synthesis, and KIMI_MODEL_API_KEY is the
        # key it pairs with — together they let session/new succeed with a
        # bogus credential, which is what this suite needs. Setting the name
        # without the key makes the CLI exit, so both must be set.
        "KIMI_MODEL_API_KEY",
    ):
        monkeypatch.setenv(var, _BOGUS_KEY)
    monkeypatch.setenv("KIMI_MODEL_NAME", "kimi-k2.7-code")
    for var in (
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "OPENAI_BASE_URL",
        "GEMINI_BASE_URL",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "CODEX_HOME",
        "CLAUDE_CONFIG_DIR",
        "KIMI_API_KEY",
        "KIMI_BASE_URL",
        "KIMI_CODE_HOME",
        "KIMI_MODEL_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)


def _spy(monkeypatch: pytest.MonkeyPatch, obj: Any, name: str) -> dict[str, Any]:
    """Wrap an async attribute of ``obj`` to record its args/return, while
    still calling through to the real implementation."""
    original = getattr(obj, name)
    captured: dict[str, Any] = {}

    async def _wrapped(*args, **kwargs):
        result = await original(*args, **kwargs)
        captured["args"] = args
        captured["kwargs"] = kwargs
        captured["result"] = result
        return result

    monkeypatch.setattr(obj, name, _wrapped)
    return captured


def _launch(agent: ACPAgent, tmp_path: Path) -> ConversationState:
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state = ConversationState.create(
        id=uuid.uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
    )
    agent._executor = AsyncExecutor()
    agent._start_acp_server(state)
    return state


@requires_npx
@pytest.mark.parametrize("provider_key", list(ACP_PROVIDERS))
def test_acp_conformance_probe(
    provider_key: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = ACP_PROVIDERS[provider_key]
    _skip_if_node_below_floor(provider_key)
    _isolate_env(monkeypatch, tmp_path)

    init_captured = _spy(monkeypatch, ClientSideConnection, "initialize")
    auth_method_calls: list[tuple[list[Any], str | None]] = []
    original_select = _select_auth_method

    def _spy_select_auth_method(auth_methods, env):
        picked = original_select(auth_methods, env)
        auth_method_calls.append((list(auth_methods), picked))
        return picked

    monkeypatch.setattr(
        "openhands.sdk.agent.acp_agent._select_auth_method", _spy_select_auth_method
    )

    agent = ACPAgent(
        acp_command=list(provider.default_command),
        acp_server=provider.key,
        acp_isolate_data_dir=True,
        acp_model=provider.default_model,
    )
    start = time.monotonic()
    try:
        _launch(agent, tmp_path)
        cold_start = time.monotonic() - start

        init_response = init_captured["result"]
        print(
            f"[acp-conformance] provider={provider.key} "
            f"cold_start={cold_start:.2f}s "
            f"protocol_version={init_response.protocol_version}"
        )
        assert 0 < init_response.protocol_version < 65536

        # agent_name matches agent_name_patterns.
        assert agent.agent_name, "server did not report an agent name"
        lowered_name = agent.agent_name.lower()
        assert any(pat in lowered_name for pat in provider.agent_name_patterns), (
            f"agent_name={agent.agent_name!r} does not match any pattern in "
            f"{provider.agent_name_patterns!r} (upstream renamed the agent?)"
        )

        # agent_version equals the pin.
        pinned_version = ACP_INSTALL_CATALOG[provider.key].packages[0].version
        assert agent.agent_version == pinned_version, (
            f"agent_version={agent.agent_version!r} != pinned {pinned_version!r} — "
            "npx resolved a different version than default_command pins"
        )

        # authMethods contains what _select_auth_method picked. Some
        # providers (claude-code, per #4830's baseline) require no auth
        # handshake at all, in which case _select_auth_method is never
        # called — only assert when the server actually offered methods.
        offered_ids = {m.id for m in init_response.auth_methods or []}
        if offered_ids:
            assert auth_method_calls, (
                f"server offered authMethods {sorted(offered_ids)!r} but "
                "_select_auth_method was never invoked"
            )
            _, picked_method_id = auth_method_calls[0]
            if picked_method_id is not None:
                assert picked_method_id in offered_ids, (
                    f"_select_auth_method picked {picked_method_id!r}, not "
                    f"present in the server's own authMethods "
                    f"{sorted(offered_ids)!r}"
                )

        # set_session_mode(default_session_mode) succeeded implicitly: a
        # rejection raises inside _start_acp_server and _launch would have
        # propagated before this line.

        # The session advertised *some* model-selection mechanism, matching
        # supports_set_session_model's premise that one exists.
        if provider.supports_set_session_model:
            assert agent._available_models is not None, (
                f"provider={provider.key} claims supports_set_session_model "
                "but the session response carried neither configOptions nor "
                "the models capability"
            )

        # The model reads back as requested.
        if provider.default_model:
            assert agent.current_model_id == provider.default_model, (
                f"requested default_model={provider.default_model!r} but the "
                f"live session reports current_model_id={agent.current_model_id!r} "
                "(the id may no longer be accepted — see the model-acceptance "
                "probe below)"
            )

        # Mid-session set_model works when supports_runtime_model_switch.
        if provider.supports_runtime_model_switch:
            candidates = [
                m.id
                for m in provider.available_models
                if m.id != agent.current_model_id
            ]
            if candidates:
                target = candidates[0]
                agent.set_acp_model(target)
                assert agent.current_model_id == target, (
                    f"set_acp_model({target!r}) reported success but "
                    f"current_model_id is {agent.current_model_id!r}"
                )

        # Model-acceptance probe (#4830 P0-2): every curated available_models
        # id and default_model must be *accepted* by the live protocol call,
        # not merely appear in our own curated list.
        session_id = agent._session_id
        conn = agent._conn
        assert session_id is not None
        assert conn is not None
        candidate_ids = list(
            dict.fromkeys(
                [m.id for m in provider.available_models]
                + ([provider.default_model] if provider.default_model else [])
            )
        )
        rejected: dict[str, str] = {}
        for model_id in candidate_ids:
            try:
                agent._executor.run_async(
                    _apply_acp_model(
                        conn,
                        session_id,
                        model_id,
                        agent_name=agent.agent_name,
                        via_config_option=agent._model_via_config_option,
                    ),
                    timeout=_MODEL_CALL_TIMEOUT,
                )
            except (ACPRequestError, ValueError) as exc:
                rejected[model_id] = str(exc)
        assert not rejected, (
            f"provider={provider.key} rejected curated model id(s) via the "
            f"live protocol call (dead catalog entries — fix _CODEX_MODELS / "
            f"_GEMINI_MODELS / _CLAUDE_MODELS or default_model in "
            f"acp_providers.py): {rejected}"
        )
    finally:
        agent.close()
