# Issue #4032 — QA evidence for *LLM profile timeout is reset after agent-server restart*

Verification for *fix(agent-server): persist LLM/profile switch to meta.json so
it survives restart*.

Run it yourself:

```bash
# The end-to-end reproduction: real agent-server process, SIGKILLed and
# restarted, real mock LLM, real agent run, real HTTP.
uv run python .pr/timeout_restart_evidence.py switch
uv run python .pr/timeout_restart_evidence.py noswitch

# Point it at another checkout to see the bug on main:
uv run python .pr/timeout_restart_evidence.py switch --repo /path/to/main

# The no-switch scenarios, in-process and fast (no network, no API key):
uv run python .pr/no_switch_repro.py
```

Nothing is mocked or monkeypatched inside the server. `timeout_restart_evidence.py`
starts a real `agent-server`, drives it over HTTP, kills it with `SIGKILL`
(a container stop, not a graceful shutdown), restarts it on the same
persistence dirs, and reads the conversation back. `OH_SECRET_KEY` is set so
secrets survive the restart — without it the restored conversation loses its
`api_key` and fails for a reason unrelated to the timeout.

Environment: macOS 15 (arm64), Python 3.13, source under test at `aa22e6985`,
compared against `main` at `059ce65d9`. The reported baseline `65ee52f81` was
also checked (see §3). A full run of one scenario takes ~90 s.

The profile under test uses `timeout=5` (the SDK default is `300`) with
`num_retries=0`, so a stalled LLM separates the two outcomes in seconds rather
than minutes.

## 1. The bug, and that it is gone

`switch` scenario: the conversation is created with the default timeout and
then moved onto the profile via `POST /conversations/{id}/switch_llm` — the
path the OpenHands app-server uses for its profile picker.

**On `main`:**

```
>>> on-disk BEFORE restart
    meta.json:       [('$.agent.llm.timeout', 300)]
    base_state.json: [('$.agent.llm.timeout', 5)]

>>> SIGKILL the agent-server (container restart)

>>> restored, COLD read (stale, from base_state.json): timeout=5,   usage_id=profile-slow
>>> restored, WARM read (live, hydrated agent):        timeout=300, usage_id=agent

>>> on-disk AFTER restart + hydration
    meta.json:       [('$.agent.llm.timeout', 300)]
    base_state.json: [('$.agent.llm.timeout', 300)]

>>> behavioural probe — stall the LLM, watch when the agent gives up
    stalled requests seen by the mock: 1
    agent gave up after: >45s (still waiting)

    => TIMEOUT PRESERVED ACROSS RESTART: False
    => REVERTED 5 -> 300 (the SDK default is 300)
```

**With this PR:**

```
>>> on-disk BEFORE restart
    meta.json:       [('$.agent.llm.timeout', 5)]
    base_state.json: [('$.agent.llm.timeout', 5)]

>>> restored, WARM read (live, hydrated agent): timeout=5, usage_id=profile-slow

>>> behavioural probe — stall the LLM, watch when the agent gives up
    stalled requests seen by the mock: 3
    agent gave up after: 17.3s

    => TIMEOUT PRESERVED ACROSS RESTART: True
```

The behavioural line is the one that matters: it is not a field read back out
of a JSON file, it is the agent actually abandoning a stalled request. 17.3 s
across 3 attempts is ~5 s each — the profile's timeout. On `main` the agent was
still waiting at 45 s, consistent with the reinstated 300 s default.

## 2. The timeout is *not* lost when no switch is involved

This is worth stating explicitly, because the report reads as though it is.
`no_switch_repro.py` covers three no-switch paths, and
`timeout_restart_evidence.py noswitch` covers the same thing end-to-end:

| scenario | at creation | after restart |
| --- | --- | --- |
| A. plain agent created with `timeout=600` | 600 | **600** |
| B. started from `agent_profile_id` (canvas path) | 600 | **600** |
| C. B, but the LLM profile is edited to 1234 afterwards | 600 | **600** |
| end-to-end `noswitch`, real server restart | 5 | **5** (17.3 s behavioural) |

A profile timeout *does* reach a conversation without any switch — in
OpenHands, `POST /profiles/{name}/activate` calls `switch_to_profile()`, which
copies the profile's LLM into `agent_settings.llm`, and conversation creation
preserves `timeout` from there. That path was never broken. Only a *live*
divergence from `meta.json` reverts, and only a switch creates one.

Scenario C is the bug report's literal repro wording ("change a profile's
timeout to 1234, restart the container"). Editing a profile does not affect an
existing conversation before *or* after a restart — the expanded LLM is frozen
at creation — so the restart is not what changes the value there. Whether a
restore should re-read the active profile is a separate design question, noted
in the PR discussion.

## 3. The reported baseline behaves the same way

The issue was filed against agent-server `65ee52f81`. The no-switch scenarios
were re-run against that exact commit and preserve the timeout there too (600
at creation, 600 after restart, for both the plain-agent and `agent_profile_id`
paths). So this is not a regression introduced after the report — the
switch-not-mirrored path was the broken one all along.

## 4. Why this was hard to pin down: the cold read lies

The `COLD`/`WARM` split above is not an artifact of the harness, it is a real
property of the server and it explains why the symptom is so slippery.

Persisted conversations hydrate lazily (#4100). Before hydration,
`GET /api/conversations/{id}` is answered from `base_state.json` on disk
(`_conversation_info` → `_load_persisted_state_sync`), which still holds the
**pre-restart** agent — so right after a restart the API reports the *correct*
timeout. The moment anything actually uses the conversation,
`ConversationState.create()` runs `state.agent = agent` with the agent rebuilt
from `meta.json` (`event_service.py`), and `base_state.json` is overwritten
with it.

Net effect on `main`: a client can read back the right timeout while the agent,
on its next real request, uses the reverted one. Anyone verifying this by hand
should touch the conversation first, then read the timeout — otherwise the
stale value looks like a pass.

With this PR the two files agree, so cold and warm reads agree too.

## 5. Scope check

`noswitch` was also run against this PR to confirm it does not perturb the
path it is not meant to touch:

```
    mode: noswitch   repo: sdk-pr-4028
    declared AFTER restart WARM: 5    behavioural: 17.3s
    => TIMEOUT PRESERVED ACROSS RESTART: True
```

Identical to `main`'s result for the same scenario, as expected: the sync is a
no-op unless the live LLM object actually changed.
