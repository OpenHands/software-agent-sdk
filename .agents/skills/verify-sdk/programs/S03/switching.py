"""Change a running conversation's model: `switch_profile`, `switch_llm`, the
`switch_llm` tool and the `route_task_to_model` tool. Each model is a real
`LLM` answering from its own local endpoint, so the program can tell which
model served each step. Profiles go to the run's default store
(`$OH_PERSISTENCE_DIR/profiles`), as a user's would.

Usage: switching.py MODE
  profile        conversation.switch_profile, persistence, stats, missing profile
  llm-object     conversation.switch_llm, registry reuse, condensers, ask_agent
  tool           the switch_llm tool (sync run, or `tool-async` for arun)
  route          route_task_to_model with structured classes and errors
  route-direct   route_task_to_model with a direct prompt template
"""

import asyncio
import json
import sys
import uuid

from pydantic import SecretStr
from S02.fake_provider import FakeProvider
from sdk_verify import conversation_dir, emit, run_paths

from openhands.sdk import LLM, Agent, Conversation, LLMProfileStore, Tool
from openhands.sdk.event import AgentErrorEvent, ObservationEvent
from openhands.tools.terminal import TerminalTool


mode = sys.argv[1]
paths = run_paths()
WORKSPACE = str(paths["workspace"])
PRICE = {"input_cost_per_token": 0.001, "output_cost_per_token": 0.002}
out: dict = {"ok": True, "mode": mode}


def fake_llm(server: FakeProvider, usage_id: str, model: str, **kw) -> LLM:
    return LLM(
        model=model,
        base_url=server.url,
        api_key=SecretStr("sk-test"),
        usage_id=usage_id,
        num_retries=1,
        **{**PRICE, **kw},
    )


def observations(conversation) -> list[dict]:
    rows = []
    for e in conversation.state.events:
        if isinstance(e, ObservationEvent):
            text = "".join(getattr(c, "text", "") for c in e.observation.to_llm_content)
            rows.append(
                {
                    "tool": e.tool_name,
                    "error": e.observation.is_error,
                    "text": text[:200],
                }
            )
        elif isinstance(e, AgentErrorEvent):
            rows.append({"tool": e.tool_name, "error": True, "text": e.error[:200]})
    return rows


store = LLMProfileStore()

if mode == "profile":
    cid = uuid.uuid4()
    with (
        FakeProvider([{"finish": "from A"}]) as a,
        FakeProvider([{"finish": "from B"}]) as b,
    ):
        store.save(
            "model-b", fake_llm(b, "b", "openai/gpt-4.1-mini"), include_secrets=True
        )
        conversation = Conversation(
            agent=Agent(
                llm=fake_llm(a, "agent", "openai/gpt-4.1"),
                tools=[Tool(name=TerminalTool.name)],
            ),
            workspace=WORKSPACE,
            persistence_dir=str(paths["conversations"]),
            conversation_id=cid,
            visualizer=None,
        )
        conversation.send_message("First task.")
        conversation.run()
        conversation.switch_profile("model-b")
        conversation.send_message("Second task.")
        conversation.run()
        try:
            conversation.switch_profile("no-such-profile")
            missing = None
        except Exception as exc:
            missing = type(exc).__name__
        state_file = json.loads(
            (conversation_dir(str(cid)) / "base_state.json").read_text()
        )
        key_a = a.requests[0]["body"].get("prompt_cache_key") if a.requests else None
        key_b = b.requests[0]["body"].get("prompt_cache_key") if b.requests else None
        out |= {
            "requests": {"a": len(a.requests), "b": len(b.requests)},
            "active_model": conversation.agent.llm.model,
            "registry": sorted(conversation.llm_registry.list_usage_ids()),
            "stats": {
                k: round(v.accumulated_cost, 4)
                for k, v in conversation.conversation_stats.usage_to_metrics.items()
            },
            "persisted_model": state_file["agent"]["llm"]["model"],
            "missing_profile_error": missing,
            "model_after_missing": conversation.agent.llm.model,
            "prompt_cache_key_kept": key_a is not None and key_a == key_b,
            "prompt_cache_key_is_conversation_id": key_a == str(cid),
            "status": conversation.state.execution_status.value,
        }
        conversation.close()

elif mode == "llm-object":
    from openhands.sdk.context.condenser import LLMSummarizingCondenser

    with (
        FakeProvider([{"finish": "from A"}]) as a,
        FakeProvider(
            [{"finish": "from B"}, {"text": "B answers the side question"}]
        ) as b,
        FakeProvider([]) as c,
    ):
        agent_llm = fake_llm(a, "agent", "openai/gpt-4o-mini")
        shared_condenser = LLMSummarizingCondenser(
            llm=agent_llm.model_copy(update={"usage_id": "condenser"}),
            max_size=80,
            keep_first=2,
        )
        conversation = Conversation(
            agent=Agent(llm=agent_llm, tools=[], condenser=shared_condenser),
            workspace=WORKSPACE,
            visualizer=None,
        )
        conversation.send_message("First task.")
        conversation.run()
        store_before = sorted(store.list())
        conversation.switch_llm(fake_llm(b, "other", "openai/gpt-4.1-mini"))
        conversation.send_message("Second task.")
        conversation.run()
        side = conversation.ask_agent("Which model are you?")
        # Same usage_id again: the registry entry wins over the new object.
        conversation.switch_llm(fake_llm(c, "other", "openai/gpt-4.1-nano"))
        out |= {
            "requests": {
                "a": len(a.requests),
                "b": len(b.requests),
                "c": len(c.requests),
            },
            "profile_store_untouched": sorted(store.list()) == store_before,
            "side_answer": side,
            "model_after_reswitch": conversation.agent.llm.model,
            "condenser_model": conversation.agent.condenser.llm.model,
            "condenser_base_url_is_b": conversation.agent.condenser.llm.base_url
            == b.url,
            "registry": sorted(conversation.llm_registry.list_usage_ids()),
        }
        conversation.close()
        # A condenser with its own model and endpoint is left alone.
        independent = LLMSummarizingCondenser(
            llm=fake_llm(c, "condenser", "anthropic/claude-sonnet-4-5-20250929"),
            max_size=80,
            keep_first=2,
        )
        conversation = Conversation(
            agent=Agent(
                llm=fake_llm(a, "agent", "openai/gpt-4o-mini"),
                tools=[],
                condenser=independent,
            ),
            workspace=WORKSPACE,
            visualizer=None,
        )
        conversation.switch_llm(fake_llm(b, "other", "openai/gpt-4.1-mini"))
        out["independent_condenser_model"] = conversation.agent.condenser.llm.model
        conversation.close()

elif mode in ("tool", "tool-async"):
    with (
        FakeProvider(
            [
                {
                    "tool": "switch_llm",
                    "args": {"profile_name": "missing-profile", "reason": "try"},
                },
                {
                    "tool": "switch_llm",
                    "args": {"profile_name": "model-b", "reason": "better fit"},
                },
            ]
        ) as a,
        FakeProvider([{"finish": "finished on B"}]) as b,
    ):
        store.save(
            "model-b", fake_llm(b, "b", "openai/gpt-4.1-mini"), include_secrets=True
        )
        agent = Agent(
            llm=fake_llm(a, "agent", "openai/gpt-4o-mini"),
            tools=[Tool(name=TerminalTool.name)],
            include_default_tools=["FinishTool", "SwitchLLMTool"],
            filter_tools_regex="^(finish|terminal)$",
        )
        conversation = Conversation(agent=agent, workspace=WORKSPACE, visualizer=None)
        conversation.send_message("Switch to model-b, then finish.")
        if mode == "tool-async":
            asyncio.run(asyncio.wait_for(conversation.arun(), timeout=60))
        else:
            conversation.run()
        tools_sent = [
            t["function"]["name"] for t in a.requests[0]["body"].get("tools", [])
        ]
        switch_description = next(
            (
                t["function"]["description"]
                for t in a.requests[0]["body"].get("tools", [])
                if t["function"]["name"] == "switch_llm"
            ),
            "",
        )
        out |= {
            "status": conversation.state.execution_status.value,
            "active_model": conversation.agent.llm.model,
            "requests": {"a": len(a.requests), "b": len(b.requests)},
            "tools_sent": sorted(tools_sent),
            "description_lists_profile": "model-b" in switch_description,
            "observations": observations(conversation),
        }
        conversation.close()

elif mode in ("route", "route-direct"):
    from openhands.sdk import OpenHandsAgentSettings
    from openhands.sdk.llm.meta_profile_store import MetaProfile, MetaProfileClass

    direct = mode == "route-direct"
    classifier_reply = '{"model": "cheap", "reason": "simple"}' if direct else "2"
    route = {"tool": "route_task_to_model", "args": {}}
    with (
        FakeProvider([route, route, route]) as a,
        FakeProvider([{"text": classifier_reply}, {"text": "7"}]) as classifier,
        FakeProvider([{"finish": "done on cheap"}]) as cheap,
        FakeProvider([{"finish": "done on strong"}]) as strong,
    ):
        store.save(
            "classifier",
            fake_llm(classifier, "classifier", "openai/gpt-4o-mini"),
            include_secrets=True,
        )
        store.save(
            "cheap",
            fake_llm(cheap, "cheap", "openai/gpt-4.1-nano"),
            include_secrets=True,
        )
        store.save(
            "strong", fake_llm(strong, "strong", "openai/gpt-4.1"), include_secrets=True
        )
        if direct:
            meta = MetaProfile(
                classifier_model="classifier",
                prompt_template=(
                    "Pick a model.\n{{ model_table }}\nTask:\n{{ instance_text }}"
                ),
                model_table="- cheap: small tasks\n- strong: hard tasks",
            )
        else:
            meta = MetaProfile(
                classifier_model="classifier",
                classes=[
                    MetaProfileClass(description="small edits", model="cheap"),
                    MetaProfileClass(description="hard debugging", model="strong"),
                ],
            )
        settings = OpenHandsAgentSettings(
            llm=fake_llm(a, "agent", "openai/gpt-4o-mini"),
            tools=[],
            enable_classify_and_switch_llm_tool=True,
            active_meta_profile="router",
            meta_profile=meta,
        )
        agent = settings.create_agent()
        conversation = Conversation(agent=agent, workspace=WORKSPACE, visualizer=None)
        conversation.send_message(
            "Debug this hard failure, routing to the right model first."
        )
        conversation.run()
        classifier_prompt = (
            json.dumps(classifier.requests[0]["body"]) if classifier.requests else ""
        )
        out |= {
            "status": conversation.state.execution_status.value,
            "active_model": conversation.agent.llm.model,
            "requests": {
                "agent": len(a.requests),
                "classifier": len(classifier.requests),
                "cheap": len(cheap.requests),
                "strong": len(strong.requests),
            },
            "classifier_saw_task": "hard failure" in classifier_prompt,
            "stats": sorted(conversation.conversation_stats.usage_to_metrics),
            "observations": observations(conversation),
        }
        conversation.close()
        # Errors come back as observations: a class index out of range, and a
        # meta-profile whose classifier profile does not exist.
        with FakeProvider([route, {"finish": "gave up"}]) as a2:
            settings = OpenHandsAgentSettings(
                llm=fake_llm(a2, "agent", "openai/gpt-4o-mini"),
                tools=[],
                enable_classify_and_switch_llm_tool=True,
                active_meta_profile="router",
                meta_profile=meta,
            )
            conversation = Conversation(
                agent=settings.create_agent(), workspace=WORKSPACE, visualizer=None
            )
            conversation.send_message("Route again.")
            conversation.run()
            out["bad_class"] = (
                observations(conversation)[0] if observations(conversation) else None
            )
            conversation.close()
        with FakeProvider([route, {"finish": "gave up"}]) as a3:
            settings = OpenHandsAgentSettings(
                llm=fake_llm(a3, "agent", "openai/gpt-4o-mini"),
                tools=[],
                enable_classify_and_switch_llm_tool=True,
                active_meta_profile="router",
                meta_profile=meta.model_copy(
                    update={"classifier_model": "no-classifier"}
                ),
            )
            conversation = Conversation(
                agent=settings.create_agent(), workspace=WORKSPACE, visualizer=None
            )
            conversation.send_message("Route once more.")
            conversation.run()
            out["missing_classifier"] = (
                observations(conversation)[0] if observations(conversation) else None
            )
            conversation.close()

else:
    raise SystemExit(f"unknown mode {mode}")

emit(out)
