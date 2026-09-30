# #5398 end-to-end evidence (2026-09-30)

Every run below was repeated on `main` (`d0f950059`) and on this branch, with the
same script, the canvas mock LLM (which records each completion request), and a
fresh state directory.

## Local agent-server: `.pr/launch_e2e.py`

What the model received for each source, compared with a stored `default` profile.

| Check | main | branch |
|---|---|---|
| `default-copy` (same contents) launches the same agent | PASS | PASS |
| `agent_settings` holding `default`'s resolved settings launches the same agent | PASS | PASS |
| stale `current_datetime` on `agent_settings` is replaced at launch | FAIL (model saw 2020-01-01) | PASS |
| stale `current_datetime` on a raw `agent` is replaced at launch | FAIL (model saw 2020-01-01) | PASS |
| a profile with a dangling LLM and a dangling MCP ref returns one 422 naming both | FAIL (only the MCP ref) | PASS |
| OpenAI gateway: its system text reaches the model | PASS | PASS |
| OpenAI gateway uses the model's LLM profile | PASS | PASS |
| `materialize` tools equal the launched agent's tools | FAIL (not reported) | PASS |
| memory preference applies to every source | PASS | PASS |
| `secret_refs` scopes the secrets the agent sees | PASS | PASS |

The gateway launches the global settings with `tools: null`, so on this
browser-capable host it now gets `browser_tool_set`, the same as a profile does.

## Docker runtime: `.pr/launch_docker_e2e.py`

The host runs natively in Docker runtime mode. Each conversation container is an
image built from the same commit, with and without chromium. The host can run
the browser. The container's HOME has no stores.

| Check | main | branch |
|---|---|---|
| host `materialize` leaves the browser to the container (`pending`) | FAIL | PASS |
| browser follows the container, not the host | FAIL without chromium (container given `browser_tool_set` it cannot run) | PASS |
| profile suffix reaches the model | PASS | PASS |
| provenance recorded in the container | PASS | PASS |
| memory preference forwarded | PASS | PASS |
| fresh timestamp on a profile launch | PASS | PASS |
| re-posting a live conversation with a now-broken profile returns 200, container keeps running | FAIL (422) | PASS |
| dangling refs fail on the host with one 422, no container started | FAIL (only the MCP ref) | PASS |
| `secret_refs` scopes secrets inside the container | PASS | PASS |
| `agent_settings` via the host: container finalizes (fresh timestamp, memory) | FAIL (stale timestamp) | PASS |
| raw `agent` via the host: container finalizes (fresh timestamp, memory) | FAIL (stale timestamp) | PASS |
| `agent_settings` default tools follow the container | FAIL with chromium (no browser) | PASS |

The branch passes 12/12 with both images. `main` passes 6/12 with each.

## Agent Canvas mock-LLM Playwright suite

`OH_AGENT_SERVER_LOCAL_PATH=<sdk> npx playwright test --config=playwright.mock-llm.config.ts`
on OpenHands/OpenHands `1ec86616b`:

| | passed | failed | did not run |
|---|---|---|---|
| main | 58 | 7 | 7 |
| branch | 54 | 8 | 10 |

The one extra failure is step 1 of `mock-llm-acp-agent.spec.ts`. It times out
clicking `Custom` in the Settings → Agent dropdown, because the option never
becomes stable, before anything is launched. Its steps 2–4 then do not run. Run
on its own, that spec fails the same way on `main` and on this branch (2 of 2
each), and it also failed on `main` `5ffdd29`. The other 7 failures are the same
on both. They are environmental on macOS: the folder browser does not follow
`/var` to `/private/var`, the local-path launcher omits the `<RUNTIME_SERVICES>`
block, and there are UI and ACP-mock steps.

## Unit suites

| Suite | main | branch |
|---|---|---|
| `tests/agent_server` | 2304 passed, 7 failed | 2327 passed, same 7 failed (`canvas_extensions`, macOS) |
| `tests/sdk` | 6618 passed, 1 failed | 6656 passed, same 1 failed (`test_truncate`) |
| `tests/cross` + `tests/workspace` | 698 passed | 698 passed |

## Stress suite

`tests/agent_server/stress/test_concurrent_conversations.py` fails with
`ConversationRunLimitExceeded` on `main` (5 of 5 local runs) and on this branch
(2 of 3). This is #5404, fixed by #5403.
