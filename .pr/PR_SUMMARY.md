# Prompt caching + `current_datetime` experiment

**Model:** `litellm_proxy/anthropic/claude-3-5-haiku-20241022` via `https://llm-proxy.eval.all-hands.dev`

**Key question:** Does putting `<CURRENT_DATETIME>` in the *dynamic* system block prevent it from being part of the cached prefix on later turns?

## Anthropic doc claim (what “cached prefix” means)
Anthropic prompt caching caches the full prefix of the request *up to and including* the content block marked with `cache_control`.

In chat requests, this means that if you mark a block on (say) the last user message, the cached prefix includes **everything that comes before that marker** (tools → system → all previous messages, then the marked message content up to the marker).

So yes: **a cache marker on the last user/tool message can cause the cached prefix to include dynamic system content**, as long as that dynamic content is identical between requests.

## What OpenHands SDK actually sends
In this repo, when prompt caching is active (`LLM._apply_prompt_caching()`):

- System message has **two content blocks**:
  - block 0 = static system prompt → marked `cache_control: {type: ephemeral}`
  - block 1 = dynamic context (includes `<CURRENT_DATETIME>...`) → **NOT** marked
- Last user/tool message’s **last content item** is marked `cache_control: {type: ephemeral}`

This matches Anthropic’s recommended “keep extending the prefix” pattern.

## Empirical run
Script: `.pr/test_prompt_caching_datetime.py`

It runs 2 separate conversations, 5 LLM requests total.

Per request we recorded:
- the **exact datetime string** present in the outgoing request’s dynamic system block
- `usage_summary.cache_read_tokens` from the response (proxy surfaces this)

### Results
| Request | Conversation/turn | Datetime in request | cache_read_tokens | prompt_tokens |
|---:|---|---|---:|---:|
| 1 | c1_t1 | `2026-02-11T19:50:23.420683` | 4523 | 4704 |
| 2 | c1_t2 | `2026-02-11T19:50:23.420683` | 4701 | 4717 |
| 3 | c1_t3 | `2026-02-11T19:50:23.420683` | 4714 | 4731 |
| 4 | c2_t1 | `2026-02-11T19:50:26.327239` | 4523 | 4705 |
| 5 | c2_t2 | `2026-02-11T19:50:26.327239` | 4702 | 4800 |

### Interpretation
- We got **very high cache reads even on turn 1** of each conversation. That indicates an existing cache entry was already present for the static system prompt prefix (org-level cache reuse).
- Within a conversation (turns 1→2→3 and 4→5), cache reads stayed high.
- **The dynamic system block (including datetime) was included in the request each time.**

What this demonstrates:
- Even though the datetime block is **not** itself marked with `cache_control`, a cache marker later in the request (on the last user message) still allows the cached prefix to encompass the entire prompt prefix.
- In practice, the value being cached is the *exact prompt prefix*; since the datetime is constant during a conversation, it can be part of that prefix across requests.

## Where to find the raw artifacts
- `.pr/prompt_cache_datetime_20260211-195022/telemetry/*.json` includes the serialized request messages plus `usage_summary` for each call.
- `.pr/prompt_cache_datetime_20260211-195022/request_snapshots.json` and `summary.json` contain the extracted per-turn info.
