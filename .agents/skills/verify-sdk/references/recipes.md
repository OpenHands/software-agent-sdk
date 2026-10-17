# Writing recipes

A recipe proves a sub-feature the way a user's program sees it: it builds an
agent with the public SDK, runs it, and checks what came out (state, files,
events, a payload). It never reruns the unit tests: CI runs those on every PR,
and the map links them on each row's `Unit:` line for reference. `sdk-map
check` rejects a `Driving it` block that calls `pytest`.

## The pieces

- **A run.** `export SDK_VERIFY_RUN=$(control-sdk new --field run)` starts each
  block: a private HOME, `OH_PERSISTENCE_DIR`, conversation store, workspace
  and tmux server. `--llm deepseek-flash` makes it a live run.
- **Conversation verbs** for the common loop, each its own process that
  resumes the persisted conversation:
  `conversation new [--tools ...] [--script FILE | --script-json JSON]`,
  `conversation send ID --text ... [--run] [--allow-error]`,
  `conversation run ID`, `conversation state ID`,
  `conversation events ID [--kind K] [--source S] [--last N]`.
- **Programs** for everything else: `programs/Snn/<name>.py`, run with
  `control-sdk program Snn/<name> [--arg ...]`. A program is a short script
  like the examples: it imports the public SDK, does one scenario, and prints
  one JSON object as its last line (`sdk_verify.emit`). Keep assertions out of
  the program; report facts and let the recipe's `--check`s judge them, so a
  reader sees the expectation in the map.
- **Commands and examples**: `control-sdk exec [--model-env] -- python
  examples/...` runs any command inside the run; `--model-env` exports
  `LLM_MODEL`/`LLM_API_KEY` from the run's real model. An `examples/` path
  marks a block as needing a model; a block whose example makes no model
  call says so with a `# no model` line and then runs without a key.
- **Checks**: every verb takes `--check FIELD OP [VALUE]` (ops: `eq ne lt le
  gt ge contains not-contains matches exists missing len-eq len-ge`; fields
  are dotted paths, list indexes allowed, `\.` for a dot inside a key) and `--field PATH` to print one
  value for the shell. A failed check exits 1; a missing model key exits 3,
  which `sdk-map run` reports as `blocked`.

## Scripted and live

A script (`--script`, or `make_llm(script=[...])` in a program) drives the
real agent loop with the SDK's `TestLLM`, one step per LLM call:

```json
[
  {"text": "Listing files.", "tool": "terminal", "args": {"command": "ls"}},
  {"tools": [{"tool": "file_editor", "args": {"command": "view", "path": "a.txt"}},
             {"tool": "terminal", "args": {"command": "cat a.txt"}}]},
  {"error": "LLMRateLimitError", "message": "rate limit"},
  {"finish": "done"}
]
```

Scripted recipes are deterministic and need no key; they prove the SDK's
side of every exchange. Live recipes (`control-sdk new --llm
deepseek-flash`, examples, the integration runner) prove the same features
against a real model; check outcomes (a file exists, the run finished, the
log reloads), never the model's wording. Give a feature both when a real
model changes what can happen (tool-call shapes, streaming, provider
errors), and only a scripted one when the model cannot matter.

## Conventions

- One scenario per bullet; every sub-feature ID appears in at least one
  bullet's label. A block is one run: it starts with `control-sdk new`.
- Programs take the conversation id or a mode as arguments rather than
  duplicating a scenario. Shared helpers live in `programs/sdk_verify.py`.
- Something the SDK cannot be driven to without outside infrastructure
  (Docker, a cloud account, a vLLM endpoint, an interactive login) is a
  bullet labeled **blocked** that names the prerequisite.
- A product bug keeps the recipe that expects correct behavior, plus
  `Known failure (reproduced <date>, #<issue>):` and what happens instead.
