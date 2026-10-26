# Live Agent Canvas compatibility review

Verified on 2026-10-26 with the real `verify-openhands` CLI and DeepSeek flash. No mocked model responses, intercepted requests, injected application state, or Canvas production changes.

- Canvas: `ec0f70fe31b637e699ab3713ba477abe3c3d6b6f` (clean detached latest-main checkout), build inputs `2eb3fb8c7b4d1fca`.
- SDK: started at `7af842900b480f4028cb785a47c1abc68049b2c5` plus the response-authority client fix, committed during the run as `d352755baaef9db42d2f46dc7d99b163362f11bb`. Server code was unchanged by that fix. The stack was restarted after that commit and verified again.
- Skill: `/private/tmp/canvas-budget-review/.agents/skills/verify-openhands/SKILL.md`.
- Driver: `.agents/skills/verify-openhands/scripts/control-openhands` from that Canvas checkout, using its configured Playwright browser.
- Run: `/var/folders/15/pyrd6jhs3tnd259qd39xsplm0000gn/T/openhands-verify/budget-sdk-review`.
- Install/build: `npm ci --ignore-scripts`, automatic `npm run build:app` during launch. No dependency or source changes.

## Results

| Case | Cap (USD) | Final recorded cost (USD) | Canvas result |
| --- | --- | --- | --- |
| Normal UI creation, then follow-up | null | 0.001664442 | Both no-tool replies finished. Live Usage changed from $0.0016 after the first call (cost 0.001569678) to $0.0017 after the second. |
| API-arranged cap, prompt sent in UI | 0.05 | 0.001237374 | Finished; desktop and phone Usage showed $0.0012. |
| API-arranged tiny cap, prompt sent in UI | 0.00000001 | 0.002607948 | Exactly one think action, then MaxBudgetReached before another call; Usage showed the full $0.0026 and chat showed the budget-stop error. |

The overshoot is expected: the completed model call is charged, then the next step is stopped. Cost remains above the configured cap; it is not clamped.

Normal page reload preserved the capped conversation's cost. Offline/online browser-context toggles preserved the visible snapshot. The isolated stack was then restarted, doctor passed, the browser reloaded and opened a fresh WebSocket, and persisted overshoot cost/cap remained intact. This proves reload/reconnect recovery, not uninterrupted automatic recovery without a reload.

F07 Display Usage and Cost opened the desktop drawer and the phone `/panel` route. The phone Usage panel was 390x804 within a 390x844 viewport with no horizontal overflow. Screenshots were visually inspected; they contain only test prompts and model usage, with no credentials.

No page JavaScript errors occurred before restart. Expected DeepSeek `/api/llm/balance` 404s were observed, as documented by F27.usage-provider-balance. Restart produced the expected temporary connection failures; doctor confirmed recovery. One attempted tab click targeted a closed drawer; opening it through the real conversation menu succeeded. An initial API arrangement omitted required workspace and returned 422; the corrected request used a run-owned folder and returned 201. Neither was a product regression.

## Downstream UI work remains

The REST conversation resource exposes top-level `max_budget_per_run`. Current Canvas successfully performs native GETs for that resource and uses its existing stats to display actual cost, but still reads legacy `max_budget_per_task` for a cap. Therefore the new cap is **not displayed** yet. This is the intended later Canvas work, not a claimed completed budget UI.

Relevant current consumers are `src/api/conversation-service/agent-server-conversation-service.api.ts` (`normalizeMetrics`), `src/utils/conversation-metrics.ts` (`combineUsageMetrics`), `src/hooks/use-live-conversation-metrics.ts`, and `src/components/features/conversation/usage-panel/usage-panel.tsx`. No legacy cap was synthesized to make the UI show a budget.

The native request receipt records browser-issued GET endpoints/status. The CLI does not record response bodies; the cap and cost in `receipt.json` are independently read from those same canonical resources with its authenticated API command. Do not interpret the network receipt as a captured browser response body.

Recorded conversation cost totaled **$0.005509764**. This excludes two tiny profile-validation completions and automatic title generation; provider billing was not fetched.

## Reproduction

From a clean current Canvas checkout with the SDK branch available:

```sh
npm ci --ignore-scripts
export PATH="$PWD/.agents/skills/verify-openhands/scripts:$PATH"
control-openhands launch --new --sdk-path /path/to/budget-sdk --run-id budget-review
# Set RUN to the printed run directory. Always pass --run "$RUN".
control-openhands doctor --run "$RUN"
control-openhands llm preset deepseek --api-key-file /private/path/to/key --run "$RUN"
control-openhands onboard --skip --run "$RUN"
control-openhands conversation start --prompt 'Reply exactly BUDGET_UI_OK. Do not run any tools. Do not inspect files or run commands.' --wait --run "$RUN"
# Save the returned id as ID.
control-openhands browser click 'testid=chat-pane-header >> testid=ellipsis-button' --run "$RUN"
control-openhands browser click 'testid=conversation-name-context-menu >> testid=display-cost-button' --run "$RUN"
control-openhands browser text 'testid=usage-panel' --run "$RUN"
control-openhands conversation send "$ID" --prompt 'Reply exactly BUDGET_UI_SECOND_OK. Do not run any tools. Do not inspect files or run commands.' --wait --run "$RUN"
control-openhands browser text 'testid=usage-panel' --run "$RUN"
control-openhands api GET /api/agent-profiles --run "$RUN"
control-openhands fixture folder --name budget-capped --run "$RUN"
```

Use the returned profile UUID and folder path to write this non-secret arrangement request to `capped.json`:

```json
{"agent_profile_id":"<profile UUID>","workspace":{"working_dir":"<returned folder>"},"max_budget_per_run":0.05,"max_iterations":3,"autotitle":false}
```

```sh
control-openhands api POST /api/conversations --write --data @capped.json --pick id --run "$RUN"
# Save the returned id as CAPPED.
control-openhands conversation send "$CAPPED" --prompt 'Reply exactly CAPPED_UI_OK. Do not run any tools. Do not inspect files or run commands.' --wait --run "$RUN"
control-openhands browser click 'testid=chat-pane-header >> testid=ellipsis-button' --run "$RUN"
control-openhands browser click 'testid=conversation-name-context-menu >> testid=display-cost-button' --run "$RUN"
control-openhands browser text 'testid=usage-panel' --run "$RUN"
control-openhands api GET "/api/conversations/$CAPPED" --pick max_budget_per_run --run "$RUN"
control-openhands browser reload --run "$RUN"
control-openhands browser click 'testid=right-panel-toggle' --run "$RUN"
control-openhands browser text 'testid=usage-panel' --run "$RUN"
control-openhands browser network-state offline --run "$RUN"
control-openhands browser text 'testid=usage-panel' --run "$RUN"
control-openhands browser network-state online --run "$RUN"
control-openhands browser viewport phone --run "$RUN"
control-openhands browser goto "/conversations/$CAPPED" --run "$RUN"
control-openhands browser click 'testid=chat-pane-header >> testid=ellipsis-button' --run "$RUN"
control-openhands browser click 'testid=conversation-name-context-menu >> testid=display-cost-button' --expect-url '/panel$' --run "$RUN"
control-openhands browser bbox 'testid=usage-panel' --run "$RUN"
```

For overshoot, repeat the API arrangement with `max_budget_per_run: 0.00000001`, switch to desktop, and send exactly:

> First call think with thought "budget probe". Then in a separate later response reply BUDGET_OVER_DONE. Do not finish in the first response. Do not use any other tools, inspect files, or run commands.

Inspect `conversation events <overshoot-id> --kinds ActionEvent,ConversationErrorEvent`, open Usage with the menu, and compare actual cost with REST `stats`. Finally:

```sh
control-openhands restart --run "$RUN"
control-openhands doctor --run "$RUN"
control-openhands browser reload --run "$RUN"
control-openhands browser click 'testid=right-panel-toggle' --run "$RUN"
control-openhands browser text 'testid=usage-panel' --run "$RUN"
control-openhands stop --run "$RUN"
```

The owned stack's final cleanup receipt is `stop.json`. Four screenshots and bounded JSON receipts are kept beside this file; private keys, settings, server logs, and browser profile are excluded.
