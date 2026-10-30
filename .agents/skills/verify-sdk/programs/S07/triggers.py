"""Legacy skills in a conversation: always-on, keyword and task triggers.

The agent gets three always-on skills (programmatic, a plain .md file loaded
with Skill.load, and an AGENTS.md third-party file), a keyword skill, a task
skill with an input, and both message suffixes. Four scripted turns:
a near miss, a match on two keywords, the keyword again, and the task
trigger. The recorder keeps what each LLM call was sent.
"""

import json

from recorder import blocks, calls, recording_llm, text
from sdk_verify import emit, run_paths

from openhands.sdk import Agent, AgentContext, Conversation
from openhands.sdk.skills import KeywordTrigger, Skill, TaskTrigger
from openhands.sdk.skills.types import InputMetadata


workspace = run_paths()["workspace"]
(workspace / "style.md").write_text("STYLE-RULES: wrap at 88 columns.\n")
(workspace / "AGENTS.md").write_text("# Agents\n\nAGENTS-RULES: run make test.\n")
skills = [
    Skill(name="inline-rules", content="INLINE-RULES: no TODOs.", trigger=None),
    Skill.load(workspace / "style.md"),
    Skill.load(workspace / "AGENTS.md"),
    Skill(
        name="crypto",
        content="CRYPTO-KNOWLEDGE: rot13 shifts by 13.",
        description="Crypto tips.",
        trigger=KeywordTrigger(keywords=["rot13", "cipher"]),
    ),
    Skill(
        name="deploy",
        content="DEPLOY-STEPS: deploy to ${ENV}.",
        description="Deploy steps.",
        trigger=TaskTrigger(triggers=["/deploy"]),
        inputs=[InputMetadata(name="ENV", description="target environment")],
    ),
]
agent = Agent(
    llm=recording_llm([{"text": f"answer {i}"} for i in range(4)]),
    tools=[],
    agent_context=AgentContext(
        skills=skills,
        system_message_suffix="SYS-SUFFIX here.",
        user_message_suffix="USER-SUFFIX here.",
    ),
)
conversation = Conversation(agent=agent, workspace=str(workspace), visualizer=None)
turns = [
    "what is a ciphertext?",  # "cipher" only as part of a word
    "Use a CIPHER, maybe ROT13.",  # both keywords; rot13 is first in the list
    "rot13 again please",  # already activated
    "/deploy the service",
]
for turn in turns:
    conversation.send_message(turn)
    conversation.run()
agent_calls = calls("agent")
system_blocks = [blocks(c["messages"][0]) for c in agent_calls]
dynamic = ["\n".join(b[1:]) for b in system_blocks]
user = [text([m for m in c["messages"] if m.role == "user"][-1]) for c in agent_calls]
activated = list(conversation.state.activated_knowledge_skills)
conversation.close()

# Every trigger type survives a JSON round-trip.
restored = [Skill.model_validate_json(s.model_dump_json()) for s in skills]

emit(
    {
        "ok": True,
        "llm_calls": len(agent_calls),
        "loaded_names": [s.name for s in skills],
        "always_on_every_call": all(
            all(m in d for m in ("INLINE-RULES", "STYLE-RULES", "AGENTS-RULES"))
            for d in dynamic
        ),
        "always_on_in_static": any("INLINE-RULES" in b[0] for b in system_blocks),
        "sys_suffix_every_call": all("SYS-SUFFIX" in d for d in dynamic),
        "user_text": user,
        "turn1_injected": "CRYPTO-KNOWLEDGE" in user[0],
        "turn2_injected": "CRYPTO-KNOWLEDGE" in user[1],
        "turn2_reported_keyword": 'keyword match for "rot13"' in user[1],
        "turn3_injected": "CRYPTO-KNOWLEDGE" in user[2],
        "turn4_task_injected": "DEPLOY-STEPS" in user[3],
        "turn4_asks_for_inputs": "ask the user to provide them" in user[3],
        "user_suffix_every_turn": all("USER-SUFFIX" in u for u in user),
        "activated": activated,
        "roundtrip_equal": restored == skills,
        "trigger_types": [
            json.loads(s.model_dump_json()).get("trigger", {})
            and json.loads(s.model_dump_json())["trigger"].get("type")
            for s in skills
        ],
    }
)
