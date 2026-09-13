"""Public Agent Server clients for orchestration and already-provisioned runs.

Conversation/Workspace remain the high-level agent interfaces. These clients
cover control-plane and runtime operations needed by dispatchers without making
consumers own HTTP paths, authentication headers, or runtime scope selection.
Caller-provided HTTP clients retain ownership of their connection pools.
"""

from typing import Any

import httpx

from openhands.sdk.client import _requests as routes


JSON = dict[str, Any]


def _decode(response: httpx.Response, operation: routes.Operation) -> JSON:
    if response.status_code in operation.allowed_statuses:
        return {}
    response.raise_for_status()
    if not response.content:
        return {}
    result = response.json()
    if not isinstance(result, dict):
        raise ValueError("Expected an Agent Server JSON object")
    return result


def _credential(result: JSON) -> str:
    value = result.get("session_api_key")
    if not isinstance(value, str) or not value:
        raise ValueError("Runtime returned an empty session credential")
    return value


class AgentServerClient:
    def __init__(
        self, host: str, api_key: str, *, http_client: httpx.Client | None = None
    ):
        self.host = host.rstrip("/")
        self._api_key = api_key
        self._owns_client = http_client is None
        self._http = http_client

    def close(self) -> None:
        if self._owns_client and self._http is not None:
            self._http.close()

    def _send(self, operation: routes.Operation) -> JSON:
        if self._http is None:
            self._http = httpx.Client(timeout=90)
        response = self._http.request(
            operation.method,
            self.host + operation.path,
            headers={"X-Session-API-Key": self._api_key},
            **operation.options,
        )
        return _decode(response, operation)

    def get_server_info(self) -> JSON:
        return self._send(routes.Operation("GET", "/server_info"))

    def create_conversation(
        self,
        *,
        conversation_id: str,
        agent_profile_id: str,
        working_dir: str,
        title: str,
        max_iterations: int = 160,
        tags: dict[str, str] | None = None,
        plugins: list[dict[str, Any]] | None = None,
    ) -> JSON:
        return self._send(
            routes.create_conversation(
                conversation_id=conversation_id,
                agent_profile_id=agent_profile_id,
                working_dir=working_dir,
                title=title,
                max_iterations=max_iterations,
                tags=tags,
                plugins=plugins,
            )
        )

    def get_conversation(self, conversation_id: str) -> JSON:
        return self._send(
            routes.Operation("GET", routes.conversation_path(conversation_id))
        )

    def send_message(
        self, conversation_id: str, text: str, *, run: bool = True
    ) -> JSON:
        return self._send(routes.message(conversation_id, text, run))

    def interrupt(self, conversation_id: str) -> JSON:
        return self._send(
            routes.Operation(
                "POST",
                routes.conversation_path(conversation_id) + "/interrupt",
                {"json": {}},
            )
        )

    def get_errors(self, conversation_id: str, *, limit: int = 1) -> JSON:
        return self._send(routes.errors(conversation_id, limit))

    def runtime(self, conversation_id: str | None = None) -> "RuntimeClient":
        return RuntimeClient(self, routes.RuntimeRequests(conversation_id))

    def runtime_for_api_prefix(self, api_prefix: str) -> "RuntimeClient":
        return RuntimeClient(self, routes.RuntimeRequests.from_api_prefix(api_prefix))


class RuntimeClient:
    def __init__(self, server: AgentServerClient, requests: routes.RuntimeRequests):
        self._server = server
        self._requests = requests

    @property
    def api_prefix(self) -> str:
        return self._requests.api_prefix

    def upload(self, path: str, content: bytes, *, filename: str = "upload") -> JSON:
        return self._server._send(self._requests.upload(path, content, filename))

    def execute(
        self, command: str, *, timeout: int = 300, cwd: str | None = None
    ) -> JSON:
        return self._server._send(
            self._requests.bash(command, timeout, background=False, cwd=cwd)
        )

    def start(
        self, command: str, *, timeout: int = 300, cwd: str | None = None
    ) -> JSON:
        return self._server._send(
            self._requests.bash(command, timeout, background=True, cwd=cwd)
        )

    def get_output(self, command_id: str | None = None) -> JSON:
        return self._server._send(self._requests.output(command_id))

    def get_session_key(self) -> str:
        return _credential(
            self._server._send(self._requests.lifecycle(credentials=True))
        )

    def release(self) -> None:
        self._server._send(self._requests.lifecycle(credentials=False))


class AsyncAgentServerClient:
    def __init__(
        self, host: str, api_key: str, *, http_client: httpx.AsyncClient | None = None
    ):
        self.host = host.rstrip("/")
        self._api_key = api_key
        self._owns_client = http_client is None
        self._http = http_client

    async def aclose(self) -> None:
        if self._owns_client and self._http is not None:
            await self._http.aclose()

    async def _send(self, operation: routes.Operation) -> JSON:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=90)
        response = await self._http.request(
            operation.method,
            self.host + operation.path,
            headers={"X-Session-API-Key": self._api_key},
            **operation.options,
        )
        return _decode(response, operation)

    async def get_server_info(self) -> JSON:
        return await self._send(routes.Operation("GET", "/server_info"))

    async def create_conversation(
        self,
        *,
        conversation_id: str,
        agent_profile_id: str,
        working_dir: str,
        title: str,
        max_iterations: int = 160,
        tags: dict[str, str] | None = None,
        plugins: list[dict[str, Any]] | None = None,
    ) -> JSON:
        return await self._send(
            routes.create_conversation(
                conversation_id=conversation_id,
                agent_profile_id=agent_profile_id,
                working_dir=working_dir,
                title=title,
                max_iterations=max_iterations,
                tags=tags,
                plugins=plugins,
            )
        )

    async def get_conversation(self, conversation_id: str) -> JSON:
        return await self._send(
            routes.Operation("GET", routes.conversation_path(conversation_id))
        )

    async def send_message(
        self, conversation_id: str, text: str, *, run: bool = True
    ) -> JSON:
        return await self._send(routes.message(conversation_id, text, run))

    async def interrupt(self, conversation_id: str) -> JSON:
        return await self._send(
            routes.Operation(
                "POST",
                routes.conversation_path(conversation_id) + "/interrupt",
                {"json": {}},
            )
        )

    async def get_errors(self, conversation_id: str, *, limit: int = 1) -> JSON:
        return await self._send(routes.errors(conversation_id, limit))

    def runtime(self, conversation_id: str | None = None) -> "AsyncRuntimeClient":
        return AsyncRuntimeClient(self, routes.RuntimeRequests(conversation_id))

    def runtime_for_api_prefix(self, api_prefix: str) -> "AsyncRuntimeClient":
        return AsyncRuntimeClient(
            self, routes.RuntimeRequests.from_api_prefix(api_prefix)
        )


class AsyncRuntimeClient:
    def __init__(
        self, server: AsyncAgentServerClient, requests: routes.RuntimeRequests
    ):
        self._server = server
        self._requests = requests

    @property
    def api_prefix(self) -> str:
        return self._requests.api_prefix

    async def upload(
        self, path: str, content: bytes, *, filename: str = "upload"
    ) -> JSON:
        return await self._server._send(self._requests.upload(path, content, filename))

    async def execute(
        self, command: str, *, timeout: int = 300, cwd: str | None = None
    ) -> JSON:
        return await self._server._send(
            self._requests.bash(command, timeout, background=False, cwd=cwd)
        )

    async def start(
        self, command: str, *, timeout: int = 300, cwd: str | None = None
    ) -> JSON:
        return await self._server._send(
            self._requests.bash(command, timeout, background=True, cwd=cwd)
        )

    async def get_output(self, command_id: str | None = None) -> JSON:
        return await self._server._send(self._requests.output(command_id))

    async def get_session_key(self) -> str:
        return _credential(
            await self._server._send(self._requests.lifecycle(credentials=True))
        )

    async def release(self) -> None:
        await self._server._send(self._requests.lifecycle(credentials=False))
