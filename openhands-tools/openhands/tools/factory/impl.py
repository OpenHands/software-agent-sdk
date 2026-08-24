"""Factory spawn tool executor — creates the child conversation on the agent-server."""

from __future__ import annotations

import os
import uuid

import httpx

from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.logger import get_logger
from openhands.sdk.tool.tool import ToolExecutor
from openhands.tools.factory.definition import (
    FactorySpawnAction,
    FactorySpawnObservation,
)

logger = get_logger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:18000"
SESSION_KEY_ENV_VARS = ("OH_SESSION_API_KEYS_0", "SESSION_API_KEY")


def _server_config() -> tuple[str, str | None]:
    base_url = os.environ.get("AGENT_SERVER_URL") or DEFAULT_BASE_URL
    api_key = None
    for var in SESSION_KEY_ENV_VARS:
        if os.environ.get(var):
            api_key = os.environ[var]
            break
    return base_url.rstrip("/"), api_key


class FactorySpawnExecutor(ToolExecutor):
    """Executes a factory spawn by POSTing a child conversation to the agent-server."""

    def __call__(
        self,
        action: FactorySpawnAction,
        conversation: LocalConversation | None = None,
    ) -> FactorySpawnObservation:
        try:
            return self._spawn(action, conversation)
        except Exception as e:  # noqa: BLE001 — surface any failure to the agent
            logger.error(f"factory_spawn failed: {e}", exc_info=True)
            return FactorySpawnObservation.from_text(
                text=f"Failed to spawn factory child: {e}",
                conversation_id="unknown",
                workspace="",
                run_id="",
                is_error=True,
            )

    def _spawn(
        self,
        action: FactorySpawnAction,
        conversation: LocalConversation | None,
    ) -> FactorySpawnObservation:
        parent_id = str(conversation.id) if conversation is not None else None
        current_workspace = (
            str(conversation.state.workspace.working_dir)
            if conversation is not None
            else None
        )
        target_workspace = action.target_workspace or current_workspace
        if not target_workspace:
            raise ValueError("no workspace available on this conversation")

        base_url, api_key = _server_config()
        headers = {}
        if api_key:
            headers["X-Session-API-Key"] = api_key

        with httpx.Client(base_url=base_url, headers=headers, timeout=30.0) as client:
            parent_run_id = self._parent_run_id(client, parent_id)
            linked = bool(parent_id) and target_workspace == current_workspace
            run_id = parent_run_id or f"tree-{uuid.uuid4().hex[:8]}"
            workstream_id = (
                action.workstream_label or f"child-{uuid.uuid4().hex[:6]}"
            )

            profile_id = self._active_agent_profile_id(client)
            if not profile_id:
                raise RuntimeError(
                    "no active agent profile; configure one on the agent-server "
                    "before using factory_spawn"
                )

            payload: dict = {
                "workspace": {"working_dir": target_workspace},
                "worktree": False,
                "confirmation_policy": {"kind": "NeverConfirm"},
                "agent_profile_id": profile_id,
                "max_iterations": 500,
                "stuck_detection": True,
                "autotitle": True,
                "tags": {
                    "factory": "1",
                    "runid": run_id,
                    "workstreamid": workstream_id,
                },
                "observability_span_name": "factory_workstream",
                "observability_metadata": {
                    "factory.run_id": run_id,
                    "factory.workstream_id": workstream_id,
                },
                "initial_message": {
                    "role": "user",
                    "content": [{"type": "text", "text": action.prompt}],
                    "run": True,
                },
            }
            if linked:
                payload["parent_conversation_id"] = parent_id

            data = self._post(client, "/api/conversations", payload)
            child_id = data.get("conversation_id") or data.get("id")
            if not child_id:
                raise RuntimeError(f"no conversation id in response: {data}")

            logger.info(
                "factory_spawn -> child %s (parent %s, run %s, workspace %s)",
                child_id,
                parent_id if linked else None,
                run_id,
                target_workspace,
            )
            return FactorySpawnObservation.from_text(
                text="Factory child spawned.",
                conversation_id=str(child_id),
                workspace=target_workspace,
                parent_conversation_id=str(parent_id) if linked else None,
                run_id=run_id,
            )

    def _post(self, client: httpx.Client, path: str, payload: dict) -> dict:
        resp = client.post(path, json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"{path} -> {resp.status_code}: {resp.text[:300]}")
        if not resp.text:
            return {}
        return resp.json()

    def _get(self, client: httpx.Client, path: str) -> dict:
        resp = client.get(path)
        if resp.status_code >= 400:
            raise RuntimeError(f"{path} -> {resp.status_code}: {resp.text[:300]}")
        if not resp.text:
            return {}
        return resp.json()

    def _parent_run_id(self, client: httpx.Client, parent_id: str | None) -> str | None:
        if not parent_id:
            return None
        try:
            conv = self._get(client, f"/api/conversations/{parent_id}")
            return (conv.get("tags") or {}).get("runid")
        except Exception:  # noqa: BLE001 — inheritance is best-effort
            logger.warning("could not read parent run id for %s", parent_id)
            return None

    def _active_agent_profile_id(self, client: httpx.Client) -> str | None:
        try:
            data = self._get(client, "/api/agent-profiles")
        except Exception:  # noqa: BLE001
            return None
        pid = (data or {}).get("active_agent_profile_id")
        return str(pid) if pid else None
