# #5398 end-to-end evidence (2026-10-02, after merging `main` `048c9be`)

Every run below was repeated on `main` (`048c9be`) and on this branch
(`7554ba94e`), with the same script, the canvas mock LLM (which records each
completion request), and a fresh state directory.

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
| `materialize` tools equal the launched agent's tools | PASS | PASS |
| memory preference applies to every source | PASS | PASS |
| `secret_refs` scopes the secrets the agent sees | PASS | PASS |

Branch 10/10, `main` 7/10.

## Docker runtime: `.pr/launch_docker_e2e.py`

The host runs natively in Docker runtime mode. Each conversation container is an
image built from the same commit (stock `1.46.0-python` plus the commit's
packages), with chromium or with it removed. The host can run the browser. The
container's HOME has no stores.

The host config says whether the image ships chromium
(`OH_CONVERSATION_IMAGE_HAS_BROWSER`) and whether conversations may get the
browser (`OH_ENABLE_BROWSER`). Four scenarios:

| Scenario | main | branch |
|---|---|---|
| chromium image, config says it has chromium | 9/13 | 13/13 |
| no chromium, config says none | 9/13 | 13/13 |
| no chromium, config wrongly says it has chromium | 6/12 | 12/12 |
| chromium image, host `OH_ENABLE_BROWSER=false` | 9/13 | 13/13 |

The four checks `main` fails in every scenario:

| Check | main | branch |
|---|---|---|
| re-posting a live conversation with a now-broken profile returns 200, container keeps running | FAIL (422) | PASS |
| dangling refs fail on the host with one 422, no container started | FAIL (only the MCP ref) | PASS |
| `agent_settings` via the host: container finalizes (fresh timestamp, memory) | FAIL (stale timestamp) | PASS |
| raw `agent` via the host: container finalizes (fresh timestamp, memory) | FAIL (stale timestamp) | PASS |

With a wrong config (third row), `main` gives a container that cannot run
chromium `browser_tool_set`, for both the profile and the `agent_settings`
launch. On the branch the host preview still follows the config, but the
container leaves the browser out. In every other scenario the host preview's
tools equal the container's launch on both.

## Unit suites

| Suite | branch | main |
|---|---|---|
| `tests/agent_server` | 2407 passed, 7 failed (`canvas_extensions`) | the same 7 fail |
| `tests/sdk` | 6850 passed, 1 failed (`test_truncate`) | the same 1 fails |
| `tests/cross` + `tests/workspace` (serial) | 699 passed | not re-run |

On `main` only the failing tests were re-run. They fail the same way there, on
the same macOS machine.

## Not re-run after the merge

The Agent Canvas mock-LLM Playwright suite ran before the merge, on
OpenHands/OpenHands `1ec86616b`: branch 54 passed, 8 failed, 10 did not run;
`main` 58, 7, 7. The extra failure is a dropdown in Settings → Agent that never
becomes stable, before any launch. It fails the same way on `main` when run
alone.
