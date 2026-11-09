"""An agent with a full AgentContext talks for three turns; report what the
model was actually sent.

AgentContext carries an always-on repo skill, a keyword skill, a disabled
skill, a system and a user message suffix, two secrets and a fixed date. The
scripted model answers each turn with plain text; the recorder keeps the
messages of every call so the checks read the real prompt.
"""

import json
from base64 import urlsafe_b64encode

from recorder import blocks, calls, recording_llm, text
from sdk_verify import conversation_dir, emit, run_paths

from openhands.sdk import Agent, AgentContext, Conversation, Tool
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.secret import StaticSecret
from openhands.sdk.skills import KeywordTrigger, Skill
from openhands.sdk.utils.cipher import Cipher
from openhands.tools.terminal import TerminalTool


SECRET = "tok-9f8e7d-secret"
context = AgentContext(
    skills=[
        Skill(
            name="house-rules", content="HOUSE-RULES: indent with tabs.", trigger=None
        ),
        Skill(
            name="cat-voice",
            content="CAT-VOICE: answer like a grumpy cat.",
            description="Answers in a cat's voice.",
            trigger=KeywordTrigger(keywords=["flarglebargle"]),
        ),
        Skill(name="dropped", content="DROPPED-SKILL text", trigger=None),
    ],
    disabled_skills=["dropped", "not-loaded-anyway"],
    system_message_suffix="SYS-SUFFIX: keep answers short.",
    user_message_suffix="USER-SUFFIX: be kind.",
    secrets={
        "DEPLOY_TOKEN": SECRET,
        "DB_PASSWORD": StaticSecret(value="pw-123", description="the staging database"),
    },
    current_datetime="2026-10-13T09:30:00",
)
agent = Agent(
    llm=recording_llm([{"text": "one"}, {"text": "two"}, {"text": "three"}]),
    tools=[Tool(name=TerminalTool.name)],
    agent_context=context,
)
conversation = Conversation(
    agent=agent,
    workspace=str(run_paths()["workspace"]),
    persistence_dir=str(run_paths()["conversations"]),
    visualizer=None,
)
turns = [
    "Hello FlargleBargle friend",  # the keyword, different case
    "flarglebargle once more",  # already activated: not injected again
    "a plain question",
]
for message in turns:
    conversation.send_message(message)
    conversation.run()

agent_calls = calls("agent")
system = agent_calls[0]["messages"][0]
static, *dynamic = blocks(system)
dynamic_text = "\n".join(dynamic)
last_user = [
    text([m for m in c["messages"] if m.role == "user"][-1]) for c in agent_calls
]
activated = list(conversation.state.activated_knowledge_skills)
stored = (conversation_dir(conversation.id.hex) / "base_state.json").read_text()
events_text = "".join(
    p.read_text()
    for p in (conversation_dir(conversation.id.hex) / "events").glob("*.json")
)
conversation.close()

# Duplicate skill names are rejected; blank suffixes add nothing.
try:
    AgentContext(skills=[Skill(name="x", content="a"), Skill(name="x", content="b")])
    duplicate_rejected = False
except Exception:
    duplicate_rejected = True
blank = AgentContext(user_message_suffix="   ", system_message_suffix="")
blank_user = blank.get_user_message_suffix(
    Message(role="user", content=[TextContent(text="hi")]), []
)

# Secrets: redacted in a plain dump, round-trip through a cipher.
plain_dump = context.model_dump(mode="json")
cipher = Cipher(urlsafe_b64encode(b"k" * 32).decode("ascii"))
sealed = context.model_dump(mode="json", context={"cipher": cipher})
restored = AgentContext.model_validate(sealed, context={"cipher": cipher})

emit(
    {
        "ok": True,
        "llm_calls": len(agent_calls),
        "system_blocks": len(blocks(system)),
        "static_has_sys_suffix": "SYS-SUFFIX" in static,
        "dynamic_has_sys_suffix": "SYS-SUFFIX" in dynamic_text,
        "repo_skill_in_dynamic": "HOUSE-RULES" in dynamic_text,
        "repo_skill_in_static": "HOUSE-RULES" in static,
        "disabled_skill_in_prompt": "DROPPED-SKILL" in static + dynamic_text,
        "keyword_skill_body_in_system": "CAT-VOICE" in static + dynamic_text,
        "keyword_skill_listed": "<name>cat-voice</name>" in dynamic_text,
        "secret_names_in_dynamic": [
            n for n in ("DEPLOY_TOKEN", "DB_PASSWORD") if n in dynamic_text
        ],
        "secret_description_in_dynamic": "the staging database" in dynamic_text,
        "secret_value_sent": any(
            SECRET in text(m) for c in agent_calls for m in c["messages"]
        ),
        "datetime_in_dynamic": "2026-10-13" in dynamic_text,
        "datetime_in_static": "2026-10-13" in static,
        "turn_user_text": last_user,
        "turn1_has_skill": "CAT-VOICE" in last_user[0],
        "turn1_has_user_suffix": "USER-SUFFIX" in last_user[0],
        "turn2_has_skill": "CAT-VOICE" in last_user[1],
        "turn2_has_user_suffix": "USER-SUFFIX" in last_user[1],
        "turn3_has_user_suffix": "USER-SUFFIX" in last_user[2],
        "activated_skills": activated,
        "duplicate_rejected": duplicate_rejected,
        "blank_user_suffix": blank_user,
        "secret_in_plain_dump": SECRET in json.dumps(plain_dump),
        "secret_in_cipher_dump": SECRET in json.dumps(sealed),
        "secret_after_cipher_roundtrip": restored.secrets["DEPLOY_TOKEN"] == SECRET,
        "secret_in_persisted_state": SECRET in stored or SECRET in events_text,
    }
)
