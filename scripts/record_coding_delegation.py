"""Record a live delegated coding run for Agent Flight Recorder."""

import os
from pathlib import Path

from flight_recorder.recorder import Recorder

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.conversation import get_agent_final_response
from openhands.tools import register_builtins_agents
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.task import TaskToolSet
from openhands.tools.task_tracker import TaskTrackerTool
from openhands.tools.terminal import TerminalTool


def run(recorder: Recorder) -> int:
    prompt = os.environ.get("AFR_PROMPT")
    if not prompt:
        raise ValueError("AFR_PROMPT must contain the implementation request")
    workspace = Path(os.environ.get("AFR_WORKSPACE", os.getcwd())).resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    register_builtins_agents(enable_browser=False)
    llm = LLM(
        model=os.environ.get("LLM_MODEL", "gpt-5.5"),
        api_key=os.environ.get("LLM_API_KEY"),
        base_url=os.environ.get("LLM_BASE_URL"),
        usage_id="primary-agent",
    )
    llm.telemetry.log_enabled = True
    llm.telemetry.set_log_requests_callback(recorder.on_llm_request)
    llm.telemetry.set_log_completions_callback(recorder.on_completion_log)
    agent = Agent(
        llm=llm,
        tools=[
            Tool(name=TaskToolSet.name),
            Tool(name=TerminalTool.name),
            Tool(name=FileEditorTool.name),
            Tool(name=TaskTrackerTool.name),
        ],
        tool_concurrency_limit=2,
    )
    conversation = Conversation(
        agent=agent,
        workspace=workspace,
        callbacks=[recorder],
        visualizer=None,
    )
    try:
        conversation.send_message(prompt)
        conversation.run()
        recorder.record_metrics(conversation.conversation_stats)
        result = get_agent_final_response(conversation.state.events)
        if result:
            print(result)
    finally:
        conversation.close()
    return 0
