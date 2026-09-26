import errno
import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.agent_server.api import _add_exception_handlers
from openhands.agent_server.config import CONFIG_PATH_ENV, load_config
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import StoredConversation
from openhands.agent_server.pub_sub import Subscriber
from openhands.sdk import LLM, Agent, Event, Message, TextContent
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.io.storage_safety import StorageSafetyConfig, StorageSafetyError
from openhands.sdk.testing import TestLLM
from openhands.sdk.workspace import LocalWorkspace


class EventCollector(Subscriber[Event]):
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def __call__(self, event: Event) -> None:
        self.events.append(event)


def test_deployment_enables_storage_safety_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "missing.json"))
    monkeypatch.setenv("OH_STORAGE_SAFETY_MIN_FREE_RATIO", "0.05")
    assert load_config().storage_safety == StorageSafetyConfig(min_free_ratio=0.05)


def test_storage_error_survives_production_http_sanitization():
    app = FastAPI()
    _add_exception_handlers(app)

    @app.post("/run")
    def run():
        raise StorageSafetyError(
            "StorageLowSpace",
            "/state",
            min_free_ratio=0.05,
            free_bytes=47,
            total_bytes=1000,
        )

    response = TestClient(app).post("/run")
    assert response.status_code == 507
    assert response.json()["code"] == "StorageLowSpace"
    assert response.json()["free_bytes"] == 47
    assert "explicitly resume" in response.json()["detail"]


async def test_creation_refuses_low_disk_before_creating_files(tmp_path, monkeypatch):
    usage = shutil.disk_usage(tmp_path)
    monkeypatch.setattr(
        "openhands.sdk.io.storage_safety.shutil.disk_usage",
        lambda _: usage._replace(total=1000, free=49, used=951),
    )
    service = EventService(
        stored=StoredConversation(
            id=uuid4(), workspace=LocalWorkspace(working_dir=str(tmp_path / "ws"))
        ),
        agent=Agent(llm=LLM(model="test-model"), tools=[]),
        conversations_dir=tmp_path / "conversations",
        storage_safety=StorageSafetyConfig(),
    )
    with pytest.raises(StorageSafetyError, match="Free space or expand"):
        await service.start()
    assert not service.conversation_dir.exists()


async def test_low_disk_blocks_run_until_explicit_retry(storage_service, monkeypatch):
    service = storage_service
    conversation = service.get_conversation()
    llm = TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text="done")])]
    )
    conversation.switch_llm(llm)
    await service.send_message(Message(role="user", content=[TextContent(text="go")]))
    original_events = len(conversation.state.events)
    usage = shutil.disk_usage(service.conversation_dir)
    with monkeypatch.context() as patch:
        patch.setattr(
            "openhands.sdk.io.storage_safety.shutil.disk_usage",
            lambda _: usage._replace(free=usage.total // 100),
        )
        with pytest.raises(StorageSafetyError):
            await service.run()
        assert llm.call_count == 0
        assert len(conversation.state.events) == original_events
    assert llm.call_count == 0
    await service.run()
    assert await service.wait_for_run_completion(timeout=10) == (
        ConversationExecutionStatus.FINISHED
    )
    assert llm.call_count == 1


async def test_enospc_reports_online_without_error_write_loop(
    storage_service, monkeypatch
):
    service = storage_service
    conversation = service.get_conversation()
    conversation.switch_llm(
        TestLLM.from_messages(
            [
                Message(
                    role="assistant", content=[TextContent(text="uncommitted answer")]
                )
            ]
        )
    )
    await service.send_message(Message(role="user", content=[TextContent(text="go")]))
    collector = EventCollector()
    await service.subscribe_to_events(collector)
    attempts: list[Path] = []

    def fail_write(path: Path, _content: str) -> None:
        attempts.append(path)
        raise OSError(errno.ENOSPC, "No space left on device")

    with monkeypatch.context() as patch:
        patch.setattr("openhands.sdk.io.local.atomic_write_text", fail_write)
        await service.run()
        assert await service.wait_for_run_completion(timeout=10) == (
            ConversationExecutionStatus.ERROR
        )
    assert len(attempts) == 1
    errors = [e for e in collector.events if isinstance(e, ConversationErrorEvent)]
    assert errors
    assert json.loads(errors[-1].detail)["code"] == "StorageWriteFailed"
    assert not any(
        isinstance(e, ConversationErrorEvent) and e.code == "StorageWriteFailed"
        for e in conversation.state.events
    )
    with pytest.raises(ValueError, match="acknowledge"):
        await service.recover_storage(acknowledge_unknown_outcomes=False)
    await service.recover_storage(acknowledge_unknown_outcomes=True)
    assert conversation.state.execution_status == ConversationExecutionStatus.PAUSED


async def test_metadata_failure_requires_recovery_before_running(
    storage_service, monkeypatch
):
    service = storage_service
    conversation = service.get_conversation()
    llm = TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text="done")])]
    )
    conversation.switch_llm(llm)
    await service.send_message(Message(role="user", content=[TextContent(text="go")]))

    def fail_write(_path: Path, _content: str) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    with monkeypatch.context() as patch:
        patch.setattr("openhands.sdk.io.local.atomic_write_text", fail_write)
        with pytest.raises(StorageSafetyError):
            await service.save_meta()
    await service.run()
    assert await service.wait_for_run_completion(timeout=10) == (
        ConversationExecutionStatus.ERROR
    )
    assert llm.call_count == 0
    await service.recover_storage(acknowledge_unknown_outcomes=True)
    assert (service.conversation_dir / "meta.json").is_file()
    await service.run()
    assert await service.wait_for_run_completion(timeout=10) == (
        ConversationExecutionStatus.FINISHED
    )
    assert llm.call_count == 1
