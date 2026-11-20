"""A conversation whose agent LLM is a `RouterLLM`: route status, manual and
automatic selection, routes added from saved profiles, and what `switch_llm`
and `switch_profile` do to a router. Each route is a real `LLM` answering from
its own local endpoint (`S02/fake_provider.py`), so the program can tell which
route served each turn. Profiles go to the run's default store
(`$OH_PERSISTENCE_DIR/profiles`), as a user's would.

Usage: model_router.py MODE
  selection         status, switch_routed_llm / resume_auto_llm_routing in agent
                    runs, refused selections, persistence and resume
  add-route         add_model_router_route: wrap a plain LLM, extend a router,
                    run on the added route, refused routes
  switch-replaces   switch_profile / switch_llm on a router conversation, and the
                    router's own copy-returning switch methods
  before-first-run  a route added and selected before the first message: the
                    run's usage in conversation_stats
"""

import json
import sys
import uuid

from pydantic import SecretStr, ValidationError
from S02.fake_provider import FakeProvider
from sdk_verify import conversation_dir, emit, run_paths

from openhands.sdk import LLM, Agent, Conversation, LLMProfileStore
from openhands.sdk.llm import (
    ModelRouterSelection,
    ModelRouterStatus,
    ModelRouterUpdateRequest,
    RouterLLM,
)
from openhands.sdk.llm.router import MultimodalRouter


mode = sys.argv[1]
paths = run_paths()
WORKSPACE = str(paths["workspace"])
PRICE = {"input_cost_per_token": 0.001, "output_cost_per_token": 0.002}  # 0.14 per call
out: dict = {"ok": True, "mode": mode}


def fake_llm(server: FakeProvider, usage_id: str, model: str) -> LLM:
    return LLM(
        model=model,
        base_url=server.url,
        api_key=SecretStr("sk-test"),
        usage_id=usage_id,
        num_retries=1,
        **PRICE,
    )


def attempt(fn):
    try:
        return fn(), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"[:200]


def status_of(conversation) -> dict:
    llm = conversation.agent.llm
    if not isinstance(llm, RouterLLM):
        return ModelRouterStatus(enabled=False).model_dump(mode="json")
    return llm.status().model_dump(mode="json")


def turn(conversation, text: str) -> None:
    conversation.send_message(text)
    conversation.run()


def costs(conversation) -> dict:
    return {
        k: round(v.accumulated_cost, 4)
        for k, v in conversation.conversation_stats.usage_to_metrics.items()
    }


store = LLMProfileStore()

if mode == "selection":
    cid = uuid.uuid4()
    with (
        FakeProvider([{"finish": "A1"}, {"finish": "A2"}, {"finish": "A3"}]) as a,
        FakeProvider([{"finish": "B1"}, {"finish": "B2"}]) as b,
    ):
        router = RouterLLM(
            usage_id="router",
            llms_for_routing={
                "first": fake_llm(a, "route-first", "openai/gpt-4.1"),
                "second": fake_llm(b, "route-second", "openai/gpt-4.1-mini"),
            },
        )
        conversation = Conversation(
            agent=Agent(llm=router, tools=[]),
            workspace=WORKSPACE,
            persistence_dir=str(paths["conversations"]),
            conversation_id=cid,
            visualizer=None,
            delete_on_close=False,
        )
        out["initial"] = status_of(conversation)
        turn(conversation, "Task one.")
        out["after_auto_turn"] = status_of(conversation)
        conversation.switch_routed_llm("second")
        out["after_manual"] = status_of(conversation)
        out["router_kept"] = type(conversation.agent.llm).__name__
        out["state_agent_follows"] = (
            conversation.state.agent.llm is conversation.agent.llm
        )
        turn(conversation, "Task two.")
        out["after_manual_turn"] = status_of(conversation)
        persisted = json.loads(
            (conversation_dir(str(cid)) / "base_state.json").read_text()
        )["agent"]["llm"]
        out["persisted"] = {
            "router_name": persisted.get("router_name"),
            "selection": persisted.get("selection"),
            "routes": sorted(persisted.get("llms_for_routing", {})),
        }
        _, out["unknown_route_error"] = attempt(
            lambda: conversation.switch_routed_llm("missing")
        )
        out["after_unknown_route"] = status_of(conversation)["selection"]
        conversation.resume_auto_llm_routing()
        turn(conversation, "Task three.")
        out["after_resume_turn"] = status_of(conversation)
        out["requests"] = {"first": len(a.requests), "second": len(b.requests)}
        out["costs"] = costs(conversation)
        conversation.update_model_router_selection(
            ModelRouterSelection(mode="manual", manual_route_name="second")
        )
        out["persisted_before_resume"] = json.loads(
            (conversation_dir(str(cid)) / "base_state.json").read_text()
        )["agent"]["llm"].get("selection")
        conversation.close()
        # Resume the persisted conversation with a fresh, auto-routing router:
        # on resume the program's agent (and its LLM) wins over the stored one.
        fresh = RouterLLM(
            usage_id="router",
            llms_for_routing={
                "first": fake_llm(a, "route-first", "openai/gpt-4.1"),
                "second": fake_llm(b, "route-second", "openai/gpt-4.1-mini"),
            },
        )
        resumed = Conversation(
            agent=Agent(llm=fresh, tools=[]),
            workspace=WORKSPACE,
            persistence_dir=str(paths["conversations"]),
            conversation_id=cid,
            visualizer=None,
            delete_on_close=False,
        )
        out["resumed"] = status_of(resumed)
        out["resumed_type"] = type(resumed.agent.llm).__name__
        resumed.close()
    # Selection shapes the model refuses, and a plain-LLM conversation.
    _, out["manual_without_route"] = attempt(
        lambda: ModelRouterSelection(mode="manual")
    )
    _, out["auto_with_route"] = attempt(
        lambda: ModelRouterSelection(mode="auto", manual_route_name="first")
    )
    _, out["update_request_without_selection"] = attempt(
        lambda: ModelRouterUpdateRequest.model_validate({})
    )
    out["validation_error_type"] = ValidationError.__name__
    plain = Conversation(
        agent=Agent(llm=LLM(model="openai/gpt-4.1", usage_id="plain"), tools=[]),
        workspace=WORKSPACE,
        visualizer=None,
    )
    _, out["plain_switch_error"] = attempt(lambda: plain.switch_routed_llm("first"))
    _, out["plain_resume_error"] = attempt(plain.resume_auto_llm_routing)
    out["plain_status"] = status_of(plain)
    plain.close()

elif mode == "add-route":
    with (
        FakeProvider([{"finish": "A1"}, {"finish": "A2"}]) as a,
        FakeProvider([{"finish": "R1"}]) as r,
        FakeProvider([]) as late,
    ):
        store.save(
            "qa-reviewer",
            fake_llm(r, "reviewer", "openai/gpt-4.1-mini"),
            include_secrets=True,
        )
        conversation = Conversation(
            agent=Agent(llm=fake_llm(a, "agent", "openai/gpt-4.1"), tools=[]),
            workspace=WORKSPACE,
            visualizer=None,
        )
        turn(conversation, "Task one.")
        returned = conversation.add_model_router_route(
            "qa-reviewer", route_name="reviewer"
        )
        llm = conversation.agent.llm
        out["returned"] = returned.model_dump(mode="json")
        out["agent_llm_type"] = type(llm).__name__
        out["route_usage_ids"] = {
            name: route.usage_id for name, route in llm.llms_for_routing.items()
        }
        out["registry"] = sorted(conversation.llm_registry.list_usage_ids())
        out["registry_holds_route"] = (
            conversation.llm_registry.get("profile:qa-reviewer")
            is llm.llms_for_routing["reviewer"]
        )
        turn(conversation, "Task two (auto).")
        out["after_auto_turn"] = status_of(conversation)
        conversation.switch_routed_llm("reviewer")
        turn(conversation, "Task three (reviewer).")
        out["after_reviewer_turn"] = status_of(conversation)
        out["requests"] = {"agent": len(a.requests), "reviewer": len(r.requests)}
        out["costs"] = costs(conversation)
        _, out["duplicate_route_error"] = attempt(
            lambda: conversation.add_model_router_route(
                "qa-reviewer", route_name="default"
            )
        )
        _, out["missing_profile_error"] = attempt(
            lambda: conversation.add_model_router_route("qa-no-such-profile")
        )
        # The route is a snapshot: editing the saved profile does not change it.
        store.save(
            "qa-reviewer",
            fake_llm(late, "reviewer", "openai/gpt-4o-mini"),
            include_secrets=True,
        )
        out["route_model_after_profile_edit"] = conversation.agent.llm.llms_for_routing[
            "reviewer"
        ].model
        out["routes_after_errors"] = [
            r["name"] for r in status_of(conversation)["routes"]
        ]
        conversation.close()
        # A route added to an existing MultimodalRouter keeps its router type, and
        # base_route_name names the wrapped LLM only when there is no router yet.
        multimodal = Conversation(
            agent=Agent(
                llm=MultimodalRouter(
                    usage_id="mm",
                    llms_for_routing={
                        "primary": fake_llm(a, "mm-primary", "openai/gpt-4o-mini"),
                        "secondary": fake_llm(a, "mm-secondary", "openai/gpt-4.1-mini"),
                    },
                ),
                tools=[],
            ),
            workspace=WORKSPACE,
            visualizer=None,
        )
        extended = multimodal.add_model_router_route(
            "qa-reviewer", base_route_name="ignored"
        )
        out["multimodal_type"] = type(multimodal.agent.llm).__name__
        out["multimodal_routes"] = [route.name for route in extended.routes]
        multimodal.close()
        named = Conversation(
            agent=Agent(llm=fake_llm(a, "agent", "openai/gpt-4.1"), tools=[]),
            workspace=WORKSPACE,
            visualizer=None,
        )
        out["named_base_routes"] = [
            route.name
            for route in named.add_model_router_route(
                "qa-reviewer", base_route_name="main"
            ).routes
        ]
        named.close()

elif mode == "switch-replaces":
    with (
        FakeProvider([{"finish": "B1"}]) as b,
        FakeProvider([{"finish": "C1"}]) as c,
        FakeProvider([]) as idle,
    ):
        store.save(
            "qa-plain-b",
            fake_llm(b, "plain-b", "openai/gpt-4.1-mini"),
            include_secrets=True,
        )

        def router_conversation() -> Conversation:
            router = RouterLLM(
                usage_id="router",
                llms_for_routing={
                    "first": fake_llm(idle, "route-first", "openai/gpt-4.1"),
                    "second": fake_llm(idle, "route-second", "openai/gpt-4o-mini"),
                },
            ).with_manual_route("second")
            return Conversation(
                agent=Agent(llm=router, tools=[]), workspace=WORKSPACE, visualizer=None
            )

        by_profile = router_conversation()
        by_profile.switch_profile("qa-plain-b")
        out["after_switch_profile"] = {
            "type": type(by_profile.agent.llm).__name__,
            "model": by_profile.agent.llm.model,
            "usage_id": by_profile.agent.llm.usage_id,
            "status": status_of(by_profile),
        }
        _, out["select_after_switch_profile"] = attempt(
            lambda: by_profile.switch_routed_llm("second")
        )
        turn(by_profile, "Task on B.")
        by_profile.close()

        by_object = router_conversation()
        by_object.switch_llm(fake_llm(c, "plain-c", "openai/gpt-4.1-nano"))
        out["after_switch_llm"] = {
            "type": type(by_object.agent.llm).__name__,
            "model": by_object.agent.llm.model,
            "status": status_of(by_object),
        }
        turn(by_object, "Task on C.")
        by_object.close()
        out["requests"] = {
            "b": len(b.requests),
            "c": len(c.requests),
            "routes": len(idle.requests),
        }

        # The router's own switch methods return a copy and leave the original.
        router = RouterLLM(
            llms_for_routing={
                "first": fake_llm(idle, "first", "openai/gpt-4.1"),
                "second": fake_llm(idle, "second", "openai/gpt-4o-mini"),
            }
        )
        pinned = router.switch_llm("second")
        out["router_switch_llm"] = {
            "copy": pinned is not router,
            "original": router.selection.model_dump(mode="json"),
            "copy_selection": pinned.selection.model_dump(mode="json"),
            "active_route_name": pinned.active_route_name,
            "resumed": pinned.resume_auto_routing().selection.model_dump(mode="json"),
        }
        _, out["router_switch_unknown"] = attempt(lambda: router.switch_llm("missing"))
        _, out["router_with_route_duplicate"] = attempt(
            lambda: router.with_route("first", fake_llm(idle, "dup", "openai/gpt-4.1"))
        )

elif mode == "before-first-run":
    with (
        FakeProvider([]) as dead,
        FakeProvider([{"finish": "R1"}]) as r,
    ):
        store.save(
            "qa-early-route",
            fake_llm(r, "early", "openai/gpt-4.1-mini"),
            include_secrets=True,
        )
        conversation = Conversation(
            agent=Agent(llm=fake_llm(dead, "agent", "openai/gpt-4.1"), tools=[]),
            workspace=WORKSPACE,
            visualizer=None,
        )
        conversation.add_model_router_route("qa-early-route", route_name="early")
        conversation.switch_routed_llm("early")
        turn(conversation, "Task on the early route.")
        out["status"] = conversation.state.execution_status.value
        out["requests"] = {"base": len(dead.requests), "route": len(r.requests)}
        out["last_route_name"] = status_of(conversation)["last_route_name"]
        out["costs"] = costs(conversation)
        conversation.close()

else:
    raise SystemExit(f"unknown mode {mode}")

emit(out)
