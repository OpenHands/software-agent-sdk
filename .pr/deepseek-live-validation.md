# DeepSeek live verification — 2026-09-28

The official DeepSeek Chat Completions API accepted all five cases on both
revisions. This is compatibility evidence for these requests, **not a
reproduction of the TokenRouter rejection reported in #4965**.

## Environment and results

- macOS arm64, Python 3.13.13, SDK 1.49.6.
- Endpoint: `https://api.deepseek.com/chat/completions`.
- Requested and returned model: `deepseek-flash`.
- Thinking disabled; output limited to 128 tokens; no automatic retries.
- Base: `a350dc73ef9b4d3a801ffab2aed211a04d2120a9`.
- Tested PR revision: `86f87ccfd7a21f865aaa79f75cce86c680632d45`.
- These revisions have identical `uv.lock` files. Each run imported the SDK
  from its own checkout and used the same reproduction script.

| Case | Base HTTP | PR HTTP |
| --- | --- | --- |
| Ordinary control | 200 | 200 |
| Empty user content | 200 | 200 |
| Whitespace-only assistant text block | 200 | 200 |
| Mixed blank and valid user blocks | 200 | 200 |
| Empty tool result | 200 | 200 |

Every response contained text and ended with `finish_reason: stop`.
[Base log](deepseek-base.jsonl) and [PR log](deepseek-head.jsonl) include the
request bodies, HTTP status, response model, token usage, and response excerpts.
The requests ran at 03:50 UTC on September 28. There were ten requests in total.

## Reproduce

Use [repro_chat_content_deepseek.py](repro_chat_content_deepseek.py). From each
checkout's root, after `uv sync --frozen --group dev`, run the same script with
that checkout's `.venv/bin/python`. The script verifies the SDK import location.
Copy the script to `.pr/` on the base checkout, or pass its absolute path.

Configure `LLM_API_KEY` privately in the process environment; do not put its
value in source code or logs. Select the current model explicitly:

```sh
export LLM_MODEL=deepseek-flash
.venv/bin/python .pr/repro_chat_content_deepseek.py --live
```

Omit `--live` for an offline serialization inspection. That mode blocks network
connections. Live mode sends at most five requests, stops if the control fails,
and does not follow HTTP redirects. API usage can incur charges.

## What this establishes

The program calls the SDK's public `Message.to_chat_dict()` and sends the
resulting messages directly over HTTP. This is **not** the `LLM.completion()`
or LiteLLM transport path, a full Agent task, or a benchmark.

The fixed system message uses string serialization on both revisions, following
DeepSeek's documented schema. All target messages use the SDK's list serializer
without any subsequent filtering or rewriting. The base logs retain empty
arrays and blank text blocks; the PR logs show the SDK normalizing them.
DeepSeek nevertheless accepted both sets of payloads in this run.

These are constructed message histories, including an artificial tool call.
The test does not establish how a full Agent produces those histories, nor does
it validate Anthropic cache hits. It differs from the original Windows and
TokenRouter `glm-5.3-free` environment, which remains independently unverified.

The earlier [loopback evidence](validation.md) is a separate test: its local
server deliberately rejects the shapes described in the issue. Its HTTP
400-to-200 comparison must not be presented as a live DeepSeek result.
