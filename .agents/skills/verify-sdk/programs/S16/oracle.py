"""Ask the Oracle through a scripted agent, the way example 58 wires it: a
saved LLM profile named `oracle` in the conversation's profile store, and
`Tool(name="ask_oracle")` on the agent.

Usage: oracle.py answer|empty|missing|params

- answer: the `oracle` profile points at a local OpenAI-compatible endpoint
  that answers "Use one typed setting."; the agent asks with context.
- empty: the endpoint answers with an empty string.
- missing: the profile store has no `oracle` profile.
- params: `Tool(name="ask_oracle", params={...})`.

Every mode but params also calls the executor without a conversation.
"""

import sys

from pydantic import SecretStr
from S14.toolkit import converse, tool_results
from S16 import fake_openai
from sdk_verify import emit, run_paths

from openhands.sdk import LLM, LocalConversation, Tool
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.tools.ask_oracle import ORACLE_PROFILE_NAME, AskOracleTool


mode = sys.argv[1]
store_dir = (
    run_paths()["run"] / "profiles" / mode
)  # one store per mode: the run is shared
store_dir.mkdir(parents=True, exist_ok=True)
if mode in ("answer", "empty"):
    base_url = fake_openai.serve("Use one typed setting." if mode == "answer" else "")
    LLMProfileStore(store_dir).save(
        ORACLE_PROFILE_NAME,
        LLM(
            model="openai/oracle-model",
            api_key=SecretStr("local"),
            base_url=base_url,
            usage_id="oracle",
            num_retries=0,
        ),
        include_secrets=True,
    )

params = {"model": "x"} if mode == "params" else None
question = {
    "question": "Should I add one setting or two?",
    "context": "The tool needs a profile name.",
}
result: dict = {"ok": True, "mode": mode}
try:
    conversation = converse(
        [Tool(name=AskOracleTool.name, params=params)],
        [{"tool": "ask_oracle", "args": question}, {"finish": "done"}],
        conversation_kwargs={"profile_store_dir": str(store_dir)},
        conversation_class=LocalConversation,
    )
except Exception as exc:  # what a program sees when the spec is rejected
    result["error"] = f"{type(exc).__name__}: {exc}"[:400]
    emit(result)
    raise SystemExit(0)

rows = tool_results(conversation)
oracle = rows[0]
usage = conversation.conversation_stats.usage_to_metrics
description = conversation.agent.tools_map["ask_oracle"].description
result.update(
    {
        "status": conversation.state.execution_status.value,
        "is_error": oracle["is_error"],
        "text": oracle["text"][:400],
        "agent_model": conversation.agent.llm.model,
        "usage_ids": sorted(usage),
        "oracle_requests": len(fake_openai.requests),
        "oracle_saw_question": any(
            "one setting or two" in str(r) for r in fake_openai.requests
        ),
        "oracle_saw_context": any(
            "needs a profile name" in str(r) for r in fake_openai.requests
        ),
        "oracle_got_tools": any("tools" in r for r in fake_openai.requests),
        "description_mentions_second_opinion": "second opinion" in description.lower(),
    }
)
# The same tool called outside any conversation (as a direct executor call).
from openhands.tools.ask_oracle.definition import AskOracleAction  # noqa: E402
from openhands.tools.ask_oracle.impl import AskOracleExecutor  # noqa: E402


alone = AskOracleExecutor()(AskOracleAction(**question))
result["no_conversation_is_error"] = alone.is_error
result["no_conversation_text"] = alone.text
conversation.close()
emit(result)
