# Core tools and tool presets

A Python program gives an agent its working tools by name: `Agent(llm=..., tools=[Tool(name="terminal"), Tool(name="file_editor"), Tool(name="task_tracker")])`, or through a preset (`get_default_agent(llm)`, `get_gpt5_agent(llm)`, `get_gemini_agent(llm)`, `get_default_tools()`, `get_gemini_tools()`). Importing a tool module registers it, and the conversation resolves each `Tool` spec into a live tool bound to `conversation.state.workspace.working_dir`. The terminal tool runs shell commands in a persistent session (tmux when available, else a plain subprocess shell, PowerShell on Windows), keeps `cd` and exported variables between calls, reports exit code and cwd in metadata, accepts `is_input=True` to type into a running process, `timeout` for a hard limit and `reset=True` for a fresh shell, and truncates long output while saving the full text. With tmux it keeps a pane pool so parallel terminal calls run side by side. The file editor (`view`, `create`, `str_replace`, `insert`, `undo_edit`) is the claude-style editor; `view` also returns images and PDFs. `apply_patch` (GPT-5 preset) applies an OpenAI-style `*** Begin Patch` envelope. The Gemini set (`read_file`, `write_file`, `edit`, `list_directory`) replaces the file editor in the Gemini preset. `glob` and `grep` are read-only search tools that use ripgrep and fall back to Python. Tool output passes through the conversation's secret masking.

Source: `openhands-tools/openhands/tools/terminal/`, `openhands-tools/openhands/tools/file_editor/`, `openhands-tools/openhands/tools/apply_patch/`, `openhands-tools/openhands/tools/gemini/`, `openhands-tools/openhands/tools/glob/`, `openhands-tools/openhands/tools/grep/`, `openhands-tools/openhands/tools/preset/`, `openhands-sdk/openhands/sdk/tool/defaults.py`

Needs: `llm` (examples and real-LLM tests), `tmux` (tmux backend and pane pool; without it the subprocess backend is used), `git` (t04), `network` (t08 downloads an image)

Examples: `examples/04_llm_specific_tools/01_gpt5_apply_patch_preset.py`, `examples/04_llm_specific_tools/02_gemini_file_tools.py` (most `examples/01_standalone_sdk/*` scripts also use `TerminalTool` and `FileEditorTool`)

## Sub-features

- `S14.terminal-exec`: a `TerminalAction(command=...)` runs in the workspace directory and returns a `TerminalObservation` with output and `exit_code` (also at top level); a command that runs and fails keeps `is_error` false (only its exit code says so), while a refused call (two commands at once, a timeout over the cap, input with no running command) sets `is_error`; chained and multi-line commands, background servers and `cd` persist across calls. Unit: `tests/tools/terminal/test_terminal_tool.py`, `tests/tools/terminal/test_terminal_exit_code_top_level.py`, `tests/tools/terminal/test_terminal_session.py`. Example: `examples/04_llm_specific_tools/02_gemini_file_tools.py`. Real-LLM: `tests/integration/tests/t02_add_bash_hello.py`, `tests/integration/tests/t03_jupyter_write_file.py`, `tests/integration/tests/t04_git_staging.py`.
- `S14.terminal-backend-select`: `TerminalTool.create(conv_state, terminal_type=None|"tmux"|"subprocess"|"powershell", shell_path=...)` picks tmux when installed, else subprocess, and PowerShell on Windows; `shell_path` picks the shell binary for the subprocess backend; an invalid working directory raises `ValueError`. Unit: `tests/tools/terminal/test_terminal_tool_auto_detection.py`, `tests/tools/terminal/test_session_factory.py`, `tests/tools/terminal/test_shell_path_configuration.py`. Example: none. Real-LLM: none.
- `S14.terminal-interactive`: `is_input=True` sends text or control keys (`C-c`, `C-d`, arrows) to a running process; a command that is still running reports that it is waiting and the next call continues it. Unit: `tests/tools/terminal/test_send_keys.py`, `tests/tools/terminal/test_terminal_session.py::test_interactive_command`, `::test_ctrl_c`, `::test_python_interactive_input`. Example: `examples/01_standalone_sdk/06_interactive_terminal_w_reasoning.py` (excluded from CI). Real-LLM: `tests/integration/tests/t07_interactive_commands.py`.
- `S14.terminal-timeout`: a command that produces no new output for `no_change_timeout_seconds` returns control with a "still running" note; an explicit `timeout` is a hard limit and is rejected above 90% of `OH_RUNTIME_IDLE_TIMEOUT_SECONDS` when that variable is set. Unit: `tests/tools/terminal/test_timeout_policy.py`, `tests/tools/terminal/test_terminal_session.py::test_long_running_command_follow_by_execute`, `::test_no_change_timeout_strips_heredoc_wrapper`. Example: none. Real-LLM: none.
- `S14.terminal-reset`: `reset=True` kills the session and starts a fresh shell in the working directory; a reset combined with `is_input` is rejected. Unit: `tests/tools/terminal/test_terminal_reset.py`. Example: none. Real-LLM: none.
- `S14.terminal-pane-pool`: with tmux the executor runs a pane pool (`is_pooled`), declares no shared resource key so parallel terminal calls run concurrently, and rebuilds a broken pool; `terminal_type="subprocess"` turns pool mode off. Unit: `tests/tools/terminal/test_tmux_pane_pool.py`, `tests/tools/terminal/test_pool_integration.py`. Example: `examples/01_standalone_sdk/45_parallel_tool_execution.py`. Real-LLM: none.
- `S14.terminal-env`: exported variables persist in the session, the parent environment is inherited, `TerminalTool.create(..., env={...})` adds client-controlled variables, and a very large environment does not break startup. Unit: `tests/tools/terminal/test_terminal_session.py::test_environment_variable_persistence`, `::test_environment_variable_inheritance_from_parent`, `tests/tools/terminal/test_large_environment.py`. Example: none. Real-LLM: none.
- `S14.terminal-output-truncation`: long output is truncated for the LLM with metadata kept, and the full output is written under the conversation's observation persistence directory (`full_output_save_dir`). Unit: `tests/tools/terminal/test_observation_truncation.py`, `tests/tools/terminal/test_terminal_session.py::test_session_truncates_large_command_output`, `::test_truncation_preserves_metadata_in_llm_content`. Example: none. Real-LLM: none.
- `S14.terminal-metadata`: the PS1 marker carries cwd, exit code, username and Python interpreter into `CmdOutputMetadata`, and survives corrupted or interleaved prompts. Unit: `tests/tools/terminal/test_terminal_ps1_metadata.py`, `tests/tools/terminal/test_ps1_corruption.py`, `tests/tools/terminal/test_terminal_parsing.py`. Example: none. Real-LLM: none.
- `S14.terminal-input-encoding`: heredocs are sent in chunks and complete correctly, terminal query sequences (such as the DSR cursor-position request and OSC queries) are filtered from output, other escape sequences pass through, and literal arguments that would be mangled by the shell are guarded. Unit: `tests/tools/terminal/test_heredoc_chunked_send.py`, `tests/tools/terminal/test_escape_filter.py`, `tests/tools/terminal/test_literal_arg_guard.py`. Example: none. Real-LLM: none.
- `S14.terminal-secret-masking`: secret values registered on the conversation are masked in terminal output and in tmux debug logs. Unit: `tests/tools/terminal/test_secrets_masking.py`, `tests/tools/terminal/test_tmux_log_redaction.py`. Example: `examples/01_standalone_sdk/12_custom_secrets.py`. Real-LLM: none.
- `S14.terminal-cleanup`: closing the conversation closes the terminal session (and tmux panes), and executor shutdown tolerates interpreter teardown. Unit: `tests/tools/terminal/test_conversation_cleanup.py`, `tests/tools/terminal/test_shutdown_handling.py`. Example: none. Real-LLM: none.
- `S14.terminal-windows`: the PowerShell backend runs commands, sends keys and handles Ctrl+C on Windows. Unit: `tests/tools/terminal/test_windows_terminal.py`, `tests/tools/terminal/test_windows_ctrl_c.py` (both skip off Windows), `tests/tools/terminal/test_windows_terminal_send_keys.py` (runs anywhere). Example: none. Real-LLM: none.
- `S14.file-editor-view`: `view` lists a directory (two levels deep, symlinks, truncated when large; hidden entries are left out and counted in a note such as "1 hidden files/directories"), shows a file with line numbers or a `view_range`, and returns image content for images and text for a PDF it can read (an ASCII PDF shows its text), while other binary files are refused ("appears to be binary"). Unit: `tests/tools/file_editor/test_basic_operations.py`, `tests/tools/file_editor/test_view_supported_binary_files.py`. Example: none. Real-LLM: `tests/integration/tests/t08_image_file_viewing.py` (needs a vision model).
- `S14.file-editor-edit`: `create` (fails on an existing file), `str_replace` (must match exactly once), `insert` at a line, and `undo_edit` (per-file history) change files and return a diff snippet. Unit: `tests/tools/file_editor/test_basic_operations.py`, `tests/tools/file_editor/test_file_editor_tool.py`, `tests/tools/file_editor/utils/test_history.py`, `tests/tools/file_editor/test_visualize_diff.py`. Example: `examples/01_standalone_sdk/01_hello_world.py`. Real-LLM: `tests/integration/tests/t01_fix_simple_typo.py`.
- `S14.file-editor-validation`: relative paths, missing files, binary files, oversized files and invalid parameters return error observations; paths resolve against the workspace root; non-UTF-8 files keep their encoding; memory use stays bounded on large files. Unit: `tests/tools/file_editor/test_file_validation.py`, `tests/tools/file_editor/test_error_handling.py`, `tests/tools/file_editor/test_exceptions.py`, `tests/tools/file_editor/test_workspace_root.py`, `tests/tools/file_editor/utils/test_encoding.py`, `tests/tools/file_editor/utils/test_file_cache.py`, `tests/tools/file_editor/test_memory_usage.py`. Example: none. Real-LLM: none.
- `S14.apply-patch`: `ApplyPatchAction(patch="*** Begin Patch ... *** End Patch")` adds, updates (context-matched hunks) and deletes files relative to the workspace and reports a `DiffError` for a bad patch. Unit: `tests/tools/apply_patch/test_apply_patch_executor.py`. Example: `examples/04_llm_specific_tools/01_gpt5_apply_patch_preset.py`. Real-LLM: `tests/integration/run_infer.py --tool-preset gpt5` (t01 to t08).
- `S14.gemini-file-tools`: `read_file` (offset and limit), `write_file`, `edit` (replace with expected occurrence count) and `list_directory` work on workspace paths, and concurrent calls on the same file are serialized by a shared file lock. Unit: `tests/tools/gemini/read_file/test_read_file.py`, `tests/tools/gemini/write_file/test_write_file.py`, `tests/tools/gemini/edit/test_edit.py`, `tests/tools/gemini/list_directory/test_list_directory.py`, `tests/tools/gemini/test_cross_tool_locking.py`. Example: `examples/04_llm_specific_tools/02_gemini_file_tools.py`. Real-LLM: `tests/integration/run_infer.py --tool-preset gemini`.
- `S14.glob`: `glob` matches a pattern under an optional path, returns paths sorted by modification time (capped), uses `rg --files` and falls back to Python `glob` with the same results. Unit: `tests/tools/glob/test_glob_tool.py`, `tests/tools/glob/test_glob_executor.py`, `tests/tools/glob/test_consistency.py`. Example: `examples/01_standalone_sdk/24_planning_agent_workflow.py` (planning preset). Real-LLM: none.
- `S14.grep`: `grep` searches file contents by regex with an `include` filter, returns matching files (capped), uses ripgrep and falls back to Python with the same results. Unit: `tests/tools/grep/test_grep_tool.py`, `tests/tools/grep/test_grep_executor.py`, `tests/tools/grep/test_consistency.py`. Example: `examples/01_standalone_sdk/24_planning_agent_workflow.py`. Real-LLM: none.
- `S14.tool-output-masking`: file editor views, the grep pattern, glob paths and apply_patch commit content are masked through one chokepoint when the conversation has secrets; without a conversation nothing is masked. Unit: `tests/tools/test_tool_output_secret_masking.py`. Example: none. Real-LLM: none.
- `S14.shared-working-dir`: terminal, file editor and other tools take their working directory from the conversation workspace and need no `params` for it. Unit: `tests/tools/test_working_dir_standardization.py`, `tests/cross/test_registry_directories.py`. Example: none. Real-LLM: none.
- `S14.preset-default`: `get_default_tools(enable_browser=..., enable_sub_agents=...)` returns `terminal`, `file_editor`, `task_tracker` (plus `browser_tool_set` and `task_tool_set` when asked); `get_default_agent(llm, cli_mode=...)` adds the default condenser, drops the browser in CLI mode and, with `finish_tool_response_schema`, registers a structured finish tool; the SDK's `default_tool_specs()` matches it. Unit: `tests/tools/test_preset_default.py`, `tests/sdk/tool/test_defaults.py`, `tests/cross/test_default_tool_names.py`, `tests/integration/test_tool_presets.py::test_get_tools_for_preset_default`. Example: `examples/01_standalone_sdk/24_planning_agent_workflow.py`. Real-LLM: every `tests/integration/tests/t*.py` with the default preset.
- `S14.preset-gpt5-gemini`: `get_gpt5_tools()` swaps `file_editor` for `apply_patch`, `get_gemini_tools()` swaps it for the four Gemini tools; `get_gpt5_agent(llm)` and `get_gemini_agent(llm)` wrap them with a condenser. Unit: `tests/integration/test_tool_presets.py` (tool lists only; the `get_*_agent` builders are not unit-tested). Example: `examples/04_llm_specific_tools/01_gpt5_apply_patch_preset.py`, `examples/04_llm_specific_tools/02_gemini_file_tools.py`. Real-LLM: `tests/integration/run_infer.py --tool-preset gpt5` / `--tool-preset gemini`.

## How to get to it (agent POV)

- Tool specs: `from openhands.sdk import Tool`; `Tool(name="terminal")`, `Tool(name="file_editor")`, `Tool(name="apply_patch")`, `Tool(name="read_file")` etc., `Tool(name="glob")`, `Tool(name="grep")`. Import the tool module first (`from openhands.tools.terminal import TerminalTool`) so it registers; use `TerminalTool.name` rather than a string. `Tool(name=..., params={...})` forwards keyword arguments to the tool's `create()` (for the terminal: `terminal_type`, `shell_path`, `no_change_timeout_seconds`, `username`, `env`). All sub-features.
- `from openhands.tools.gemini import GEMINI_FILE_TOOLS` is the list of four Gemini specs (`S14.gemini-file-tools`).
- Presets: `openhands.tools.preset.default` (`get_default_tools`, `get_default_agent`, `register_default_tools`), `openhands.tools.preset.gpt5` (`get_gpt5_tools`, `get_gpt5_agent`), `openhands.tools.preset.gemini` (`get_gemini_tools`, `get_gemini_agent`). `openhands.sdk.tool.default_tool_specs()` is the SDK-side default list. `S14.preset-*`.
- Direct use without an LLM: `TerminalExecutor(working_dir=..., terminal_type=...)` with `TerminalAction`, `FileEditorExecutor(workspace_root=...)` with `FileEditorAction`, or `conversation.execute_tool("terminal", TerminalAction(command="ls"))` on a live conversation.
- Environment: `OH_RUNTIME_IDLE_TIMEOUT_SECONDS` caps the terminal `timeout` (`S14.terminal-timeout`). The full output of truncated observations goes under the conversation's persistence directory (`Conversation(persistence_dir=...)`).
- Integration runner: `tests/integration/run_infer.py --tool-preset default|gemini|gpt5|planning --eval-ids t01_fix_simple_typo,...` builds the agent from `tests/integration/base.py::get_tools_for_preset`.

## Driving it

Preconditions:
- `uv sync --dev` has run; `control-sdk` is on `PATH` (`export PATH="$PWD/.agents/skills/verify-sdk/scripts:$PATH"`). `sdk-map run` does both.
- `tmux` and `rg` on `PATH` (both present here); the programs hide `rg` or force the subprocess backend themselves where a bullet needs the fallback.
- Live bullets: `DEEPSEEK_API_KEY` exported.

- **A project fixed with terminal, glob, grep and apply_patch (`S14.terminal-exec`, `S14.shared-working-dir`, `S14.glob`, `S14.grep`, `S14.apply-patch`).** A scripted agent lays out a small project with the terminal, finds the Python files with `glob`, finds the bug with `grep`, fixes it with one `apply_patch` envelope that updates, adds and deletes files, has a bad hunk and an escaping path refused, and runs the result. No tool gets a path parameter: all of them work in the conversation workspace.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  CID=$(control-sdk conversation new --tools terminal,glob,grep,apply_patch --script S14/search_and_patch.json --field id)
  control-sdk conversation send "$CID" --text "Fix add in src/calc.py" --run \
    --check execution_status eq finished --check last_agent_message eq "calc.add fixed"
  control-sdk conversation events "$CID" --kind ObservationEvent \
    --check count eq 8 \
    --check events.1.text contains "Found 2 file" --check events.1.text contains "'**/*.py'" \
    --check events.2.text contains "Found 1 file" --check events.2.text contains "'return a - b'" \
    --check events.2.text contains src/calc.py \
    --check events.3.is_error eq false --check events.3.text eq "Done!" \
    --check events.4.is_error eq true --check events.4.text contains "Invalid Context" \
    --check events.5.is_error eq true --check events.5.text contains "escaping paths are not allowed" \
    --check events.6.text contains "5 1.0"
  control-sdk exec --expect-output "return a + b" -- cat src/calc.py
  control-sdk exec --expect-exit 1 -- test -e notes/todo.txt
  control-sdk exec --expect-exit 1 -- test -e ../outside.txt
  ```
- **The terminal on the subprocess backend (`S14.terminal-exec`, `S14.terminal-backend-select`, `S14.terminal-interactive`, `S14.terminal-timeout`, `S14.terminal-reset`, `S14.terminal-env`, `S14.terminal-output-truncation`, `S14.terminal-metadata`, `S14.terminal-input-encoding`, `S14.terminal-secret-masking`, `S14.terminal-cleanup`).** A program configures `Tool(name="terminal", params={"terminal_type": "subprocess", "shell_path": ..., "no_change_timeout_seconds": 3, "env": {...}})`, registers a conversation secret, and a scripted agent runs 27 terminal calls: `cd` and `export` that persist, a failing command, two commands at once, a 400-line heredoc, a background HTTP server, a prompt answered with `is_input`, an idle command continued with `""` and stopped with `C-c`, a hard `timeout`, a timeout above the `OH_RUNTIME_IDLE_TIMEOUT_SECONDS` cap, 40,000 characters of output, a DSR query, a fake PS1 marker, a list literal, a secret, a reset, and three calls in one response; then it closes the conversation.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S14/terminal --arg subprocess --env OH_RUNTIME_IDLE_TIMEOUT_SECONDS=100 \
    --check pooled eq false --check steps.shell.text contains my-shell \
    --check cwd_is_sub eq true --check steps.persist.text contains "FOO=bar CLIENT=from-client PARENT=inherited" \
    --check steps.persist.username exists --check steps.persist.py_interpreter matches python \
    --check steps.fail.exit_code eq 2 --check steps.fail.is_error eq false \
    --check steps.two_commands.is_error eq true --check steps.two_commands.text contains "Cannot execute multiple commands" \
    --check heredoc_lines eq 400 --check steps.server_up.text contains "status 200" \
    --check steps.answer.text contains "hello bob" --check steps.answer.exit_code eq 0 \
    --check steps.idle.exit_code eq -1 --check steps.idle.text contains "no new output after 3 seconds" \
    --check steps.more.text contains "Below is the output of the previous command" \
    --check steps.ctrl_c.exit_code eq 130 \
    --check steps.hard.text contains "timed out after 1.0 seconds" --check steps.after_hard.exit_code eq 130 \
    --check steps.capped.is_error eq true --check steps.capped.text contains "Refusing to run" \
    --check big_text_len le 30000 --check big_saved_files len-eq 1 --check big_notice_names_saved_file eq true \
    --check escape_filtered eq true --check steps.fake_ps1.exit_code eq 3 \
    --check steps.literal.is_error eq true --check steps.literal.text contains "list literal" \
    --check steps.secret.text contains "token=<secret-hidden>" --check secret_leaked eq false \
    --check steps.reset.text contains "Terminal session has been reset" \
    --check after_reset_in_workspace eq true --check steps.after_reset.text not-contains FOO=bar \
    --check steps.reset_input.kind eq AgentErrorEvent \
    --check steps.reset_input.text contains "Cannot use reset=True with is_input=True" \
    --check parallel_calls eq 3 --check parallel_overlap eq false \
    --check shell_alive_before_close eq true --check shell_alive_after_close eq false \
    --check tmux_servers_after_close eq 0
  ```
  A nonzero exit is reported through `exit_code` with `is_error` false; `is_error` marks calls the tool refused. The three calls in one response run one after another (the single session declares the `terminal:session` resource).
- **The terminal on the default backend, the tmux pane pool (`S14.terminal-backend-select`, `S14.terminal-pane-pool`, `S14.terminal-exec`, `S14.terminal-timeout`, `S14.terminal-reset`, `S14.terminal-env`, `S14.terminal-output-truncation`, `S14.terminal-metadata`, `S14.terminal-secret-masking`, `S14.terminal-cleanup`).** The same program without `terminal_type`; with tmux installed the executor runs the pane pool, the three calls in one response overlap, and no cap applies to `timeout` without `OH_RUNTIME_IDLE_TIMEOUT_SECONDS`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S14/terminal --arg auto \
    --check pooled eq true --check backend_at_end eq TmuxPanePool \
    --check cwd_is_sub eq true --check steps.persist.text contains "FOO=bar CLIENT=from-client PARENT=inherited" \
    --check steps.fail.exit_code eq 2 --check steps.fail.is_error eq false \
    --check heredoc_lines eq 400 --check steps.server_up.text contains "status 200" \
    --check steps.idle.exit_code eq -1 --check steps.hard.text contains "timed out after 1.0 seconds" \
    --check steps.capped.is_error eq false --check steps.capped.text contains capped-ran \
    --check big_saved_files len-eq 1 --check big_notice_names_saved_file eq true \
    --check steps.fake_ps1.exit_code eq 3 --check steps.literal.is_error eq true \
    --check steps.secret.text contains "token=<secret-hidden>" --check secret_leaked eq false \
    --check after_reset_in_workspace eq true --check steps.after_reset.text not-contains FOO=bar \
    --check parallel_calls eq 3 --check parallel_overlap eq true \
    --check bracketed_paste_in_output eq false \
    --check shell_alive_before_close eq true --check shell_alive_after_close eq false \
    --check tmux_servers_after_close eq 0
  ```
- **Interactive input on the pane pool (`S14.terminal-interactive`, `S14.terminal-pane-pool`).** The prompt, idle and `C-c` steps of the previous bullet, checked as the subprocess bullet checks them.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S14/terminal --arg auto \
    --check steps.prompt.exit_code eq -1 --check steps.answer.text contains "hello bob" \
    --check steps.more.is_error eq false --check steps.ctrl_c.exit_code eq 130
  ```
  Expected: as on the subprocess backend, `is_input` answers the prompt, `""` shows more output and `C-c` stops the idle command. Known failure (reproduced 2026-10-13, not yet filed): on the default tmux backend every one of these calls returns `is_error` with "No previous running command to interact with." (or "... to retrieve logs from."). `TerminalExecutor._prepare_pooled_session` interrupts a still-running command whenever a pane is checked out, so the follow-up the observation itself recommends ("You may wait longer ... by sending empty command '', ... send keys ("C-c", ...)") can never reach the process.
- **Terminal query sequences under tmux (`S14.terminal-input-encoding`).** A command prints a DSR cursor-position query, then the agent runs `echo next`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S14/terminal --arg auto \
    --check escape_filtered eq true --check dsr_reply_visible eq false --check after_dsr_clean eq true
  ```
  Expected: the query is dropped and nothing else shows, as on the subprocess backend. Known failure (reproduced 2026-10-13, not yet filed): the query itself is filtered, but tmux answers it into the shell's input; the observation shows `^[[13;2R` and the next command's output starts with the echoed earlier command and `bash: syntax error near unexpected token ';'`.
- **A reset keeps the configured backend (`S14.terminal-reset`, `S14.terminal-backend-select`).** The subprocess run's `echo shell=$0` after `reset=True`, and the session type at the end.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S14/terminal --arg subprocess \
    --check steps.shell.text contains my-shell \
    --check steps.shell_after_reset.text contains my-shell --check backend_at_end eq SubprocessTerminal
  ```
  Expected: a reset starts a fresh shell of the same kind. Known failure (reproduced 2026-10-13, not yet filed): `TerminalExecutor._reset_single_session` builds the new session with `terminal_type=None`, so an executor forced to `subprocess` comes back from a reset on tmux (`backend_at_end` is `TmuxTerminal`, `$0` is `/bin/bash`), dropping `shell_path`.
- **Windows PowerShell backend (`S14.terminal-windows`), blocked.** Needs a Windows host; on Linux `terminal_type="powershell"` has no PowerShell to start.
- **The file editor over a small repository (`S14.file-editor-view`, `S14.file-editor-edit`, `S14.file-editor-validation`, `S14.tool-output-masking`).** The program lays out a nested tree, a hidden file, a PNG, an ASCII PDF, a binary blob, an 11 MB log, a Latin-1 file and a file holding a registered secret; a scripted agent views and edits them.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S14/file_editor \
    --check steps.view_dir.text contains "up to 2 levels deep" --check dir_lists_second_level eq true \
    --check dir_lists_third_level eq false --check dir_lists_hidden eq false --check dir_notes_hidden eq true \
    --check steps.create.is_error eq false \
    --check steps.create_again.is_error eq true --check steps.create_again.text contains "File already exists" \
    --check steps.view_file.text contains "cat -n" --check steps.view_range.text contains "def bye" \
    --check steps.view_range.text not-contains greet \
    --check steps.replace.is_error eq false --check steps.replace.text contains "snippet" \
    --check steps.replace_ambiguous.is_error eq true --check steps.replace_ambiguous.text contains "Multiple occurrences" \
    --check steps.replace_missing.text contains "did not appear verbatim" \
    --check steps.insert.text contains "import os" --check steps.undo.text contains "undone successfully" \
    --check app_py contains "return 'bye'" --check app_py contains "return 'hi'" --check app_py not-contains "import os" \
    --check steps.relative.is_error eq true --check steps.relative.text contains "should be an absolute path. Maybe you meant" \
    --check steps.missing.is_error eq true \
    --check steps.image.content_types contains ImageContent \
    --check steps.pdf.is_error eq false --check steps.pdf.text contains "Quarterly Report" \
    --check steps.binary.is_error eq true --check steps.binary.text contains "appears to be binary" \
    --check steps.huge.is_error eq true --check steps.huge.text contains "too large" \
    --check steps.latin.is_error eq false --check latin_still_latin1 eq true \
    --check steps.secret.text contains "API_TOKEN=<secret-hidden>" --check secret_leaked eq false
  ```
- **glob and grep with and without ripgrep, and masked search output (`S14.glob`, `S14.grep`, `S14.tool-output-masking`, `S14.apply-patch`).** The same scripted searches run on a host with `rg` and on one whose `PATH` has no `rg`, over 150 files plus a small tree; a registered secret is the grep pattern and the content of an `apply_patch` hunk.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S14/search --arg 150 \
    --check rg_available eq true --check same_files eq true --check same_truncated eq true \
    --check rg.glob_py.count eq 2 --check newest_first eq true \
    --check rg.glob_in_path.count eq 100 --check rg.glob_in_path.truncated eq true \
    --check rg.grep_include.files len-eq 2 --check rg.grep_include.text contains "filtered by '*.py'" \
    --check rg.grep_many.truncated eq true --check python.grep_many.truncated eq true \
    --check rg.grep_secret.files.0 eq creds.txt --check rg.grep_secret.text contains "<secret-hidden>" \
    --check rg.patch.is_error eq false --check added_file contains sk-live-4242abcd \
    --check secret_in_observations eq false
  ```
- **Exactly 100 matches (`S14.glob`).** The same searches over exactly 100 files.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S14/search --arg 100 \
    --check same_files eq true --check rg.glob_in_path.count eq 100 --check same_truncated eq true \
    --check rg.glob_in_path.truncated eq false
  ```
  Expected: both backends report 100 files and `truncated` false. Known failure (reproduced 2026-10-13, not yet filed): with ripgrep `glob` reports `truncated: true` and "[Results truncated to first 100 files ...]" for exactly 100 matches (`len(file_paths) >= 100` in `GlobExecutor._execute_with_ripgrep`), while the Python fallback (`> 100`) and `grep` report `false`.
- **Tool presets build working agents (`S14.preset-default`, `S14.preset-gpt5-gemini`, `S14.gemini-file-tools`, `S14.apply-patch`).** Each preset's agent does a scripted task with its own file tools; the default preset also reports the tool lists of all three and the SDK's `default_tool_specs()`, and builds a structured finish tool.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S14/presets --arg default \
    --check status eq finished --check errors len-eq 0 --check hello contains hello \
    --check agent_tool_specs eq '["terminal", "file_editor", "task_tracker"]' \
    --check tools_map contains think --check condenser eq LLMSummarizingCondenser \
    --check condenser_usage_id eq condenser \
    --check default_tools eq '["terminal", "file_editor", "task_tracker", "browser_tool_set"]' \
    --check default_tools_no_browser_subagents eq '["terminal", "file_editor", "task_tracker", "task_tool_set"]' \
    --check sdk_default_tool_specs eq '["terminal", "file_editor", "task_tracker"]' \
    --check gpt5_tools eq '["terminal", "apply_patch", "task_tracker"]' \
    --check gemini_tools eq '["terminal", "read_file", "write_file", "edit", "list_directory", "task_tracker"]'
  control-sdk program S14/presets --arg structured \
    --check status eq finished --check agent_tool_specs contains FinishTool --check finish_schema eq Outcome \
    --check errors.0 contains "outcome_summary" --check parsed_type eq Outcome \
    --check parsed.outcome_summary eq "wrote nothing" --check parsed.success eq true
  control-sdk program S14/presets --arg gpt5 \
    --check status eq finished --check errors len-eq 0 --check demo eq "patched in" \
    --check tools_map contains apply_patch --check tools_map not-contains file_editor \
    --check condenser_max_size eq 80
  control-sdk program S14/presets --arg gemini \
    --check status eq finished --check tools_map not-contains file_editor \
    --check results.1.text contains "showing lines 6-8 of 21" \
    --check results.2.text contains "2 replacement" \
    --check errors len-eq 1 --check errors.0 contains "Expected 5 occurrences but found 1" \
    --check facts contains "LINE TWO" --check facts contains "LINE THREE" --check facts contains "ALPHA beta ALPHA" \
    --check results.6.tool eq list_directory --check results.6.is_error eq false
  ```
  The two `edit` calls on `FACTS.txt` in one response (with `tool_concurrency_limit=4`) both land.
- **The tools with a real model (`S14.terminal-exec`, `S14.file-editor-edit`, `S14.shared-working-dir`).** Live: the model picks its own calls; check the files.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  CID=$(control-sdk conversation new --tools terminal,file_editor --field id)
  control-sdk conversation send "$CID" --run \
    --text "Create hello.sh in the current directory that prints 'hello from sh', make it executable, run it with the terminal, then finish." \
    --check execution_status eq finished
  control-sdk exec --expect-output "hello from sh" -- sh ./hello.sh
  ```
- **The GPT-5 and Gemini preset examples (`S14.preset-gpt5-gemini`, `S14.apply-patch`, `S14.gemini-file-tools`).** Live; both examples write into the run's workspace.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env -- python examples/04_llm_specific_tools/01_gpt5_apply_patch_preset.py
  control-sdk exec --expect-output GPT5 -- ls
  control-sdk exec --model-env -- python examples/04_llm_specific_tools/02_gemini_file_tools.py
  ```
  The first writes `GPT5_DEMO.txt`; the second writes and then deletes `FACTS.txt` and logs completions under `logs/gemini`.
- **Task tests through the integration runner (`S14.terminal-exec`, `S14.terminal-interactive`, `S14.file-editor-view`, `S14.file-editor-edit`, `S14.apply-patch`, `S14.gemini-file-tools`).** Live.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env -- python tests/integration/run_infer.py --llm-config '{"model": "deepseek/deepseek-flash"}' --eval-ids t01_fix_simple_typo,t02_add_bash_hello,t03_jupyter_write_file,t04_git_staging,t07_interactive_commands
  control-sdk exec --model-env -- python tests/integration/run_infer.py --llm-config '{"model": "deepseek/deepseek-flash"}' --tool-preset gpt5 --eval-ids t01_fix_simple_typo,t02_add_bash_hello
  control-sdk exec --model-env -- python tests/integration/run_infer.py --llm-config '{"model": "deepseek/deepseek-flash"}' --tool-preset gemini --eval-ids t01_fix_simple_typo,t02_add_bash_hello
  ```
  t07 runs on the default tmux backend, so it meets the pane-pool failure above unless the model avoids interactive input. `t08_image_file_viewing` needs a vision model and `network`; deepseek-flash has no vision, so it is left out.

## Gotchas

- `tests/tools/terminal` as a whole runs well past 300 s; run it file by file or give `test_terminal_session.py` a longer timeout. Under heavy parallel load `tests/tools/terminal/test_terminal_session.py::test_session_truncates_multiline_output[subprocess]` failed once and passed on its own rerun (2 passed in 3.96 s), so treat it as timing-sensitive.
- The auto-detected backend depends on the host: with tmux installed every default `TerminalTool` uses the pane pool, and `TerminalExecutor.session` raises in pool mode. Force `terminal_type="subprocess"` to test the single-session path.
- Examples read `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL`; the GPT-5 example also accepts `OPENAI_API_KEY` and defaults to `openai/gpt-5.1`, the Gemini example defaults to `gemini/gemini-3.1-pro-preview`. `tests/examples/test_examples.py` pins them to `openhands/gpt-5.1` and `openhands/gemini-3.1-pro-preview` (`_LLM_SPECIFIC_EXAMPLES`). Both examples write into the current directory.
- `examples/01_standalone_sdk/06_interactive_terminal_w_reasoning.py` and `01_hello_world.py` are in `_EXCLUDED_EXAMPLES`, so CI never runs them.
- Integration tests need both `LLM_API_KEY` and `LLM_BASE_URL`; `--llm-config` must be JSON. t08 skips unless the model supports vision and downloads an image from python.org; t04 needs `git`; t03 asks for "Jupyter IPython" but there is no Jupyter tool, so the agent uses the terminal.
- The integration runner never enables the browser for the default preset (`test_get_tools_for_preset_default_with_browser` is skipped, issue #2124 in the skip message), so presets there run without browser tools.
- Truncated terminal output and browser screenshots are saved only when the conversation has a `persistence_dir`; `env_observation_persistence_dir` is `None` otherwise and the full text is dropped.
- `glob` with the Python fallback uses a process-wide `os.chdir`, so it declares itself not parallel-safe; with ripgrep it is.
- The subprocess backend leaks bracketed-paste switches (`\x1b[?2004l`, `\x1b[?2004h`) into observation text; the tmux backend does not.
- `TerminalObservation.timeout` stays `false` even when a hard `timeout` fires; read `exit_code` (-1) and the "[The command timed out after ...]" note instead.
- With the tmux pane pool, checking a pane out interrupts whatever is still running in it, so a command left running by a no-change timeout cannot be continued with `""`, `is_input` or `C-c` (see the known failure in Driving it).
