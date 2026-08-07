"""End-to-end live validation for the ask_oracle tool.

Unlike a unit-level ``conversation.execute_tool()`` call, this drives the real
agent loop:

  1. Wire two LLM profiles: the agent's ``primary`` model and an ``oracle`` model
     (the latter saved under the conventional name "oracle", with
     ``log_completions`` enabled so we can independently read what it returned).
  2. Start a normal conversation and ask the agent to consult the oracle and
     summarize the weather in two words. Let it run to completion.
  3. Confirm from the conversation events that the agent actually emitted an
     ``ask_oracle`` action and received an ``AskOracleObservation`` in-loop.
  4. Independently confirm the response *came from the oracle profile* by reading
     the oracle LLM's own completion log (telemetry), not just the observation.

Run:
    OPENHANDS_SUPPRESS_BANNER=1 \
    LLM_API_KEY=... LLM_BASE_URL=https://llm-proxy.eval.all-hands.dev \
    ASK_ORACLE_PRIMARY_MODEL=litellm_proxy/openai/gpt-5.1 \
    ASK_ORACLE_MODEL=litellm_proxy/openai/gpt-5-mini \
    uv run python .pr/ask_oracle_live_validation.py
"""

import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, LocalConversation, Tool
from openhands.sdk.conversation.response_utils import get_agent_final_response
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.sdk.llm import llm_profile_store
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.tools.ask_oracle import (
    ORACLE_PROFILE_NAME,
    AskOracleAction,
    AskOracleObservation,
)


RESULT_PATH = Path(__file__).with_name("ask_oracle_live_validation.json")
PRIMARY_MODEL = os.getenv("ASK_ORACLE_PRIMARY_MODEL", "litellm_proxy/openai/gpt-5.1")
ORACLE_MODEL = os.getenv("ASK_ORACLE_MODEL", "litellm_proxy/openai/gpt-5-mini")
BASE_URL = os.getenv("LLM_BASE_URL", "https://llm-proxy.eval.all-hands.dev")


def _text_from_content(content: object) -> str:
    """Pull text out of a message ``content`` that may be a str or a list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("text")
        )
    return ""


def read_oracle_logged_response(log_dir: Path) -> str:
    """Read the oracle's response text straight from its completion log.

    This is an independent channel from the AskOracleObservation: it proves the
    text the agent acted on actually came from the oracle profile's LLM call.
    Handles both Chat Completions (``choices[].message``) and Responses API
    (``output[].content[]``) log shapes.
    """
    logs = sorted(log_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    if not logs:
        return ""
    response = json.loads(logs[-1].read_text()).get("response") or {}

    # Chat Completions shape.
    for choice in response.get("choices", []):
        text = _text_from_content(choice.get("message", {}).get("content"))
        if text.strip():
            return text.strip()

    # Responses API shape.
    for item in response.get("output", []):
        if item.get("type") == "message":
            text = _text_from_content(item.get("content"))
            if text.strip():
                return text.strip()
    return ""


started_at = datetime.now(UTC).isoformat()
api_key = os.environ.get("LLM_API_KEY") or os.environ["LITELLM_API_KEY"]
profile_store_dir = Path(tempfile.mkdtemp()) / "profiles"
oracle_log_dir = Path(tempfile.mkdtemp()) / "oracle-logs"
oracle_log_dir.mkdir(parents=True, exist_ok=True)
setattr(llm_profile_store, "_DEFAULT_PROFILE_DIR", profile_store_dir)

try:
    primary_llm = LLM(
        model=PRIMARY_MODEL,
        api_key=SecretStr(api_key),
        base_url=BASE_URL,
        usage_id="primary",
    )
    # The oracle profile logs its completions so we can read its response back.
    oracle_llm = LLM(
        model=ORACLE_MODEL,
        api_key=SecretStr(api_key),
        base_url=BASE_URL,
        usage_id="oracle",
        log_completions=True,
        log_completions_folder=str(oracle_log_dir),
    )

    store = LLMProfileStore()
    store.save(ORACLE_PROFILE_NAME, oracle_llm, include_secrets=True)

    # End-to-end: the agent decides to call ask_oracle, which consults the
    # "oracle" profile, then answers using the Oracle's response.
    agent = Agent(llm=primary_llm, tools=[Tool(name="ask_oracle")])
    conversation = LocalConversation(agent=agent, workspace=Path.cwd())
    conversation.send_message(
        "Call the oracle to ask it for its opinion on the weather today, "
        "then just tell me in two words how it's like."
    )
    conversation.run()

    events = list(conversation.state.events)
    oracle_actions = [
        e
        for e in events
        if isinstance(e, ActionEvent) and isinstance(e.action, AskOracleAction)
    ]
    oracle_observations = [
        e
        for e in events
        if isinstance(e, ObservationEvent)
        and isinstance(e.observation, AskOracleObservation)
    ]
    final_answer = get_agent_final_response(events)
    oracle_logged_response = read_oracle_logged_response(oracle_log_dir)

    asked_question = oracle_actions[0].action.question if oracle_actions else None
    observation_text = (
        oracle_observations[0].observation.text if oracle_observations else None
    )

    combined = conversation.state.stats.get_combined_metrics()

    result = {
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "issue": "https://github.com/OpenHands/software-agent-sdk/issues/3672",
        "scenario": (
            "Agent loop: primary model calls ask_oracle, which consults the "
            "'oracle' profile; the agent then answers in two words."
        ),
        "primary_profile": {"model": PRIMARY_MODEL, "usage_id": "primary"},
        "oracle_profile": {
            "profile_name": ORACLE_PROFILE_NAME,
            "model": ORACLE_MODEL,
            "base_url": BASE_URL,
            "usage_id": "oracle",
        },
        "end_to_end": {
            "agent_called_ask_oracle": bool(oracle_actions),
            "oracle_question_asked_by_agent": asked_question,
            "ask_oracle_observation_in_loop": bool(oracle_observations),
            "observation_is_error": (
                oracle_observations[0].observation.is_error
                if oracle_observations
                else None
            ),
            "observation_text": observation_text,
            "oracle_logged_response": oracle_logged_response,
            "oracle_logged_response_matches_observation": (
                bool(oracle_logged_response)
                and oracle_logged_response == observation_text
            ),
            "final_agent_answer": final_answer,
            "conversation_finished": conversation.state.execution_status.value,
        },
        "accumulated_cost": combined.accumulated_cost,
    }
finally:
    shutil.rmtree(profile_store_dir.parent, ignore_errors=True)
    shutil.rmtree(oracle_log_dir.parent, ignore_errors=True)

RESULT_PATH.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
