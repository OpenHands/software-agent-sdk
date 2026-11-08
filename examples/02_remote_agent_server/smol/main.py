"""Run a conversation in an ephemeral local Smol microVM."""

import os

from pydantic import SecretStr

from openhands.sdk import LLM, Conversation, RemoteConversation
from openhands.tools.preset.default import get_default_agent
from openhands.workspace import SmolWorkspace


llm = LLM(
    usage_id="agent",
    model=os.environ["LLM_MODEL"],
    api_key=SecretStr(os.environ["LLM_API_KEY"]),
    base_url=os.environ.get("LLM_BASE_URL"),
)

with SmolWorkspace(server_image=os.environ["OPENHANDS_SERVER_IMAGE"]) as workspace:
    conversation = Conversation(
        agent=get_default_agent(llm=llm, cli_mode=True),
        workspace=workspace,
        callbacks=[lambda event: print(event)],
    )
    assert isinstance(conversation, RemoteConversation)
    try:
        conversation.send_message(
            "Write a small Python program that prints hello and run it."
        )
        conversation.run()
    finally:
        conversation.close()
    print(f"EXAMPLE_COST: {workspace.accumulated_cost or 0}")
