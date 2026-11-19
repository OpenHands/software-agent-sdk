import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.subagent.registry import _reset_registry_for_tests
from openhands.tools.preset import register_builtins_agents
from openhands.tools.task.manager import TaskManager, TaskStatus


@pytest.fixture(autouse=True)
def _register_agents():
    _reset_registry_for_tests()
    register_builtins_agents()
    yield
    _reset_registry_for_tests()


def _make_parent(
    tmp_path: Path, persistence_dir: Path, conversation_id: uuid.UUID
) -> LocalConversation:
    llm = LLM(model="gpt-4o", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    return LocalConversation(
        agent=agent,
        workspace=str(tmp_path / "ws"),
        visualizer=None,
        delete_on_close=False,
        persistence_dir=str(persistence_dir),
        conversation_id=conversation_id,
    )


def test_subagent_tasks_survive_parent_conversation_resume(tmp_path: Path):
    persistence_dir = tmp_path / "conv_state"
    persistence_dir.mkdir(parents=True)
    (tmp_path / "ws").mkdir(parents=True)
    parent_id = uuid.uuid4()

    parent1 = _make_parent(tmp_path, persistence_dir, parent_id)
    manager1 = TaskManager()

    def fake_run(conv: LocalConversation):
        conv._state.execution_status = ConversationExecutionStatus.FINISHED

    with (
        patch.object(LocalConversation, "send_message"),
        patch.object(LocalConversation, "run", autospec=True, side_effect=fake_run),
        patch(
            "openhands.tools.task.manager.get_agent_final_response",
            return_value="turn1 handoff",
        ),
    ):
        t1 = manager1.start_task(
            prompt="investigate A1",
            subagent_type="general-purpose",
            conversation=parent1,
        )

    assert t1.id == "task_00000001"
    assert t1.status == TaskStatus.COMPLETED
    orig_conv_id = t1.conversation_id
    manager1.close()

    # Simulate a new CLI invocation (`openhands --resume <parent_id>`)
    parent2 = _make_parent(tmp_path, persistence_dir, parent_id)
    manager2 = TaskManager()

    with (
        patch.object(LocalConversation, "send_message"),
        patch.object(LocalConversation, "run", autospec=True, side_effect=fake_run),
        patch(
            "openhands.tools.task.manager.get_agent_final_response",
            return_value="turn2 reverified handoff",
        ),
    ):
        resumed = manager2.start_task(
            prompt="re-verify fact A1-1",
            subagent_type="general-purpose",
            resume="task_00000001",
            conversation=parent2,
        )
        t2 = manager2.start_task(
            prompt="investigate A2",
            subagent_type="general-purpose",
            conversation=parent2,
        )

    assert resumed.id == "task_00000001"
    assert resumed.conversation_id == orig_conv_id
    assert resumed.result == "turn2 reverified handoff"
    assert t2.id == "task_00000002"


def test_resume_uses_original_subagent_type(tmp_path: Path):
    persistence_dir = tmp_path / "conv_state"
    persistence_dir.mkdir(parents=True)
    (tmp_path / "ws").mkdir(parents=True)
    parent = _make_parent(tmp_path, persistence_dir, uuid.uuid4())
    manager = TaskManager()

    def fake_run(conv: LocalConversation):
        conv._state.execution_status = ConversationExecutionStatus.FINISHED

    with (
        patch.object(LocalConversation, "send_message"),
        patch.object(LocalConversation, "run", autospec=True, side_effect=fake_run),
        patch(
            "openhands.tools.task.manager.get_agent_final_response",
            return_value="ok",
        ),
    ):
        t1 = manager.start_task(
            prompt="explore",
            subagent_type="code-explorer",
            conversation=parent,
        )
        assert manager._tasks[t1.id].subagent_type == "code-explorer"
        with patch(
            "openhands.tools.task.manager.get_agent_factory",
            wraps=__import__(
                "openhands.tools.task.manager", fromlist=["get_agent_factory"]
            ).get_agent_factory,
        ) as spy:
            manager.start_task(
                prompt="follow up",
                subagent_type="general-purpose",
                resume=t1.id,
                conversation=parent,
            )
        assert spy.call_args_list[0].args[0] == "code-explorer"
