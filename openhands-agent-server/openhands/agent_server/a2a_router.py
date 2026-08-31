"""A2A (Agent2Agent) protocol server-mode router.

Exposes the agent-server as an A2A agent (Google's Agent2Agent protocol, now
the Linux Foundation ``a2a-spec``) over the JSON-RPC 2.0 transport, following
the same pattern the SDK already uses for ACP.

Protocol objects (``AgentCard``, ``Task``, ``TaskStatus``, ``TextPart``, ...)
come from the ``a2a-sdk`` package, which is an OPTIONAL dependency
(``pip install openhands-agent-server[a2a]``). When ``a2a-sdk`` is not
installed, importing this module raises (the ``a2a_types`` attribute access
below fails); ``api.py`` imports the router lazily inside a try/except so the
server keeps running without the A2A endpoints — they simply do not mount
(and a warning is logged). On top of that, mounting is gated behind
``Config.a2a_enabled`` (default False, CLI flag ``--a2a``).

Endpoints (mounted only when ``a2a_enabled`` is True AND a2a-sdk imports):

- ``GET /.well-known/agent-card.json`` — AgentCard discovery document
  (mounted at the app root: well-known URIs must live outside any API prefix).
- ``POST /api/a2a`` — JSON-RPC 2.0 endpoint with methods:
  ``message/send``, ``message/stream`` (SSE), ``tasks/get``, ``tasks/cancel``.

Mapping to agent-server concepts:

- A2A task == OpenHands conversation; ``taskId`` is the conversationId.
- New task → ``conversation_service.start_conversation`` (resolved from the
  active agent profile, or an explicit ``agentProfileId`` param) +
  ``event_service.send_message(Message(role="user", ...), run=True)``.
- ``tasks/get`` → conversation execution status + ``get_agent_final_response``
  as a text artifact.
- ``tasks/cancel`` → ``conversation_service.interrupt_conversation``.

Auth accepts either the agent-server's usual ``X-Session-API-Key`` header or
the A2A-conventional ``Authorization: Bearer ***`` header, both validated
against ``config.session_api_keys``.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError, model_validator

from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.event_service import EventService
from openhands.agent_server.init_router import require_initialized
from openhands.agent_server.models import StartConversationRequest
from openhands.agent_server.pub_sub import Subscriber
from openhands.agent_server.utils import utc_now
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent
from openhands.sdk.llm.message import Message as SDKMessage
from openhands.sdk.llm.message import TextContent
from openhands.sdk.workspace import LocalWorkspace


if TYPE_CHECKING:
    # Statically (and in dev/CI environments) a2a-sdk is present, so pyright
    # gets full types. At RUNTIME the else-branch import is guarded: when
    # a2a-sdk is missing the module attribute access below raises and api.py
    # skips mounting the A2A routers instead of crashing the server.
    from a2a import types as a2a_types
else:
    a2a_sdk_import_error: Exception | None = None
    try:
        from a2a import types as a2a_types
    except Exception as exc:  # a2a-sdk missing or broken
        a2a_sdk_import_error = exc
        a2a_types = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

A2A_MEDIA_TYPE = "text/event-stream"

# Re-exported a2a-sdk models used below (aliasing also fails fast at import
# time when a2a-sdk is missing, which api.py converts into "don't mount").
AgentCard = a2a_types.AgentCard
AgentCapabilities = a2a_types.AgentCapabilities
AgentProvider = a2a_types.AgentProvider
AgentSkill = a2a_types.AgentSkill
Artifact = a2a_types.Artifact
Part = a2a_types.Part
Task = a2a_types.Task
TaskArtifactUpdateEvent = a2a_types.TaskArtifactUpdateEvent
TaskState = a2a_types.TaskState
TaskStatus = a2a_types.TaskStatus
TaskStatusUpdateEvent = a2a_types.TaskStatusUpdateEvent
TextPart = a2a_types.TextPart

# JSON-RPC 2.0 error codes (plus the A2A-style task-not-found extension).
JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603
JSONRPC_TASK_NOT_FOUND = -32001

# ConversationExecutionStatus values that end an A2A task. IDLE means the
# conversation finished its run and is ready for new input; FINISHED is the
# explicit terminal state. ERROR / STUCK / DELETING are terminal failures.
_TERMINAL_EXECUTION_STATUSES = frozenset(
    {"idle", "finished", "error", "stuck", "deleting"}
)

# ConversationExecutionStatus → a2a TaskState value.
_EXECUTION_STATUS_TO_TASK_STATE: dict[str, str] = {
    "idle": "completed",
    "finished": "completed",
    "running": "working",
    "paused": "input-required",
    "waiting_for_confirmation": "input-required",
    "error": "failed",
    "stuck": "failed",
    "deleting": "failed",
}


class MessageSendParams(a2a_types.MessageSendParams):
    """Params for ``message/send`` / ``message/stream``.

    ``agentProfileId`` is an OpenHands extension: selects the agent profile to
    launch the conversation from. When omitted, the server's active agent
    profile (or the first stored profile) is used.

    ``message.messageId`` is required by the a2a-sdk model but is OPTIONAL on
    the wire here (spec rev 0.3): a UUID is filled in when the client omits
    it, so hand-rolled clients are not rejected with invalid-params.
    """

    agentProfileId: str | None = None  # noqa: N815 - A2A field name

    @model_validator(mode="before")
    @classmethod
    def _default_message_id(cls, data: Any) -> Any:
        message = data.get("message") if isinstance(data, dict) else None
        if isinstance(message, dict) and not message.get("messageId"):
            message = {**message, "messageId": str(uuid.uuid4())}
            return {**data, "message": message}
        return data


TaskGetParams = a2a_types.TaskQueryParams
TaskCancelParams = a2a_types.TaskQueryParams


class JSONRPCErrorObject(BaseModel):
    """JSON-RPC 2.0 error object."""

    code: int
    message: str
    data: str | None = None


class JSONRPCResponse(BaseModel):
    """JSON-RPC 2.0 response envelope for the A2A endpoint."""

    jsonrpc: str = "2.0"
    id: str | int | None = None
    result: Any | None = None
    error: JSONRPCErrorObject | None = None


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


async def check_a2a_session_api_key(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    """A2A auth: accept either ``X-Session-API-Key`` or ``Authorization: Bearer``.

    A2A clients conventionally send ``Authorization: Bearer ***`` while the
    agent-server's own clients send ``X-Session-API-Key``. Both are validated
    against ``config.session_api_keys`` (empty list disables auth, same as the
    built-in dependency).
    """
    config: Config | None = getattr(request.app.state, "config", None)
    if config is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server not fully initialized",
        )
    if not config.session_api_keys:
        return
    header_key = request.headers.get("X-Session-API-Key")
    bearer_key = None
    if authorization and authorization.startswith("Bearer "):
        bearer_key = authorization[len("Bearer ") :].strip()
    for candidate in (header_key, bearer_key):
        if candidate and candidate in config.session_api_keys:
            return
    raise HTTPException(status.HTTP_401_UNAUTHORIZED)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _server_version() -> str:
    try:
        return version("openhands-agent-server")
    except PackageNotFoundError:
        return "dev"


def _agent_card_skills() -> list[AgentSkill]:
    """List stored agent profiles as A2A skills (best-effort, never raises)."""
    try:
        from openhands.agent_server.persistence import get_agent_profile_store

        skills: list[AgentSkill] = []
        for summary in get_agent_profile_store().list_summaries():
            name = str(summary.get("name", "profile"))
            skill_id = str(summary.get("id") or name)
            skills.append(
                AgentSkill(
                    id=skill_id,
                    name=name,
                    description=f"OpenHands agent profile '{name}'",
                    tags=[str(summary.get("agent_kind", "openhands"))],
                )
            )
        return skills
    except Exception:
        logger.debug("Could not list agent profiles for the A2A card", exc_info=True)
        return []


def _resolve_agent_profile_id() -> str | None:
    """Resolve the agent profile a new A2A conversation should launch from.

    Prefers the active profile pointer from persisted settings, then falls
    back to the first stored profile. Best-effort: returns None when no
    profile is available.
    """
    try:
        from openhands.agent_server.persistence import (
            get_agent_profile_store,
            get_settings_store,
        )

        settings = get_settings_store().load()
        if settings is not None and settings.active_agent_profile_id is not None:
            return str(settings.active_agent_profile_id)
        for summary in get_agent_profile_store().list_summaries():
            sid = summary.get("id")
            if sid is not None:
                return str(sid)
    except Exception:
        logger.debug("Could not resolve an agent profile for A2A", exc_info=True)
    return None


def _parse_task_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise ValueError(f"Invalid taskId (not a conversationId): {raw}") from None


def _task_state_for_execution_status(execution_status: Any) -> str:
    raw = str(getattr(execution_status, "value", execution_status) or "")
    return _EXECUTION_STATUS_TO_TASK_STATE.get(raw, "unknown")


def _task_status(state_value: str) -> TaskStatus:
    return TaskStatus(
        state=state_value,  # type: ignore[arg-type]  # str coerces to TaskState
        timestamp=utc_now().isoformat(),
    )


async def _get_event_service_for(
    conversation_service: ConversationService, task_id: uuid.UUID
) -> EventService | None:
    return await conversation_service.get_event_service(task_id)


async def _task_from_event_service(
    task_id: uuid.UUID, event_service: EventService
) -> Task:
    """Build an A2A Task snapshot from a conversation's event service."""
    execution_status = None
    final_response = ""
    try:
        state = await event_service.get_state()
        execution_status = getattr(state, "execution_status", None)
    except Exception:
        logger.debug("A2A: could not read conversation state", exc_info=True)
    try:
        final_response = await event_service.get_agent_final_response()
    except Exception:
        logger.debug("A2A: could not read agent final response", exc_info=True)

    artifacts: list[Artifact] = []
    if final_response:
        parts: list[Part] = [Part(root=TextPart(text=final_response))]
        artifacts.append(
            Artifact(
                artifact_id=str(uuid.uuid4()),
                name="response",
                parts=parts,
            )
        )
    return Task(
        id=str(task_id),
        context_id=str(task_id),
        status=_task_status(_task_state_for_execution_status(execution_status)),
        artifacts=artifacts,
    )


def _jsonrpc(result: Any, rpc_id: str | int | None) -> JSONRPCResponse:
    return JSONRPCResponse(jsonrpc="2.0", id=rpc_id, result=result)


def _jsonrpc_error(
    code: int, message: str, rpc_id: str | int | None = None, data: str | None = None
) -> JSONRPCResponse:
    return JSONRPCResponse(
        jsonrpc="2.0",
        id=rpc_id,
        error=JSONRPCErrorObject(code=code, message=message, data=data),
    )


def _unwrap_part(part: Any) -> Any:
    """Return the concrete part inside a ``Part`` root model."""
    return getattr(part, "root", part)


def _user_text(message: a2a_types.Message) -> str:
    return "".join(
        inner.text
        for inner in map(_unwrap_part, message.parts)
        if isinstance(inner, TextPart)
    )


class _SendFailed:
    """Sentinel enqueued when the background user-message send raises."""


class _QueueSubscriber(Subscriber):
    """Bridges agent-server events into an asyncio queue for the SSE stream.

    ``subscribe_to_events`` immediately replays the conversation's CURRENT
    execution status to the new subscriber. When that status is terminal
    (e.g. IDLE before a run has started), the replayed snapshot must NOT
    close the stream — so the FIRST terminal state snapshot is swallowed
    (exactly once, and only if it is the first state event we see).
    """

    def __init__(self, queue: asyncio.Queue):
        self._queue = queue
        self._seen_state_event = False

    async def __call__(self, event: Any):
        if (
            isinstance(event, ConversationStateUpdateEvent)
            and not self._seen_state_event
        ):
            self._seen_state_event = True
            value = str(getattr(event, "value", "") or "")
            if value in _TERMINAL_EXECUTION_STATUSES:
                logger.debug(
                    "A2A: dropping pre-run terminal state snapshot (%s)", value
                )
                return
        await self._queue.put(event)


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

# Mounted at the app ROOT (outside /api): well-known URIs must be discoverable
# at /.well-known/ regardless of API prefixes.
a2a_agent_card_router = APIRouter(tags=["A2A"])

# Mounted under api_router (prefix /api) → served at /api/a2a. Uses its own
# auth dependency (Bearer OR X-Session-API-Key) plus the standard dormant
# gate; this deliberately does not go through the shared api_router
# dependency group, which only honors the X-Session-API-Key header.
a2a_router = APIRouter(
    prefix="/a2a",
    tags=["A2A"],
    dependencies=[
        Depends(check_a2a_session_api_key),
        Depends(require_initialized),
    ],
)


@a2a_agent_card_router.get(
    "/.well-known/agent-card.json",
    response_model=AgentCard,
    response_model_exclude_none=True,
)
async def get_agent_card(request: Request) -> AgentCard:
    """Return the A2A AgentCard discovery document."""
    base_url = str(request.base_url).rstrip("/")
    input_modes: list[str] = ["text/plain"]
    return AgentCard(
        name="OpenHands Agent Server",
        description=(
            "OpenHands software-agent SDK agent server, exposed as an A2A "
            "agent. Each A2A task maps to one OpenHands conversation running "
            "the server's configured agent profile."
        ),
        url=f"{base_url}/api/a2a",
        version=_server_version(),
        capabilities=AgentCapabilities(streaming=True, push_notifications=False),
        default_input_modes=input_modes,
        default_output_modes=["text/plain"],
        skills=_agent_card_skills(),
        provider=AgentProvider(
            organization="OpenHands",
            url="https://github.com/OpenHands/software-agent-sdk",
        ),
    )


@a2a_router.post("", response_model=JSONRPCResponse, response_model_exclude_none=True)
async def a2a_jsonrpc_endpoint(
    request: Request,
    conversation_service: ConversationService = Depends(get_conversation_service),
) -> Any:
    """JSON-RPC 2.0 endpoint for A2A methods.

    Supported methods: ``message/send``, ``message/stream`` (returns an SSE
    stream rather than a JSON envelope), ``tasks/get``, ``tasks/cancel``.
    JSON-RPC-level errors are returned as 200 responses carrying a JSON-RPC
    error object (``-32601`` method not found, ``-32700`` parse error,
    ``-32602`` invalid params). Every error response echoes the request
    ``id`` (null for parse errors, per the JSON-RPC 2.0 spec).
    """
    raw = await request.body()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        # Raw JSONResponse (not the response_model) so that "id": null is
        # serialized explicitly — exclude_none on JSONRPCResponse would drop
        # the key, and JSON-RPC 2.0 requires a null id on parse errors.
        return JSONResponse(
            status_code=200,
            content={
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": JSONRPC_PARSE_ERROR, "message": "Parse error"},
            },
        )

    rpc_id = payload.get("id") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
        return _jsonrpc_error(JSONRPC_INVALID_REQUEST, "Invalid Request", rpc_id)
    method = payload.get("method")
    params = payload.get("params") or {}
    config: Config = request.app.state.config

    if not isinstance(method, str):
        return _jsonrpc_error(
            JSONRPC_INVALID_REQUEST, "Invalid Request: missing method", rpc_id
        )

    if method in ("message/send", "message/stream"):
        try:
            send_params = MessageSendParams.model_validate(params)
        except ValidationError as exc:
            return _jsonrpc_error(
                JSONRPC_INVALID_PARAMS, "Invalid params", rpc_id, data=str(exc)
            )
        if method == "message/send":
            handle_result = await _handle_message_send(
                send_params, conversation_service, config, rpc_id
            )
            if isinstance(handle_result, JSONRPCResponse):
                return handle_result
            return _jsonrpc(handle_result, rpc_id)
        return await _handle_message_stream(
            send_params, conversation_service, config, rpc_id
        )

    if method == "tasks/get":
        try:
            get_params = TaskGetParams.model_validate(params)
            task_id = _parse_task_id(get_params.id)
        except (ValidationError, ValueError) as exc:
            return _jsonrpc_error(
                JSONRPC_INVALID_PARAMS, "Invalid params", rpc_id, data=str(exc)
            )
        event_service = await _get_event_service_for(conversation_service, task_id)
        if event_service is None:
            return _jsonrpc_error(
                JSONRPC_TASK_NOT_FOUND, f"Task not found: {get_params.id}", rpc_id
            )
        return _jsonrpc(await _task_from_event_service(task_id, event_service), rpc_id)

    if method == "tasks/cancel":
        try:
            cancel_params = TaskCancelParams.model_validate(params)
            task_id = _parse_task_id(cancel_params.id)
        except (ValidationError, ValueError) as exc:
            return _jsonrpc_error(
                JSONRPC_INVALID_PARAMS, "Invalid params", rpc_id, data=str(exc)
            )
        try:
            cancelled = await conversation_service.interrupt_conversation(task_id)
        except ValueError:
            cancelled = False
        if not cancelled:
            return _jsonrpc_error(
                JSONRPC_TASK_NOT_FOUND,
                f"Task not found: {cancel_params.id}",
                rpc_id,
            )
        return _jsonrpc(
            Task(
                id=cancel_params.id,
                context_id=cancel_params.id,
                status=_task_status("canceled"),
            ),
            rpc_id,
        )

    return _jsonrpc_error(
        JSONRPC_METHOD_NOT_FOUND, f"Method not found: {method}", rpc_id
    )


async def _start_or_get_conversation(
    send_params: MessageSendParams,
    conversation_service: ConversationService,
    config: Config,
    rpc_id: str | int | None,
) -> tuple[uuid.UUID, EventService, bool] | JSONRPCResponse:
    """Return ``(task_id, event_service, created)`` or a JSON-RPC error.

    Reuses the conversation named by ``message.taskId`` when present,
    otherwise starts a new one from the resolved agent profile. Every error
    response echoes *rpc_id* so clients can correlate the failure.
    """
    if send_params.message.task_id:
        try:
            task_id = _parse_task_id(send_params.message.task_id)
        except ValueError as exc:
            return _jsonrpc_error(JSONRPC_INVALID_PARAMS, str(exc), rpc_id)
        event_service = await _get_event_service_for(conversation_service, task_id)
        if event_service is None:
            return _jsonrpc_error(
                JSONRPC_TASK_NOT_FOUND,
                f"Task not found: {send_params.message.task_id}",
                rpc_id,
            )
        return task_id, event_service, False

    profile_id = send_params.agentProfileId or _resolve_agent_profile_id()
    if profile_id is None:
        return _jsonrpc_error(
            JSONRPC_INTERNAL_ERROR,
            "No agent profile configured; create one via /api/agent-profiles "
            "or pass agentProfileId in the params",
            rpc_id,
        )
    try:
        start_request = StartConversationRequest(
            agent_profile_id=uuid.UUID(profile_id),
            workspace=LocalWorkspace(working_dir=str(config.workspace_path)),
        )
        info, _created = await conversation_service.start_conversation(start_request)
    except ValueError as exc:
        return _jsonrpc_error(
            JSONRPC_INVALID_PARAMS, f"Could not start conversation: {exc}", rpc_id
        )
    event_service = await _get_event_service_for(conversation_service, info.id)
    if event_service is None:
        return _jsonrpc_error(
            JSONRPC_INTERNAL_ERROR, f"Conversation {info.id} is not available", rpc_id
        )
    return info.id, event_service, True


async def _send_user_message(
    send_params: MessageSendParams, event_service: EventService
) -> None:
    text = _user_text(send_params.message)
    message = SDKMessage(role="user", content=[TextContent(text=text)])
    await event_service.send_message(message, run=True)


async def _handle_message_send(
    send_params: MessageSendParams,
    conversation_service: ConversationService,
    config: Config,
    rpc_id: str | int | None,
) -> Task | JSONRPCResponse:
    started = await _start_or_get_conversation(
        send_params, conversation_service, config, rpc_id
    )
    if isinstance(started, JSONRPCResponse):
        return started
    task_id, event_service, _created = started
    await _send_user_message(send_params, event_service)
    return await _task_from_event_service(task_id, event_service)


def _sse_chunk(payload: JSONRPCResponse) -> str:
    return (
        "data: "
        + json.dumps(
            payload.model_dump(exclude_none=True, mode="json"), separators=(",", ":")
        )
        + "\r\n\r\n"
    )


def _state_update_to_status_update(
    task_id: uuid.UUID, event: ConversationStateUpdateEvent, final: bool
) -> TaskStatusUpdateEvent | None:
    if getattr(event, "key", None) != "execution_status":
        return None
    return TaskStatusUpdateEvent(
        task_id=str(task_id),
        context_id=str(task_id),
        status=_task_status(_task_state_for_execution_status(event.value)),
        final=final,
    )


def _artifact_update(task_id: uuid.UUID, text: str, last_chunk: bool) -> Any:
    parts: list[Part] = [Part(root=TextPart(text=text))]
    return TaskArtifactUpdateEvent(
        task_id=str(task_id),
        context_id=str(task_id),
        artifact=Artifact(artifact_id=str(uuid.uuid4()), parts=parts),
        last_chunk=last_chunk,
    )


async def _handle_message_stream(
    send_params: MessageSendParams,
    conversation_service: ConversationService,
    config: Config,
    rpc_id: str | int | None,
) -> Any:
    """``message/stream``: run the task and stream A2A updates over SSE."""
    started = await _start_or_get_conversation(
        send_params, conversation_service, config, rpc_id
    )
    if isinstance(started, JSONRPCResponse):
        return started
    task_id, event_service, _created = started

    async def event_stream() -> AsyncIterator[str]:
        queue: asyncio.Queue = asyncio.Queue()
        subscriber = _QueueSubscriber(queue)
        # Send the user message FIRST, then subscribe. Subscribing before
        # sending races with subscribe_to_events' immediate replay of the
        # current (pre-run, terminal) status, which used to close the stream
        # before the run even started. The subscriber additionally swallows
        # a replayed terminal snapshot defensively (see _QueueSubscriber).
        send_task = asyncio.create_task(_send_user_message(send_params, event_service))

        def _on_send_done(task: asyncio.Task) -> None:
            if not task.cancelled() and task.exception() is not None:
                queue.put_nowait(_SendFailed())

        send_task.add_done_callback(_on_send_done)
        # Let the send task take its first step before subscribing, so the
        # user message is genuinely in flight (and mock-based tests observe
        # send_message) when the subscriber's status replay happens.
        await asyncio.sleep(0)

        subscriber_id = await event_service.subscribe_to_events(subscriber)
        try:
            # Initial task snapshot; subsequent state updates arrive via the
            # subscription as TaskStatusUpdateEvents.
            yield _sse_chunk(
                _jsonrpc(
                    Task(
                        id=str(task_id),
                        context_id=str(task_id),
                        status=_task_status("submitted"),
                    ),
                    rpc_id,
                )
            )

            while True:
                event = await queue.get()
                if isinstance(event, _SendFailed):
                    yield _sse_chunk(
                        _jsonrpc_error(
                            JSONRPC_INTERNAL_ERROR,
                            "Failed to send user message",
                            rpc_id,
                        )
                    )
                    return
                if isinstance(event, ConversationStateUpdateEvent):
                    status_value = str(getattr(event, "value", "") or "")
                    is_terminal = status_value in _TERMINAL_EXECUTION_STATUSES
                    update = _state_update_to_status_update(
                        task_id, event, final=is_terminal
                    )
                    if update is not None:
                        yield _sse_chunk(_jsonrpc(update, rpc_id))
                    if is_terminal:
                        task = await _task_from_event_service(task_id, event_service)
                        for artifact in task.artifacts or []:
                            yield _sse_chunk(
                                _jsonrpc(
                                    _artifact_update(
                                        task_id,
                                        "".join(
                                            inner.text
                                            for inner in map(
                                                _unwrap_part, artifact.parts
                                            )
                                            if isinstance(inner, TextPart)
                                        ),
                                        last_chunk=True,
                                    ),
                                    rpc_id,
                                )
                            )
                        yield _sse_chunk(_jsonrpc(task, rpc_id))
                        return
                else:
                    # Surface agent message events as incremental artifacts.
                    llm_message = getattr(event, "llm_message", None)
                    if llm_message is not None and getattr(event, "source", None) == (
                        "agent"
                    ):
                        text = "".join(
                            getattr(part, "text", "")
                            for part in getattr(llm_message, "content", [])
                        )
                        if text:
                            yield _sse_chunk(
                                _jsonrpc(
                                    _artifact_update(
                                        task_id, text, last_chunk=False
                                    ),
                                    rpc_id,
                                )
                            )
        finally:
            send_task.cancel()
            await event_service.unsubscribe_from_events(subscriber_id)

    return StreamingResponse(event_stream(), media_type=A2A_MEDIA_TYPE)
