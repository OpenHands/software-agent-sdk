"""Which model a sub-agent's rolled-up usage is attributed to.

Two persisted parents with the task tool each delegate once, on priced
`LLM`s that answer from a local endpoint (so the SDK computes real costs and
records each call's model):
1. `same`: the parent (`openai/gpt-4o-mini`) delegates to `kin`, whose agent
   reuses the parent's LLM copy (same model).
2. `mixed`: the parent (`openai/gpt-4o-mini`) delegates to `outsider`, whose
   agent runs on `openai/gpt-4o`.
For each parent the program reports the `task:<id>` entry the SDK stores in
the parent's `conversation_stats.usage_to_metrics` and the parent's
`get_combined_metrics()`: model name, the accumulated token usage's model,
cost and call count.
"""

from pydantic import SecretStr
from S02.fake_provider import FakeProvider
from sdk_verify import emit, run_paths

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.subagent import register_agent
from openhands.tools.task import TaskToolSet


paths = run_paths()
PRICE = {"input_cost_per_token": 0.001, "output_cost_per_token": 0.002}  # 0.14 per call


def priced(server: FakeProvider, model: str, usage_id: str) -> LLM:
    return LLM(
        model=model,
        base_url=server.url,
        api_key=SecretStr("sk-test"),
        usage_id=usage_id,
        num_retries=1,
        **PRICE,
    )


SERVER: dict[str, FakeProvider] = {}

register_agent(
    "kin",
    lambda llm: Agent(llm=llm, tools=[]),
    "Answers on the parent's model.",
)
register_agent(
    "outsider",
    lambda _llm: Agent(
        llm=priced(SERVER["now"], "openai/gpt-4o", "outsider"), tools=[]
    ),
    "Answers on another model.",
)


def view(metrics) -> dict:
    usage = metrics.accumulated_token_usage
    return {
        "model_name": metrics.model_name,
        "usage_model": usage.model if usage is not None else None,
        "cost": round(metrics.accumulated_cost, 6),
        "calls": len(metrics.costs),
        "call_models": sorted({c.model for c in metrics.costs}),
    }


def run_parent(subagent: str) -> dict:
    script = [
        {"tool": "task", "args": {"prompt": "Help.", "subagent_type": subagent}},
        {"finish": "child done"},  # the sub-agent's only call
        {"finish": "parent done"},
    ]
    with FakeProvider(script) as server:
        SERVER["now"] = server
        conversation = Conversation(
            agent=Agent(
                llm=priced(server, "openai/gpt-4o-mini", "agent"),
                tools=[Tool(name=TaskToolSet.name)],
            ),
            workspace=str(paths["workspace"]),
            persistence_dir=str(paths["conversations"]),
            visualizer=None,
        )
        conversation.send_message("Delegate once.")
        conversation.run()
        stats = conversation.conversation_stats
        tasks = sorted(k for k in stats.usage_to_metrics if k.startswith("task:"))
        out = {
            "status": conversation.state.execution_status.value,
            "usage_ids": sorted(stats.usage_to_metrics),
            "task": view(stats.usage_to_metrics[tasks[0]]) if tasks else None,
            "combined": view(stats.get_combined_metrics()),
        }
        conversation.close()
        return out


emit({"ok": True, "same": run_parent("kin"), "mixed": run_parent("outsider")})
