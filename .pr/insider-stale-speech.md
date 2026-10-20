# Insider Codex stale speech fence

Bead `smolpaws-vstale`. Companion to enyst/insider#4; based on existing Voice draft #5180, not a new provider integration.

The relay must not speak a pending saved result after newer user input. Transcript deltas start one input generation; the corresponding final closes that utterance without starting another. A later saved user message also suppresses answer extraction. If a typed message races the original send, the relay declines attribution rather than speaking another turn's output. Saved Cat events remain unchanged.

No REST or WebSocket schemas, TypeScript contracts, Codex pin, tools, approvals, or run-admission behavior change. The browser still requires explicit playback resume for Codex; the pinned API cannot retract queued audio or identify which input produced it. Follow-up steering remains a separate change.

Verification:
- 52 relay/router tests pass, including real child-process protocol fixtures. New stale-speech and concurrent-typed-input regressions failed before implementation.
- Existing voice route test over live HTTP sockets passes (1 selected).
- Ruff check/format, targeted Pyright, and diff whitespace checks pass.
- Used sibling insider-refresh-oct15 environment, with this branch's SDK/server first on PYTHONPATH. Pyright explicitly uses that interpreter. No dependency changes.
- No real microphone, Codex login, provider inference, deployment, or paid resources.

Limits: transcript notifications lack utterance IDs, so input ownership is conservative. This is not physical-playback evidence. Already queued provider audio cannot be revoked; local suppression protects the listener.
