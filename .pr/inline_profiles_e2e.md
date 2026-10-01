# Inline profiles, LLM override and gateway profile: end-to-end evidence (2026-09-30)

Every run below was repeated on this branch and on its base (#5406), with the
same script, the canvas mock LLM (which records each completion request), and a
fresh state directory.

## Local agent-server: `.pr/launch_e2e.py`

| Check | #5406 | branch |
|---|---|---|
| inline `agent_profile` launches the same agent as the stored `default` | FAIL (422, not a source) | PASS, recorded `inline: true` |
| per-launch `llm_profile_ref` override: the model called is `mock-fast` | FAIL (422, extra field) | PASS, recorded `llm_profile_ref: mock-fast` |
| OpenAI gateway launches the active profile (its suffix and tools) | FAIL (global settings: no suffix, 20 tools) | PASS (profile suffix, the profile's 5 tools) |
| OpenAI gateway uses the model's LLM profile | PASS | PASS |
| memory preference applies to every source, inline included | FAIL (inline unsupported) | PASS |
| the other 7 checks of #5406 | PASS | PASS |

## Docker runtime: `.pr/launch_docker_e2e.py`

The host runs natively in Docker runtime mode. Each container image is built
from the same commit, and the container's HOME has no stores. The host resolves
an inline profile with a `mock-fast` override and forwards only `agent_settings`.

| Check | #5406 | branch |
|---|---|---|
| inline profile with an LLM override launches through the host: `mock-fast` called, profile suffix present, `inline: true`, `llm_profile_ref: mock-fast` | FAIL (422, extra field) | PASS |
| the other 12 checks of #5406 | PASS | PASS |

The branch passes 13/13 with and without chromium in the container.

## OpenAI gateway with an ACP active profile: `.pr/gateway_acp_e2e.py`

Global settings and the active Agent Profile both run the canvas mock ACP
server (`mock-acp-server.py`), and the request's model names a saved LLM profile.

| Check | #5406 | this branch before the fix (`a2507336`) | this branch |
|---|---|---|---|
| `/v1/chat/completions` answers through the ACP agent | PASS | FAIL (422: `llm_profile_ref` applies only to an OpenHands profile) | PASS |
| an unknown model is a 404 | PASS | FAIL (422) | PASS |

After merging #5408 into this branch, `.pr/launch_e2e.py` still passes 12/12.

## Unit suites

After merging #5408 into both:

| Suite | #5406 | branch |
|---|---|---|
| `tests/sdk` | 6660 passed, 1 failed (`test_truncate`) | 6663 passed, same 1 failed |
| `tests/agent_server` | 2327 passed, 7 failed (`canvas_extensions`, macOS) | 2337 passed, same 7 failed |
| `tests/cross` + `tests/workspace` | 697 passed | 697 passed |

On both, the gateway live test failed under xdist because the tmux socket path
exceeded the macOS limit ("File name too long"). The file passes serially with a
short `TMUX_TMPDIR` (21 passed).
