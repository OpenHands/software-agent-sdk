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

### What changes

1. A `load_memory()` function reads `MEMORY.md` from the project workspace
   and from `~/.openhands/memory/MEMORY.md` (user-global), concatenates
   them, truncates to a char budget, and returns the combined text.

2. The system message suffix template gets a new `<MEMORY_CONTEXT>` block
   (after `<REPO_CONTEXT>`) that renders the loaded memory.

3. The `<MEMORY>` section in `system_prompt.j2` is updated to instruct the
   agent to write learnings to `MEMORY.md` at the end of a task.

### What doesn't change

- No new abstractions (`MemoryStore`, `MemoryLoader` class, etc.). It's a
  function that reads two files.
- No content filtering heuristics. The 6K char budget caps the blast radius.
  If memory is poisoned, the user deletes the file. Same as AGENTS.md.
- No Cloud-specific code in the SDK. Cloud persistence (seeding the file
  into the workspace at pod start, extracting it at pod shutdown) is handled
  by the app server / deploy layer, not the SDK.
- The agent writes to `MEMORY.md` using the file editor tool it already has.
  No new write API needed in the SDK.

### Memory file locations

```
<workspace>/MEMORY.md              # Project memory (agent-written)
~/.openhands/memory/MEMORY.md      # User memory (cross-project)
```

Both are plain Markdown. Neither is git-tracked by default.

### Memory loading

```python
def load_memory(
    project_dir: str | None = None,
    user_memory_dir: str | None = None,
    budget: int = 6000,
) -> str | None:
    """Read project + user memory files, combine, and truncate.

    Returns combined memory text, or None if no memory files exist.
    Errors are logged and treated as no-memory (advisory, never blocking).
    """
```

- Reads project `MEMORY.md` from `project_dir` (workspace root)
- Reads user `MEMORY.md` from `user_memory_dir` (defaults to
  `~/.openhands/memory/`)
- Concatenates: user memory first, then project memory (project appears
  later in prompt → higher attention weight)
- If combined size exceeds `budget`, truncates from the top (oldest entries
  removed first, keeping the tail / most recent)
- Returns `None` if neither file exists
- I/O errors are caught, logged, and treated as "file doesn't exist"

### Size budget

6,000 chars (~1,500 tokens). Our root AGENTS.md alone is ~18K chars. The
system prompt with all skills is already substantial. Memory should be a
small supplement, not a second AGENTS.md.

### System prompt instruction

Replace the current `<MEMORY>` block in `system_prompt.j2`:

```
<MEMORY>
* Use `MEMORY.md` in your workspace for persistent memory across sessions.
* At the END of a task, record key learnings in MEMORY.md. Only write things
  that would genuinely help in future sessions:
  - Surprising behaviors, gotchas, error patterns
  - Architectural decisions and their rationale
  - User preferences and workflow shortcuts
  - Environment-specific configuration
* Do NOT record obvious facts or anything trivially re-discoverable.
* Prefix each entry with the date: `## YYYY-MM-DD`
* MEMORY.md is automatically included in your system prompt next time.
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

{{ memory_context }}
</MEMORY_CONTEXT>
{% endif %}
```

### Cloud persistence (not SDK scope)

Cloud workspaces are ephemeral K8s pods. Memory survives across sessions via
the app server, not the SDK:

1. **Session start**: App server reads memory from durable storage (GCS
   bucket, same `prod-openhands-sessions` bucket used for conversation
   events) and writes it to `/workspace/MEMORY.md` before the agent starts.
2. **During session**: Agent reads/writes `MEMORY.md` normally.
3. **Session end**: App server reads `/workspace/MEMORY.md` from the pod
   (via the agent-server file download API) and persists it back to GCS.

This could also work with a shared volume mount per user/project, but the
GCS approach is simpler — it reuses existing infrastructure and doesn't
require `ReadWriteMany` PVs.

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

- `load_memory()` with: single file, both files, neither file, unreadable
  file, file exceeding budget (verify truncation keeps tail), empty files
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
