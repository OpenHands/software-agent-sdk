import json
from contextlib import asynccontextmanager, closing

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
from openhands.sdk.conversation.goal import run_goal
from openhands.sdk.conversation.impl.remote_conversation import RemoteConversation
from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.testing import TestLLM
from openhands.sdk.workspace import RemoteWorkspace
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


def test_remote_goal_filters_abandoned_branch(tmp_path):
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
                        role="assistant", content=[TextContent(text="CURRENT_RESULT")]
                    )
                ],
                usage_id="remote-agent",
            )
            info = await start_conversation_with_test_llm(
                service,
                parent_llm=agent_llm,
                workspace_dir=str(workspace),
                usage_id="remote-agent",
                initial_text="ROOT_REQUEST",
            )
            event_service = await service.get_event_service(info.id)
            conv = event_service.get_conversation()
            root = conv.state.leaf_event_id
            stale = MessageEvent(
                source="agent",
                llm_message=Message(
                    role="assistant", content=[TextContent(text="ABANDONED_RESULT")]
                ),
            )
            with conv._state:
                conv._on_event(stale)
            captured.update(
                info=info, root=root, stale=stale, conv=conv, agent_llm=agent_llm
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
                agent=Agent(llm=placeholder_llm("remote-agent"), tools=[]),
                workspace=RemoteWorkspace(host=url, working_dir=str(workspace)),
                conversation_id=captured["info"].id,
                visualizer=None,
            )
        ) as remote:
            remote.navigate_to(captured["root"])
            outcome = run_goal(remote, "Build current branch", judge, max_iterations=1)
            transcript = json.dumps(judge._inputs[-1])
            evidence = {
                "transport": "real localhost HTTP and WebSocket",
                "status": outcome.status,
                "old_branch_on_disk": captured["stale"].id
                in captured["conv"].state.events,
                "old_in_agent": "ABANDONED_RESULT"
                in json.dumps(captured["agent_llm"]._inputs),
                "old_in_judge": "ABANDONED_RESULT" in transcript,
                "current_in_judge": "CURRENT_RESULT" in transcript,
            }
            print("REMOTE_PROOF=" + json.dumps(evidence))
            assert evidence["old_branch_on_disk"]
            assert not evidence["old_in_agent"]
            assert evidence["current_in_judge"]
            assert not evidence["old_in_judge"]
