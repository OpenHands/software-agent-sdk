# OpenHands Agent Flight Recorder

Agent Flight Recorder captures local OpenHands SDK runs without changing their
execution, stores portable `.afr` traces, and provides a native Linux desktop
viewer for timeline, context, delegation, and diagnostic investigation.

## Install

From the repository root:

```bash
make build
```

For an isolated package environment:

```bash
uv sync --package openhands-agent-flight-recorder
```

## Record

A target uses `module:function` syntax. The function receives a live `Recorder`:

```bash
uv run agent-flight-recorder record \
  --output ./trace.afr my_project.run:recorded_run
```

Recorder callbacks are bounded and failure-isolated. Queue pressure may omit
low-priority stream deltas; omission counts are retained as warning records.

### Record A Delegated Coding Run

Use a disposable worktree or test project because the agent will edit the selected
workspace. From the repository root:

```bash
export LLM_API_KEY="..."
export LLM_MODEL="gpt-5.5"
export LLM_BASE_URL="https://llm-proxy.app.all-hands.dev"
export AFR_WORKSPACE="/path/to/disposable-worktree"
export AFR_PROMPT='Use the task tool to delegate repository analysis and implementation to separate subagents. Implement the requested change, run focused tests, and summarize the result.'

uv run agent-flight-recorder record \
  --output /tmp/coding-run.afr \
  scripts.record_coding_delegation:run
```

The recorder follows opt-in callbacks into local `TaskToolSet` child conversations.
Ordinary callbacks remain scoped to the parent conversation. Validate and inspect
the resulting orchestration with:

```bash
uv run agent-flight-recorder validate /tmp/coding-run.afr
uv run agent-flight-recorder /tmp/coding-run.afr
```

In the desktop application, expand the primary agent in the left hierarchy, select
a child agent, and inspect its handoff, returned result, duration, and usage. The
timeline shows delegated work in separate agent lanes.

### Record From The Local Web Interface

When a web development checkout launches this repository through
`OH_AGENT_SERVER_LOCAL_PATH`, enable recording in the same shell:

```bash
OH_AGENT_SERVER_LOCAL_PATH=/home/jaeyongyoo/openhands-dev/software-agent-sdk \
OH_ENABLE_FLIGHT_RECORDER=true \
npm run dev
```

Create a conversation in the web interface and ask the agent to use the task tool
for delegated analysis and implementation. After the run, use the conversation ID
from the web URL to query the local agent server:

```bash
curl http://127.0.0.1:8000/api/conversations/CONVERSATION_ID/flight-recorder
```

The response contains `trace_id`, `bundle_path`, and `active`. Open the returned
local path with:

```bash
uv run agent-flight-recorder /absolute/path/from/bundle_path
```

If the local server uses session authentication, include
`-H "X-Session-API-Key: $SESSION_API_KEY"` in the `curl` command. An active trace
is a consistent snapshot when opened; reopen it after later web prompts to load
new activity. Closing or evicting the conversation finalizes checksums, and the
endpoint continues returning the newest trace without reactivating the runtime.

## Validate And Move Traces

```bash
uv run agent-flight-recorder validate ./trace.afr
uv run agent-flight-recorder import ./trace.afr
uv run agent-flight-recorder export TRACE_ID ./exported-trace.zip
```

Validation checks format version, trace identity, ordering, record identity, and
checksums when the bundle is finalized.

## View

```bash
uv run agent-flight-recorder ./trace.afr
```

The desktop application shows run and activity hierarchy, a swimlane timeline,
workspace changes, model context provenance and differences, delegated agents,
usage, and evidence-backed findings. Reconstructed or inferred information is
explicitly labeled and unresolved evidence remains visible.

## Package For Linux

```bash
uv sync --extra package --package openhands-agent-flight-recorder
uv run pyinstaller agent-flight-recorder/agent-flight-recorder.spec
```

The executable is written to `dist/agent-flight-recorder`.

## Limitations

- Capture is local and single-process in this release.
- Exact model context requires captured completion request logs; otherwise the
  viewer presents an evidence-linked reconstruction.
- Interpretive diagnostics are optional. Deterministic diagnostics remain
  available if interpretation is disabled or fails.
- The recorder observes runs but does not launch, pause, resume, or modify them.

## Troubleshooting

- Set `QT_QPA_PLATFORM=offscreen` for GUI tests on headless Linux.
- Native X11 launch requires the Qt XCB runtime libraries, including
  `libxcb-cursor`, `libxcb-keysyms`, `libxcb-render-util`, `libxcb-xkb`, and
  `libxkbcommon-x11`. Install the corresponding packages for your distribution
  if PyInstaller reports them as unresolved.
- Run `validate` before importing a trace received from another machine.
- Inspect `recorder.warning` records when a trace appears incomplete under load.
- Re-run `make build` if workspace entry points or imports do not resolve.