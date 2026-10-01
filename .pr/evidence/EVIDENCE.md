# Direct-routing classifier MiniMax evidence

This directory holds evidence for the end-to-end acceptance criterion of
issue #5411 / PR #5412: a direct-routing classifier whose `classifier_model`
is backed by a provider that requires a non-empty `user` turn (MiniMax) must
complete successfully end to end.

## What the bug is

`build_classifier_messages(meta, transcript)` previously produced a **single
`system` message** with no `user` turn in the direct-routing branch
(`meta.prompt_template is not None`). MiniMax rejects system-only requests
with HTTP 400:

```json
{"base_error":{"message":"invalid params, chat content is empty (2013)"}}
```

The fix makes both routing modes return `[system, user]`; the rendered prompt
body (transcript + model table) moves into the `user` turn.

## How to reproduce (requires a MiniMax-backed endpoint)

`repro.py` runs the actual SDK router tool (`route_task_to_model`) against a
user-supplied litellm proxy + model group, using a direct-routing meta-profile
whose `classifier_model` is `minimax-m3`. It prints the classifier request
messages and the provider response / error.

```bash
# from the software-agent-sdk repo root, on the PR branch
uv sync --group dev

export OH_PROXY_URL="http://127.0.0.1:4000"   # your litellm proxy
export OH_PROXY_KEY="sk-..."                  # a virtual key with minimax-m3 access
export OH_CLASSIFIER_MODEL="minimax-m3"       # model group name on the proxy

# After the fix (HEAD of this PR):
uv run python .pr/evidence/repro.py

# Before the fix (base 3c8be74) — checkout base and re-run:
git checkout 3c8be74 -- openhands-sdk/openhands/sdk/tool/builtins/classify_and_switch_llm.py
uv run python .pr/evidence/repro.py
git checkout HEAD -- openhands-sdk/openhands/sdk/tool/builtins/classify_and_switch_llm.py
```

Expected:

| Revision | Classifier messages | Result |
|----------|---------------------|--------|
| base `3c8be74` | `[{system: <full prompt>}]` | 400 `chat content is empty (2013)` |
| HEAD (this PR) | `[{system: prefix}, {user: rendered body}]` | 200, routes to selected model |

`run.sh` wraps both runs and tees output to `before.log` / `after.log`.

## Already-observed before-evidence

The `chat content is empty (2013)` failure was observed live during the
original investigation (against a local litellm proxy whose `minimax-m3` group
is backed by MiniMax) and is quoted verbatim in issue #5411. The `after` run on
that same proxy with the fix applied succeeded and routed to the selected
target model.

## Status

- The code change is assessed as sound by the review (no material defect,
  behavior-preserving for class mode, no public API change).
- Unit tests prove the `[system, user]` message shape for both modes
  (`test_build_classifier_messages_*`).
- Authentic MiniMax provider responses require a MiniMax-backed endpoint;
  capture with `run.sh` once endpoint access is available, then attach
  `before.log` / `after.log` here.

_This evidence note was authored by an AI agent (OpenHands) on behalf of @juanmichelini._
