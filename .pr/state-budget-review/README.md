# State-owned budget: focused live verification

Verified on 2026-10-26 against SDK `c13bcc6a3e7670cfe7b5f6c472d6183cb4105b35` and Canvas `d5aef0488bca7f7e7a55e9a10cc420bc6c46a4a9`. Four UI checks and three server/SDK assertions passed. Actual conversation spend was **$0.002609748**, plus tiny provider-profile validation requests, below the $0.02 allowance.

The cap persisted in `base_state.json`, and neither server conversation's `meta.json` contained any budget-named key, including nested keys. Local resume and mutation used the same persisted state. Canvas still displayed the cap, updated its percentage live, retained it after restart, and displayed full overshoot cost.

Both requested verification skills were used: Canvas `.agents/skills/verify-openhands/SKILL.md` and SDK `.agents/skills/verify-agent-server/SKILL.md`. The latter attached to the same run-owned Agent Server; this was a focused check, not a full API or feature-map audit. Real `deepseek/deepseek-flash` calls were used with bounded no-tool prompts, except the explicitly requested `think` action. No response interception, model stubs, browser-state injection, or source edits occurred.

## Observations

| Check | Actual result | Evidence |
| --- | --- | --- |
| Normal UI cap | Cost $0.00125133 / cap $0.05; 2.50%. Desktop and phone Usage fit within the viewport. | [Initial receipt](normal-observation.json), [desktop](normal-desktop.png), [phone after follow-up](normal-phone.png) |
| Live UI update | Direct composer follow-up, without reload: cost $0.00125133 → $0.001331268; cap $0.05; 2.50% → 2.66%. Four-decimal cost text remains $0.0013 because of rounding. | [After receipt](normal-live-observation.json), [video](state-live.mp4), [GIF](state-live.gif) |
| Restart/reload | Normal cap $0.05 and actual cost $0.001331268 retained. Both conversations' selected persisted cap/cost facts were unchanged across backend restart. Usage was explicitly reopened after reload. | [Before persistence](persistence-pre-restart.json), [after persistence](persistence-post-restart.json), [UI](normal-restart-observation.json), [video](state-restart.mp4) |
| Overshoot | After restart, one think action cost $0.00127848 against cap $0.0001: 1278.48%. Actual cost and percentage remain visible; only bar width clamps to 100%. Existing MaxBudgetReached stops the next step. | [Receipt](over-observation.json), [events](over-events.json), [desktop](over-desktop.png), [phone](over-phone.png) |
| Attached REST reads | Normal and tiny caps read back as 0.05 and 0.0001 before/after restart; finished/error statuses match their runs. | [Normal](server-normal-get.json), [normal after restart](server-normal-post-restart.json), [tiny cap after restart](server-over-post-restart.json), [overshoot](server-over-get.json) |
| Persisted ownership | Final `base_state.json` caps/costs match REST; recursive scans of both `meta.json` objects find no budget key paths. Only these selected facts are published. | [Persistence facts](persistence-final.json) |
| Local SDK, no LLM call | State mutation is visible through the property; property mutation persists 0.07; omission on resume retains 0.07; explicit 0.08 override persists; clearing to null survives another resume. | [Runnable probe](local-state-probe.py), [result](local-state-summary.json) |

The [Canvas ledger](canvas-ledger.md) has four pass rows. The [three Agent Server entry rows](server-ledger-rows.json) are all passes; its [aggregate report](server-ledger.json) groups them under one ad-hoc budget feature. These proposed IDs are PR evidence labels, not additions to the permanent map.

The previous wider five-theme/planner verification remains historical evidence for its original SDK revision. This focused rerun does not claim those scenarios were repeated. No enforcement behavior changed to prevent an in-flight LLM call from overshooting.

## Reproduction

Use the exact commits above, Node 24, installed Canvas dependencies, and the SDK checkout's built `.venv`. `C` and `S` below name the documented CLI scripts; every command targets its explicit owned run.

```sh
C=/path/to/canvas/.agents/skills/verify-openhands/scripts/control-openhands
S=/path/to/sdk/.agents/skills/verify-agent-server/scripts/control-agent-server
"$C" launch --new --run-id state-budget-review --min-free-mb 1000 --sdk-path /path/to/sdk
# Set BUDGET_RUN to the printed run directory.
"$C" doctor --run "$BUDGET_RUN"
"$C" onboard --skip --run "$BUDGET_RUN"
"$C" llm preset deepseek --api-key-file /private/path/to/deepseek.key --run "$BUDGET_RUN"
```

The fresh default agent profile referenced missing LLM profile `default`. Repoint it through supported APIs: GET `/api/agent-profiles/default`, take `body.profile`, set only `llm_profile_ref` to `deepseek-flash`, and POST that profile object to the same path with `--write`. Then use its UUID and `fixture folder --name budget-ui` result in each create request:

```json
{"agent_profile_id":"<default profile UUID>","workspace":{"working_dir":"<run fixture folder>"},"max_budget_per_run":0.05,"max_iterations":3,"autotitle":false}
```

Create the second fixture with cap `0.0001`. Normal UI creation uses `conversation send <normal-id> --prompt 'Reply exactly STATE_BUDGET_OK. Do not run any tools. Do not inspect files or run commands.' --wait`. Open Usage through the conversation menu and verify `browser bbox testid=usage-panel` reports `insideViewport:true`. Inspect a screenshot before recording.

For the no-reload follow-up, record the visible Usage panel and composer, then run:

```sh
"$C" browser fill testid=chat-input 'Reply exactly STATE_LIVE_OK. Do not run any tools. Do not inspect files or run commands.' --run "$BUDGET_RUN"
"$C" browser click testid=submit-button --run "$BUDGET_RUN"
"$C" conversation wait "$NORMAL" --fresh --run "$BUDGET_RUN"
```

Pause the recording during `restart`, resume it, then `browser reload` and reopen Usage with `right-panel-toggle`. On phone, the selected Usage panel is opened using the same toggle. Selecting an already active Usage menu item alone can leave the chat visible; one temporary-driver lookup hit that condition and was corrected through the real panel control.

Overshoot prompt:

> First call think with thought "budget probe". Then in a separate later response reply DONE. Do not finish in the first response. Do not use any other tools, inspect files, or run commands.

The Agent Server CLI attached directly to the returned `agentServer` port. Set `STATE_BUDGET_SESSION_KEY` privately from the Canvas run's session key, without printing it or passing its value in command arguments:

```sh
"$S" attach --url http://127.0.0.1:18801 --key-env STATE_BUDGET_SESSION_KEY --name state-budget-review --print-run
# Set SERVER_RUN to the returned attachment directory.
"$S" doctor --run "$SERVER_RUN"
"$S" api GET "/api/conversations/$NORMAL" --expect 200 --check max_budget_per_run eq 0.05 --quiet --run "$SERVER_RUN"
"$S" exec --run "$SERVER_RUN" --cwd /path/to/sdk --expect-output LOCAL_STATE_BUDGET_OK -- /path/to/sdk/.venv/bin/python .pr/state-budget-review/local-state-probe.py "$BUDGET_RUN/private/local-state-probe"
```

The persisted server facts were read from this run's `private/state/dev_conversations/<UUID-without-hyphens>/base_state.json` and `meta.json`; raw state objects were not published or changed. The local probe creates its own separate run-owned persistence directory and does not call `send_message`, `run` or an LLM completion.

## Health, checks and cleanup

Final [Canvas doctor](canvas-doctor-final.json) and [attached Agent Server doctor](server-doctor-final.json) passed. The Canvas served fingerprint remained `c4139faed8d2b9d7`; its source inputs stayed unchanged. Agent Server reports package version 1.58.2 and `build_git_sha:unknown` in this local launch, so the attached doctor does not independently attest the SDK commit. The SDK checkout was explicitly selected at launch and remained at the stated frozen source SHA.

[Browser diagnostics](browser-errors.json) show zero page JavaScript errors. Other recorded errors are the known unsupported DeepSeek balance endpoint and expected transient connection refusals during the deliberate restart; [WebSocket lifecycle](websocket-lifecycle.json) records subsequent opened connections. The report claims reload/reopen recovery, not automatic panel restoration or uninterrupted recovery without reload.

[Artifact syntax checks](artifact-checks.json) ran `node --check meter-observer.js` and `python -m py_compile local-state-probe.py`; repository pre-commit hooks were also run on edited evidence files. The parent task's [source checks](source-checks.txt) record the focused product test commands and counts separately, including overlapping reruns.

The published live recording is 15.5 seconds with no manual pause. The six-second restart recording cuts 10.8 seconds while the backend restarts; the CLI also trims still lead-ins. Every published screenshot and representative first/middle/last video frames were visually inspected.

The [attachment stop](server-attachment-stop.json) ends only its own helper context; the [Canvas stop](canvas-stop.json) confirms the owned browser/launcher stopped and all five ports closed. Only `publish-manifest.json` entries are intended for publication. The machine-specific driver, raw logs/state, private profiles/keys and command scratch log are excluded. [Privacy scan](privacy-scan.json) checks every published file byte, including media metadata, against the actual DeepSeek and run session keys.

This report was generated by an AI agent (OpenHands) on behalf of the requester.
