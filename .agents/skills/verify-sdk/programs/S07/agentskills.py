"""AgentSkills directories in a project, used by a scripted agent.

Lays out <workspace>/.agents/skills/ with a full `rot13` skill (frontmatter
fields, triggers, scripts/ references/ assets/, a .mcp.json, inline
commands), a trigger-only `hidden` skill (disable-model-invocation), a skill
with an over-long description and one whose name does not match its
directory, plus a skill in ~/.agents/skills. The agent calls invoke_skill
for a padded name, an unknown name, the hidden skill, the same skill again
and the home skill; the user's messages hit both skills' triggers.
"""

import json
import os
import re
from base64 import urlsafe_b64encode
from pathlib import Path

from recorder import blocks, calls, recording_llm, text
from sdk_verify import emit, run_paths

from openhands.sdk import Agent, AgentContext, Conversation
from openhands.sdk.event import ObservationEvent
from openhands.sdk.skills import (
    Skill,
    discover_skill_resources,
    load_skills_from_dir,
    validate_skill_name,
)
from openhands.sdk.skills.utils import load_mcp_config
from openhands.sdk.utils.cipher import Cipher


paths = run_paths()
workspace = paths["workspace"]
skills_dir = workspace / ".agents" / "skills"


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


write(
    skills_dir / "rot13" / "SKILL.md",
    """---
name: rot13
description: Encrypt text with <rot13> & friends.
triggers:
  - encrypt
license: MIT
compatibility: needs bash
metadata:
  author: verify-sdk
allowed-tools: terminal
---
ROT13-BODY: run scripts/encrypt.sh. Answer: !`echo INLINE-$((6*7))`.

```sh
!`echo FENCED`
```

Escaped \\!`echo ESCAPED`.
""",
)
write(skills_dir / "rot13" / "scripts" / "encrypt.sh", "#!/bin/sh\ntr a-z n-za-m\n")
write(skills_dir / "rot13" / "references" / "guide.md", "# Guide\nREFERENCE-ONLY\n")
write(skills_dir / "rot13" / "assets" / "table.txt", "abc\n")
write(
    skills_dir / "rot13" / ".mcp.json",
    json.dumps(
        {
            "mcpServers": {
                "helper": {
                    "command": "echo",
                    "args": ["${UNSET_VAR:-fallback}", "${SKILL_ROOT}"],
                    "env": {"TOKEN": "${HELPER_TOKEN}"},
                }
            }
        }
    ),
)
write(
    skills_dir / "hidden" / "SKILL.md",
    "---\nname: hidden\ndescription: Only by trigger.\ndisable-model-invocation: true\n"
    "triggers:\n  - sesame\n---\nHIDDEN-BODY\n",
)
write(
    skills_dir / "longdesc" / "SKILL.md",
    "---\nname: longdesc\ndescription: " + "x" * 1500 + "\n---\nLONG-BODY\n",
)
write(
    skills_dir / "badname" / "SKILL.md",
    "---\nname: other-name\ndescription: Name differs from its directory.\n---\nBAD\n",
)
write(
    paths["home"] / ".agents" / "skills" / "homeskill" / "SKILL.md",
    "---\nname: homeskill\ndescription: Lives in the home directory.\n---\nHOME-BODY\n",
)

# Loader view of the same directory, as a program inspecting skills would.
os.environ["HELPER_TOKEN"] = "from-env"
repo, knowledge, agentskills = load_skills_from_dir(skills_dir)
rot13 = agentskills.get("rot13")
resources = discover_skill_resources(skills_dir / "rot13")

script = [
    {"tool": "invoke_skill", "args": {"name": "  rot13 "}},
    {"tool": "invoke_skill", "args": {"name": "nope"}},
    {"tool": "invoke_skill", "args": {"name": "hidden"}},
    {"tool": "invoke_skill", "args": {"name": "rot13"}},
    {"tool": "invoke_skill", "args": {"name": "homeskill"}},
    {"finish": "done"},
    {"text": "opened"},
]
agent = Agent(
    llm=recording_llm(script),
    tools=[],
    agent_context=AgentContext(load_project_skills=True, load_user_skills=True),
)
conversation = Conversation(agent=agent, workspace=str(workspace), visualizer=None)
conversation.send_message("Please encrypt hello")
conversation.run()
conversation.send_message("open sesame")
conversation.run()
first = calls("agent")[0]
dynamic = "\n".join(blocks(first["messages"][0])[1:])
user_texts = [
    text([m for m in c["messages"] if m.role == "user"][-1]) for c in calls("agent")
]
observations = [
    {"text": e.observation.text[:600], "is_error": e.observation.is_error}
    for e in conversation.state.events
    if isinstance(e, ObservationEvent) and e.tool_name == "invoke_skill"
]
invoked = list(conversation.state.invoked_skills)
longdesc_entry = dynamic.split("<name>longdesc</name>")[-1].split("</skill>")[0]
conversation.close()


def tools_for(skills, **agent_kwargs):
    agent = Agent(
        llm=recording_llm([{"text": "ok"}], usage_id="probe"),
        tools=[],
        agent_context=AgentContext(skills=skills),
        **agent_kwargs,
    )
    probe = Conversation(agent=agent, workspace=str(workspace), visualizer=None)
    probe.send_message("hi")
    probe.run()
    probe.close()
    return calls("probe")[-1]["tools"]


hidden = agentskills["hidden"]
cipher = Cipher(urlsafe_b64encode(b"s" * 32).decode("ascii"))
secret_config = load_mcp_config(
    skills_dir / "rot13" / ".mcp.json",
    skills_dir / "rot13",
    {"HELPER_TOKEN": "from-secret"}.get,
)
secret_skill = Skill(
    name="with-mcp", content="x", mcp_tools=secret_config["mcpServers"]
)
write(workspace / "broken" / ".mcp.json", "{not json")
write(workspace / "broken" / "SKILL.md", "---\nname: broken\ndescription: d\n---\nB\n")
try:
    Skill.load(workspace / "broken" / "SKILL.md")
    broken_error = None
except Exception as exc:
    broken_error = type(exc).__name__

emit(
    {
        "ok": True,
        "dict_sizes": [len(repo), len(knowledge), len(agentskills)],
        "agentskills": sorted(agentskills),
        "fields": {
            "description": rot13.description,
            "license": rot13.license,
            "compatibility": rot13.compatibility,
            "metadata": rot13.metadata,
            "allowed_tools": rot13.allowed_tools,
            "is_agentskills_format": rot13.is_agentskills_format,
            "triggers": rot13.get_triggers(),
        },
        "reference_md_is_skill": "guide" in {**repo, **knowledge, **agentskills},
        "name_errors": validate_skill_name("Bad_Name", "bad-name"),
        "resources": {
            "scripts": rot13.resources.scripts if rot13.resources else None,
            "references": rot13.resources.references if rot13.resources else None,
            "assets": rot13.resources.assets if rot13.resources else None,
            "root_ok": bool(rot13.resources)
            and Path(rot13.resources.skill_root) == (skills_dir / "rot13").resolve(),
        },
        "discovered_scripts": resources.scripts,
        "mcp_args": rot13.mcp_tools["helper"].args if rot13.mcp_tools else None,
        "mcp_env_token": rot13.mcp_tools["helper"].env["TOKEN"].get_secret_value(),
        "mcp_env_token_with_secret": secret_skill.mcp_tools["helper"]
        .env["TOKEN"]
        .get_secret_value(),
        "invalid_mcp_json_error": broken_error,
        "tools": first["tools"],
        "listed": [
            n
            for n in (
                "rot13",
                "hidden",
                "longdesc",
                "homeskill",
                "badname",
                "other-name",
            )
            if f"<name>{n}</name>" in dynamic
        ],
        "description_escaped": "&lt;rot13&gt; &amp; friends" in dynamic,
        "body_in_prompt": "ROT13-BODY" in dynamic,
        "longdesc_full_in_prompt": "x" * 1500 in dynamic,
        "longdesc_notice": re.sub(r"x{20,}", "x…", longdesc_entry).strip(),
        "longdesc_points_to_invoke_skill": 'invoke_skill(name="longdesc")'
        in longdesc_entry,
        "longdesc_leaks_location": "SKILL.md" in longdesc_entry,
        "turn1_trigger_injected": "ROT13-BODY" in user_texts[0],
        "turn2_hidden_injected": "HIDDEN-BODY" in user_texts[-1],
        "observations": observations,
        "obs_is_error": [o["is_error"] for o in observations],
        "first_has_body": "ROT13-BODY" in observations[0]["text"],
        "inline_rendered": "INLINE-42" in observations[0]["text"],
        "fenced_literal": "!`echo FENCED`" in observations[0]["text"],
        "escaped_literal": "!`echo ESCAPED`" in observations[0]["text"]
        and "\\!`echo ESCAPED`" not in observations[0]["text"],
        "footer_relative": "located at `.agents/skills/rot13`"
        in observations[0]["text"],
        "footer_home_absolute": "located at "
        f"`{(paths['home'] / '.agents' / 'skills' / 'homeskill').resolve()}`"
        in observations[4]["text"],
        "unknown_error_lists": "Available skills" in observations[1]["text"],
        "hidden_refused": "only be activated by trigger" in observations[2]["text"],
        "invoked": invoked,
        "tools_default_tools_off": tools_for([rot13], include_default_tools=[]),
        "tools_hidden_only": tools_for([hidden]),
        "tools_legacy_only": tools_for([Skill(name="legacy", content="L")]),
        "secret_in_dump": "from-secret" in secret_skill.model_dump_json(),
        "skill_json_roundtrip": Skill.model_validate_json(
            rot13.model_dump_json()
        ).content
        == rot13.content,
        "secret_after_cipher": "from-secret"
        in json.dumps(
            Skill.model_validate(
                secret_skill.model_dump(mode="json", context={"cipher": cipher}),
                context={"cipher": cipher},
            )
            .mcp_tools["helper"]
            .model_dump(mode="json", context={"expose_secrets": True})
        ),
    }
)
