import asyncio
import os
from collections.abc import Awaitable, Callable, Coroutine, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from openhands.sdk.logger import get_logger
from openhands.sdk.observability.trajectory_journal import (
    Journal,
    Origin,
    boundary_evidence,
    encode,
)


logger = get_logger(__name__)
_MAX_STREAM_BYTES = 256 * 1024
_REQUEST_FIELDS = frozenset(
    {
        "model",
        "instructions",
        "tools",
        "tool_choice",
        "max_tokens",
        "max_completion_tokens",
        "max_output_tokens",
        "temperature",
        "top_p",
        "stream",
        "stream_options",
        "parallel_tool_calls",
        "reasoning",
        "reasoning_effort",
        "response_format",
        "include",
        "store",
        "truncation",
        "previous_response_id",
        "text",
        "stop",
        "seed",
        "thinking",
        "top_k",
        "frequency_penalty",
        "presence_penalty",
        "n",
        "logprobs",
        "top_logprobs",
        "prompt_cache_retention",
        "prompt_cache_key",
        "allowed_openai_params",
        "safety_settings",
        "modalities",
        "audio",
        "service_tier",
    }
)
_TRANSPORT_FIELDS = {
    "messages",
    "input",
    "api_key",
    "api_base",
    "api_version",
    "base_url",
    "extra_headers",
    "headers",
    "timeout",
    "drop_params",
    "max_retries",
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
    "aws_region_name",
    "custom_llm_provider",
    "client",
    "metadata",
    "vertex_credentials",
    "vertex_project",
    "vertex_location",
}
_EXTRA_BODY_FIELDS = {
    "return_token_ids",
    "chat_template_kwargs",
    "thinking",
    "reasoning",
}


class ShadowCapture:
    def __init__(
        self,
        journal: Journal | None,
        witness: Journal | None = None,
        origin: Origin = "unknown",
    ) -> None:
        self.journal = journal
        self.witness = witness
        self.origin = origin
        self.shadow_failed = journal is None

    @property
    def enabled(self) -> bool:
        return not self.shadow_failed or self.witness is not None

    @classmethod
    def from_environment(cls, conversation_id: str) -> "ShadowCapture | None":
        root = os.environ.get("OPENHANDS_TRAJECTORY_SHADOW_SPOOL")
        cohort = os.environ.get("OPENHANDS_TRAJECTORY_SHADOW_IDS", "").split(",")
        if not root or conversation_id not in cohort:
            return None
        origin = os.environ.get("OPENHANDS_TRAJECTORY_SHADOW_ORIGIN")
        witness_root = os.environ.get("OPENHANDS_TRAJECTORY_WITNESS_SPOOL")
        witness = None
        if witness_root:
            if Path(witness_root).resolve() == Path(root).resolve():
                logger.error("Trajectory witness and shadow require distinct spools")
            else:
                witness = _open_spool(witness_root, conversation_id, "witness")
        return cls(
            _open_spool(root, conversation_id, "shadow"),
            witness,
            origin
            if origin in ("interactive", "automation", "evaluation")
            else "unknown",
        )

    def append_event(self, event: BaseModel, *, action_id: str | None) -> None:
        if not self.enabled:
            return
        with self._guard("event"):
            body = event.model_dump(mode="json")
            self.append(
                "event",
                {
                    "event_id": body["id"],
                    "event_kind": body["kind"],
                    "parent_id": body.get("parent_id"),
                    "event_time": body["timestamp"],
                    "action_id": action_id,
                },
                record_id=f"event:{body['id']}",
                payloads=(body,),
            )

    def append_tool_result(
        self,
        action_id: str,
        status: str,
        result: BaseModel,
        *,
        execution_id: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        with self._guard("tool_result"):
            body = result.model_dump(mode="json")
            self.append(
                "tool_result",
                {
                    "action_id": action_id,
                    "status": status,
                    "event_id": body["id"],
                    "execution_id": execution_id,
                },
                record_id=f"tool_result:{body['id']}",
                payloads=(body,),
            )

    def admit(self) -> "AdmissionTicket | None":
        witness = self.witness
        if witness is None:
            return None
        ticket = AdmissionTicket(self, uuid4().hex)
        try:
            witness.append("admitted", {"origin": self.origin}, run_id=ticket.run_id)
        except Exception as exc:
            self.witness = None
            logger.error(
                "Trajectory admission failed for %s (%s)",
                witness.conversation_id,
                type(exc).__name__,
            )
            return None
        return ticket

    @contextmanager
    def _guard(self, kind: str) -> Iterator[None]:
        try:
            yield
        except Exception as exc:
            logger.error(
                "Could not encode trajectory %s (%s)", kind, type(exc).__name__
            )
            self.append(
                "capture_gap",
                {"gap_id": uuid4().hex, "kind": kind, "error_type": type(exc).__name__},
            )

    def append(
        self,
        kind: str,
        data: dict[str, Any],
        *,
        record_id: str | None = None,
        payloads: tuple[Any, ...] = (),
    ) -> None:
        run = _RUN.get()
        run_id = run.id if run is not None and run.capture is self else None
        witness = self.witness
        if witness is not None:
            try:
                evidence = boundary_evidence(kind, run_id, data, payloads)
                if evidence is not None:
                    key, checksum = evidence
                    witness.append(
                        "receipt",
                        {"key": key, "sha256": checksum},
                        run_id=run_id,
                        record_id=key,
                    )
            except Exception as exc:
                self.witness = None
                logger.error(
                    "Trajectory witness failed for %s at %s (%s)",
                    witness.conversation_id,
                    kind,
                    type(exc).__name__,
                )
        journal = self.journal
        if journal is not None and not self.shadow_failed:
            try:
                journal.append(
                    kind,
                    data,
                    run_id=run_id,
                    record_id=record_id,
                    payloads=payloads,
                )
            except Exception as exc:
                self.shadow_failed = True
                logger.error(
                    "Trajectory shadow failed for %s at %s (%s)",
                    journal.conversation_id,
                    kind,
                    type(exc).__name__,
                )


def _open_spool(root: str, conversation_id: str, name: str) -> Journal | None:
    try:
        return Journal(Path(root), conversation_id)
    except Exception as exc:
        logger.error(
            "Could not open trajectory %s for %s (%s)",
            name,
            conversation_id,
            type(exc).__name__,
        )
        return None


@dataclass(frozen=True)
class Run:
    capture: ShadowCapture
    id: str


@dataclass(frozen=True)
class AdmissionTicket:
    capture: ShadowCapture
    run_id: str


_ADMISSION: ContextVar[AdmissionTicket | None] = ContextVar(
    "trajectory_admission", default=None
)


@contextmanager
def use_admission(ticket: AdmissionTicket | None) -> Iterator[None]:
    token = _ADMISSION.set(ticket)
    try:
        yield
    finally:
        _ADMISSION.reset(token)


_RUN: ContextVar[Run | None] = ContextVar("trajectory_run", default=None)


def active_capture() -> ShadowCapture | None:
    run = _RUN.get()
    return run.capture if run is not None and run.capture.enabled else None


@contextmanager
def capture_run(
    capture: ShadowCapture | None,
    branch_head: Callable[[], str | None],
    outcome: Callable[[], str],
) -> Iterator[None]:
    ticket = _ADMISSION.get()
    if capture is None:
        run = None
    elif ticket is not None and ticket.capture is capture:
        run = Run(capture, ticket.run_id)
    else:
        run = Run(capture, uuid4().hex)
    token = _RUN.set(run)
    try:
        if capture is None:
            yield
            return
        capture.append(
            "run_start", {"branch_head": branch_head(), "origin": capture.origin}
        )
        try:
            yield
        except BaseException as error:
            status = (
                "cancelled" if isinstance(error, asyncio.CancelledError) else "error"
            )
            capture.append("run_end", {"status": status, "branch_head": branch_head()})
            raise
        else:
            capture.append(
                "run_end", {"status": outcome(), "branch_head": branch_head()}
            )
    finally:
        _RUN.reset(token)


@dataclass
class Request:
    capture: ShadowCapture
    id: str
    attempts: int = 0
    selected_attempt: str | None = None


@dataclass
class Attempt:
    request: Request
    id: str
    number: int
    dispatched: bool = False
    received: bool = False
    stream_index: int = 0
    stream_bytes: int = 0
    stream_truncated: bool = False
    closed: bool = False
    lock: Lock = field(default_factory=Lock)


_REQUEST: ContextVar[Request | None] = ContextVar("trajectory_request", default=None)
_ATTEMPT: ContextVar[Attempt | None] = ContextVar("trajectory_attempt", default=None)


@contextmanager
def _request_scope() -> Iterator[None]:
    capture = active_capture()
    existing = _REQUEST.get()
    if capture is None or (existing is not None and existing.capture is capture):
        yield
        return
    request = Request(capture, uuid4().hex)
    token = _REQUEST.set(request)
    status = "completed"
    try:
        yield
    except BaseException as error:
        status = "aborted" if isinstance(error, asyncio.CancelledError) else "failed"
        raise
    finally:
        if request.attempts:
            capture.append(
                "request_end",
                {
                    "request_id": request.id,
                    "status": status,
                    "selected_attempt": request.selected_attempt
                    if status == "completed"
                    else None,
                },
            )
        _REQUEST.reset(token)


def record_request[**P, T](fn: Callable[P, T]) -> Callable[P, T]:
    @wraps(fn)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        with _request_scope():
            return fn(*args, **kwargs)

    return wrapped


def record_async_request[**P, T](
    fn: Callable[P, Coroutine[Any, Any, T]],
) -> Callable[P, Coroutine[Any, Any, T]]:
    @wraps(fn)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        with _request_scope():
            return await fn(*args, **kwargs)

    return wrapped


@contextmanager
def capture_attempt() -> Iterator[None]:
    request = _REQUEST.get()
    if request is None or active_capture() is not request.capture:
        yield
        return
    attempt = Attempt(request, uuid4().hex, request.attempts + 1)
    token = _ATTEMPT.set(attempt)
    status = "completed"
    error_type = None
    try:
        yield
    except BaseException as error:
        status = "aborted" if isinstance(error, asyncio.CancelledError) else "failed"
        error_type = type(error).__name__
        raise
    finally:
        with attempt.lock:
            attempt.closed = True
            if attempt.dispatched:
                if status == "completed":
                    request.selected_attempt = attempt.id
                request.capture.append(
                    "attempt_end",
                    {
                        "attempt_id": attempt.id,
                        "status": status,
                        "error_type": error_type,
                        "response_received": attempt.received,
                        "stream_truncated": attempt.stream_truncated,
                    },
                )
        _ATTEMPT.reset(token)


def transport_request(adapter: str, kwargs: dict[str, Any]) -> None:
    attempt = _ATTEMPT.get()
    if attempt is None or active_capture() is not attempt.request.capture:
        return
    input_key = "messages" if adapter == "chat" else "input"
    inputs = kwargs[input_key]
    request = {key: value for key, value in kwargs.items() if key in _REQUEST_FIELDS}
    extras = kwargs.get("extra_body")
    unsupported = set(kwargs) - _REQUEST_FIELDS - _TRANSPORT_FIELDS - {"extra_body"}
    if isinstance(extras, dict):
        request["extra_body"] = {
            k: v for k, v in extras.items() if k in _EXTRA_BODY_FIELDS
        }
        unsupported.update(
            f"extra_body.{k}" for k in extras if k not in _EXTRA_BODY_FIELDS
        )
    elif extras is not None:
        unsupported.add("extra_body")
    tools = request.pop("tools", None)
    attempt.dispatched = True
    attempt.request.attempts += 1
    attempt.request.capture.append(
        "attempt_start",
        {
            "adapter": adapter,
            "request_id": attempt.request.id,
            "attempt_id": attempt.id,
            "attempt": attempt.number,
            "input_key": input_key,
            "input_count": len(inputs) if isinstance(inputs, list) else None,
            "input_value": None if isinstance(inputs, list) else inputs,
            "parameters": request,
            "unsupported_options": sorted(unsupported),
        },
        payloads=(tools, *(inputs if isinstance(inputs, list) else [])),
    )


def _readable_reasoning(value: Any) -> bool:
    if isinstance(value, dict):
        if any(
            isinstance(value.get(key), str) and bool(value[key])
            for key in ("reasoning_content", "thinking", "reasoning")
        ):
            return True
        if value.get("type") == "reasoning":
            return any(
                isinstance(item, dict) and bool(item.get("text"))
                for item in value.get("summary") or []
            )
        return any(
            _readable_reasoning(value.get(key))
            for key in ("choices", "message", "thinking_blocks", "output", "content")
        )
    return isinstance(value, list) and any(map(_readable_reasoning, value))


def transport_response(response: BaseModel) -> None:
    attempt = _ATTEMPT.get()
    if attempt is not None and attempt.dispatched:
        with attempt.request.capture._guard("attempt_response"):
            body = response.model_dump(mode="json")
            attempt.received = True
            attempt.request.capture.append(
                "attempt_response",
                {
                    "attempt_id": attempt.id,
                    "readable_reasoning": "available"
                    if _readable_reasoning(body)
                    else "unavailable",
                },
                payloads=(body,),
            )


def transport_fragment(fragment: object) -> None:
    attempt = _ATTEMPT.get()
    if attempt is None or not attempt.dispatched:
        return
    with attempt.lock:
        if attempt.closed or attempt.stream_truncated:
            return
        with attempt.request.capture._guard("stream_fragment"):
            if not isinstance(fragment, BaseModel):
                attempt.stream_truncated = True
                attempt.request.capture.append(
                    "stream_limit", {"attempt_id": attempt.id}
                )
                return
            body = fragment.model_dump(mode="json")
            attempt.stream_bytes += len(encode(body))
            if attempt.stream_bytes > _MAX_STREAM_BYTES:
                attempt.stream_truncated = True
                attempt.request.capture.append(
                    "stream_limit", {"attempt_id": attempt.id}
                )
                return
            attempt.request.capture.append(
                "stream_fragment",
                {"attempt_id": attempt.id, "index": attempt.stream_index},
                payloads=(body,),
            )
            attempt.stream_index += 1


def record_attempt[**P, T](fn: Callable[P, T]) -> Callable[P, T]:
    @wraps(fn)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        with capture_attempt():
            return fn(*args, **kwargs)

    return wrapped


def record_async_attempt[**P, T](
    fn: Callable[P, Awaitable[T]],
) -> Callable[P, Coroutine[Any, Any, T]]:
    @wraps(fn)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        with capture_attempt():
            return await fn(*args, **kwargs)

    return wrapped


def selected_attempt_id() -> str | None:
    request = _REQUEST.get()
    if request is not None and request.capture is active_capture():
        return request.selected_attempt
    return None
