from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from flight_recorder.models.database import TraceIndex
from flight_recorder.recorder import Recorder
from flight_recorder.services.bundles import BundleService

from openhands.agent_server.conversation_router import conversation_router
from openhands.agent_server.conversation_service import (
    ConversationService,
    _ConversationRecord,
)
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import FlightRecorderTraceInfo, StoredConversation
from openhands.sdk import Agent, Message
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.llm import TextContent
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.testing import TestLLM
from openhands.sdk.workspace import LocalWorkspace


def test_flight_recorder_captures_llm_completion_log(tmp_path: Path) -> None:
    service = EventService(
        stored=_stored(uuid4(), tmp_path / "workspace"),
        conversations_dir=tmp_path / "conversations",
    )
    recorder = MagicMock(spec=Recorder)
    service._flight_recorder = recorder
    request_callbacks = []
    completion_callbacks = []
    llm = MagicMock(log_completions=False, usage_id="agent", model="gpt-4o")
    llm.telemetry.set_log_requests_callback.side_effect = request_callbacks.append
    llm.telemetry.set_log_completions_callback.side_effect = completion_callbacks.append

    service._setup_llm_log_streaming(MagicMock(get_all_llms=lambda: [llm]))
    request_callbacks[0]("call-1", '{"llm_call_id":"call-1"}')
    completion_callbacks[0](
        "completion.json",
        '{"llm_call_id":"call-1","response":{"id":"response-1"}}',
    )

    assert llm.telemetry.log_enabled is True
    recorder.on_llm_request.assert_called_once_with(
        "call-1", '{"llm_call_id":"call-1"}'
    )
    recorder.on_completion_log.assert_called_once_with(
        "completion.json",
        '{"llm_call_id":"call-1","response":{"id":"response-1"}}',
    )


@pytest.mark.asyncio
async def test_event_service_records_web_run_and_finalizes_trace(
    tmp_path: Path,
) -> None:
    conversation_id = uuid4()
    llm = TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text="Implemented")])]
    )
    stored = StoredConversation(
        id=conversation_id,
        workspace=LocalWorkspace(working_dir=str(tmp_path / "workspace")),
        confirmation_policy=NeverConfirm(),
        initial_message=None,
        metrics=None,
    )
    service = EventService(
        stored=stored,
        conversations_dir=tmp_path / "conversations",
        agent=Agent(llm=llm, tools=[]),
        enable_flight_recorder=True,
    )

    await service.start()
    await service.send_message(
        Message(role="user", content=[TextContent(text="Implement a helper")]),
        run=True,
    )
    assert service._run_task is not None
    await service._run_task
    active = service.get_flight_recorder_info()
    assert active is not None
    assert active.conversation_id == conversation_id
    assert active.active is True

    await service.close()
    await service.save_meta()

    finished = service.get_flight_recorder_info()
    assert finished is not None
    assert finished.active is False
    validation = BundleService().validate_bundle(Path(finished.bundle_path))
    assert validation.trace_id == finished.trace_id
    assert validation.record_count > 4

    trace_dir = service.conversation_dir / "flight-recorder"
    bundles_before = list(trace_dir.glob("*.afr"))
    async with ConversationService(
        conversations_dir=service.conversations_dir
    ) as restarted:
        cold = await restarted.get_flight_recorder_info(conversation_id)
    assert cold == finished
    assert list(trace_dir.glob("*.afr")) == bundles_before


def test_flight_recorder_endpoint_returns_latest_trace(tmp_path: Path) -> None:
    conversation_id = uuid4()
    info = FlightRecorderTraceInfo(
        conversation_id=conversation_id,
        trace_id="trace-id",
        bundle_path=str(tmp_path / "trace.afr"),
        active=False,
    )
    conversation_service = AsyncMock(spec=ConversationService)
    conversation_service.get_flight_recorder_info.return_value = info
    app = FastAPI()
    app.include_router(conversation_router, prefix="/api")
    app.dependency_overrides[get_conversation_service] = lambda: conversation_service

    response = TestClient(app).get(
        f"/api/conversations/{conversation_id}/flight-recorder"
    )

    assert response.status_code == 200
    assert response.json() == info.model_dump(mode="json")
    conversation_service.get_flight_recorder_info.assert_awaited_once_with(
        conversation_id
    )


@pytest.mark.asyncio
async def test_parent_trace_merges_server_native_child_conversation(
    tmp_path: Path,
) -> None:
    conversations_dir = tmp_path / "conversations"
    parent_id = uuid4()
    child_id = uuid4()
    parent_stored = _stored(parent_id, tmp_path / "workspace")
    child_stored = _stored(
        child_id,
        tmp_path / "workspace",
        parent_conversation_id=parent_id,
    )
    parent_bundle, parent_recorder = _record_bundle(
        conversations_dir, parent_id, "parent"
    )
    child_bundle, child_recorder = _record_bundle(conversations_dir, child_id, "child")
    _write_latest(parent_id, parent_recorder, parent_bundle, conversations_dir)
    _write_latest(child_id, child_recorder, child_bundle, conversations_dir)
    service = ConversationService(conversations_dir=conversations_dir)
    service._conversation_records = {
        parent_id: _ConversationRecord(
            stored=parent_stored,
            execution_status=ConversationExecutionStatus.FINISHED,
        ),
        child_id: _ConversationRecord(
            stored=child_stored,
            execution_status=ConversationExecutionStatus.FINISHED,
        ),
    }

    info = await service.get_flight_recorder_info(parent_id)

    assert info is not None
    assert Path(info.bundle_path).name == "orchestration.afr"
    records = list(
        BundleService.iter_record_file(Path(info.bundle_path) / "records.jsonl")
    )
    child_start = next(
        record
        for record in records
        if record.kind == "agent.started"
        and record.span_id == child_recorder.agent_span_id
    )
    assert child_start.trace_id == parent_recorder.trace_id
    assert child_start.parent_span_id == parent_recorder.agent_span_id


def _stored(
    conversation_id: UUID,
    workspace: Path,
    *,
    parent_conversation_id: UUID | None = None,
) -> StoredConversation:
    return StoredConversation(
        id=conversation_id,
        workspace=LocalWorkspace(working_dir=str(workspace)),
        confirmation_policy=NeverConfirm(),
        initial_message=None,
        metrics=None,
        parent_conversation_id=parent_conversation_id,
    )


def _record_bundle(
    conversations_dir: Path, conversation_id: UUID, label: str
) -> tuple[Path, Recorder]:
    trace_dir = conversations_dir / conversation_id.hex / "flight-recorder"
    bundle = trace_dir / f"{label}.afr"
    recorder = Recorder(bundle, TraceIndex(trace_dir / f"{label}.db"))
    recorder.close()
    return bundle, recorder


def _write_latest(
    conversation_id: UUID,
    recorder: Recorder,
    bundle: Path,
    conversations_dir: Path,
) -> None:
    info = FlightRecorderTraceInfo(
        conversation_id=conversation_id,
        trace_id=recorder.trace_id,
        bundle_path=str(bundle),
        active=False,
    )
    path = conversations_dir / conversation_id.hex / "flight-recorder" / "latest.json"
    path.write_text(info.model_dump_json())
