# Agent Plugins fixture corpus

Checked-in [Agent Plugins](https://agent-plugins.org) v1.0.0 packages, used by

- `tests/sdk/plugin/test_agent_plugins_conformance.py` — the client conformance
  checklist, one test per item, driven through `Plugin.load()`.
- `tests/sdk/conversation/test_local_conversation_agent_plugins.py` — the same
  packages driven all the way to a configured `Conversation`.

Every package is loaded from disk as-is. Nothing here is fetched at test time,
so the suite runs offline.

| Package | What it is for |
| --- | --- |
| `agent-plugins-example/` | The official example plugin, vendored verbatim (see below). Skills-only. |
| `full-package/` | Every component type the SDK implements: skills, an `mcp.json` with both supported transports, and a `dev.openhands` extension (manifest data plus commands, agents and hooks). |
| `manifest-only/` | A manifest and nothing else — every fixed component location is absent. |
| `non-fatal-manifest/` | Carries both violations the spec marks non-fatal: an unknown top-level field and a non-object `extensions`. |
| `unsupported-schema/` | Declares `1.1.0`, a published schema version this SDK does not vendor. |
| `fatal-manifest/` | A `name` that violates §5.5. Ships a skill and an `mcp.json` that must never be discovered. |
| `partial-failures/` | One broken entry per component type beside a working sibling: a malformed skill, a malformed agent, and five MCP entries that must each be skipped on their own (unsupported `sse` transport, escaping `command`, a `${PLUGIN_ROOT}` `command` that must not be expanded, escaping `cwd`, plain-`http` remote URL). Its `good-server` carries neither `args` nor `cwd`, so it also pins the stdio defaults. |
| `wrong-locations/` | The Claude Code layout (`.mcp.json`, `hooks/`, `commands/`, `agents/`) plus an unsupported `lsp/` component type, all at the root. Only `skills/` may load. |
| `mcp-version-mismatch/` | A 1.0.0 manifest beside an `mcp.json` targeting 1.1.0: MCP is disabled, the skill is not. |

## The vendored example plugin

`agent-plugins-example/` is a verbatim copy of
[agentplugins/agent-plugins-example](https://github.com/agentplugins/agent-plugins-example)
at commit `5f3f5084a821aefa792e79500dd8f0462ab83473` (2026-08-05), MIT licensed.

It is vendored rather than fetched so the conformance claim is tested against
real upstream bytes without adding a network-touching test to the PR gate. To
refresh it, replace the directory with a new checkout and update the commit
above.

## Adding a package

Keep each package minimal and single-purpose: one failure boundary per package,
named after the behavior it pins. A package that must never load ships the
components it would otherwise contribute, so a test can assert they stayed
undiscovered.
