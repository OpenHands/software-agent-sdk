# Runtime package architecture constraints

This document captures the durable constraints for the runtime-oriented packages
that sit next to the core SDK:

- `openhands-tools/openhands/tools`
- `openhands-workspace/openhands/workspace`
- `openhands-agent-server/openhands/agent_server`
- `.github/run-eval`

## `openhands-tools`: tool implementations and presets

### Package-level invariants

- `openhands.tools.__all__` is the curated public surface of the published
  `openhands-tools` distribution.
- Tool implementations should expose typed action/observation schemas and
  register through the SDK tool registry instead of relying on hidden globals.
- Most tools split declarative registration (`definition.py`) from runtime
  behavior (`impl.py`, `core.py`, or `executor.py`).
- Tool descriptions and schemas are user-facing contracts; compatibility matters.
- Heavy or optional dependencies should not leak into the default import surface
  unnecessarily (for example, browser tools are intentionally not re-exported
  from `openhands.tools.__init__`).

### First-level component map

| Component | Responsibility | Durable constraints / invariants |
| --- | --- | --- |
| `apply_patch/` | Structured patch application | Encapsulates patch parsing/application instead of scattering ad hoc text patch logic across agents. |
| `browser_use/` | Browser automation and recording | Browser tools are optional/heavy; runtime assets and recordings are part of the package contract and must remain packaged when needed. |
| `delegate/` | Multi-agent delegation | Delegation routes through registered subagent types and renders tool descriptions from registry/workspace context instead of hard-coding agent choices in prompts. |
| `file_editor/` | High-signal file editing tool | Editing uses explicit commands (`view`, `create`, `str_replace`, `insert`, `undo_edit`), absolute paths, and exact-match semantics; resource declarations lock per target file so reads do not race partial writes. |
| `gemini/` | Gemini-style file tool family | Provides an alternate file-editing surface as a bundle of distinct tools (`GEMINI_FILE_TOOLS`) instead of overloading the standard file editor. |
| `glob/` | Filename/path discovery | Remains a read-only exploration tool rather than mutating workspace state. |
| `grep/` | Content search | Remains a read-only exploration tool with results shaped for agent consumption. |
| `planning_file_editor/` | Planning-only editor | Editing is restricted to `PLAN.md`; default location is `.agents_tmp/PLAN.md`, while legacy root-level `PLAN.md` is still honored for backward compatibility. |
| `preset/` | Default agent and default tool bundles | Centralizes the opinionated built-in tool/agent set so other packages do not each invent their own defaults. |
| `task/` | Blocking delegated task execution | Packages subagent task execution as a toolset instead of folding that orchestration into unrelated agent logic. |
| `task_tracker/` | Structured task-list state for agents | Externalizes work planning into an explicit tool; if a save directory is configured, persistence is file-backed (`TASKS.json`) rather than hidden in prompt state. |
| `terminal/` | Persistent shell execution | Session state persists across calls; long-running commands use soft timeouts and interactive follow-up; reset is explicit rather than silently replacing the session. |
| `tom_consult/` | Optional Tom-based user-model consultation | Keeps this capability optional and isolated so core agents do not depend on Tom-specific runtime code. |
| `utils/` | Shared tool helpers | Hosts reusable implementation helpers rather than duplicating timeout/process utilities in each tool package. |

## `openhands-workspace`: deployable workspace backends

### Package-level invariants

- `openhands.workspace.__all__` is the published surface for deployable
  workspace implementations.
- Imports should stay lightweight; `DockerDevWorkspace` is lazy-loaded to avoid
  build-time dependencies on plain import.
- Backends remain compatible with SDK workspace types (`RemoteWorkspace`,
  `TargetType`, `PlatformType`) rather than inventing parallel abstractions.
- External side effects should stay behind explicit backend methods, not happen
  at import time.

### First-level component map

| Component | Responsibility | Durable constraints / invariants |
| --- | --- | --- |
| `apptainer/` | Apptainer-backed execution | Provides a container workspace that still conforms to the shared workspace contract rather than a separate execution model. |
| `cloud/` | OpenHands Cloud workspace | Represents cloud-hosted execution while preserving the SDK workspace interface expected by conversations and tools. |
| `docker/` | Docker-backed workspaces and dev helpers | Docker support includes both runtime workspace logic and dev-oriented helpers, but the public import surface must stay lightweight. |
| `remote_api/` | Runtime API remote workspace | Bridges the SDK's remote workspace abstraction to an API-driven backend instead of requiring conversation code to know transport details. |

## `openhands-agent-server`: API/runtime host for conversations

### Package-level invariants

- The REST API under `/api/**` is public and backward compatibility matters.
- REST contract breaks require deprecation notice plus a runway of five minor
  releases before removal or mandatory replacement.
- Async routes/services must not block the event loop while waiting on the
  conversation state's synchronous FIFO lock.
- Runtime-loaded non-Python assets belong in the PyInstaller spec when needed.

### First-level component map

| Module group | Responsibility | Durable constraints / invariants |
| --- | --- | --- |
| Bootstrap (`__main__.py`, `api.py`, `config.py`, `openapi.py`, `logging_config.py`, `middleware.py`, `dependencies.py`) | Server startup, dependency wiring, and HTTP surface setup | Startup/config code should assemble the server around SDK primitives rather than re-implementing agent logic. |
| Conversation API (`conversation_router.py`, `conversation_router_acp.py`, `conversation_service.py`, `models.py`) | Conversation CRUD, lifecycle, and request/response modeling | Conversation services should preserve SDK conversation semantics; state mutation stays synchronized and persistence-aware. |
| Event delivery (`event_router.py`, `event_service.py`, `pub_sub.py`, `sockets.py`) | Event streaming over HTTP/WebSocket/SSE-style channels | Delivery layers publish the existing event stream; they must not invent alternate authoritative state. |
| Environment/file/tool routers (`bash_router.py`, `bash_service.py`, `desktop_router.py`, `desktop_service.py`, `file_router.py`, `git_router.py`, `hooks_router.py`, `hooks_service.py`, `llm_router.py`, `skills_router.py`, `skills_service.py`, `tool_router.py`, `server_details_router.py`, `vscode_router.py`, `vscode_service.py`) | API adapters around specific SDK/runtime capabilities | These modules expose bounded server-side capabilities; they should adapt SDK/runtime primitives, not fork their behavior. |
| Support modules (`env_parser.py`, `tool_preload_service.py`, `utils.py`) | Shared runtime helpers | Shared support code should remain plumbing, not a second business-logic layer. |
| Asset directories (`docker/`, `vscode_extensions/`) | Bundled runtime assets | Runtime assets are part of the packaged server and must stay aligned with the PyInstaller/data-file rules in the package AGENTS file. |

## `.github/run-eval`: evaluation model registry and workflow bridge

### Invariants

- `resolve_model_config.py` is the source of truth for evaluation model entries.
- Model configuration here must stay aligned with SDK LLM/model-feature support.
- Existing working model configs should only be changed for a confirmed reason.
- Structural integrity is guarded by tests in `tests/github_workflows/`.

## Cross-package boundaries worth preserving

- `openhands-tools` depends on the SDK tool abstractions; it should not bypass
  them with package-private execution channels.
- `openhands-workspace` implements concrete workspace backends but still speaks
  the SDK workspace contract.
- `openhands-agent-server` hosts conversations remotely; it should expose the SDK
  model rather than redefining it.
- `.github/run-eval` is operational glue around model definitions, not a second
  LLM abstraction layer.
