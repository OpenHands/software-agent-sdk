# Browser tools and session recording

A Python program adds `Tool(name="browser_tool_set")` (or `Tool(name=BrowserToolSet.name)`, or `get_default_tools(enable_browser=True)`) to its agent. When the conversation resolves it, `BrowserToolSet.create()` starts one `BrowserToolExecutor` (a `browser-use` server driving a local Chromium over CDP) and returns 14 tools that share it: `browser_navigate`, `browser_click`, `browser_type`, `browser_get_state`, `browser_get_content`, `browser_scroll`, `browser_go_back`, `browser_list_tabs`, `browser_switch_tab`, `browser_close_tab`, `browser_get_storage`, `browser_set_storage`, `browser_start_recording` and `browser_stop_recording`. The executor is a class-level singleton, so sub-agents reuse the parent's browser. Chromium is found on `PATH`, in standard install locations or in the Playwright cache; if it cannot start, the conversation goes on without browser tools and without the browser prompt guidance. Each action has a timeout, repeated infrastructure failures restart the browser session, and observations carry page text plus an optional screenshot that is sent to the LLM as an image and saved to disk. Recording injects rrweb into every page, flushes captured events every 5 seconds to numbered JSON files under `.agent_tmp/browser_observations/recording-<timestamp>/`, and returns event and file counts on stop. `conversation.close()` closes the browser.

Source: `openhands-tools/openhands/tools/browser_use/`, `openhands-tools/openhands/tools/preset/default.py`, `openhands-sdk/openhands/sdk/tool/defaults.py`

Needs: `chromium` (every real browser action), `llm` (examples, t05, t06), `network` (example 15, example 38, t06; recording loads rrweb from unpkg.com)

Examples: `examples/01_standalone_sdk/15_browser_use.py`, `examples/01_standalone_sdk/38_browser_session_recording.py`

## Sub-features

- `S15.toolset-create`: `BrowserToolSet.create(conv_state)` returns the 14 browser tools, all bound to one executor, each with the browser prompt guidance and an MCP-convertible schema; the set takes no LLM-facing parameters and is registered on import. Unit: `tests/tools/browser_use/test_browser_toolset.py` (`test_browser_toolset_create_includes_all_browser_tools`, `test_browser_toolset_create_tools_have_shared_executor`, `test_browser_toolset_create_tools_can_generate_mcp_schema`), `tests/cross/test_automatic_registration.py::test_browser_tool_automatic_registration`. Example: `examples/01_standalone_sdk/15_browser_use.py`. Real-LLM: `tests/integration/tests/t05_simple_browsing.py`.
- `S15.shared-executor`: repeated `create()` calls (parent and sub-agents) reuse one executor; construction is serialized without holding the shared lock; a second call's executor config is ignored with a warning; a closing executor is waited for before a new one is built. Unit: `tests/tools/browser_use/test_browser_toolset.py` (`test_browser_toolset_create_multiple_calls_share_executor`, `test_browser_toolset_shared_executor_survives_multiple_subagents`, `test_browser_toolset_warns_when_config_ignored`, `test_browser_toolset_create_waits_for_shared_executor_close`). Example: none. Real-LLM: none.
- `S15.degrade-on-failure`: when the executor fails to start, `create()` logs a warning and returns no tools, tool resolution for the rest of the agent succeeds, and the system prompt drops the browser guidance; a profile pinned to the browser still resolves on a browserless runtime. Unit: `tests/tools/browser_use/test_browser_toolset.py` (`test_browser_toolset_create_degrades_when_executor_fails`, `test_resolve_tool_survives_browser_executor_failure`, `test_no_browser_guidance_when_browser_fails_to_start`, `test_migrated_profile_with_pinned_browser_resolves_on_browserless_runtime`). Example: none. Real-LLM: none.
- `S15.chromium-detection`: `BrowserToolExecutor.check_chromium_available()` (cached) finds `google-chrome`, `chrome`, `chromium`, `chromium-browser` or `microsoft-edge` on `PATH`, standard Linux, macOS and Windows install paths (Edge on Windows), or Playwright's cache, and returns `None` otherwise; `BrowserToolSet.is_usable()` and `list_usable_tools()` follow it; a missing binary raises with install instructions (`uvx playwright install chromium`). Unit: `tests/tools/browser_use/test_chromium_detection.py`, `tests/cross/test_automatic_registration.py::test_browser_tool_usable_listing_respects_chromium_availability`. Example: none. Real-LLM: none.
- `S15.executor-config`: `headless`, `allowed_domains`, `session_timeout_minutes`, `init_timeout_seconds`, `action_timeout_seconds` (must be positive), `inject_scripts` and extra keyword arguments reach the browser-use server; each executor gets its own profile directory unless `user_data_dir` is given; initialization is lazy and idempotent and times out cleanly. Unit: `tests/tools/browser_use/test_browser_executor.py` (`test_browser_executor_config_passing`, `test_browser_executor_rejects_non_positive_action_timeout`, `test_browser_executor_initialization_lazy`, `test_browser_executor_initialization_idempotent`), `tests/tools/browser_use/test_browser_initialization.py`. Example: none. Real-LLM: none.
- `S15.action-routing`: each action type is routed to its server method, an unsupported action and a raised exception come back as error observations, and a slow action is cut off at `action_timeout_seconds`. Unit: `tests/tools/browser_use/test_browser_executor.py` (`test_browser_executor_action_routing_*`, `test_browser_executor_unsupported_action_handling`, `test_browser_executor_error_wrapping`, `test_browser_executor_timeout_wrapping_live_service`). Example: none. Real-LLM: none.
- `S15.session-recovery`: consecutive timeouts reset the browser session after the third; the last try before a reset uses a fixed 30-second timeout (`DEGRADED_TIMEOUT_SECONDS`, shorter than the 300-second default but longer than a smaller `action_timeout_seconds`); a success resets the counter; ordinary action errors do not count. Unit: `tests/tools/browser_use/test_browser_executor.py` (`test_issue_2412_consecutive_failures_reset_session`, `test_issue_2412_success_resets_failure_counter`, `test_issue_2412_action_errors_do_not_trigger_reset`, `test_issue_2412_degraded_timeout_after_failures`). Example: none. Real-LLM: none.
- `S15.navigate-and-read`: `browser_navigate` (optionally in a new tab), `browser_get_state` (indexed interactive elements, optional screenshot), `browser_get_content` (markdown, `extract_links`, `start_from_char` paging), `browser_scroll` and `browser_go_back` work on a real page. Unit: `tests/tools/browser_use/test_browser_executor_e2e.py` (`test_navigate_action`, `test_get_state_action`, `test_get_state_with_screenshot`, `test_get_content_action`, `test_scroll_action`, `test_go_back_action`; skipped without Chromium). Example: `examples/01_standalone_sdk/15_browser_use.py`. Real-LLM: `tests/integration/tests/t05_simple_browsing.py`, `tests/integration/tests/t06_github_pr_browsing.py`.
- `S15.interact`: `browser_click` (by element index, optionally into a new tab) and `browser_type` change a real page, and concurrent actions on one executor stay consistent. Unit: `tests/tools/browser_use/test_browser_executor_e2e.py` (`test_click_action`, `test_type_action`, `test_concurrent_actions`; skipped without Chromium). Example: none. Real-LLM: `tests/integration/tests/t05_simple_browsing.py`.
- `S15.tabs`: `browser_list_tabs`, `browser_switch_tab` and `browser_close_tab` manage tabs by id. Unit: `tests/tools/browser_use/test_browser_executor_e2e.py` (`test_navigate_new_tab`, `test_list_tabs_action`, `test_switch_tab_action`, `test_close_tab_action`; skipped without Chromium). Example: none. Real-LLM: none.
- `S15.storage`: `browser_get_storage` returns cookies and local/session storage and `browser_set_storage` restores a saved `storage_state`. Unit: `tests/tools/browser_use/test_browser_executor_e2e.py` (`test_get_storage_action`, `test_set_storage_action`; skipped without Chromium). Example: none. Real-LLM: none.
- `S15.observation`: `BrowserObservation` renders text or error, turns `screenshot_data` into an image content item with the detected MIME type, truncates long text, handles an empty screenshot, and writes the screenshot as `browser_screenshot_<hash>.<ext>` to the conversation's observation directory. Unit: `tests/tools/browser_use/test_browser_observation.py`, `tests/tools/browser_use/test_browser_executor_e2e.py::TestBrowserExecutorE2E::test_save_screenshot` (skipped without Chromium). Example: none. Real-LLM: none.
- `S15.recording-start-stop`: `browser_start_recording` initializes the browser and injects rrweb; `browser_stop_recording` returns a summary with event and file counts; stopping without a recording is an error. Unit: `tests/tools/browser_use/test_browser_executor.py` (`test_start_recording_initializes_session`, `test_stop_recording_returns_summary_with_event_counts`, `test_stop_recording_without_active_session_returns_error`), `tests/tools/browser_use/test_browser_executor_e2e.py` (`test_start_recording`, `test_stop_recording_without_start`, `test_recording_captures_events`, `test_recording_save_to_file`; skipped without Chromium). Example: `examples/01_standalone_sdk/38_browser_session_recording.py`. Real-LLM: none.
- `S15.recording-storage`: events flush periodically (`flush_interval_seconds`, default 5) into numbered, timestamped JSON chunks; each recording gets its own `recording-<timestamp>` subfolder; concurrent flushes do not corrupt the buffer; the reported file count matches the files on disk. Unit: `tests/tools/browser_use/test_recording_flush.py`. Example: `examples/01_standalone_sdk/38_browser_session_recording.py`. Real-LLM: none.
- `S15.cleanup`: `executor.close()` (and `__del__`) closes all browser sessions, falls back to `close_browser`, always stops the async executor, releases the shared executor, and serializes concurrent closes; `conversation.close()` triggers it. Unit: `tests/tools/browser_use/test_browser_cleanup.py`, `tests/tools/browser_use/test_browser_executor_e2e.py::TestBrowserExecutorE2E::test_executor_initialization_and_cleanup` (skipped without Chromium). Example: `examples/01_standalone_sdk/38_browser_session_recording.py`. Real-LLM: none.
- `S15.prompt-guidance`: the browser guidance renders once in the system prompt, before external-service guidance, and survives a custom system prompt and a persona. Unit: `tests/tools/browser_use/test_browser_toolset.py` (`test_browser_guidance_renders_once_before_external_services`, `test_custom_system_prompt_keeps_browser_guidance`, `test_persona_keeps_browser_guidance`). Example: none. Real-LLM: none.
- `S15.preset-browser`: `get_default_tools(enable_browser=True)` appends `browser_tool_set` before the sub-agent tool, `get_default_agent(llm, cli_mode=True)` leaves it out, and `register_builtins_agents(enable_browser=False)` skips the browser-only `web-researcher` sub-agent. Unit: `tests/sdk/tool/test_defaults.py::test_explicit_browser_appends_before_sub_agents`, `tests/cross/test_default_tool_names.py::test_browser_name_matches_browser_tool_set`, `tests/tools/test_builtin_agents.py::test_register_builtins_agents_skips_web_researcher_without_browser`. Example: `examples/01_standalone_sdk/15_browser_use.py`. Real-LLM: none.

## How to get to it (agent POV)

- `from openhands.tools.browser_use import BrowserToolSet`; `Agent(llm=..., tools=[Tool(name=BrowserToolSet.name)])`. Executor options go in `params`: `Tool(name=BrowserToolSet.name, params={"headless": False, "allowed_domains": ["example.com"], "action_timeout_seconds": 60})`. Covers every sub-feature.
- Presets: `get_default_tools(enable_browser=True)` and `get_default_agent(llm)` (browser on unless `cli_mode=True`). `S15.preset-browser`.
- Without the LLM: `conversation.execute_tool("browser_navigate", BrowserNavigateAction(url="about:blank"))` warms the browser before `run()` (example 38). Action classes import from `openhands.tools.browser_use.definition` (`BrowserNavigateAction`, `BrowserClickAction`, `BrowserGetStateAction`, `BrowserStartRecordingAction`, ...).
- Directly: `BrowserToolExecutor(headless=True, ...)` from `openhands.tools.browser_use.impl`, called with an action; `BrowserToolExecutor.check_chromium_available()` to probe.
- Files: screenshots under the conversation's observation persistence directory; recordings under `BROWSER_RECORDING_OUTPUT_DIR` (`.agent_tmp/browser_observations`, relative to the process working directory).
- Real-LLM runner: `tests/integration/run_infer.py --eval-ids t05_simple_browsing,t06_github_pr_browsing` (those tests set `enable_browser = True`).

## Driving it

Preconditions:
- `uv sync --dev` has run; `control-sdk` is on `PATH` (`export PATH="$PWD/.agents/skills/verify-sdk/scripts:$PATH"`). `sdk-map run` does both.
- A Playwright Chromium in `$PLAYWRIGHT_BROWSERS_PATH` (here `/opt/pw-browsers`; elsewhere `uvx playwright install chromium`, which needs `network`). The SDK's detector does not read that variable, so the programs link the run's `~/.cache/ms-playwright` to it (`programs/S15/site.py`). Pages come from a local HTTP server the programs start, never the internet.
- Live bullets: `DEEPSEEK_API_KEY` exported.

- **Browse a local site (`S15.toolset-create`, `S15.navigate-and-read`, `S15.interact`, `S15.tabs`, `S15.storage`, `S15.observation`, `S15.recording-start-stop`, `S15.prompt-guidance`, `S15.action-routing`, `S15.cleanup`).** A scripted agent with `Tool(name="browser_tool_set")` and a real headless Chromium works through a two-page site. It navigates, reads state with a screenshot, reads content with links and from character 2,000, and scrolls. It types a name and clicks Greet, follows a link and goes back, opens a second tab, reads storage, restores a saved `storage_state`, clicks a missing element, and stops a recording that never started. Then it switches to the first tab and closes the second. Element indexes and tab ids are known only once a page loads, so each turn swaps in the next scripted model with `conversation.switch_llm`. The agent follows the flow the browser guidance prescribes: `browser_get_state` after an interaction, then `browser_get_content`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S15/browse --timeout 300 \
    --check status eq finished --check browser_tools len-eq 14 --check shared_executors eq 1 \
    --check mcp_schemas eq true --check guidance_on_all eq true \
    --check steps.navigate.text contains "Navigated to: http://127.0.0.1" \
    --check steps.state.text contains '"interactive_elements"' --check steps.state.text contains "your name" \
    --check steps.state.content_types contains ImageContent \
    --check screenshot_files len-eq 1 --check screenshot_named_in_state eq true \
    --check steps.content.text contains "# Home Page" --check steps.content.text contains "[Go to page two]" \
    --check steps.content_paged.text contains "started from char 2,000" \
    --check steps.content_paged.text not-contains "# Home Page" \
    --check steps.scroll.text eq "Scrolled down" \
    --check steps.type.text contains "Typed 'Ada' into element" --check steps.click.text contains "Clicked element" \
    --check steps.after_click.text contains "Hello Ada" \
    --check steps.state_page2.text contains page2.html --check steps.on_page2.text contains "# Second Page" \
    --check steps.back.text eq "Navigated back" --check steps.state_after_back.text contains index.html \
    --check steps.new_tab.text contains "Opened new tab with URL" --check tabs_seen eq 2 \
    --check steps.switch_tab.text contains "Switched to tab" --check steps.close_tab.text contains "Closed tab" \
    --check tabs_left eq 1 \
    --check storage_has_page_values eq true --check steps.set_storage.text eq "Storage set successfully" \
    --check storage_restored eq true \
    --check steps.missing_element.text contains "Element with index 99999 not found" \
    --check steps.stop_unstarted.text contains "Not recording" \
    --check chrome_processes_before_close ge 1 --check chrome_processes_after_close eq 0
  ```
  The page sets the cookie `session=abc123` and `localStorage.theme=dark` (`storage_has_page_values`); the restore adds the cookie `restored` and the key `restored_key`.
- **No Chromium (`S15.degrade-on-failure`, `S15.chromium-detection`, `S15.prompt-guidance`).** The run's `HOME` has no Playwright cache and no Chromium is on `PATH` or in the system paths. A scripted agent with the terminal and the browser tool set still runs its terminal command.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S15/browser_setup --arg nochrome \
    --check detected eq null --check is_usable eq false \
    --check usable_tools contains terminal --check usable_tools not-contains browser_tool_set \
    --check status eq finished --check tools_map eq '["finish", "terminal", "think"]' \
    --check terminal_text contains "still working" --check guidance_in_prompt eq false \
    --check warnings.0 contains "Browser tools are unavailable" \
    --check warnings.0 contains "uvx playwright install chromium"
  ```
- **One browser shared between conversations (`S15.shared-executor`, `S15.cleanup`).** Two conversations open at once with different browser `params`, then a third after both close.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S15/browser_setup --arg shared --timeout 300 \
    --check detected matches "ms-playwright/chromium" \
    --check same_executor eq true --check second_sees_first_page eq true --check ignored_warning eq true \
    --check third_new_executor eq true --check third_is_error eq false --check third_text contains page2.html
  ```
- **Executor options (`S15.executor-config`).** `params={"allowed_domains": ["127.0.0.1"], "inject_scripts": [...], "action_timeout_seconds": 12}`. The agent opens the local page and then `https://example.com/`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S15/browser_setup --arg config --timeout 300 \
    --check steps.allowed.is_error eq false --check steps.content.text contains "INJECTED MARKER" \
    --check steps.blocked_state.text contains about:blank \
    --check steps.blocked_domain.text not-contains "Navigated to: https://example.com/"
  ```
  Expected: the page outside `allowed_domains` is not opened, and the observation says so. Known failure (reproduced 2026-10-13, not yet filed): the browser does stay on `about:blank`, and browser-use logs that the navigation was blocked by security policy, but `browser_navigate` returns "Navigated to: https://example.com/" with `is_error` false. The agent is told it reached a page it never reached.
- **A hung page: timeouts, reset and recovery (`S15.session-recovery`, `S15.action-routing`, `S15.cleanup`).** With `action_timeout_seconds` 12, the agent opens a page whose script never yields, then makes three more calls. The third call triggers the session reset. Then it opens a normal page and reads its state. The program counts this run's Chromium processes after `conversation.close()`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S15/browser_setup --arg hang --timeout 400 \
    --check steps.allowed.is_error eq false \
    --check steps.hang1.is_error eq true --check steps.hang1.text contains "timed out after 12 seconds" \
    --check steps.hang2.text contains "timed out after 12 seconds" \
    --check steps.hang3.text contains "timed out after 30 seconds" \
    --check steps.hang3.text contains "The browser session has been reset" --check reset_warning eq true \
    --check steps.recovered.is_error eq false --check steps.recovered_state.text contains page2.html \
    --check chrome_processes_after_close eq 0
  ```
  Expected: after the reset the browser restarts on the next action ("The browser will be restarted on the next action. Please retry your action."), and closing the conversation leaves no Chromium behind. Known failure (reproduced 2026-10-13, not yet filed): both later calls time out again after 12 s. In a run with a 40 s timeout, the first call after the reset failed and the second worked. The reset's cleanup (`run_async(self.cleanup, timeout=5.0)`) does not end the hung Chromium. It is still running after `conversation.close()` and after the program exits, re-parented to PID 1. Four of four hung-page runs left one behind.
- **Session recording (`S15.recording-start-stop`, `S15.recording-storage`), blocked: needs `network` to unpkg.com.** Recording loads rrweb from `https://unpkg.com/rrweb@2.0.0-alpha.17/...`, which the egress proxy refuses here. A scripted agent starts recording on the local page, scrolls, changes page and comes back, waits past one 5-second flush, and stops.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S15/browser_setup --arg record --timeout 300 \
    --check steps.start.text not-contains "Error" --check steps.stop.text contains "Recording stopped" \
    --check recording_folders len-eq 1 --check recording_files len-ge 1 --check events_on_disk ge 1
  ```
  Here `start` answers "Error: Unable to start recording. The rrweb library failed to load from CDN." and `stop` answers "Error: Not recording.", both with `is_error` false, and an empty `recording-<timestamp>` folder is still created.
- **Presets (`S15.preset-browser`).**
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S15/browser_setup --arg preset \
    --check with_browser_and_subagents eq '["terminal", "file_editor", "task_tracker", "browser_tool_set", "task_tool_set"]' \
    --check default_agent_tools contains browser_tool_set --check cli_agent_tools not-contains browser_tool_set \
    --check builtins_without_browser not-contains web-researcher --check added_with_browser eq '["web-researcher"]'
  ```
- **Browsing with a real model on a local page (`S15.navigate-and-read`, `S15.interact`).** Live. t05 serves its own page on `localhost:8000` and asks the agent to read it.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  mkdir -p "$SDK_VERIFY_RUN/home/.cache"
  ln -sfn "${PLAYWRIGHT_BROWSERS_PATH:-/opt/pw-browsers}" "$SDK_VERIFY_RUN/home/.cache/ms-playwright"
  control-sdk exec --model-env -- python tests/integration/run_infer.py --llm-config '{"model": "deepseek/deepseek-flash"}' --eval-ids t05_simple_browsing
  ```
  The detector looks only in `~/.cache/ms-playwright` (the run's `HOME`), hence the link the programs also make.
- **The browsing examples and the GitHub task (`S15.navigate-and-read`, `S15.recording-start-stop`, `S15.recording-storage`), blocked: need `network` (openhands.dev, unpkg.com, github.com) and a real model.**
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env -- python examples/01_standalone_sdk/15_browser_use.py
  control-sdk exec --model-env --expect-output "recording" -- python examples/01_standalone_sdk/38_browser_session_recording.py
  control-sdk exec --model-env -- python tests/integration/run_infer.py --llm-config '{"model": "deepseek/deepseek-flash"}' --eval-ids t06_github_pr_browsing
  ```
  Example 15 summarizes the openhands.dev blog. Example 38 prints the recording folder, the file and event counts and the rrweb event types. t06 must describe a GitHub PR.

## Gotchas

- `examples/01_standalone_sdk/15_browser_use.py` is in `_EXCLUDED_EXAMPLES` of `tests/examples/test_examples.py`, so CI never runs it; example 38 does run on the `test-examples` label and exits 1 if the browser cannot initialize.
- Recordings are written to `.agent_tmp/browser_observations/` relative to the process working directory, not under `persistence_dir`, although example 38's comments say "persistence directory". Folders from earlier runs stay there; the example reports only the newest `recording-*` folder by name.
- Recording loads rrweb from unpkg.com (`RecordingConfig.cdn_url`, 10 s load timeout); if the CDN is unreachable, `browser_start_recording` fails with an error message instead of recording.
- The executor is a class-level singleton (`BrowserToolSet._shared_executor`): in one process, a second conversation reuses the first browser and ignores new `params`. Tests reset it explicitly.
- A missing Chromium is not an error at agent build time: the tools silently disappear (warning only), and the agent works without them. Check `list_usable_tools()` or the warning before blaming the model.
- The integration runner never enables the browser for the default preset (skip in `tests/integration/test_tool_presets.py`, issue #2124 in its message); only t05 and t06 turn it on themselves. t05 binds port 8000 on 127.0.0.1.
- The `browser-use` dependency logs heavily; `openhands-tools/openhands/tools/browser_use/logging_fix.py` tames it, so expect fewer logs than browser-use's defaults.
- The Chromium detector ignores `PLAYWRIGHT_BROWSERS_PATH`: it looks on `PATH`, in system install paths and in `~/.cache/ms-playwright` only. With browsers installed elsewhere (here `/opt/pw-browsers`), link that directory to `~/.cache/ms-playwright` or the tools silently disappear.
- Browser-use's own failures come back as plain text with `is_error` false: "Element with index 99999 not found", "Error: Not recording. Call browser_start_recording first.", "Error: Unable to start recording. The rrweb library failed to load from CDN." Only timeouts and raised exceptions set `is_error`.
- `browser_get_content` right after a `browser_click` can show the page from before the click; the browser guidance prescribes `browser_get_state` after each interaction, and with it the content is current.
- A failed `browser_start_recording` still creates an empty `recording-<timestamp>` folder.
- The shared executor keeps the first conversation's `full_output_save_dir`, so a later conversation's screenshots land in the first one's observation directory.
