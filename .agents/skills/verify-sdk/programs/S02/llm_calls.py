"""Call a real `LLM` directly, the way a program uses one outside an agent,
against a local endpoint that answers from a script. One scenario per mode.

Usage: llm_calls.py MODE
  retries         transient errors retried, others raised at once, listener
  no-response     empty replies retried with a temperature bump, then raised
  errors          provider errors mapped to the SDK's typed exceptions
  timeouts        per-attempt timeout and async stream idle timeout
  streaming       sync/async streaming callbacks, and stream without callback
  prompted        text replies parsed back into tool calls (non-native)
  cache-too-small a prompt-cache rejection retried without cache markers
  token-count     get_token_count, with and without tool definitions
  context-guard   a short context window refused (or allowed by env)
  inline-image    http image URLs inlined as data: URLs
  serialization   dump, masking, round trip, model_copy, load_from_env
  features        model feature table and the chat options sent per model
"""

import asyncio
import json
import sys
import time

from pydantic import SecretStr
from S02.fake_provider import FakeProvider
from sdk_verify import emit, run_paths

from openhands.sdk import LLM
from openhands.sdk.llm import ImageContent, Message, TextContent


mode = sys.argv[1]
KEY = SecretStr("sk-test-secret")
FAST = {"retry_min_wait": 0, "retry_max_wait": 0, "retry_multiplier": 0.1}
MESSAGES = [
    Message(role="system", content=[TextContent(text="You are terse.")]),
    Message(role="user", content=[TextContent(text="Say hi.")]),
]


def text_of(response) -> str:
    return "".join(getattr(c, "text", "") for c in response.message.content)


def attempt(fn):
    """Run fn; return (result, error type name, seconds)."""
    started = time.monotonic()
    try:
        return fn(), None, round(time.monotonic() - started, 1)
    except Exception as exc:
        return None, type(exc).__name__, round(time.monotonic() - started, 1)


def chat_llm(server: FakeProvider, **kw) -> LLM:
    return LLM(
        model=kw.pop("model", "openai/gpt-4o-mini"),
        base_url=kw.pop("base_url", server.url),
        api_key=KEY,
        usage_id="direct",
        **kw,
    )


out: dict = {"ok": True, "mode": mode}

if mode == "retries":
    seen: list = []
    with FakeProvider(
        [
            {"status": 429, "message": "Rate limit reached"},
            {"status": 503},
            {"text": "hi"},
        ]
    ) as s:
        llm = chat_llm(
            s,
            num_retries=3,
            retry_listener=lambda n, m, _e: seen.append([n, m]),
            **FAST,
        )
        r, err, _ = attempt(lambda: llm.completion(MESSAGES))
        out["transient"] = {
            "text": text_of(r) if r else None,
            "error": err,
            "requests": len(s.requests),
            "listener": seen,
        }
    with FakeProvider([{"status": 400, "message": "Invalid parameter: foo"}] * 3) as s:
        r, err, _ = attempt(
            lambda: chat_llm(s, num_retries=3, **FAST).completion(MESSAGES)
        )
        out["non_retryable"] = {"error": err, "requests": len(s.requests)}
    with FakeProvider([{"status": 429, "message": "Rate limit reached"}] * 5) as s:
        r, err, _ = attempt(
            lambda: chat_llm(s, num_retries=3, **FAST).completion(MESSAGES)
        )
        out["exhausted"] = {"error": err, "requests": len(s.requests)}
    budget = {"status": 429, "message": "budget_exceeded: Budget has been exceeded"}
    with FakeProvider([budget] * 3) as s:
        r, err, _ = attempt(
            lambda: chat_llm(s, num_retries=3, **FAST).completion(MESSAGES)
        )
        out["budget_denial"] = {"error": err, "requests": len(s.requests)}

elif mode == "no-response":
    with FakeProvider([{"empty": True}, {"empty": True}, {"text": "hi"}]) as s:
        llm = chat_llm(s, num_retries=3, temperature=0.0, **FAST)
        r, err, _ = attempt(lambda: llm.completion(MESSAGES))
        out["chat"] = {
            "text": text_of(r) if r else None,
            "error": err,
            "requests": len(s.requests),
            "temperatures": [q["body"].get("temperature") for q in s.requests],
        }
    with FakeProvider([{"empty": True}] * 3) as s:
        r, err, _ = attempt(
            lambda: chat_llm(s, num_retries=2, **FAST).completion(MESSAGES)
        )
        out["chat_exhausted"] = {"error": err, "requests": len(s.requests)}
    with FakeProvider([{"empty": True}, {"text": "hi"}]) as s:
        llm = chat_llm(s, num_retries=2, **FAST)
        r, err, _ = attempt(lambda: asyncio.run(llm.acompletion(MESSAGES)))
        out["chat_async"] = {"text": text_of(r) if r else None, "error": err}
    # On the Responses path a no-response is a stream that ends without its
    # completed event.
    incomplete = {"text": "lost", "incomplete": True}
    with FakeProvider([incomplete, {"text": "hi"}]) as s:
        llm = chat_llm(
            s,
            model="openai/gpt-4o-mini",
            api_mode="responses",
            stream=True,
            temperature=0.0,
            num_retries=2,
            **FAST,
        )
        r, err, _ = attempt(lambda: llm.responses(MESSAGES, on_token=lambda _e: None))
        out["responses"] = {
            "text": text_of(r) if r else None,
            "error": err,
            "requests": len(s.requests),
            "temperatures": [q["body"].get("temperature") for q in s.requests],
        }
    with FakeProvider([incomplete] * 2) as s:
        llm = chat_llm(
            s,
            model="openai/gpt-5-mini",
            api_mode="responses",
            stream=True,
            num_retries=2,
            **FAST,
        )

        async def ignore(event) -> None:
            pass

        r, err, _ = attempt(
            lambda: asyncio.run(llm.aresponses(MESSAGES, on_token=ignore))
        )
        out["responses_async_exhausted"] = {"error": err, "requests": len(s.requests)}

elif mode == "errors":
    cases = {
        "context_window": (
            400,
            "This model's maximum context length is 128000 tokens. However, your "
            "messages resulted in 200000 tokens. "
            "Please reduce the length of the messages.",
        ),
        "malformed_history": (
            400,
            "messages.3: `tool_use` ids were found without `tool_result` blocks "
            "immediately after: toolu_1",
        ),
        "auth_401_incorrect_key": (401, "Incorrect API key provided: sk-test."),
        "auth_401_invalid_key": (401, "Invalid API key"),
        "rate_limit": (429, "Rate limit reached for requests"),
        "service_unavailable": (503, "The server is overloaded"),
        "bad_request": (400, "Invalid value for parameter foo"),
        "content_policy": (400, "Output blocked by content filtering policy"),
    }
    mapped = {}
    for name, (status, message) in cases.items():
        with FakeProvider([{"status": status, "message": message}]) as s:
            _, err, _ = attempt(lambda: chat_llm(s, num_retries=1).completion(MESSAGES))
            mapped[name] = err
    with FakeProvider([{"delay": 3, "text": "late"}]) as s:
        _, err, _ = attempt(
            lambda: chat_llm(s, num_retries=1, timeout=1).completion(MESSAGES)
        )
        mapped["timeout"] = err
    with FakeProvider([]) as s:
        dead = LLM(
            model="openai/gpt-4o-mini", base_url=s.url, api_key=KEY, num_retries=1
        )
    _, err, _ = attempt(lambda: dead.completion(MESSAGES))
    mapped["connection_refused"] = err
    out["mapped"] = mapped

elif mode == "timeouts":
    with FakeProvider(
        [{"delay": 3, "text": "late"}, {"delay": 3, "text": "late"}]
    ) as s:
        _, err, secs = attempt(
            lambda: chat_llm(s, num_retries=1, timeout=1).completion(MESSAGES)
        )
        out["timeout"] = {"error": err, "seconds": secs, "requests": len(s.requests)}
    with FakeProvider([{"stall": 4, "text": "stalled stream"}]) as s:
        llm = chat_llm(s, num_retries=1, stream=True, stream_idle_timeout=1)

        async def on_token(chunk) -> None:
            pass

        _, err, secs = attempt(
            lambda: asyncio.run(llm.acompletion(MESSAGES, on_token=on_token))
        )
        out["stream_idle"] = {"error": err, "seconds": secs}
    # The same stall inside an async agent run ends the conversation in ERROR.
    from openhands.sdk import Agent, Conversation

    with FakeProvider([{"stall": 4, "text": "stalled"}]) as s:
        llm = LLM(
            model="openai/gpt-4o-mini",
            base_url=s.url,
            api_key=KEY,
            usage_id="agent",
            stream=True,
            stream_idle_timeout=1,
            num_retries=1,
        )
        conversation = Conversation(
            agent=Agent(llm=llm, tools=[]),
            workspace=str(run_paths()["workspace"]),
            visualizer=None,
            token_callbacks=[lambda _chunk: None],
        )
        conversation.send_message("hi")
        _, err, _ = attempt(lambda: asyncio.run(conversation.arun()))
        out["conversation"] = {
            "status": conversation.state.execution_status.value,
            "raised": err,
            "error_events": [
                getattr(e, "code", None)
                for e in conversation.state.events
                if type(e).__name__ == "ConversationErrorEvent"
            ],
        }
        conversation.close()

elif mode == "streaming":
    with FakeProvider([{"text": "plain reply"}]) as s:
        r = chat_llm(s, stream=True).completion(MESSAGES)
        out["no_callback"] = {
            "text": text_of(r),
            "stream_sent": s.requests[0]["body"].get("stream"),
        }
    chunks: list = []
    with FakeProvider([{"text": "async streamed reply", "usage": {"cached": 40}}]) as s:
        llm = chat_llm(s, stream=True)

        async def collect(chunk) -> None:
            chunks.append(chunk)

        r = asyncio.run(llm.acompletion(MESSAGES, on_token=collect))
        out["async_callback"] = {
            "text": text_of(r),
            "chunks": len(chunks),
            "cache_read_tokens": llm.metrics.accumulated_token_usage.cache_read_tokens
            if llm.metrics.accumulated_token_usage
            else None,
        }
    sync_chunks: list = []
    with FakeProvider(
        [{"text": "sync streamed reply", "tool": "lookup", "args": {"q": 1}}]
    ) as s:
        r = chat_llm(s, stream=True).completion(MESSAGES, on_token=sync_chunks.append)
        out["sync_callback"] = {
            "text": text_of(r),
            "chunks": len(sync_chunks),
            "tool_calls": [t.name for t in r.message.tool_calls or []],
            "tool_arguments": [t.arguments for t in r.message.tool_calls or []],
        }

elif mode == "prompted":
    from openhands.sdk.tool.builtins.finish import FinishTool

    tools = FinishTool.create()
    replies = {
        "valid": "<function=finish>\n<parameter=message>bye</parameter>\n</function>",
        "unknown_tool": "<function=no_such_tool>\n<parameter=x>1</parameter>\n"
        "</function>",
        "unknown_parameter": "<function=finish>\n<parameter=bogus>1</parameter>\n"
        "</function>",
    }
    parsed = {}
    with FakeProvider([{"text": t} for t in replies.values()]) as s:
        for name in replies:
            llm = chat_llm(s, native_tool_calling=False, num_retries=1)
            r, err, _ = attempt(lambda: llm.completion(MESSAGES, tools=tools))
            calls = (r.message.tool_calls or []) if r else []
            parsed[name] = {
                "error": err,
                "tool_calls": [[c.name, json.loads(c.arguments)] for c in calls],
            }
        out["tools_param_sent"] = any("tools" in q["body"] for q in s.requests)
    out["parsed"] = parsed

elif mode == "cache-too-small":
    rejection = (
        "The cached content is of 1171 tokens. The minimum token count to start "
        "caching is 4096."
    )
    with FakeProvider(
        [{"status": 400, "message": rejection}, {"text": "uncached"}]
    ) as s:
        llm = chat_llm(
            s,
            model="anthropic/claude-sonnet-4-5-20250929",
            base_url=s.root,
            num_retries=1,
        )
        r, err, _ = attempt(lambda: llm.completion(MESSAGES))
        out |= {
            "caching_active": llm.is_caching_prompt_active(),
            "text": text_of(r) if r else None,
            "error": err,
            "requests": len(s.requests),
            "cache_markers": [
                json.dumps(q["body"]).count("cache_control") for q in s.requests
            ],
        }
    with FakeProvider([{"text": "ok"}]) as s:
        llm = chat_llm(
            s,
            model="anthropic/claude-sonnet-4-5-20250929",
            base_url=s.root,
            caching_prompt=False,
        )
        llm.completion(MESSAGES)
        out["caching_off_markers"] = json.dumps(s.requests[0]["body"]).count(
            "cache_control"
        )

elif mode == "token-count":
    from openhands.sdk.tool.builtins.finish import FinishTool

    llm = LLM(model="openai/gpt-4o-mini", api_key=KEY, usage_id="count")
    tools = FinishTool.create()
    plain = llm.get_token_count(MESSAGES)
    with_tools = llm.get_token_count(MESSAGES, tools=tools)
    longer = llm.get_token_count(
        MESSAGES + [Message(role="user", content=[TextContent(text="word " * 200)])]
    )
    out |= {"plain": plain, "with_tools": with_tools, "longer": longer}

elif mode == "context-guard":
    import os

    _, err, _ = attempt(
        lambda: LLM(model="openai/gpt-4o-mini", api_key=KEY, max_input_tokens=1000)
    )
    out["short_window_error"] = err
    out["allow_env"] = os.environ.get("ALLOW_SHORT_CONTEXT_WINDOWS")
    import litellm

    from openhands.sdk.llm.llm import DEFAULT_MAX_OUTPUT_TOKENS_CAP

    # A model whose metadata reports only max_tokens (the context window, as
    # some providers do) gets a capped output budget.
    litellm.register_model(
        {
            "openai/verify-wide-model": {
                "max_tokens": 200000,
                "max_input_tokens": 200000,
                "litellm_provider": "openai",
                "mode": "chat",
            }
        }
    )
    wide = LLM(model="openai/verify-wide-model", api_key=KEY)
    out["fallback_max_output"] = wide.effective_max_output_tokens
    out["fallback_cap"] = DEFAULT_MAX_OUTPUT_TOKENS_CAP
    out["configured_max_output"] = wide.max_output_tokens

elif mode == "inline-image":
    import os

    with FakeProvider([{"text": "a pixel"}, {"text": "a pixel"}]) as s:
        image = Message(
            role="user",
            content=[
                TextContent(text="Describe."),
                ImageContent(image_urls=[s.image_url]),
            ],
        )
        for name, flag in (("inline", True), ("as_url", False)):
            llm = chat_llm(s, inline_image_urls=flag)
            r, err, _ = attempt(lambda: llm.completion([MESSAGES[0], image]))
            body = s.requests[-1]["body"] if s.requests else {}
            urls = [
                part["image_url"]["url"]
                for m in body.get("messages", [])
                if isinstance(m.get("content"), list)
                for part in m["content"]
                if part.get("type") == "image_url"
            ]
            out[name] = {"error": err, "image_urls": [u[:30] for u in urls]}
    out["allow_private_hosts"] = os.environ.get("OH_INLINE_IMAGE_ALLOW_PRIVATE_HOSTS")

elif mode == "serialization":
    import os

    llm = LLM(model="openai/gpt-4o-mini", api_key=KEY, usage_id="ser", temperature=0.2)
    dumped = llm.model_dump(mode="json")
    text = llm.model_dump_json()
    back = LLM.model_validate_json(text)
    revealed = llm.model_dump(mode="json", context={"expose_secrets": True})
    copy = llm.model_copy(update={"temperature": 0.7})
    from_env = LLM.load_from_env()
    out |= {
        "api_key_dumped": dumped.get("api_key"),
        "secret_in_json": "sk-test-secret" in text,
        "roundtrip_model": back.model,
        "roundtrip_temperature": back.temperature,
        "exposed_key": revealed.get("api_key") == "sk-test-secret",
        "copy_temperature": copy.temperature,
        "copy_keeps_model": copy.model == llm.model,
        "copy_keeps_key": copy.api_key.get_secret_value() == "sk-test-secret"
        if isinstance(copy.api_key, SecretStr)
        else False,
        "env": {
            "model": from_env.model,
            "num_retries": from_env.num_retries,
            "temperature": from_env.temperature,
            "native_tool_calling": from_env.native_tool_calling,
            "key_set": from_env.api_key is not None,
            "LLM_MODEL": os.environ.get("LLM_MODEL"),
        },
    }

elif mode == "features":
    from openhands.sdk.llm.utils.model_features import get_features

    models = [
        "openai/gpt-5-mini",
        "openai/gpt-4o-mini",
        "anthropic/claude-sonnet-4-5-20250929",
        "anthropic/claude-opus-4-5-20251101",
        "litellm_proxy/anthropic/claude-sonnet-4-5-20250929",
        "deepseek/deepseek-chat",
        "moonshot/kimi-k2-0711-preview",
    ]
    table = {}
    for model in models:
        f = get_features(model)
        table[model] = {
            "reasoning_effort": f.supports_reasoning_effort,
            "thinking_mode": f.thinking_mode,
            "sampling_params": f.supports_sampling_params,
            "prompt_cache": f.supports_prompt_cache,
            "responses_api": f.supports_responses_api,
            "vision": f.supports_vision,
            "inline_images": f.requires_inline_image_data,
            "force_string": f.force_string_serializer,
            "stop_words": f.supports_stop_words,
        }
    out["features"] = table
    out["proxy_prefix_stripped"] = (
        table["litellm_proxy/anthropic/claude-sonnet-4-5-20250929"]
        == table["anthropic/claude-sonnet-4-5-20250929"]
    )
    sent = {}
    probes = {
        "gpt5": {"model": "openai/gpt-5-mini", "api_mode": "chat"},
        "gpt4o": {"model": "openai/gpt-4o-mini"},
        "azure": {"model": "azure/gpt-4o", "api_version": "2024-10-21"},
        "openrouter": {"model": "openrouter/openai/gpt-4o-mini"},
    }
    with FakeProvider([{"text": "ok"}] * len(probes)) as s:
        for name, kw in probes.items():
            llm = chat_llm(s, temperature=0.5, top_p=0.9, num_retries=1, **kw)
            _, err, _ = attempt(lambda: llm.completion(MESSAGES))
            body = s.requests[-1]["body"]
            headers = s.requests[-1]["headers"]
            sent[name] = {
                "error": err,
                "reasoning_effort": body.get("reasoning_effort"),
                "temperature": body.get("temperature"),
                "top_p": body.get("top_p"),
                "max_tokens": "max_tokens" in body,
                "max_completion_tokens": "max_completion_tokens" in body,
                "openrouter_headers": "http-referer" in headers or "x-title" in headers,
            }
    out["sent"] = sent

else:
    raise SystemExit(f"unknown mode {mode}")

emit(out)
