"""Example: configure an Oracle LLM profile for the ask_oracle tool.

Set `OPENAI_API_KEY` for the primary OpenAI profile and `LITELLM_API_KEY` for
the eval proxy Oracle profile before running live. Optional overrides:

    ASK_ORACLE_PRIMARY_MODEL=openai/gpt-5-nano
    ASK_ORACLE_MODEL=litellm_proxy/openai/gpt-5-mini
    ASK_ORACLE_BASE_URL=https://llm-proxy.eval.all-hands.dev
"""

import os
import shutil
import tempfile
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    LLMProfileStore,
    LocalConversation,
    OpenHandsAgentSettings,
)
from openhands.sdk.llm import llm_profile_store
from openhands.sdk.tool.builtins import AskOracleAction


primary_api_key = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
oracle_api_key = os.getenv("LITELLM_API_KEY") or os.getenv("LLM_API_KEY")

if not primary_api_key or not oracle_api_key:
    print(
        "Set OPENAI_API_KEY (or LLM_API_KEY) and LITELLM_API_KEY "
        "to run the live ask_oracle example."
    )
    print("EXAMPLE_COST: 0")
    raise SystemExit(0)

profile_store_dir = Path(tempfile.mkdtemp()) / "profiles"
setattr(llm_profile_store, "_DEFAULT_PROFILE_DIR", profile_store_dir)
store = LLMProfileStore()

primary_llm = LLM(
    model=os.getenv("ASK_ORACLE_PRIMARY_MODEL", "openai/gpt-5-nano"),
    api_key=SecretStr(primary_api_key),
    usage_id="ask-oracle-example-primary",
    max_output_tokens=1000,
    reasoning_effort="low",
)
oracle_llm = LLM(
    model=os.getenv("ASK_ORACLE_MODEL", "litellm_proxy/openai/gpt-5-mini"),
    api_key=SecretStr(oracle_api_key),
    base_url=os.getenv("ASK_ORACLE_BASE_URL", "https://llm-proxy.eval.all-hands.dev"),
    usage_id="ask-oracle-example-oracle",
    max_output_tokens=1000,
    reasoning_effort="low",
)

try:
    store.save("oracle", oracle_llm, include_secrets=True)
    settings = OpenHandsAgentSettings(llm=primary_llm, oracle_llm_profile="oracle")
    agent = settings.create_agent()
    conversation = LocalConversation(agent=agent, workspace=Path.cwd())
    conversation._ensure_agent_ready()

    print(f"Configured tools: {sorted(agent.tools_map)}")
    observation = conversation.execute_tool(
        "ask_oracle",
        AskOracleAction(
            question=(
                "In one sentence, recommend whether a feature flag should be stored "
                "as one nullable setting or as a separate boolean plus string."
            ),
            context="Prefer the simplest backwards-compatible SDK settings design.",
        ),
    )

    print("Oracle said:")
    print(observation.text)
    print("EXAMPLE_COST: 0")
finally:
    shutil.rmtree(profile_store_dir.parent, ignore_errors=True)
