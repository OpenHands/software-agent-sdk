# Agent Plugins Example

A copyable reference package for the [Agent Plugins Specification v1.0.0](https://agent-plugins.org/specification).

This repository demonstrates the portable core and includes an Agent Skill that helps migrate existing plugins from client-specific formats. It is a reference example, not a substitute for the normative specification; if they differ, the specification wins.

## Layout

```text
agent-plugins-example/
├── plugin.json                         # Required portable manifest
├── skills/                             # Portable Agent Skills
│   └── migrate-agent-plugin/
│       ├── SKILL.md
│       └── references/
│           ├── client-extensions.md
│           ├── migration-guide.md
│           └── validation-checklist.md
├── README.md
└── LICENSE
```

The smallest valid plugin needs only `plugin.json`. This example includes a skill so it is useful as well as structurally illustrative.

## The portable core

Agent Plugins v1 defines two portable component types:

- Agent Skills under immediate child directories of `skills/`.
- MCP servers in an optional root `mcp.json` using the matching v1.0.0 MCP schema.

The root `plugin.json` schema is closed. Do not add `hooks`, `agents`, `commands`, `mcpServers`, `lspServers`, or arbitrary client fields at its top level. Put portable MCP configuration in `mcp.json` and client-owned manifest data inside `extensions`.

See the authoritative documentation for the [plugin manifest](https://agent-plugins.org/plugin-authors/manifest), [skills](https://agent-plugins.org/plugin-authors/skills), [MCP servers](https://agent-plugins.org/plugin-authors/mcp-servers), and [client extensions](https://agent-plugins.org/plugin-authors/client-extensions).

## Add client-specific capabilities safely

Hooks and similar capabilities are not portable v1 components. A client can add them through a reverse-domain extension namespace it owns and documents:

```text
your-plugin/
├── plugin.json
├── skills/
├── mcp.json                            # Optional portable MCP configuration
└── com.vendor.client/                  # Optional client extension
    └── hooks/
        └── hooks.json
```

If a client uses both manifest data and files, `plugin.json` can contain an extension object:

```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
  "name": "your-plugin",
  "extensions": {
    "com.vendor.client": {
      "settingDefinedByThatClient": true
    }
  }
}
```

`com.vendor.client` is illustrative. Use only a namespace and fields documented by the client that owns them. Other clients ignore an extension they do not implement, leaving the portable skills and MCP configuration usable.

## Migrate without breaking the existing plugin

Use an additive migration:

1. Add and validate the root `plugin.json` without deleting working platform files.
2. Move or copy reusable skills into `skills/<skill-name>/SKILL.md`.
3. Convert portable MCP servers to root `mcp.json` with explicit transport types.
4. Keep hooks, agents, commands, LSP, UI, and marketplace metadata in a client extension or a separate compatibility package required by that platform.
5. Test the portable core and every supported client package before removing legacy files.

The included [`migrate-agent-plugin` skill](skills/migrate-agent-plugin/SKILL.md) contains the full migration workflow, artifact mapping, extension strategy, and validation checklist.

## Copy this example

1. Copy this directory and rename it to match your plugin.
2. Update `name`, `version`, `description`, `author`, and other allowed metadata in `plugin.json`.
3. Replace the example skill or add more immediate children under `skills/`.
4. Add `mcp.json` only if the plugin provides MCP servers.
5. Add client extensions only for namespaces implemented by your target clients.
6. Validate paths, schemas, skills, and each supported client integration.

The directory name and manifest name do not have to match under the portable specification, but keeping them identical is strongly recommended for predictable packaging and discovery.
