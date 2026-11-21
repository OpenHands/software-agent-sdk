import json
from contextlib import asynccontextmanager, closing

import pytest
from fastapi import FastAPI
from pydantic import PrivateAttr

from openhands.agent_server.api import add_domain_error_handler
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_router import conversation_router
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.event_router import event_router
from openhands.agent_server.sockets import conversation_sockets_router
from openhands.sdk import Agent
from openhands.sdk.conversation.goal import GoalAsked, run_goal
from openhands.sdk.conversation.impl.remote_conversation import RemoteConversation
from openhands.sdk.event import ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import Tool
from openhands.sdk.workspace import RemoteWorkspace
from openhands.tools.ask_user import AskUserTool
from tests.agent_server.canvas_extensions.test_canvas_extension_bridge import (
    _LiveServer,
)
from tests.agent_server.conftest import install_test_context
from tests.agent_server.stress.scripts import (
    placeholder_llm,
    start_conversation_with_test_llm,
)


class CaptureLLM(TestLLM):
    _inputs: list = PrivateAttr(default_factory=list)

    def completion(self, messages, *args, **kwargs):
        self._inputs.append([m.model_dump(mode="json") for m in messages])
        return super().completion(messages, *args, **kwargs)


@pytest.mark.parametrize(
    "mode",
    [
        "abandoned-question",
        "current-pending",
        "abandoned-answer",
        "current-answer",
        "empty-head",
    ],
)
def test_remote_goal_branch_cases(tmp_path, mode):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = ConversationService(conversations_dir=tmp_path / "conversations")
    captured = {}

    @asynccontextmanager
    async def lifespan(app):
        async with service:
            agent_llm = CaptureLLM.from_messages(
                [
                    Message(
                        role="assistant",
                        content=[],
                        tool_calls=[
                            MessageToolCall(
                                id="ask-case",
                                name="ask_user",
                                origin="completion",
                                arguments=json.dumps(
                                    {
                                        "question": "Which branch?",
                                        "options": [{"label": "A"}, {"label": "B"}],
                                    }
                                ),
                            )
                        ],
                    ),
                    Message(
                        role="assistant", content=[TextContent(text="CURRENT_RESULT")]
                    ),
                ],
                usage_id="remote-agent",
            )
            info = await start_conversation_with_test_llm(
                service,
                parent_llm=agent_llm,
                workspace_dir=str(workspace),
                usage_id="remote-agent",
                initial_text="ROOT_REQUEST",
                tools=[Tool(name=AskUserTool.name)],
            )
            event_service = await service.get_event_service(info.id)
            conv = event_service.get_conversation()
            root = conv.state.leaf_event_id
            await event_service.run()
            await event_service._run_task
            shown = next(
                e
                for e in conv.state.events
                if isinstance(e, ObservationEvent) and e.tool_name == "ask_user"
            )
            if mode in ("abandoned-answer", "current-answer"):
                await event_service.send_message(
                    Message(role="user", content=[TextContent(text="A")]), run=False
                )
            captured.update(
                info=info, root=root, shown=shown, conv=conv, agent_llm=agent_llm
            )
            yield

    app = FastAPI(lifespan=lifespan)
    add_domain_error_handler(app)
    install_test_context(app, Config())
    app.include_router(conversation_router, prefix="/api")
    app.include_router(event_router, prefix="/api")
    app.include_router(conversation_sockets_router)
    app.dependency_overrides[get_conversation_service] = lambda: service
    judge = CaptureLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[
                    TextContent(text='{"score":1.0,"complete":true,"missing":""}')
                ],
            )
        ],
        usage_id="remote-judge",
    )
    with _LiveServer(app) as live:
        url = f"http://127.0.0.1:{live.port}"
        with closing(
            RemoteConversation(
                agent=Agent(
                    llm=placeholder_llm("remote-agent"),
                    tools=[Tool(name=AskUserTool.name)],
                ),
                workspace=RemoteWorkspace(host=url, working_dir=str(workspace)),
                conversation_id=captured["info"].id,
                visualizer=None,
            )
        ) as remote:
            if mode == "abandoned-question":
                remote.navigate_to(captured["root"])
            elif mode == "abandoned-answer":
                remote.navigate_to(captured["shown"].id)
            elif mode == "empty-head":
                remote.navigate_to(None)
                assert remote.state.active_branch() == []
            before = len(captured["conv"].state.events)
            result = run_goal(remote, "Build current branch", judge, max_iterations=1)
            print(
                "REMOTE_CASE="
                + json.dumps(
                    {
                        "case": mode,
                        "result": type(result).__name__,
                        "judge_calls": judge.call_count,
                        "events_added": len(captured["conv"].state.events) - before,
                    }
                )
            )
            if mode in ("current-pending", "abandoned-answer"):
                assert isinstance(result, GoalAsked)
                assert judge.call_count == 0
                assert len(captured["conv"].state.events) == before
            else:
                assert result.status == "complete"
                assert judge.call_count == 1
