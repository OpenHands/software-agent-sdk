import os
import sys

from pydantic import SecretStr

from openhands.sdk import (
    Conversation,
    get_logger,
)
from openhands.sdk.llm import LLM
from openhands.sdk.llm.streaming import ModelResponseStream
from openhands.tools.preset.default import get_default_agent


logger = get_logger(__name__)


api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
if not api_key:
    raise RuntimeError("Set LLM_API_KEY or OPENAI_API_KEY in your environment.")

model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
base_url = os.getenv("LLM_BASE_URL")
llm = LLM(
    model=model,
    api_key=SecretStr(api_key),
    base_url=base_url,
    usage_id="stream-demo",
    stream=True,
)

agent = get_default_agent(llm=llm, cli_mode=True)


def on_token(chunk: ModelResponseStream) -> None:
    choices = chunk.choices
    for choice in choices:
        delta = choice.delta
        if delta is not None:
            content = getattr(delta, "content", None)
            if isinstance(content, str):
                sys.stdout.write(content)
                sys.stdout.flush()


conversation = Conversation(
    agent=agent,
    workspace=os.getcwd(),
    token_callbacks=[on_token],
)

story_prompt = (
    "Tell me a long story about LLM streaming, make sure it has multiple paragraphs. "
)
conversation.send_message(story_prompt)
print("Token Streaming:")
print("-" * 100 + "\n")
conversation.run()

cleanup_prompt = (
    "Thank you. Please delete the streaming story file now that I've read it, "
    "then confirm the deletion."
)
conversation.send_message(cleanup_prompt)
print("Token Streaming:")
print("-" * 100 + "\n")
conversation.run()
