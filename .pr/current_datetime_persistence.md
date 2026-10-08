# Current Datetime Persistence Evidence

PR head: 28680304c40a23aa1f3e71860deb8611909c4dc6
Comparison base: 39d34ec006f3f92f39863ca890afa5d90720a8ec

Environment: Windows x86_64, CPython 3.13.13, using the repository's locked
dependencies. The reported production installation is Agent Canvas on macOS
26.5.2; this reproduction uses the same FileSettingsStore persistence entry
point but does not run the full Agent Canvas deployment. The base run used a
separate checkout at `39d34ec006f3f92f39863ca890afa5d90720a8ec`; both commands
run the harness from the PR checkout against that checkout's Python project.

The script writes existing settings.json inputs with file schema 3 and nested
agent-settings schema 6, then calls FileSettingsStore.load(), a no-op
FileSettingsStore.update(lambda current: current) (which saves under the file
lock), and load() again.

## Commands

From the comparison-base checkout, set the harness path to the PR checkout:

~~~powershell
$env:OPENHANDS_SUPPRESS_BANNER = "1"
uv run --project D:\Project\2025\software-agent-sdk-base4710 --locked --no-sync python D:\Project\2025\software-agent-sdk-pr4710\.pr\reproduce_current_datetime_persistence.py --expect-runtime persisted
~~~

From the PR checkout:

~~~powershell
$env:OPENHANDS_SUPPRESS_BANNER = "1"
uv run --project D:\Project\2025\software-agent-sdk-pr4710 --locked --no-sync python D:\Project\2025\software-agent-sdk-pr4710\.pr\reproduce_current_datetime_persistence.py --expect-runtime omitted
~~~

## Base Output

~~~json
{"case": "OpenHands", "formatted_current_datetime": "2024-03-15T14:30:00Z", "input_agent_schema": 6, "input_file_schema": 3, "loaded_current_datetime": "2024-03-15T14:30:00Z", "reloaded_current_datetime": "2024-03-15T14:30:00Z", "saved_agent_schema": 8, "saved_current_datetime": "2024-03-15T14:30:00Z", "saved_file_schema": 5}
{"case": "ACP", "formatted_current_datetime": null, "input_agent_schema": 6, "input_file_schema": 3, "loaded_current_datetime": null, "reloaded_current_datetime": null, "saved_agent_schema": 8, "saved_current_datetime": null, "saved_file_schema": 5}
~~~

## PR Head Output

~~~json
{"case": "OpenHands", "formatted_current_datetime": "2026-10-08T18:23", "input_agent_schema": 6, "input_file_schema": 3, "loaded_current_datetime": "2026-10-08T18:23:53.343416+08:00", "reloaded_current_datetime": "2026-10-08T18:23:53.354437+08:00", "saved_agent_schema": 9, "saved_current_datetime": "<absent>", "saved_file_schema": 6}
{"case": "ACP", "formatted_current_datetime": null, "input_agent_schema": 6, "input_file_schema": 3, "loaded_current_datetime": null, "reloaded_current_datetime": null, "saved_agent_schema": 9, "saved_current_datetime": null, "saved_file_schema": 6}
~~~

The base keeps the old non-null timestamp after save and reload. The PR head
migrates it to the live default, omits it from the saved OpenHands settings,
and retains an explicitly configured ACP None through save and reload.
