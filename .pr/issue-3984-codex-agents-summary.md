# Issue 3984: Codex AGENTS.md investigation

Show-me page: https://enyst.github.io/arch/codex-agents-vs-claude-rules.html

## Concise answer for the issue thread

Codex CLI has directory-scoped `AGENTS.md`, not Claude-style glob rules. It
loads the `AGENTS.md` chain from project root to the active CWD deterministically
(root to CWD, byte-limited, with `AGENTS.override.md` preferred when present),
then its base prompt tells the model to check for deeper or outside-CWD
`AGENTS.md` files when applicable. That means the initial CWD chain is
mechanically injected, but unseen nested files rely on model instruction-following;
there is no Read/Edit/Write path-glob trigger equivalent to Claude Code rules.

## Evidence inspected

- `openai/codex` at `98d28aab54ed86714901b6619400598598876dd0`
- `smolpaws/smolpaws` at `b15dc60f72b9b926cd9931efed1e081190969679`
- Published the show-me HTML artifact to `enyst/enyst.github.io` in commit
  `7fd63f4ae96f766c4238b7501486651a353ea3e1`.

This artifact was created by an AI agent (OpenHands) on behalf of the issue request.
