"""Programs that use more than one model: the registry, routers and fallback.
Real `LLM`s answer from local endpoints (`S02/fake_provider.py`), one endpoint
per model, so the program can see which model served each call.

Usage: routing.py MODE
  registry     LLMRegistry CRUD, independent metrics, a failing subscriber
  discovery    every LLM the agent reaches lands in the conversation registry
  multimodal   MultimodalRouter: text, image and over-long turns in an agent run
  validation   router configurations that must be refused
  random       RandomRouter spreads calls over its LLMs
  fallback     FallbackStrategy: transient, non-transient, quota, metrics, lazy profiles
"""

import sys

from pydantic import SecretStr
from S02.fake_provider import FakeProvider
from sdk_verify import emit, run_paths

from openhands.sdk import LLM, Agent, Conversation, LLMRegistry, Tool
from openhands.sdk.llm import ImageContent, Message, TextContent
from openhands.tools.terminal import TerminalTool


mode = sys.argv[1]
WORKSPACE = str(run_paths()["workspace"])
FAST = {"num_retries": 1, "retry_min_wait": 0, "retry_max_wait": 0}
PRICE = {"input_cost_per_token": 0.001, "output_cost_per_token": 0.002}  # 0.14 per call
HI = [Message(role="user", content=[TextContent(text="hi")])]
out: dict = {"ok": True, "mode": mode}


def fake_llm(
    server: FakeProvider, usage_id: str, model: str = "openai/gpt-4o-mini", **kw
) -> LLM:
    return LLM(
        model=model,
        base_url=server.url,
        api_key=SecretStr("sk-test"),
        usage_id=usage_id,
        **{**FAST, **kw},
    )


def attempt(fn):
    try:
        return fn(), None
    except Exception as exc:
        return None, type(exc).__name__


def text_of(response) -> str:
    return "".join(getattr(c, "text", "") for c in response.message.content)


if mode == "registry":
    registry = LLMRegistry()
    other = LLMRegistry()
    events: list = []
    registry.subscribe(events.append)
    with FakeProvider([{"text": "a"}, {"text": "b"}]) as server:
        first = fake_llm(server, "first", **PRICE)
        registry.add(first)
        _, duplicate_error = attempt(lambda: registry.add(fake_llm(server, "first")))
        copy = first.model_copy(update={"usage_id": "copy"})
        registry.add(copy)
        registry.get("first").completion(HI)
        out |= {
            "usage_ids": registry.list_usage_ids(),
            "same_instance": registry.get("first") is first,
            "duplicate_error": duplicate_error,
            "unique_registry_ids": registry.registry_id != other.registry_id,
            "first_cost": round(first.metrics.accumulated_cost, 4),
            "copy_cost": round(registry.get("copy").metrics.accumulated_cost, 4),
            "copy_metrics_independent": registry.get("copy").metrics
            is not first.metrics,
            "notified": [
                getattr(e, "llm", None).usage_id
                for e in events
                if getattr(e, "llm", None)
            ],
        }
        registry.remove("copy")
        _, missing_error = attempt(lambda: registry.get("copy"))
        out["after_remove"] = registry.list_usage_ids()
        out["missing_error"] = missing_error
        # An LLM that already spent keeps its metrics when it joins a registry.
        spent = fake_llm(server, "spent", **PRICE)
        spent.completion(HI)
        before = spent.metrics.accumulated_cost
        LLMRegistry().add(spent)
        out["independent_keeps_metrics"] = spent.metrics.accumulated_cost == before > 0
    # A subscriber that raises does not break add().
    noisy = LLMRegistry()

    def boom(_event) -> None:
        raise RuntimeError("subscriber failed")

    noisy.subscribe(boom)
    _, noisy_error = attempt(
        lambda: noisy.add(LLM(model="openai/gpt-4o-mini", usage_id="n"))
    )
    out["failing_subscriber_add_error"] = noisy_error
    out["failing_subscriber_registered"] = noisy.list_usage_ids()

elif mode == "discovery":
    from openhands.sdk.context.condenser import LLMSummarizingCondenser
    from openhands.sdk.llm.router import MultimodalRouter

    with FakeProvider([{"finish": "done"}] * 3) as server:
        router = MultimodalRouter(
            usage_id="router",
            llms_for_routing={
                "primary": fake_llm(server, "router-primary"),
                "secondary": fake_llm(server, "router-secondary"),
            },
        )
        condenser = LLMSummarizingCondenser(
            llm=fake_llm(server, "condenser"), max_size=80, keep_first=2
        )
        agent = Agent(
            llm=router, tools=[Tool(name=TerminalTool.name)], condenser=condenser
        )
        conversation = Conversation(agent=agent, workspace=WORKSPACE, visualizer=None)
        before = conversation.llm_registry.list_usage_ids()
        conversation.send_message("Finish.")
        conversation.run()
        out |= {
            "before_first_message": before,
            "registered": sorted(conversation.llm_registry.list_usage_ids()),
            "stats": sorted(conversation.conversation_stats.usage_to_metrics),
            "status": conversation.state.execution_status.value,
        }
        conversation.close()
        # Two LLM fields sharing one usage_id register once.
        shared = fake_llm(server, "agent")
        twin = Agent(
            llm=shared,
            tools=[],
            condenser=LLMSummarizingCondenser(
                llm=fake_llm(server, "agent"), max_size=80, keep_first=2
            ),
        )
        conversation = Conversation(agent=twin, workspace=WORKSPACE, visualizer=None)
        _, dup_error = attempt(lambda: conversation.send_message("hi"))
        out["duplicate_usage_ids"] = {
            "error": dup_error,
            "registered": conversation.llm_registry.list_usage_ids(),
        }
        conversation.close()

elif mode == "multimodal":
    from openhands.sdk.llm.router import MultimodalRouter

    # The text-only secondary is a model without vision; its window is small
    # enough that a long turn overflows it.
    with (
        FakeProvider(
            [{"text": "primary: image answer"}, {"text": "primary: long answer"}]
        ) as primary_server,
        FakeProvider([{"text": "secondary: short answer"}]) as secondary_server,
    ):
        primary = fake_llm(primary_server, "primary")
        secondary = fake_llm(
            secondary_server,
            "secondary",
            model="deepseek/deepseek-chat",
            max_input_tokens=20000,
        )
        router = MultimodalRouter(
            usage_id="router",
            llms_for_routing={"primary": primary, "secondary": secondary},
        )
        conversation = Conversation(
            agent=Agent(llm=router, tools=[]), workspace=WORKSPACE, visualizer=None
        )
        served = []

        def turn(content) -> None:
            before = (len(primary_server.requests), len(secondary_server.requests))
            conversation.send_message(Message(role="user", content=content))
            conversation.run()
            after = (len(primary_server.requests), len(secondary_server.requests))
            served.append(
                "primary"
                if after[0] > before[0]
                else "secondary"
                if after[1] > before[1]
                else "none"
            )

        turn([TextContent(text="A short text question.")])
        turn(
            [
                TextContent(text="What is this?"),
                ImageContent(image_urls=["https://example.com/cat.png"]),
            ]
        )
        turn([TextContent(text="word " * 30000)])
        out |= {
            "served": served,
            "secondary_vision": secondary.vision_is_active(),
            "secondary_max_input": secondary.effective_max_input_tokens,
            "agent_replies": [
                e.llm_message.content[0].text
                for e in conversation.state.events
                if type(e).__name__ == "MessageEvent" and e.source == "agent"
            ],
        }
        conversation.close()

elif mode == "validation":
    from openhands.sdk.llm.router import MultimodalRouter
    from openhands.sdk.llm.router.impl.random import RandomRouter

    a = LLM(model="openai/gpt-4o-mini", usage_id="a")
    cases = {
        "missing_secondary": lambda: MultimodalRouter(
            usage_id="r", llms_for_routing={"primary": a}
        ),
        "missing_primary": lambda: MultimodalRouter(
            usage_id="r", llms_for_routing={"secondary": a}
        ),
        "empty_random": lambda: RandomRouter(usage_id="r", llms_for_routing={}),
        "valid": lambda: MultimodalRouter(
            usage_id="r", llms_for_routing={"primary": a, "secondary": a}
        ),
    }
    out["errors"] = {name: attempt(fn)[1] for name, fn in cases.items()}

elif mode == "random":
    from openhands.sdk.llm.router.impl.random import RandomRouter

    with (
        FakeProvider([{"text": "from a"}] * 30) as server_a,
        FakeProvider([{"text": "from b"}] * 30) as server_b,
    ):
        router = RandomRouter(
            usage_id="random",
            llms_for_routing={
                "a": fake_llm(server_a, "a"),
                "b": fake_llm(server_b, "b"),
            },
        )
        texts = [text_of(router.completion(HI)) for _ in range(20)]
        out |= {
            "calls": len(texts),
            "from_a": texts.count("from a"),
            "from_b": texts.count("from b"),
        }

elif mode == "fallback":
    from openhands.sdk.llm import FallbackStrategy
    from openhands.sdk.llm.llm_profile_store import LLMProfileStore

    store_dir = run_paths()["run"] / "fallback-profiles"
    store = LLMProfileStore(store_dir)

    def primary_with(server: FakeProvider, names: list[str], **kw) -> LLM:
        return fake_llm(
            server,
            "primary",
            fallback_strategy=FallbackStrategy(
                fallback_llms=names, profile_store_dir=str(store_dir)
            ),
            **{**PRICE, **kw},
        )

    rate_limited = {"status": 429, "message": "Rate limit reached"}
    with (
        FakeProvider([rate_limited, {"text": "primary again"}]) as p,
        FakeProvider([rate_limited]) as fb1,
        FakeProvider([{"text": "from fallback 2"}]) as fb2,
    ):
        # Profiles are resolved lazily: fb2 is saved after the primary is built.
        store.save("fb1", fake_llm(fb1, "fb1", **PRICE), include_secrets=True)
        llm = primary_with(p, ["fb1", "fb2"])
        store.save("fb2", fake_llm(fb2, "fb2", **PRICE), include_secrets=True)
        first, err = attempt(lambda: llm.completion(HI))
        out["transient"] = {
            "text": text_of(first) if first else None,
            "error": err,
            "primary_requests": len(p.requests),
            "fallback1_requests": len(fb1.requests),
            "fallback2_requests": len(fb2.requests),
            "primary_cost": round(llm.metrics.accumulated_cost, 4),
        }
        second, err = attempt(lambda: llm.completion(HI))
        out["next_call"] = {
            "text": text_of(second) if second else None,
            "primary_requests": len(p.requests),
        }

    with (
        FakeProvider([{"status": 400, "message": "Invalid parameter"}]) as p,
        FakeProvider([{"text": "unused"}]) as fb,
    ):
        store.save("fb-unused", fake_llm(fb, "fb-unused"), include_secrets=True)
        _, err = attempt(lambda: primary_with(p, ["fb-unused"]).completion(HI))
        out["non_transient"] = {"error": err, "fallback_requests": len(fb.requests)}

    budget = {"status": 429, "message": "budget_exceeded: Budget has been exceeded"}
    with FakeProvider([budget]) as p, FakeProvider([{"text": "unused"}]) as fb:
        store.save("fb-budget", fake_llm(fb, "fb-budget"), include_secrets=True)
        _, err = attempt(lambda: primary_with(p, ["fb-budget"]).completion(HI))
        out["budget_denial"] = {"error": err, "fallback_requests": len(fb.requests)}

    quota = {
        "status": 429,
        "message": "insufficient_quota: You exceeded your current quota",
    }
    with (
        FakeProvider([quota] * 3) as p,
        FakeProvider([{"text": "from quota fallback"}]) as fb,
    ):
        store.save("fb-quota", fake_llm(fb, "fb-quota"), include_secrets=True)
        r, err = attempt(
            lambda: primary_with(p, ["fb-quota"], num_retries=3).completion(HI)
        )
        out["quota"] = {
            "text": text_of(r) if r else None,
            "error": err,
            "primary_requests": len(p.requests),
        }

    with FakeProvider([rate_limited] * 2) as p, FakeProvider([rate_limited]) as fb:
        store.save("fb-down", fake_llm(fb, "fb-down"), include_secrets=True)
        _, err = attempt(lambda: primary_with(p, ["fb-down"]).completion(HI))
        out["all_fail"] = {"error": err, "fallback_requests": len(fb.requests)}

    # Responses API path, and the async chat path.
    import asyncio

    with (
        FakeProvider([rate_limited]) as p,
        FakeProvider([{"text": "responses fallback"}]) as fb,
    ):
        store.save(
            "fb-resp",
            fake_llm(fb, "fb-resp", model="openai/gpt-5-mini", api_mode="responses"),
            include_secrets=True,
        )
        llm = primary_with(
            p, ["fb-resp"], model="openai/gpt-5-mini", api_mode="responses"
        )
        r, err = attempt(lambda: llm.responses(HI))
        out["responses"] = {
            "text": text_of(r) if r else None,
            "error": err,
            "paths": [q["path"] for q in fb.requests],
        }
    with (
        FakeProvider([rate_limited]) as p,
        FakeProvider([{"text": "async fallback"}]) as fb,
    ):
        store.save("fb-async", fake_llm(fb, "fb-async"), include_secrets=True)
        llm = primary_with(p, ["fb-async"])
        r, err = attempt(lambda: asyncio.run(llm.acompletion(HI)))
        out["async"] = {"text": text_of(r) if r else None, "error": err}

else:
    raise SystemExit(f"unknown mode {mode}")

emit(out)
