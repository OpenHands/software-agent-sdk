import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    LLMProfileStore,
    LocalConversation,
    OpenHandsAgentSettings,
)
from openhands.sdk.agent.utils import make_llm_completion
from openhands.sdk.llm import Message, TextContent, llm_profile_store
from openhands.sdk.tool.builtins import AskOracleAction


RESULT_PATH = Path(__file__).with_name("ask_oracle_live_validation.json")
PRIMARY_MODEL = "openai/gpt-5-nano"
ORACLE_MODEL = "litellm_proxy/openai/gpt-5-mini"
ORACLE_BASE_URL = "https://llm-proxy.eval.all-hands.dev"


def first_text(message: Message) -> str:
    return "".join(
        content.text for content in message.content if isinstance(content, TextContent)
    ).strip()


started_at = datetime.now(UTC).isoformat()
profile_store_dir = Path(tempfile.mkdtemp()) / "profiles"
setattr(llm_profile_store, "_DEFAULT_PROFILE_DIR", profile_store_dir)

try:
    primary_llm = LLM(
        model=PRIMARY_MODEL,
        api_key=SecretStr(os.environ["OPENAI_API_KEY"]),
        usage_id="ask-oracle-live-primary",
        max_output_tokens=1000,
        reasoning_effort="low",
    )
    oracle_llm = LLM(
        model=ORACLE_MODEL,
        api_key=SecretStr(os.environ["LITELLM_API_KEY"]),
        base_url=ORACLE_BASE_URL,
        usage_id="ask-oracle-live-oracle",
        max_output_tokens=1000,
        reasoning_effort="low",
    )

    primary_response = make_llm_completion(
        primary_llm,
        [
            Message(
                role="user",
                content=[
                    TextContent(
                        text=("Reply with exactly: primary profile live check ok")
                    )
                ],
            )
        ],
    )
    primary_text = first_text(primary_response.message)

    store = LLMProfileStore()
    store.save("oracle", oracle_llm, include_secrets=True)

    settings = OpenHandsAgentSettings(
        llm=primary_llm,
        oracle_llm_profile="oracle",
        enable_switch_llm_tool=False,
    )
    agent = settings.create_agent()
    conversation = LocalConversation(agent=agent, workspace=Path.cwd())
    conversation._ensure_agent_ready()
    observation = conversation.execute_tool(
        "ask_oracle",
        AskOracleAction(
            question=(
                "Answer in two concise bullets: confirm that you are the Oracle "
                "profile and give one practical reason an agent should ask for a "
                "second opinion when stuck."
            ),
            context=(
                "The active LLM profile is OpenAI direct gpt-5-nano. The Oracle "
                "profile is gpt-5-mini through the eval LiteLLM proxy."
            ),
        ),
    )

    result = {
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "issue": "https://github.com/OpenHands/software-agent-sdk/issues/3672",
        "primary_profile": {
            "model": PRIMARY_MODEL,
            "provider": "OpenAI direct",
            "usage_id": primary_llm.usage_id,
            "response_text": primary_text,
            "succeeded": bool(primary_text),
        },
        "oracle_profile": {
            "profile_name": "oracle",
            "model": ORACLE_MODEL,
            "base_url": ORACLE_BASE_URL,
            "base_url_source": "openhands-sdk/openhands/sdk/agent/base.py",
            "usage_id": "ask-oracle-live-oracle",
        },
        "ask_oracle_tool": {
            "registered_tool_names": sorted(agent.tools_map),
            "observation_is_error": observation.is_error,
            "observation_response": observation.response,
            "observation_text": observation.text,
        },
    }
finally:
    shutil.rmtree(profile_store_dir.parent, ignore_errors=True)

RESULT_PATH.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
