"""Live check: a parent agent delegates one task on a saved, encrypted profile.

Parent runs on glm-4.7. The saved profile ``worker-air`` (glm-4.5-air, API key
encrypted at rest) is passed as ``llm_profile`` on the task call. Prints what
the parent asked for, which model each side ran on, the parent's registry and
metrics keys, then resumes the task with no ``llm_profile`` and prints the
worker's model again.

Usage: LLM_API_KEY=... uv run python .pr/e2e_task_llm_profile_live.py <tmpdir>
"""

import json
import os
import secrets
import sys
from pathlib import Path


TMP = Path(sys.argv[1]).resolve()
os.environ["OH_PERSISTENCE_DIR"] = str(TMP / "oh")  # isolated profile store

from pydantic import SecretStr  # noqa: E402

from openhands.sdk import LLM, Agent, Tool  # noqa: E402
from openhands.sdk.conversation.impl.local_conversation import (  # noqa: E402
    LocalConversation,
)
from openhands.sdk.event import ActionEvent  # noqa: E402
from openhands.sdk.llm.llm_profile_store import LLMProfileStore  # noqa: E402
from openhands.sdk.subagent import register_agent  # noqa: E402
from openhands.sdk.utils.cipher import Cipher  # noqa: E402
from openhands.tools.task import TaskToolSet  # noqa: E402


BASE_URL = "https://api.z.ai/api/coding/paas/v4"
KEY = os.environ["LLM_API_KEY"]

cipher = Cipher(secrets.token_urlsafe(32))
store = LLMProfileStore()
store.save(
    "worker-air",
    LLM(model="openai/glm-4.5-air", api_key=SecretStr(KEY), base_url=BASE_URL),
    include_secrets=True,
    cipher=cipher,
)
raw = (store.base_dir / "worker-air.json").read_text()
print("profile file holds plaintext key:", KEY in raw)

parent_llm = LLM(
    model="openai/glm-4.7", api_key=SecretStr(KEY), base_url=BASE_URL
)


def make_worker(llm: LLM) -> Agent:
    return Agent(llm=llm, tools=[])


register_agent(
    name="echo_worker",
    factory_func=make_worker,
    description="Answers one short question in plain text.",
)

conv = LocalConversation(
    agent=Agent(llm=parent_llm, tools=[Tool(name=TaskToolSet.name)]),
    workspace=str(TMP / "ws"),
    cipher=cipher,
    visualizer=None,
)


def task_actions() -> list[dict]:
    out = []
    for e in conv.state.events:
        if isinstance(e, ActionEvent) and e.tool_name == "task":
            a = e.action.model_dump() if e.action else {}
            out.append({k: a.get(k) for k in ("subagent_type", "llm_profile", "resume")})
    return out


def report(label: str) -> None:
    stats = conv.conversation_stats.usage_to_metrics
    print(f"--- {label}")
    print("task calls:", json.dumps(task_actions()))
    print("parent model:", conv.agent.llm.model)
    print("parent registry usage ids:", sorted(conv.llm_registry.list_usage_ids()))
    for key, m in sorted(stats.items()):
        models = sorted({u.model for u in m.token_usages})
        print(f"metrics[{key}]: models={models} calls={len(m.token_usages)}")


conv.send_message(
    "Use the task tool once: subagent_type 'echo_worker', llm_profile "
    "'worker-air', prompt 'Reply with the single word PONG.'. Then tell me "
    "what it replied and the task id."
)
conv.run()
report("after first delegation")

conv.send_message(
    "Resume that same task with the task tool. Do not pass llm_profile. "
    "Prompt: 'Reply with the single word PONG2.'. Then tell me the reply."
)
conv.run()
report("after resume without llm_profile")
