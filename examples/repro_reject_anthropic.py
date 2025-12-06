import os
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.security.confirmation_policy import AlwaysConfirm
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


def main():
    api_key = os.environ.get("LITELLM_API_KEY")
    if not api_key:
        raise SystemExit("LITELLM_API_KEY not set")

    llm = LLM(
        model="litellm_proxy/anthropic/claude-sonnet-4-5-20250929",
        base_url="https://llm-proxy.eval.all-hands.dev",
        api_key=SecretStr(api_key),
        usage_id="repro-reject-anthropic",
    )

    agent = Agent(
        llm=llm,
        tools=[
            Tool(name=TerminalTool.name),
            Tool(name=FileEditorTool.name),
        ],
    )

    conv = Conversation(agent=agent, workspace=os.getcwd())
    conv.set_confirmation_policy(AlwaysConfirm())

    # Intentionally trigger a file edit to get a tool_use
    conv.send_message("Create a file TEST_REPRO.txt with content 'hello world'")
    try:
        conv.run()
    except Exception as e:
        print("First run error:", e)
        raise

    # Reject pending actions
    conv.reject_pending_actions("Please explain first")

    # Run again to send the tool_result (rejection) immediately after tool_use
    try:
        conv.run()
        print("Second run completed without Anthropic tool_result error.")
    except Exception as e:
        print("Second run error:", e)
        raise


if __name__ == "__main__":
    main()
