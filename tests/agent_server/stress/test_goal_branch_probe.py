import asyncio
import json

import pytest
from pydantic import PrivateAttr

from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.testing import TestLLM
from tests.agent_server.stress.scripts import start_conversation_with_test_llm


class RecordingLLM(TestLLM):
    _inputs: list = PrivateAttr(default_factory=list)

    def completion(self, messages, *args, **kwargs):
        self._inputs.append([m.model_dump(mode="json") for m in messages])
        return super().completion(messages, *args, **kwargs)


@pytest.mark.asyncio
async def test_http_goal_judges_only_active_branch(
    client, conversation_service, tmp_path
):
    llm = RecordingLLM.from_messages(
        [
            Message(
                role="assistant", content=[TextContent(text="ACTIVE_BRANCH_RESULT")]
            ),
            Message(
                role="assistant",
                content=[
                    TextContent(text='{"score": 1.0, "complete": true, "missing": ""}')
                ],
            ),
        ],
        usage_id="branch-probe",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    info = await start_conversation_with_test_llm(
        conversation_service,
        parent_llm=llm,
        workspace_dir=str(workspace),
        usage_id="branch-probe",
        initial_text="ROOT_REQUEST",
    )
    service = await conversation_service.get_event_service(info.id)
    conv = service.get_conversation()
    root_id = conv.state.leaf_event_id
    stale = MessageEvent(
        source="agent",
        llm_message=Message(
            role="assistant", content=[TextContent(text="ABANDONED_BRANCH_RESULT")]
        ),
    )
    with conv._state:
        conv._on_event(stale)
    response = await client.post(
        f"/api/conversations/{info.id}/navigate", json={"event_id": root_id}
    )
    assert response.status_code == 200, response.text
    assert stale.id not in [e.id for e in conv.state.active_branch()]
    response = await client.post(
        f"/api/conversations/{info.id}/goal",
        json={"objective": "Build current branch", "max_iterations": 1},
    )
    assert response.status_code == 200, response.text
    await asyncio.wait_for(service._goal_loop_task, timeout=20)
    assert service._goal_loop_outcome.status == "complete"
    judge_input = json.dumps(llm._inputs[-1])
    agent_input = json.dumps(llm._inputs[0])
    evidence = {
        "http_navigate": 200,
        "http_goal": 200,
        "stale_on_disk": stale.id in conv.state.events,
        "stale_in_active_branch": stale.id
        in [e.id for e in conv.state.active_branch()],
        "stale_in_agent_input": "ABANDONED_BRANCH_RESULT" in agent_input,
        "stale_in_judge_input": "ABANDONED_BRANCH_RESULT" in judge_input,
        "active_in_judge_input": "ACTIVE_BRANCH_RESULT" in judge_input,
        "status": service._goal_loop_outcome.status,
    }
    print("PROBE_EVIDENCE=" + json.dumps(evidence))
    assert not evidence["stale_in_agent_input"]
    assert evidence["active_in_judge_input"]
    assert not evidence["stale_in_judge_input"]
