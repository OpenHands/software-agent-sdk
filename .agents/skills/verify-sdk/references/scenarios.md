# Situations a happy path never shows

A family can be fully mapped, every bullet passing, and someone else still
files a bug on it the same week. Reading the fixes other people sent while
this map was written and since it landed (2026-10-13 to 2026-11-11), the
behavior was usually mapped, and often its bullet passed. What the recipe
lacked was the *situation*: a model that is not the scripted one, a file or
value shaped like a user's, two features on at once, a failure halfway
through, or a program installed from a wheel instead of this checkout. A few
times the recipe saw the bug and wrote it down as the way things are.

Ask these questions in three places:

- **Writing recipes** ([recipes.md](recipes.md)): after a sub-feature's happy
  path is written, walk the list and add a bullet for each question that has
  an answer on this surface.
- **The daily pass** ([daily.md](daily.md) step 2): for each merged change,
  ask the questions of the behavior it touched, not only whether it does what
  its PR says.
- **A full pass** ([maintenance.md](maintenance.md) §4): each source reader
  returns, with its drift list, the questions its family has no bullet for.

A question with no answer on a surface is skipped. One that needs what the
run lacks is `blocked` with the prerequisite. Every bullet added this way is
replayed before it is written.

These questions are illustrative, not exhaustive. Ask them where a wrong
answer would hurt a program using the SDK; unit tests own the rest (one
contributor's edge-case pass, #6348, found about 70 unhappy-path bugs at
that level, and its tests now pin them). When a report fits none of them,
add the question.

## 1. Did we write a bug down as the contract?

`S15` noted that `browser_get_content` right after a click "can show the page
from before the click" and told the agent to call `browser_get_state` first
(#6340 fixed the tool). `S16` asserted that a `task_tracker` status of
`pending` is a validation error, which is what DeepSeek sends (#6005).

- A Gotcha or expected result that a user would call wrong is a finding.
  File it (or find the issue) and write `Known failure (reproduced <date>,
  #<issue>)`, or link the PR, doc or issue comment that decided it is
  intended. A workaround in the guidance is not a decision.
- Take inputs from what users and models send and from the product's own
  documentation: the example in a tool's description (`grep` advertises
  `include="*.{ts,tsx}"`; the map drove `*.py`, and the braces matched
  nothing), the names other agents' tools use, the documented secret names.

## 2. Was the model, or the provider, not ours?

The scripted `TestLLM` accepts any message shape and sends exactly what the
script says; DeepSeek is the only live model. About a quarter of the outside fixes
(leaving out the edge-case pass) came from another model or provider: Claude 4.6 rejects a request that ends
with the assistant turn (#5447), gpt-oss loses its reasoning after a tool
call (#6100), strict endpoints reject blank content blocks (#4965), a model
repeats an invalid call while changing its thought text (#6277), providers
send `Retry-After` (#6273), OpenRouter needs `session_id` (#6164), Codex
subscriptions need session headers (#6363), a custom alias reports
`usage.cost` (#6543), a pinned ACP adapter refuses a new model (#5995).

- For a request-shaping change, check the formatted request against the
  provider families that treat it differently (`format_messages_for_llm`,
  the `sent.*` checks in `S02`), not only the one under test: the request's
  last role, empty and whitespace content, reasoning fields, provider
  headers.
- In a script, send what other models send: synonyms (`pending`,
  `completed`), a field missing per turn, the same invalid call with a new
  thought, an empty reply.
- A transport behavior (retries, headers, cost) gets one bullet with the
  provider's real response shape, from a local fake server
  (`programs/S16/fake_openai.py`) when no key is at hand.

## 3. Were two features on at once?

A sub-agent loading an LLM profile encrypted with `OH_SECRET_KEY` (#5564), a
sub-agent under a parent with MCP servers (#5743) or a scoped tool list
(#5519), `fork(title=)` on a conversation with user tags (#4814): each
family's bullets pass with the other family off.

- For each sub-feature, ask which of these reaches it and drive the ones
  that do: secrets (registered, encrypted, masked), sub-agents and profiles,
  MCP servers, plugins and marketplaces, `LocalConversation` versus
  `RemoteConversation` (the twin links name the same promise on the server).

## 4. Was the data like a user's?

Fixtures are short, ASCII, LF-terminated, UTF-8 and unique. Give each file,
path, name and secret a bullet with one that is not:

- files with CRLF endings, a BOM, no trailing newline, another encoding, or
  behind a symlink; paths with spaces or non-ASCII; a case-only rename;
- secrets that are short or a prefix of another (#6160, #6258, #6348);
- tokens with `/`, `@`, `:` in a plugin source URL (#6509); Azure DevOps
  and `ssh://` URLs (#5986, #6348); a plugin without `$schema`;
- a stray or missing file in a persistence directory (#6174, #6349).

## 5. What happens when something fails halfway?

Closing a conversation while a terminal command runs (#6139), a hook that
times out with a child process (#6348), Docker or Apptainer startup that
fails after `docker run` (#6348), an auto-loaded marketplace that cannot be
cloned (#6460), a plugin that fails to load (#6439), an MCP close that times
out (#5722).

- Make the dependency fail for real and assert what the program is told and
  what is left running: `pgrep` for children, `docker ps` for containers.

## 6. Was it run where programs run?

`sdk-map run` imports the SDK from this checkout with `uv.lock`'s versions,
on Linux with a UTF-8 locale. Programs install the wheel with `pip` or
`uvx`, resolve newer dependencies, and run on macOS and Windows: the browser
recording JS was missing from the wheel (#4443), an unlocked install pulled
mcp 2 and lost the browser tools (#5152), tmux needs a UTF-8 locale (#6261)
and a short socket path on macOS (#6038), Windows picked the wrong Chromium
(#6730).

- When a change adds a non-Python file a tool reads at runtime, check the
  package data (`pyproject.toml`) or replay the bullet against a built wheel
  in a fresh virtualenv.
- A row `blocked` on an OS, account or Docker was not checked; the report
  lists it as blocked, never as covered.
