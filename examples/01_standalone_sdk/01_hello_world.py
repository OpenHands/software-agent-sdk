import os

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.task_tracker import TaskTrackerTool
from openhands.tools.terminal import TerminalTool


# Credits for Claude Sonnet 4.5 model are available at
# https://app.all-hands.dev/settings/api-keys
# For Anthropic or others use LiteLLM model names, e.g.
# LLM_MODEL="anthropic/claude-sonnet-4-5-20250929"

llm = LLM(
    model=os.getenv("LLM_MODEL", "openhands/claude-sonnet-4-5-20250929"),
    api_key=os.getenv("LLM_API_KEY"),
)

agent = Agent(
    llm=llm,
    tools=[
        Tool(name=TerminalTool.name),
        Tool(name=FileEditorTool.name),
        Tool(name=TaskTrackerTool.name),
    ],
)

cwd = os.getcwd()
conversation = Conversation(agent=agent, workspace=cwd)

conversation.send_message("Write 3 facts about the current project into FACTS.txt.")
conversation.run()
print("All done!")
