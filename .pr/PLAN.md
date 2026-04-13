# Memory: Persistent Auto-Learning Across Sessions — Implementation Plan

> Research and design document for [#2037](https://github.com/OpenHands/software-agent-sdk/issues/2037)

## Table of Contents

1. [Research Summary](#1-research-summary)
2. [Trade-Off Analysis](#2-trade-off-analysis)
3. [Current SDK State](#3-current-sdk-state)
4. [Proposed Design](#4-proposed-design)
5. [Implementation Plan](#5-implementation-plan)
6. [Open Questions](#6-open-questions)

---

## 1. Research Summary

### 1.1 Claude Code's Memory System

**Architecture**: File-based, layered precedence chain.

**File Hierarchy** (broader → narrower):

| Layer | Location | Purpose |
|-------|----------|---------|
| Enterprise/System | `/Library/Application Support/ClaudeCode/CLAUDE.md` | Org-wide policies |
| User | `~/.claude/CLAUDE.md` | Personal preferences (not version-controlled) |
| Project | `<repo>/CLAUDE.md` | Shared project rules (version-controlled via Git) |
| Rules | `<repo>/.claude/rules/*.md` | Modular conditional rules (YAML front-matter `paths:` field) |
| Auto-Memory | `~/.claude/projects/<project>/memory/MEMORY.md` + topic files | Agent-written learnings (per-project, per-user) |

**Key Implementation Details** (confirmed from [claw-code](https://github.com/ultraworkers/claw-code) Rust port):

- `prompt.rs`: A `SystemPromptBuilder` assembles the system prompt by:
  1. Calling `discover_instruction_files()` which scans for `CLAUDE.md` and `.claw/instructions.md`
  2. Building a `ProjectContext` with instruction files, git status, and config
  3. Rendering instruction files with `render_instruction_files()` into a `# Claude instructions` section
  4. Each file is tagged with `scope: <path>` metadata
  5. Content is truncated per-file (`MAX_INSTRUCTION_FILE_CHARS = 4000`) and total (`MAX_TOTAL_INSTRUCTION_CHARS = 12000`)

- `config.rs`: Configuration loaded from User → Project → Local scopes with `ConfigLoader`
- `bootstrap.rs`: Defines `BootstrapPlan` with ordered phases for session initialization

**Auto-Memory Behavior**:

- MEMORY.md is an index file with short entries (date + description + pointer to topic files)
- **200-line cap** (~25KB) — only first 200 lines loaded into every session
- Topic files (e.g., `debugging.md`, `api-conventions.md`) loaded on demand
- Agent can write to MEMORY.md via explicit "memorize" commands or automatically
- Disablable via `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` or `autoMemoryEnabled` setting
- `/memory` command opens memory files for manual editing

**RAG Expansion**: When project knowledge exceeds context limits, a RAG feature auto-activates for semantic retrieval without user configuration.

### 1.2 OpenClaw's Memory System

**Architecture**: Hybrid file + retrieval index.

**Memory Layers**:

| Layer | Storage | Purpose |
|-------|---------|---------|
| Working Memory | LLM context window | Ephemeral, current session |
| Episodic (Daily Logs) | `memory/YYYY-MM-DD.md` | Semi-permanent session records |
| Long-term | `MEMORY.md`, `USER.md`, `AGENTS.md`, `SOUL.md` | Curated durable facts |
| Retrieval Index | Per-agent SQLite (+ sqlite-vec) | Searchable semantic/keyword index |

**Key Implementation Details**:

- **Hybrid retrieval**: Combines BM25 keyword matching (FTS5) + vector similarity (sqlite-vec/cosine), with ranked selection
- **Default limits**: Max 6 search results, 4000ms timeout
- **CLI tools**: `memory_search` (semantic search) and `memory_get` (read file/line range)
- **Startup injection**: Profile/bootstrap files loaded at session start; recent daily logs (today + yesterday) loaded for temporal recency
- **MEMORY.md** loaded only in private/main sessions (privacy control)

**Consolidation ("Dreaming")**:

- Opt-in background process that promotes short-term items into MEMORY.md
- Writes phase summaries to `DREAMS.md`
- Scheduled jobs: `memory-writer` (hourly), `memory-janitor` (4x/day)
- Heuristics: score, recall frequency, query diversity gates
- Modes: off, core, rem, deep

**Embedding Providers**: Auto-detects from API keys — OpenAI, Gemini, Voyage, Mistral, local models.

**Alternative Backends**: Plugins for LanceDB, Mem0+Qdrant, Pinecone, Weaviate, Chroma.

### 1.3 Claw Code (Rust Implementation — Claude Code Reference)

The [ultraworkers/claw-code](https://github.com/ultraworkers/claw-code) repo provides a public Rust reimplementation of the Claude Code CLI. Key observations from the source:

- **`rust/crates/runtime/src/prompt.rs`**: Confirms the system prompt assembly pipeline:
  - `ProjectContext::discover()` → finds instruction files
  - `SystemPromptBuilder::build()` → assembles sections: intro → system → tasks → actions → DYNAMIC_BOUNDARY → environment → project context → instruction files → config
  - `render_instruction_files()` formats each file with scope metadata
  - Truncation enforced per-file (4K chars) and total (12K chars)

- **`rust/crates/runtime/src/config.rs`**: Three-tier config (User/Project/Local) with scope-aware merging, supporting MCP, hooks, permissions, plugins

- **`rust/crates/runtime/src/bootstrap.rs`**: Multi-phase bootstrap plan (DaemonWorker → Bridge → Daemon → BackgroundSession → Template → EnvironmentRunner → MainRuntime)

---

## 2. Trade-Off Analysis

### 2.1 File-Only (Claude Code) vs. File + Index (OpenClaw)

| Dimension | File-Only (Claude Code) | File + Index (OpenClaw) |
|-----------|------------------------|------------------------|
| **Simplicity** | ✅ Very simple — just Markdown files | ❌ Requires SQLite + embeddings |
| **Portability** | ✅ Git-friendly, human-readable | ⚠️ Index files not Git-friendly |
| **Semantic Recall** | ❌ No semantic search (lexical only) | ✅ Hybrid BM25 + vector similarity |
| **Scalability** | ⚠️ 200-line cap, context limits | ✅ Scales via index + retrieval |
| **Setup Cost** | ✅ Zero dependencies | ❌ Needs embedding provider + vector DB |
| **Cloud Compatibility** | ⚠️ Needs persistent file storage | ⚠️ Also needs persistent storage |
| **Security** | ⚠️ Memory poisoning risk via files | ⚠️ Same + index corruption risk |
| **Consolidation** | ❌ Manual only | ✅ Automated dreaming/compaction |

### 2.2 Key Design Trade-Offs for Our SDK

| Decision | Option A | Option B | Recommendation |
|----------|----------|----------|----------------|
| Storage format | File-only (MEMORY.md) | File + retrieval index | **File-only for V1** — simplicity matches our design principles; can add index later |
| Memory scope | Per-project only | Per-project + per-user global | **Both** — project MEMORY.md (in workspace) + user MEMORY.md (~/.openhands/) |
| Auto-writing | Agent writes autonomously | Agent writes on explicit instruction | **Both** — system prompt instructs autonomous writes, user can also request |
| Size management | Hard truncation | Summarization/consolidation | **Hard truncation for V1** — line cap like Claude Code, consolidation as V2 |
| Cloud storage | File on workspace | Database/API storage | **Abstracted via MemoryStore interface** — file for CLI, API for Cloud |

---

## 3. Current SDK State

### 3.1 What We Already Have

Our SDK already has the foundational pieces:

1. **`<MEMORY>` section in system prompt** (`system_prompt.j2` lines 8-13): Already instructs the agent to use `AGENTS.md` as persistent memory. This is the read/write memory mechanism that works today.

2. **Third-party skill loading** (`skill.py`): Loads `AGENTS.md`, `.cursorrules`, `CLAUDE.md` from repo root as always-active skills with `trigger=None`. These are injected into the system message suffix.

3. **Skills infrastructure** (`AgentContext.get_system_message_suffix()`): Categorizes skills into repo-skills (always in context) and available-skills (progressive disclosure). Third-party files like AGENTS.md become repo-skills.

4. **Conversation persistence** (`ConversationState.persistence_dir`): Existing mechanism for persisting conversation state and events to disk.

5. **User skills directory** (`~/.openhands/skills/`): Infrastructure for user-level skill files that persist across projects.

### 3.2 What's Missing

1. **Dedicated MEMORY.md file** distinct from AGENTS.md — AGENTS.md is repo guidance, MEMORY.md should be agent-learned insights
2. **Structured memory loading** with size limits, truncation, and priority ordering
3. **Memory write instruction** in the system prompt telling the agent to autonomously record learnings
4. **Cloud-compatible memory storage** — file-based works for CLI but Cloud needs an abstraction
5. **Per-user memory** across projects (preferences, style, common patterns)
6. **Memory management** — truncation strategy, deduplication, staleness

---

## 4. Proposed Design

### 4.1 Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      System Prompt Assembly                      │
│  ┌───────────┐  ┌──────────────┐  ┌───────────────────────────┐ │
│  │ Base       │  │ AGENTS.md    │  │ MEMORY.md (auto-learned) │ │
│  │ Prompt     │  │ (repo rules) │  │ - Project memory          │ │
│  │            │  │              │  │ - User global memory      │ │
│  └───────────┘  └──────────────┘  └───────────────────────────┘ │
│         ↑              ↑                     ↑                   │
│         │              │                     │                   │
│     Static        Skills loader        MemoryStore               │
│                  (existing)           (new component)            │
└─────────────────────────────────────────────────────────────────┘
                                              │
                              ┌───────────────┼───────────────┐
                              │               │               │
                        ┌─────┴─────┐  ┌──────┴──────┐  ┌────┴────┐
                        │ LocalFile  │  │  CloudAPI   │  │ Custom  │
                        │ Store      │  │  Store      │  │ Store   │
                        │ (CLI)      │  │ (Cloud)     │  │         │
                        └───────────┘  └─────────────┘  └─────────┘
```

### 4.2 Memory File Locations

```
# Per-project memory (in workspace, git-trackable)
<workspace>/MEMORY.md          # Agent-written project learnings

# Per-user memory (personal, not git-tracked)
~/.openhands/memory/MEMORY.md  # Global cross-project learnings
```

### 4.3 Memory Store Interface

```python
class MemoryStore(Protocol):
    """Interface for reading and writing persistent memory."""

    def read_project_memory(self, project_id: str) -> str | None:
        """Read project-specific memory content."""
        ...

    def read_user_memory(self) -> str | None:
        """Read user-level global memory content."""
        ...

    def write_project_memory(self, project_id: str, content: str) -> None:
        """Write project-specific memory content."""
        ...

    def write_user_memory(self, content: str) -> None:
        """Write user-level global memory content."""
        ...
```

### 4.4 Memory Loading Pipeline

At session start, memory is loaded in this order (higher = lower priority):

1. **User global memory** (`~/.openhands/memory/MEMORY.md` or Cloud API)
2. **Project memory** (`<workspace>/MEMORY.md` or Cloud API)

Both are injected into the system prompt suffix alongside existing AGENTS.md content, wrapped in a `<MEMORY_CONTEXT>` block.

### 4.5 Memory Size Limits

Following Claude Code's proven approach:

| Limit | Value | Rationale |
|-------|-------|-----------|
| Per-file max | 4,000 chars | Matches Claude Code's `MAX_INSTRUCTION_FILE_CHARS` |
| Total memory budget | 8,000 chars | Leave room for other prompt content |
| Line cap for auto-memory | 200 lines | Claude Code's proven MEMORY.md cap |
| Truncation strategy | Keep first N lines + "[truncated]" notice | Simple, predictable behavior |

### 4.6 System Prompt Changes

Update the `<MEMORY>` section in `system_prompt.j2`:

```jinja2
<MEMORY>
* Use `AGENTS.md` under the repository root as your persistent memory for repository-specific knowledge and context.
* Use `MEMORY.md` for recording learnings, patterns, and insights discovered during this session.
* As you complete tasks, autonomously write down key learnings so you can be more effective in future conversations. Anything saved in MEMORY.md will be included in your system prompt next time.
* Focus on recording: surprising behaviors, gotchas, error patterns, architectural decisions, user preferences, and workflow shortcuts.
* Keep entries concise — MEMORY.md has a {{memory_line_cap}}-line limit.
* For more information about skills, see: https://docs.openhands.dev/overview/skills
</MEMORY>
```

### 4.7 Cloud Implementation Strategy

For the Cloud, MEMORY.md can't live on a filesystem that's recreated per session. Options:

**Recommended approach: Store memory via the conversation/workspace API**

1. The agent server exposes a memory endpoint: `GET/PUT /api/v1/memory/{scope}` where scope is `project` or `user`
2. Memory content is stored in the existing persistence layer (same infra as conversation state)
3. At session start, the server reads memory and injects it into `AgentContext.system_message_suffix`
4. The agent writes to MEMORY.md in the workspace; at session end (or periodically), the server persists it back

```
Session Start:
  Cloud API → read memory → inject into system prompt suffix

During Session:
  Agent writes to MEMORY.md in workspace (normal file operations)

Session End / Periodic:
  Cloud API ← read MEMORY.md from workspace ← persist to storage
```

This keeps the agent's experience identical between CLI and Cloud — it always just reads/writes a file.

---

## 5. Implementation Plan

### Phase 1: Core Memory Loading (SDK changes only)

**Goal**: Load MEMORY.md files and inject them into the system prompt.

1. **Add MEMORY.md to third-party skill files** in `Skill.PATH_TO_THIRD_PARTY_SKILL_NAME`
   - File: `openhands-sdk/openhands/sdk/skills/skill.py`
   - Add `"memory.md": "memory"` to the mapping
   - This automatically loads `<workspace>/MEMORY.md` as an always-active skill

2. **Add user-level memory loading** in `load_available_skills()`
   - File: `openhands-sdk/openhands/sdk/skills/skill.py`
   - Check `~/.openhands/memory/MEMORY.md` when `include_user=True`
   - Load as a skill with `trigger=None` (always active)

3. **Add memory-specific truncation**
   - File: `openhands-sdk/openhands/sdk/skills/skill.py` or new `memory.py`
   - Apply 200-line / 4000-char cap specifically for memory files
   - Add `[truncated — keep MEMORY.md concise]` notice

4. **Update system prompt** to instruct auto-learning
   - File: `openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2`
   - Enhance the `<MEMORY>` section with auto-learning instructions

### Phase 2: Memory Store Abstraction

**Goal**: Enable Cloud-compatible memory persistence.

5. **Define `MemoryStore` protocol**
   - New file: `openhands-sdk/openhands/sdk/memory/store.py`
   - Protocol with `read_project_memory()`, `read_user_memory()`, etc.

6. **Implement `LocalFileMemoryStore`**
   - New file: `openhands-sdk/openhands/sdk/memory/local.py`
   - Reads/writes MEMORY.md from workspace and `~/.openhands/memory/`

7. **Integrate MemoryStore into AgentContext**
   - File: `openhands-sdk/openhands/sdk/context/agent_context.py`
   - Add optional `memory_store: MemoryStore | None` field
   - Load memory content at context construction time

### Phase 3: Cloud Integration

**Goal**: Memory works in OpenHands Cloud.

8. **Implement `CloudMemoryStore`**
   - New file: `openhands-agent-server/openhands/server/memory/cloud_store.py`
   - Uses existing persistence infrastructure (S3/DB) to store memory
   - Memory keyed by (user_id, project_id/repo)

9. **Add memory API endpoints**
   - `GET /api/v1/memory/project/{project_id}` — read project memory
   - `PUT /api/v1/memory/project/{project_id}` — write project memory
   - `GET /api/v1/memory/user` — read user global memory
   - `PUT /api/v1/memory/user` — write user global memory

10. **Session lifecycle hooks**
    - On session start: Load memory → inject into context
    - On session end: Read workspace MEMORY.md → persist to Cloud store
    - Periodic sync during long sessions

### Phase 4: Advanced Features (Future)

11. **Memory consolidation** — Periodic summarization of growing memory files
12. **Memory deduplication** — Detect and merge duplicate entries
13. **Semantic retrieval** — Add vector index for large memory stores (OpenClaw-style)
14. **Memory analytics** — Track which memories are most useful
15. **Team memory** — Shared memory across team members for a project

---

## 6. Open Questions

1. **Should MEMORY.md be git-tracked?**
   - Pro: Shared across team, versioned
   - Con: Contains agent-specific learnings that may not generalize
   - Recommendation: Optional — `.gitignore` MEMORY.md by default, let users opt-in

2. **Should memory entries have timestamps?**
   - Pro: Enables staleness detection and cleanup
   - Con: Adds complexity
   - Recommendation: Yes, encourage dated entries in the system prompt instruction

3. **How to handle conflicting memories across sessions?**
   - E.g., Session A writes "use pytest", Session B writes "use unittest"
   - Recommendation: Last-write-wins for V1; consolidation in V2

4. **Memory poisoning / security**
   - Malicious content in MEMORY.md could influence agent behavior
   - Recommendation: Same trust model as AGENTS.md — treat as user-provided content, apply existing security analyzers

5. **Should we support the `/memory` command pattern?**
   - Claude Code has `/memory` to view/edit memory in-session
   - Recommendation: Not for V1; the agent can already read/write files

6. **What's the right default for `load_memory` in AgentContext?**
   - Should memory loading be opt-in or opt-out?
   - Recommendation: Opt-in initially (like `load_user_skills`), flip to opt-out once stable

---

## References

- [Claude Code Memory Documentation](https://code.claude.com/docs/en/memory)
- [OpenClaw Memory Concepts](https://docs.openclaw.ai/concepts/memory)
- [OpenClaw Memory Config Reference](https://docs.openclaw.ai/reference/memory-config)
- [OpenClaw GitHub — Memory Design Doc](https://github.com/openclaw/openclaw/blob/main/docs/concepts/memory.md)
- [claw-code Rust Implementation](https://github.com/ultraworkers/claw-code) — `rust/crates/runtime/src/prompt.rs`, `config.rs`, `bootstrap.rs`
- [Claude Code System Prompt (Memory Instruction)](https://github.com/Leonxlnx/claude-code-system-prompts/blob/main/prompts/24_memory_instruction.md)
- [How Claude Code Builds a System Prompt](https://dbreunig.com/2026/04/04/how-claude-code-builds-a-system-prompt.html)
- [Local-First RAG Using SQLite for AI Agent Memory (OpenClaw)](https://pingcap.com/blog/local-first-rag-using-sqlite-ai-agent-memory-openclaw/)
