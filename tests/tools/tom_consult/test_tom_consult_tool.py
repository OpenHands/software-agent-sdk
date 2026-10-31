"""Tests for TomConsultTool declared_resources."""

import os
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest

from openhands.sdk.tool import DeclaredResources
from openhands.tools.tom_consult.definition import (
    ConsultTomAction,
    ConsultTomObservation,
    SleeptimeComputeTool,
    TomConsultTool,
)
from openhands.tools.tom_consult.executor import TomConsultExecutor


@pytest.mark.parametrize("tool_class", [TomConsultTool, SleeptimeComputeTool])
def test_tom_store_writes_are_durable_without_background_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool_class
):
    """Factory-created stores preserve synchronous writes with no writer to leak."""
    monkeypatch.setattr(
        "openhands.tools.tom_consult.definition.get_user_persistence_dir",
        lambda: tmp_path,
    )
    tools = tool_class.create(conv_state=Mock())
    executor = tools[0].executor
    assert isinstance(executor, TomConsultExecutor)
    calls: list[str] = []
    original_fsync = os.fsync

    def spy_fsync(fd: int) -> None:
        original_fsync(fd)
        calls.append(threading.current_thread().name)

    monkeypatch.setattr(os, "fsync", spy_fsync)
    try:
        executor.file_store.write("processing_history.json", "{}")
        executor.file_store.flush()
        assert calls == [threading.current_thread().name]
        assert (tmp_path / "processing_history.json").read_text() == "{}"
    finally:
        executor.close()
        executor.file_store.close()


@pytest.mark.parametrize(
    "action",
    [
        ConsultTomAction(reason="unclear intent", use_user_message=True),
        ConsultTomAction(
            reason="need guidance",
            use_user_message=False,
            custom_query="What does the user prefer?",
        ),
    ],
    ids=["use-user-message", "custom-query"],
)
def test_consult_tom_declared_resources(action):
    """TomConsultTool always declares safe with no resource keys."""
    tool = TomConsultTool(
        action_type=ConsultTomAction,
        observation_type=ConsultTomObservation,
        description="test",
        executor=None,
    )

    resources = tool.declared_resources(action)

    assert isinstance(resources, DeclaredResources)
    assert resources.declared is True
    assert resources.keys == ()
