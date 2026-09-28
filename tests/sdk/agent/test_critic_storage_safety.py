import asyncio
import json
from contextlib import closing
from types import SimpleNamespace

import pytest

from openhands.sdk import Agent, Conversation
from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.critic import PassCritic
from openhands.sdk.event import ActionEvent
from openhands.sdk.io import LocalFileStore
from openhands.sdk.io.storage_safety import StorageSafetyConfig, StorageSafetyError
from openhands.sdk.llm import Message, MessageToolCall
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool.builtins.finish import FinishAction


@pytest.mark.parametrize("asynchronous", [False, True])
def test_low_disk_after_primary_response_skips_critic_and_retains_response(
    tmp_path, monkeypatch, asynchronous
):
    usage = SimpleNamespace(total=100_000_000, used=80_000_000, free=20_000_000)
    monkeypatch.setattr(
        "openhands.sdk.io.storage_safety.shutil.disk_usage", lambda _: usage
    )
    answer = "The complete primary response must survive."
    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                tool_calls=[
                    MessageToolCall(
                        id="finish",
                        name="finish",
                        arguments=json.dumps({"message": answer}),
                        origin="completion",
                    )
                ],
            )
        ]
    )
    completion = TestLLM.completion
    evaluate = PassCritic.evaluate
    critic_calls: list[int] = []

    def consume_space_after_completion(self, *args, **kwargs):
        response = completion(self, *args, **kwargs)
        usage.free = 4_000_000
        return response

    def track_critic(self, events, git_patch=None):
        critic_calls.append(len(events))
        return evaluate(self, events, git_patch)

    monkeypatch.setattr(TestLLM, "completion", consume_space_after_completion)
    monkeypatch.setattr(PassCritic, "evaluate", track_critic)
    with closing(
        Conversation(
            agent=Agent(llm=llm, tools=[], condenser=None, critic=PassCritic()),
            workspace=tmp_path / "workspace",
            persistence_dir=tmp_path / "history",
            storage_safety=StorageSafetyConfig(),
            visualizer=None,
        )
    ) as conversation:
        conversation.send_message("Complete this task")
        with pytest.raises(StorageSafetyError) as caught:
            if asynchronous:
                asyncio.run(conversation.arun())
            else:
                conversation.run()
        assert caught.value.code == "StorageLowSpace"
        assert critic_calls == []
        assert llm.call_count == 1
        assert conversation.state.execution_status == ConversationExecutionStatus.PAUSED
        assert conversation.state.persistence_dir is not None
        persisted = EventLog(LocalFileStore(conversation.state.persistence_dir))
        actions = [event for event in persisted if isinstance(event, ActionEvent)]
        assert len(actions) == 1
        assert isinstance(actions[0].action, FinishAction)
        assert actions[0].action.message == answer
        assert actions[0].critic_result is None
        with pytest.raises(StorageSafetyError):
            conversation.check_storage_safety()
