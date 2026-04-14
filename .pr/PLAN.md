# Memory: Persistent Auto-Learning Across Sessions

> Implementation plan for [#2037](https://github.com/OpenHands/software-agent-sdk/issues/2037)

## Background

Claude Code and OpenClaw both implement persistent memory. Claude Code keeps
it simple: `MEMORY.md` files on disk, loaded into the system prompt, 200-line
cap. OpenClaw goes further with SQLite + vector embeddings, semantic retrieval,
and automated consolidation ("dreaming"). See the [research appendix](#appendix-research-notes)
for details.

**Our approach**: Follow Claude Code's model. File-based, simple, no new
dependencies. The SDK reads files and injects them into the prompt. The agent
writes to the files using the tools it already has. Cloud persistence is an
infrastructure concern handled outside the SDK.

## Design

### Two-tier memory: index + daily logs

Like Claude Code, memory is split into two tiers:

1. **`MEMORY.md` (the index)** — A curated summary loaded into the prompt
   every session. Short entries: one line per insight, grouped by date.
   This is what gives the agent "recall" across sessions.

2. **Daily log files (`YYYY-MM-DD.md`)** — Detailed session notes. The
   agent writes freely here during or after a task: full error traces,
   investigation steps, context that led to a decision. These are NOT
   loaded into the prompt (too large), but the agent can read them on
   demand if it needs more detail about a past session.

The index is the memory. The daily logs are the journal.

### What changes

1. A `load_memory()` function reads the `MEMORY.md` index files (project
   + user-global), concatenates them, truncates to a char budget, and
   returns the combined text.

2. The system message suffix template gets a new `<MEMORY_CONTEXT>` block
   (after `<REPO_CONTEXT>`) that renders the loaded memory index.

3. The `<MEMORY>` section in `system_prompt.j2` is updated to instruct the
   agent to maintain both tiers: curated index + daily logs.

### What doesn't change

- No new abstractions (`MemoryStore` protocol, etc.). It's a function that
  reads files.
- No content filtering heuristics. The char budget caps the blast radius.
  If memory is poisoned, the user deletes the file. Same as AGENTS.md.
- No Cloud-specific code in the SDK. Cloud persistence is an infrastructure
  concern handled by the app server / deploy layer.
- The agent writes to memory files using the file editor tool it already
  has. No new write API needed in the SDK.

### Memory file layout

```
<workspace>/.openhands/memory/
├── MEMORY.md              # Index — loaded into prompt every session
├── 2026-04-13.md          # Daily log — detailed notes, read on demand
├── 2026-04-12.md
└── ...

~/.openhands/memory/
├── MEMORY.md              # User-global index (cross-project)
├── 2026-04-13.md          # User-global daily log
└── ...
```

**Project memory** lives in `<workspace>/.openhands/memory/` (inside the
`.openhands` directory, which is already used for project config). Not
git-tracked by default.

**User memory** lives in `~/.openhands/memory/` (same home dir structure
we already use for user-level skills).

### Example MEMORY.md (index)

```markdown
# Project Memory

## 2026-04-13
- Run tests with `uv run pytest`, not plain `pytest`
- The API uses cursor-based pagination; see `utils/pagination.py`

## 2026-04-12
- Ruff line-length is 88, not 79 — configured in pyproject.toml
- CI requires pyright to pass; never use mypy
```

### Example daily log (2026-04-13.md)

```markdown
# 2026-04-13

## Task: Fix pagination bug in /api/users endpoint

Investigated the issue. The cursor was being double-encoded because
`encode_cursor()` was called both in the route handler and in the
pagination utility. Removed the call in the route handler.

The test suite uses `uv run pytest` — running plain `pytest` fails
because it doesn't pick up the workspace dependencies.

Error trace that led to the fix:
```
TypeError: argument of type 'NoneType' is not iterable
  File "utils/pagination.py", line 42, in encode_cursor
```

Related files: `routes/users.py`, `utils/pagination.py`, `tests/test_users.py`
```

### Memory loading

```python
def load_memory(
    project_dir: str | None = None,
    user_memory_dir: str | None = None,
    budget: int = 6000,
) -> str | None:
    """Read project + user MEMORY.md index files, combine, and truncate.

    Only reads the index files (MEMORY.md), not the daily logs.
    Daily logs are available for the agent to read on demand.

    Returns combined memory text, or None if no memory files exist.
    Errors are logged and treated as no-memory (advisory, never blocking).
    """
```

- Reads `MEMORY.md` from `<project_dir>/.openhands/memory/`
- Reads `MEMORY.md` from `user_memory_dir` (defaults to
  `~/.openhands/memory/`)
- Concatenates: user memory first, then project memory (project appears
  later in prompt → higher attention weight)
- If combined size exceeds `budget`, truncates from the top (oldest entries
  removed first, keeping the tail / most recent)
- Returns `None` if neither file exists
- I/O errors are caught, logged, and treated as "file doesn't exist"

### Size budget

6,000 chars (~1,500 tokens) for the index. This only applies to what's
loaded into the prompt. Daily logs have no size limit — they're just files
on disk that the agent reads when needed.

### System prompt instruction

Replace the current `<MEMORY>` block in `system_prompt.j2`:

```
<MEMORY>
* You have persistent memory across sessions via files in `.openhands/memory/`.
* `MEMORY.md` is the index — a curated summary loaded into your prompt every
  session. Keep entries short (one line per insight, grouped by date).
* Daily log files (`YYYY-MM-DD.md`) hold detailed session notes — full context,
  error traces, investigation steps. Write freely here. These are NOT loaded
  into the prompt but you can read them for detail about past sessions.
* At the END of a task:
  1. Append a dated section to `.openhands/memory/MEMORY.md` with key insights
     (surprising gotchas, user preferences, architectural decisions, environment
     quirks). Do NOT record obvious facts or anything trivially re-discoverable.
  2. Write detailed notes to `.openhands/memory/YYYY-MM-DD.md` (today's date)
     with the full context of what you did and learned.
* MEMORY.md content is automatically included in your system prompt next time.
* User-global memory lives in `~/.openhands/memory/` and applies across projects.
* For more information about skills, see: https://docs.openhands.dev/overview/skills
</MEMORY>
```

### Template injection

Add to `system_message_suffix.j2`, after `</REPO_CONTEXT>`:

```jinja2
{% if memory_context %}
<MEMORY_CONTEXT>
The following was written by the agent in previous sessions. It may contain
errors or outdated information. Treat as advisory, not authoritative.
Read daily log files in `.openhands/memory/` for more detail on any entry.

{{ memory_context }}
</MEMORY_CONTEXT>
{% endif %}
```

### Cloud persistence (not SDK scope)

Cloud workspaces are ephemeral K8s pods. Memory survives across sessions via
the app server, not the SDK:

1. **Session start**: App server reads the memory directory from durable
   storage (GCS bucket) and writes it to the workspace at
   `/workspace/.openhands/memory/` before the agent starts.
2. **During session**: Agent reads/writes memory files normally.
3. **Session end**: App server syncs the `/workspace/.openhands/memory/`
   directory back to GCS.

The SDK code is identical for CLI and Cloud. It always just reads local files.

## Implementation

### Files to create

1. `openhands-sdk/openhands/sdk/memory.py` — the `load_memory()` function
2. `tests/sdk/test_memory.py` — unit tests

### Files to modify

3. `openhands-sdk/openhands/sdk/context/agent_context.py`
   - Add `memory_context: str | None = None` field
   - Pass it to the template in `get_system_message_suffix()`

4. `openhands-sdk/openhands/sdk/context/prompts/templates/system_message_suffix.j2`
   - Add `<MEMORY_CONTEXT>` block

5. `openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2`
   - Update `<MEMORY>` section

### Tests

- `load_memory()` with: project index only, user index only, both, neither,
  unreadable file, index exceeding budget (verify truncation keeps tail),
  empty files
- `get_system_message_suffix()` with memory_context set → verify
  `<MEMORY_CONTEXT>` block appears in output
- `get_system_message_suffix()` without memory_context → verify no
  `<MEMORY_CONTEXT>` block

### Wiring (caller-side)

The caller (agent-server or CLI) is responsible for calling `load_memory()`
and passing the result as `memory_context` when constructing `AgentContext`.
This keeps `AgentContext` a plain data container — it doesn't do I/O.

Example in agent-server's conversation startup:

```python
from openhands.sdk.memory import load_memory

memory = load_memory(project_dir="/workspace")
context = AgentContext(..., memory_context=memory)
```

## Open questions

1. **Opt-in or opt-out?** Recommend opt-in for V1, flip to opt-out once
   proven.
2. **Should agent-server auto-wire memory loading?** Or leave it to
   each caller? Recommend auto-wire in agent-server, leave SDK as a
   building block.
3. **Daily log granularity**: One file per day, or one per session?
   Per-day is simpler (fewer files) and matches how humans think about
   time. Multiple sessions on the same day append to the same file.

---

## Appendix: Research Notes

### Claude Code

- File-based, layered: enterprise → user → project → rules → auto-memory
- `MEMORY.md` is an index file with pointers to topic files
  (`debugging.md`, `api-conventions.md`)
- 200-line cap (~25KB), topic files loaded on demand
- Agent writes via "memorize" commands or autonomously
- Confirmed from [claw-code](https://github.com/ultraworkers/claw-code)
  Rust port: `SystemPromptBuilder` truncates per-file (4K) and total (12K)
- RAG auto-activates when project knowledge exceeds context limits

### OpenClaw

- Hybrid file + SQLite retrieval index (BM25 + vector similarity)
- Daily log files (`memory/YYYY-MM-DD.md`), long-term `MEMORY.md`
- Consolidation ("dreaming"): background jobs promote daily logs to
  long-term memory
- Needs embedding provider + vector DB — much heavier than file-only

### Why file-only

| | File-only (Claude Code) | File + Index (OpenClaw) |
|-|------------------------|------------------------|
| Simplicity | ✅ Just Markdown | ❌ SQLite + embeddings |
| Setup cost | ✅ Zero deps | ❌ Embedding provider |
| Portability | ✅ Git-friendly | ⚠️ Index not portable |
| Scalability | ⚠️ Char cap | ✅ Scales via retrieval |

File-only is the right starting point. If we hit scaling limits, we can
add retrieval later.

### References

- [Claude Code Memory Docs](https://code.claude.com/docs/en/memory)
- [OpenClaw Memory Concepts](https://docs.openclaw.ai/concepts/memory)
- [claw-code Rust source](https://github.com/ultraworkers/claw-code)
- [Claude Code System Prompt](https://github.com/Leonxlnx/claude-code-system-prompts/blob/main/prompts/24_memory_instruction.md)
