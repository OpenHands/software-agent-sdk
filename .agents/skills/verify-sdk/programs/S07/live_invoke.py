"""A real model finds an AgentSkills skill in <available_skills> and uses it.

The workspace has .agents/skills/rot13 (SKILL.md with a script); the agent
has the terminal and AgentContext(load_project_skills=True). The task names
no skill; the checks read whether invoke_skill ran and what was produced.
"""

from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import Agent, AgentContext, Conversation, Tool
from openhands.tools.terminal import TerminalTool


workspace = run_paths()["workspace"]
skill = workspace / ".agents" / "skills" / "rot13"
(skill / "scripts").mkdir(parents=True, exist_ok=True)
(skill / "SKILL.md").write_text(
    "---\nname: rot13\ndescription: Encode or decode text with ROT13.\n---\n"
    "To ROT13 text, run `sh scripts/encrypt.sh 'TEXT'` from this skill's directory.\n"
)
(skill / "scripts" / "encrypt.sh").write_text(
    "#!/bin/sh\nprintf '%s\\n' \"$1\" | tr 'A-Za-z' 'N-ZA-Mn-za-m'\n"
)
agent = Agent(
    llm=make_llm(),
    tools=[Tool(name=TerminalTool.name)],
    agent_context=AgentContext(load_project_skills=True),
)
conversation = Conversation(agent=agent, workspace=str(workspace), visualizer=None)
conversation.send_message(
    "Encode the text 'hello world' with ROT13 using the skill available to you, "
    "write the result to answer.txt, then finish."
)
conversation.run()
answer = workspace / "answer.txt"
result = {
    "ok": True,
    "status": conversation.state.execution_status.value,
    "invoked": list(conversation.state.invoked_skills),
    "answer": answer.read_text().strip() if answer.is_file() else None,
}
conversation.close()
emit(result)
