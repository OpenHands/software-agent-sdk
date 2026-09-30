# #5398 end-to-end evidence (2026-09-30)

Every run below was repeated on `main` (`5ffdd29`) and on this branch, with the
same script, the canvas mock LLM (which records each completion request), and a
fresh state directory.

## Local agent-server: `.pr/launch_e2e.py`

What the model received for each source, compared with a stored `default` profile.

| Check | main | branch |
|---|---|---|
| `default-copy` (same contents) launches the same agent | PASS | PASS |
| inline `agent_profile` launches the same agent | FAIL (422, unsupported) | PASS |
| `agent_settings` holding `default`'s resolved settings launches the same agent | PASS | PASS |
| stale `current_datetime` on `agent_settings` is replaced at launch | FAIL (model saw 2020-01-01) | PASS |
| stale `current_datetime` on a raw `agent` is replaced at launch | FAIL (model saw 2020-01-01) | PASS |
| per-launch `llm_profile_ref` override | FAIL (422, unsupported) | PASS |
| a profile with a dangling LLM and a dangling MCP ref returns one 422 naming both | FAIL (only the MCP ref) | PASS |
| OpenAI gateway launches the active profile (suffix, tools) | FAIL (global settings) | PASS |
| OpenAI gateway uses the model's LLM profile | PASS | PASS |
| `materialize` tools equal the launched agent's tools | FAIL (not reported) | PASS |
| memory preference applies to every source | FAIL (inline source unsupported) | PASS |
| `secret_refs` scopes the secrets the agent sees | PASS | PASS |

## Docker runtime: `.pr/launch_docker_e2e.py`

The host runs natively in Docker runtime mode. Each conversation container is an
image built from the same commit. The host can run the browser. The container
image used here has chromium removed, and its HOME has no stores.

| Check | main | branch |
|---|---|---|
| host `materialize` leaves the browser to the container (`pending`) | FAIL | PASS |
| browser follows the container, not the host | FAIL (container given `browser_tool_set` it cannot run) | PASS |
| profile suffix reaches the model | PASS | PASS |
| provenance recorded in the container | PASS | PASS |
| meta-profile routing works with no stores in the container | FAIL (not expressible on a profile) | PASS |
| memory preference forwarded | PASS | PASS |
| fresh timestamp on a profile launch | PASS | PASS |
| re-posting a live conversation with a now-broken profile returns 200, container keeps running | FAIL (422) | PASS |
| dangling refs fail on the host with one 422, no container started | FAIL (only the MCP ref) | PASS |
| `secret_refs` scopes secrets inside the container | PASS | PASS |
| `agent_settings` via the host: container finalizes (fresh timestamp, memory) | FAIL (stale timestamp) | PASS |
| raw `agent` via the host: container finalizes (fresh timestamp, memory) | FAIL (stale timestamp) | PASS |

With a chromium-capable container image, the branch passes every check as well.
A branch host with a `main` container image still launches every source (all
201). The finalize-only behaviors (timestamp refresh, browser default) then stay
as the old container implements them.

## Agent Canvas mock-LLM Playwright suite

`OH_AGENT_SERVER_LOCAL_PATH=<sdk> npx playwright test --config=playwright.mock-llm.config.ts`
on OpenHands/OpenHands `1ec86616b`:

| | passed | failed | did not run |
|---|---|---|---|
| main | 54 | 8 | 10 |
| branch | 54 | 8 | 10 |

The failure sets are identical. They are environmental on macOS: the folder
browser does not follow `/var` to `/private/var`, the local-path launcher omits
the `<RUNTIME_SERVICES>` block, and there are UI and ACP-mock steps.

## Unit suites

| Suite | main | branch |
|---|---|---|
| `tests/agent_server` | 2295 passed, 7 failed | 2324 passed, same 7 failed (`canvas_extensions`, macOS) |
| `tests/sdk` | 6618 passed, 1 failed | 6663 passed, same 1 failed (`test_truncate`) |
| `tests/cross` + `tests/workspace` | 697 passed, 1 failed | 697 passed, same 1 failed (gateway live test: macOS tmux socket path). It passes serially with a short `TMUX_TMPDIR` (21/21). |
