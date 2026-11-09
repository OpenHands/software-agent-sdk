"""Call the run's real model directly: a completion, a streamed completion
with a callback, and `generate`, then report the metrics the SDK recorded.

Usage: live_llm.py   (in a run created with `control-sdk new --llm ...`)
"""

from sdk_verify import emit, make_llm

from openhands.sdk.llm import Message, TextContent


messages = [
    Message(role="system", content=[TextContent(text="Answer with one word.")]),
    Message(
        role="user",
        content=[TextContent(text="What colour is the sky on a clear day?")],
    ),
]
llm = make_llm(usage_id="live")
first = llm.completion(messages)
chunks: list = []
streamer = make_llm(usage_id="live-stream", stream=True)
streamed = streamer.completion(messages, on_token=chunks.append)
generated = llm.generate(messages)


def text(response) -> str:
    return "".join(getattr(c, "text", "") for c in response.message.content)


usage = llm.metrics.accumulated_token_usage
emit(
    {
        "ok": True,
        "model": llm.model,
        "uses_responses_api": llm.uses_responses_api(),
        "completion_text": text(first)[:100],
        "streamed_text": text(streamed)[:100],
        "stream_chunks": len(chunks),
        "generate_text": text(generated)[:100],
        "calls_recorded": len(llm.metrics.token_usages),
        "prompt_tokens": usage.prompt_tokens if usage else 0,
        "completion_tokens": usage.completion_tokens if usage else 0,
        "accumulated_cost": llm.metrics.accumulated_cost,
        "latencies": len(llm.metrics.response_latencies),
    }
)
