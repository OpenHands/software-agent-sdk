from openhands.sdk import LLM, Conversation
from openhands.tools.preset.default import get_default_agent


# Configure LLM and create agent
llm = LLM(
    model="gemini/gemini-2.5-flash",
)
agent = get_default_agent(llm=llm, cli_mode=True)

# Start a conversation
conversation = Conversation(agent=agent, workspace=".")
conversation.send_message("run ls")
conversation.run()
