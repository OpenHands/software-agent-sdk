from pydantic import SecretStr

from openhands.sdk import Agent
from openhands.sdk.agent import AgentBase
from openhands.sdk.llm import LLM


def test_resolve_diff_ignores_litellm_extra_body_diffs():
    tools = []
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    original_agent = Agent(llm=llm, tools=tools)

    serialized = original_agent.model_dump_json()
    deserialized_agent = AgentBase.model_validate_json(serialized)

    runtime_agent = Agent(
        llm=LLM(
            model="gpt-4o-mini",
            api_key=SecretStr("test-key"),
            usage_id="test-llm",
        ),
        tools=tools,
    )

    injected = deserialized_agent.model_copy(
        update={
            "llm": deserialized_agent.llm.model_copy(
                update={
                    "litellm_extra_body": {
                        "metadata": {
                            "session_id": "sess-xyz",
                            "tags": ["app:openhands", "model:gpt-4o-mini"],
                            "trace_version": "9.9.9",
                        }
                    }
                }
            )
        }
    )

    resolved = runtime_agent.resolve_diff_from_deserialized(injected)
    assert resolved.llm.litellm_extra_body == runtime_agent.llm.litellm_extra_body
