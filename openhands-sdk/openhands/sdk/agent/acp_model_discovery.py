"""Ask an ACP server which models it offers, without starting a conversation."""

from __future__ import annotations

import shutil
import tempfile
import uuid
from collections.abc import Mapping
from pathlib import Path

from openhands.sdk.agent.acp_agent import (
    ACPAgent,
    _acp_error_detail,
    _classify_acp_init_error,
)
from openhands.sdk.agent.acp_models import (
    ACPEffortOption,
    ACPModelDiscovery,
    ACPModelDiscoveryError,
)
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.credential import VersionedCredentialBinding
from openhands.sdk.event import Event
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.logger import get_logger
from openhands.sdk.secret import SecretValue
from openhands.sdk.settings.model import ACPAgentSettings
from openhands.sdk.workspace.local import LocalWorkspace


logger = get_logger(__name__)

_PROBE_DIR_PREFIX = ".acp-model-discovery-"


def _efforts_by_model(agent: ACPAgent) -> dict[str, list[ACPEffortOption]]:
    """Read each model's effort levels by selecting it in the probe session."""
    if not agent.reports_effort:
        return {}
    efforts: dict[str, list[ACPEffortOption]] = {}
    if agent.current_model_id:
        efforts[agent.current_model_id] = agent.available_efforts
    for model in agent.available_models:
        if model.model_id in efforts:
            continue
        try:
            agent.set_acp_model(model.model_id)
        except Exception as exc:
            logger.debug("Could not probe effort for model %r: %s", model.model_id, exc)
            continue
        efforts[model.model_id] = agent.available_efforts
    return efforts


def discover_acp_models(
    settings: ACPAgentSettings,
    *,
    secrets: Mapping[str, SecretValue] | None = None,
    credential_bindings: Mapping[str, VersionedCredentialBinding] | None = None,
    work_root: str | Path | None = None,
) -> ACPModelDiscovery:
    """Start the server in a throwaway session and report what it offers."""
    agent = ACPAgent(
        acp_command=settings.resolve_acp_command(),
        acp_server=settings.acp_server,
        acp_args=list(settings.acp_args),
        acp_session_mode=settings.acp_session_mode,
        acp_startup_timeout=settings.acp_startup_timeout,
        acp_isolate_data_dir=settings.acp_isolate_data_dir,
        acp_file_secrets=list(settings.acp_file_secrets),
    )
    probe_dir = Path(tempfile.mkdtemp(prefix=_PROBE_DIR_PREFIX, dir=work_root))
    state: ConversationState | None = None
    errors: list[ConversationErrorEvent] = []

    def on_event(event: Event) -> None:
        if isinstance(event, ConversationErrorEvent):
            errors.append(event)

    try:
        for name, binding in (credential_bindings or {}).items():
            agent.activate_file_credential_binding(name, binding)
        workspace = probe_dir / "workspace"
        workspace.mkdir()
        state = ConversationState.create(
            id=uuid.uuid4(),
            agent=agent,
            workspace=LocalWorkspace(working_dir=str(workspace)),
            persistence_dir=str(probe_dir),
        )
        state.secret_registry.update_secrets(dict(secrets or {}))
        with state:
            agent.init_state(state, on_event=on_event)
            current_model_id = agent.current_model_id
            current_effort = agent.current_effort
            available_efforts = agent.available_efforts
            efforts_by_model = _efforts_by_model(agent)
        return ACPModelDiscovery(
            agent_name=agent.agent_name or None,
            agent_version=agent.agent_version or None,
            current_model_id=current_model_id,
            available_models=agent.available_models,
            current_effort=current_effort,
            available_efforts=available_efforts,
            efforts_by_model=efforts_by_model,
            supports_runtime_model_switch=agent.supports_runtime_model_switch,
        )
    except Exception as exc:
        if errors:
            error = ACPModelDiscoveryError(code=errors[0].code, detail=errors[0].detail)
        else:
            registry = state.secret_registry if state is not None else None
            error = ACPModelDiscoveryError(
                code=_classify_acp_init_error(exc),
                detail=_acp_error_detail(exc, registry),
            )
        logger.info(
            "ACP model discovery failed: server=%s code=%s",
            settings.acp_server,
            error.code,
        )
        return ACPModelDiscovery(error=error)
    finally:
        try:
            agent.close()
        except Exception:
            logger.warning(
                "Failed to stop the ACP model-discovery server", exc_info=True
            )
        shutil.rmtree(probe_dir, ignore_errors=True)
