"""A conversation run on a real full disk (a small tmpfs filled mid-step).

Only the LLM provider call is stubbed. Setup:
    sudo mkdir -p /mnt/ohfull && sudo mount -t tmpfs -o size=2m tmpfs /mnt/ohfull
    sudo chown "$(id -u):$(id -g)" /mnt/ohfull
Run:
    uv run python .pr/7102/repro_tmpfs.py /mnt/ohfull sync|async
"""

import asyncio
import errno
import json
import shutil
import sys
from pathlib import Path

from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse
from pydantic import SecretStr

import openhands.sdk.llm.llm as llm_module
from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation


root, mode = Path(sys.argv[1]), sys.argv[2]
for p in root.iterdir():
    shutil.rmtree(p) if p.is_dir() else p.unlink()
filler = root / "filler.bin"
state_dir = root / "state"
calls = 0


def fill_disk() -> None:
    with open(filler, "wb") as f:
        try:
            while True:
                f.write(b"\0" * 4096)
                f.flush()
        except OSError as e:
            assert e.errno == errno.ENOSPC


def provider(*args, **kwargs) -> ModelResponse:
    global calls
    calls += 1
    if calls == 1:
        fill_disk()
    message = LiteLLMMessage(content=f"reply {calls}", role="assistant")
    return ModelResponse(
        id=f"r{calls}", choices=[Choices(finish_reason="stop", index=0, message=message)]
    )


async def aprovider(*args, **kwargs) -> ModelResponse:
    return provider(*args, **kwargs)


llm_module.litellm_completion = provider
llm_module.litellm_acompletion = aprovider


def new_conversation(conversation_id=None) -> LocalConversation:
    llm = LLM(model="gpt-4o", api_key=SecretStr("k"), usage_id="agent", num_retries=0)
    return LocalConversation(
        agent=Agent(llm=llm, tools=[]),
        workspace=str(root),
        persistence_dir=str(state_dir),
        conversation_id=conversation_id,
        visualizer=None,
        stuck_detection=False,
    )


def persisted_status(conv: LocalConversation) -> str:
    base = state_dir / conv.id.hex / "base_state.json"
    return json.loads(base.read_text())["execution_status"]


def run(conv: LocalConversation) -> None:
    conv.run() if mode == "sync" else asyncio.run(conv.arun())


conv = new_conversation()
conv.send_message("hi")
try:
    run(conv)
    print("run() returned normally")
except Exception as e:
    print(f"run() raised {type(e).__name__}")
    print(f"  cause: {type(e.__cause__).__name__}: {e.__cause__}")
print(f"in-memory status: {conv.state.execution_status.value}")
print(f"persisted status (last good snapshot): {persisted_status(conv)}")
try:
    with conv.state:
        pass
    print("read-only `with state:` while disk is full: ok")
except OSError as e:
    print(f"read-only `with state:` while disk is full: raised {e}")

filler.unlink()
with conv.state:
    pass
print(f"space restored -> persisted status: {persisted_status(conv)}")
reopened = new_conversation(conv.id)
reopened.send_message("again")
run(reopened)
ids = [e.id for e in reopened.state.events]
print(
    f"reopened + rerun: status={reopened.state.execution_status.value}, "
    f"events={len(ids)}, duplicates={len(ids) - len(set(ids))}"
)
