import json
import os
import pathlib
import time
from datetime import datetime

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation, Event, LLMConvertibleEvent
from openhands.sdk.context import AgentContext
from openhands.sdk.logger import get_logger
from openhands.sdk.tool import Tool
from openhands.tools.terminal import TerminalTool


logger = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now().isoformat()


def _extract_request_datetime(messages: list[dict]) -> str | None:
    for m in messages:
        if m.get("role") != "system":
            continue
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not (isinstance(item, dict) and item.get("type") == "text"):
                continue
            txt = item.get("text", "")
            if "<CURRENT_DATETIME>" not in txt:
                continue
            for line in txt.splitlines():
                if "The current date and time is:" in line:
                    return line.split("The current date and time is:", 1)[1].strip()
    return None


def main() -> int:
    run_id = time.strftime("%Y%m%d-%H%M%S")
    out_dir = pathlib.Path(".pr") / f"prompt_cache_datetime_{run_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    api_key = os.getenv("LITELLM_API_KEY") or os.getenv("LLM_API_KEY")
    assert api_key, "Set LITELLM_API_KEY (preferred) or LLM_API_KEY"

    base_url = os.getenv("LLM_BASE_URL", "https://llm-proxy.eval.all-hands.dev")
    model = os.getenv(
        "LLM_MODEL",
        "litellm_proxy/anthropic/claude-3-5-haiku-20241022",
    )

    llm = LLM(
        usage_id="prompt-cache-datetime",
        model=model,
        base_url=base_url,
        api_key=SecretStr(api_key),
        caching_prompt=True,
        log_completions=True,
        log_completions_folder=str(out_dir / "telemetry"),
    )

    request_snapshots: list[dict] = []

    def cb(event: Event):
        if isinstance(event, LLMConvertibleEvent):
            msg = event.to_llm_message()
            if msg.role == "system":
                logger.info(
                    "System message emitted (%d content blocks)", len(msg.content)
                )

    def _run_turn(convo: Conversation, user_text: str, turn_id: str):
        convo.send_message(user_text)
        llm_messages = [
            e.to_llm_message()
            for e in convo.state.events
            if isinstance(e, LLMConvertibleEvent)
        ]
        formatted = llm.format_messages_for_llm(llm_messages)
        request_dt = _extract_request_datetime(formatted)

        before = llm.metrics.model_dump()
        convo.run()
        after = llm.metrics.model_dump()

        request_snapshots.append(
            {
                "turn_id": turn_id,
                "user": user_text,
                "request_datetime": request_dt,
                "metrics_before": before,
                "metrics_after": after,
            }
        )

    # Conversation 1
    agent1 = Agent(
        llm=llm,
        tools=[Tool(name=TerminalTool.name)],
        agent_context=AgentContext(current_datetime=_now_iso()),
    )
    convo1 = Conversation(agent=agent1, workspace=os.getcwd(), callbacks=[cb])

    _run_turn(convo1, "Reply with exactly: OK", "c1_t1")
    _run_turn(convo1, "Reply with exactly: OK2", "c1_t2")
    _run_turn(convo1, "Reply with exactly: OK3", "c1_t3")

    # Conversation 2
    agent2 = Agent(
        llm=llm,
        tools=[Tool(name=TerminalTool.name)],
        agent_context=AgentContext(current_datetime=_now_iso()),
    )
    convo2 = Conversation(agent=agent2, workspace=os.getcwd(), callbacks=[cb])

    _run_turn(convo2, "Reply with exactly: HELLO", "c2_t1")
    _run_turn(convo2, "Reply with exactly: HELLO2", "c2_t2")

    # Summarize per-turn deltas
    summary = []
    for snap in request_snapshots:
        b = snap["metrics_before"]
        a = snap["metrics_after"]
        summary.append(
            {
                "turn_id": snap["turn_id"],
                "request_datetime": snap["request_datetime"],
                "cache_read_tokens_delta": a.get("cache_read_tokens", 0)
                - b.get("cache_read_tokens", 0),
                "cache_write_tokens_delta": a.get("cache_write_tokens", 0)
                - b.get("cache_write_tokens", 0),
                "prompt_tokens_delta": a.get("prompt_tokens", 0)
                - b.get("prompt_tokens", 0),
                "completion_tokens_delta": a.get("completion_tokens", 0)
                - b.get("completion_tokens", 0),
                "accumulated_cost_delta": a.get("accumulated_cost", 0.0)
                - b.get("accumulated_cost", 0.0),
            }
        )

    (out_dir / "request_snapshots.json").write_text(
        json.dumps(request_snapshots, indent=2), encoding="utf-8"
    )
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    print("Wrote logs to", out_dir)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
