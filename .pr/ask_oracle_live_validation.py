"""Run live end-to-end validation for the ask_oracle tool.

This script drives a normal agent loop, verifies that the agent emitted an
``AskOracleAction``, and independently compares the tool observation with the
Oracle profile's completion log.
"""

import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, LocalConversation, Tool
from openhands.sdk.conversation.response_utils import get_agent_final_response
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.tools.ask_oracle import (
    ORACLE_PROFILE_NAME,
    AskOracleAction,
    AskOracleObservation,
)


RESULT_PATH = Path(__file__).with_name("ask_oracle_live_validation.json")
PRIMARY_MODEL = os.getenv("ASK_ORACLE_PRIMARY_MODEL", "openai/gpt-5.1")
ORACLE_MODEL = os.getenv("ASK_ORACLE_MODEL", "openai/gpt-5-mini")
BASE_URL = os.getenv("LLM_BASE_URL", "https://llm-proxy.eval.all-hands.dev")


def _text_from_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("text")
        )
    return ""


def _read_logged_response(log_dir: Path) -> str:
    logs = sorted(log_dir.glob("*.json"), key=lambda path: path.stat().st_mtime)
    if not logs:
        return ""
    response = json.loads(logs[-1].read_text()).get("response") or {}

    for choice in response.get("choices", []):
        text = _text_from_content(choice.get("message", {}).get("content"))
        if text.strip():
            return text.strip()

    for item in response.get("output", []):
        if item.get("type") == "message":
            text = _text_from_content(item.get("content"))
            if text.strip():
                return text.strip()
    return ""


started_at = datetime.now(UTC).isoformat()
api_key = os.environ.get("LLM_API_KEY") or os.environ["LITELLM_API_KEY"]
git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()

with tempfile.TemporaryDirectory() as temp_dir:
    temp_path = Path(temp_dir)
    profile_store_dir = temp_path / "profiles"
    oracle_log_dir = temp_path / "oracle-logs"
    oracle_log_dir.mkdir(parents=True)

    primary_llm = LLM(
        model=PRIMARY_MODEL,
        api_key=SecretStr(api_key),
        base_url=BASE_URL,
        usage_id="primary",
    )
    oracle_llm = LLM(
        model=ORACLE_MODEL,
        api_key=SecretStr(api_key),
        base_url=BASE_URL,
        usage_id="oracle",
        log_completions=True,
        log_completions_folder=str(oracle_log_dir),
    )
    store = LLMProfileStore(profile_store_dir)
    store.save(ORACLE_PROFILE_NAME, oracle_llm, include_secrets=True)

    conversation = LocalConversation(
        agent=Agent(llm=primary_llm, tools=[Tool(name="ask_oracle")]),
        workspace=Path.cwd(),
        profile_store_dir=profile_store_dir,
    )
    initial_primary_model = conversation.agent.llm.model
    conversation.send_message(
        "Call ask_oracle exactly once. Ask the Oracle to include the token "
        "ORACLE_LIVE_OK in its recommendation. Then answer me with a concise "
        "summary that includes that token."
    )
    conversation.run()

    events = list(conversation.state.events)
    oracle_actions = [
        event
        for event in events
        if isinstance(event, ActionEvent) and isinstance(event.action, AskOracleAction)
    ]
    oracle_observations = [
        event
        for event in events
        if isinstance(event, ObservationEvent)
        and isinstance(event.observation, AskOracleObservation)
    ]
    observation_text = (
        oracle_observations[0].observation.text if oracle_observations else ""
    )
    oracle_logged_response = _read_logged_response(oracle_log_dir)
    final_answer = get_agent_final_response(events)
    active_primary_model = conversation.agent.llm.model
    combined_metrics = conversation.state.stats.get_combined_metrics()

    checks = {
        "agent_called_ask_oracle": len(oracle_actions) == 1,
        "ask_oracle_observation_in_loop": len(oracle_observations) == 1,
        "observation_is_success": (
            len(oracle_observations) == 1
            and not oracle_observations[0].observation.is_error
        ),
        "oracle_log_matches_observation": (
            bool(oracle_logged_response) and oracle_logged_response == observation_text
        ),
        "oracle_token_reached_observation": "ORACLE_LIVE_OK" in observation_text,
        "oracle_token_reached_final_answer": "ORACLE_LIVE_OK" in final_answer,
        "primary_model_unchanged": active_primary_model == initial_primary_model,
        "conversation_finished": conversation.state.execution_status.value
        == "finished",
    }

    result = {
        "validated_git_sha": git_sha,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "primary_model": PRIMARY_MODEL,
        "oracle_profile": {
            "name": ORACLE_PROFILE_NAME,
            "model": ORACLE_MODEL,
            "base_url": BASE_URL,
        },
        "checks": checks,
        "oracle_question": (
            oracle_actions[0].action.question if oracle_actions else None
        ),
        "oracle_observation": observation_text,
        "oracle_logged_response": oracle_logged_response,
        "final_agent_answer": final_answer,
        "accumulated_cost": combined_metrics.accumulated_cost,
        "passed": all(checks.values()),
    }
    conversation.close()

RESULT_PATH.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
if not result["passed"]:
    raise SystemExit("Live ask_oracle validation failed")
