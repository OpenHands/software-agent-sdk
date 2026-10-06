# Canvas demonstration — scripted provider

This is a real Chrome browser using Agent Canvas and the B-source Agent Server.
The local HTTP provider uses `TestLLM` scripted responses. It verifies UI, tool,
event, and request integration; **it is not real-model behavior evidence**.

- SDK source: `20532dd3e5fb88e75e880ce07a6afbdff98df1b3` (1.53.0 development source).
- Canvas source: `a07364828c8f202e7745c6bce3dcef3915ae7ac1` (package 1.20.0).
- Final run: **1 passed**, 11.828 seconds, 2026-10-06 21:18 UTC.
- [Notes and reset](01-notes-reset.png), [history recovered](02-history-recovered.png),
  [full recording](canvas-scripted-demo.webm) (242,159 bytes).
- [Verification summary](verification.json) records event IDs, forgotten IDs,
  runtime versions, assertions, and artifact hashes.

The browser created a conversation from an agent profile configured with
`agent_reset`. A real `file_editor.create` wrote `retry-notes.md`; `new_context`
completed with its real result; a real `file_editor.view` reread the notes.
Both file observations were successful. The notes omitted the original
`Retry-After: 27` value. The next provider request retained the reset call/result
and omitted that original value. A browser follow-up then executed
`conversation_history.search` and `read`, retrieving the original event with
`in_active_view: false`; the final provider request and UI answer contained it.
The event log contained one voluntary reset and zero summary rescues.

The scripted sequence included a title response for Canvas's background title
request, followed by seven main-agent responses: create notes, reset, read notes,
first answer, history search, history read, final answer. Earlier trial runs
revealed this title request consuming a scripted turn; they are not the evidence
reported above. Final assertions checked actual file creation and successful
file-tool observations, rather than relying on the scripted answer's claims.

The isolated Canvas checkout ran `npm run dev:minimal` with
`OH_AGENT_SERVER_LOCAL_PATH` pointing to B, separate state/key paths, backend
port `18416`, frontend port `31416`, and `VITE_DO_NOT_TRACK=1`. The existing
`tests/e2e/mock-llm/scripts/mock-llm-server.py` ran with B's Python environment on
port `19416`. The browser command was:

```sh
MOCK_LLM_PORT=19416 \
MOCK_LLM_BACKEND_URL=http://localhost:31416 \
MOCK_LLM_SESSION_API_KEY=issue4916-local-demo \
npx playwright test --config .pr/issue4916-playwright.config.ts
```

The session key and provider key were demo-only dummy values. No real model
credentials, cookies, or external inference were used. The temporary Playwright
scenario/config and full raw request/event capture remain in the isolated Canvas
worktree under `.pr/` and `.agent_tmp/final/evidence/`; they are not proposed
repository changes. The original Canvas checkout and its existing `.DS_Store`
files were untouched.

This development stack omits automation and VS Code, so ancillary 404 toasts can
appear (visible at the edge of the screenshots). The notes/reset/
history tools themselves completed successfully. Canvas renders the new tools
through its existing generic `NEWCONTEXT` and `CONVERSATIONHISTORY` cards; this
does not claim a dedicated Canvas feature UI or full automation-stack validation.
