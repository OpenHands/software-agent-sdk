"""Consult the Oracle end-to-end with the ask_oracle tool.

The Oracle is a saved LLM profile resolved by convention under the name
``oracle``. This example wires two profiles — the agent's primary model and a
separate ``oracle`` model — adds ``Tool(name="ask_oracle")`` to the agent, then
drives a normal conversation: the agent decides to call ``ask_oracle``, the tool
consults the ``oracle`` profile, and the agent uses the Oracle's answer to reply.

Usage:
    LLM_API_KEY=... LLM_BASE_URL=https://llm-proxy.app.all-hands.dev \
        uv run python examples/01_standalone_sdk/55_ask_oracle_tool/main.py
"""

import os

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, LocalConversation, Tool
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.tools.ask_oracle import ORACLE_PROFILE_NAME


PRIMARY_PROFILE = "example-primary"
DEFAULT_BASE_URL = "https://llm-proxy.app.all-hands.dev"
# The agent's primary model (follows the standard LLM_MODEL env like other
# examples). The Oracle defaults to the same model; override ASK_ORACLE_MODEL to
# point the "oracle" profile at a different/stronger model.
PRIMARY_MODEL = os.getenv("ASK_ORACLE_PRIMARY_MODEL") or os.getenv(
    "LLM_MODEL", "openai/gpt-5.5"
)
ORACLE_MODEL = os.getenv("ASK_ORACLE_MODEL", PRIMARY_MODEL)

api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."
base_url = os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL)

store = LLMProfileStore()
store.save(
    PRIMARY_PROFILE,
    LLM(
        model=PRIMARY_MODEL,
        api_key=SecretStr(api_key),
        base_url=base_url,
        usage_id="primary",
    ),
    include_secrets=True,
)
# The Oracle model is saved under the conventional profile name "oracle".
store.save(
    ORACLE_PROFILE_NAME,
    LLM(
        model=ORACLE_MODEL,
        api_key=SecretStr(api_key),
        base_url=base_url,
        usage_id="oracle",
    ),
    include_secrets=True,
)

try:
    agent = Agent(llm=store.load(PRIMARY_PROFILE), tools=[Tool(name="ask_oracle")])
    conversation = LocalConversation(agent=agent, workspace=os.getcwd())

    print(f"Primary model: {conversation.agent.llm.model}")
    print(f"Oracle model:  {ORACLE_MODEL}")
    conversation.send_message(
        "Call the oracle to ask it for its opinion on the weather today, "
        "then just tell me in two words how it's like."
    )
    conversation.run()

    combined = conversation.state.stats.get_combined_metrics()
    print(f"Total cost: ${combined.accumulated_cost:.6f}")
    print(f"EXAMPLE_COST: {combined.accumulated_cost}")
finally:
    store.delete(PRIMARY_PROFILE)
    store.delete(ORACLE_PROFILE_NAME)
