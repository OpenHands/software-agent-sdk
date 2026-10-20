# Insider Codex stale speech fence

Bead `smolpaws-vstale`. Companion to enyst/insider#4; based on existing Voice draft #5180, not a new provider integration.

The relay must not speak a pending saved result after newer user input. Transcript deltas start one input generation; the corresponding final closes that utterance without starting another. A later saved user message also suppresses answer extraction. If a typed message races the original send, the relay declines attribution rather than speaking another turn's output. Saved Cat events remain unchanged.

No REST or WebSocket schemas, TypeScript contracts, Codex pin, tools, approvals, or run-admission behavior change. The browser still requires explicit playback resume for Codex; the pinned API cannot retract queued audio or identify which input produced it. Follow-up steering remains a separate change.

Verification:
- 53 relay/router tests pass, including real child-process protocol fixtures. New stale-speech and concurrent-typed-input regressions failed before implementation.
- Existing voice route test over live HTTP sockets passes (1 selected).
- Ruff check/format, targeted Pyright, and diff whitespace checks pass.
- Used sibling insider-refresh-oct15 environment, with this branch's SDK/server first on PYTHONPATH. Pyright explicitly uses that interpreter. No dependency changes.
- No real microphone, Codex login, provider inference, deployment, or paid resources.

Limits: transcript notifications lack utterance IDs, so input ownership is conservative. This is not physical-playback evidence. Already queued provider audio cannot be revoked; local suppression protects the listener.

## Codex re-review: delivery boundary

Codex checks speech ownership again when routing a queued delivery. Our relay
checked its generation before an awaited write-lock acquisition. Reproduced a
new user transcript arriving while appendSpeech waited behind that lock: stale
speech still reached the child process. The write now checks the captured speech
generation after acquiring the lock, immediately before writing. Superseded
speech returns without an RPC wait; saved work remains untouched. The regression
uses the real child protocol fixture and passed only after the fix.

Source comparison used openai/codex 014489466a and fetched upstream 66d0de9983;
relevant TUI/core voice paths are unchanged between them. Also checked the actual
pinned rust-v0.154.0 parser: V3 emits transcript events, not the OpenAI-style
speech-start event our browser fallback expects. Transcript-boundary and browser
relay work remain separate follow-ups, not claimed as fixed here.
