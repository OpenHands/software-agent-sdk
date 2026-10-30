# Design: MCP Resources and Prompts in the SDK (#6191)

## Problem

`openhands.sdk.mcp` is tools-only. MCP servers that also advertise
resources (static or templated content) and prompts (server-rendered
message templates) expose none of that to OpenHands users, so the only
way to surface such content is duplicating it in AGENTS.md or wrapping
it in nonstandard tools.

## Scope (per the issue triage)

SDK surface only. Downstream agent-server REST and TypeScript client
surfaces follow the repository's SDK-first flow in later PRs.
Non-goals: server-level instructions (#5458), Canvas browsing UI,
wrapping resources/prompts as agent-facing tools, implicit loading into
the system prompt.

## Shape

New module `openhands/sdk/mcp/resources_prompts.py` with four functions
over the existing native `dict[str, MCPServer]` config:

| Function | Returns |
| --- | --- |
| `list_mcp_resources(config, server=None)` | `{server: MCPResourceListing}` — resources + templates (with parsed RFC 6570 parameters) |
| `read_mcp_resource(config, server, uri)` | `list[MCPResourceContent]` — text or base64 blob, URI + MIME type |
| `list_mcp_prompts(config, server=None)` | `list[MCPPromptInfo]` — declared arguments with required flags |
| `render_mcp_prompt(config, server, name, arguments)` | `MCPRenderedPrompt` — messages preserving role and content block type |

## Key decision: one connection per server, per call

The alternative was reusing the existing composite `MCPClient` from
`create_mcp_tools`. Rejected for two reasons measured against fastmcp
3.2.0:

- The composite mounts each server with a prefix and rewrites resource
  URIs to `protocol://{server}/path`, so the server's original URI (an
  acceptance criterion) is lost.
- Attribution would rely on parsing prefixes, which differ between
  single-server (no prefix) and multi-server configs.

Instead every call builds a single-server client through the same
`_prepare_mcp_config` boundary (OAuth and secret handling included),
runs one operation, and closes. Results therefore always carry the
true server key and original URIs, single- and multi-server behave
identically, and tools-only configs are untouched: `create_mcp_tools`
and its connection lifecycle are not modified.

## Errors

- A method-not-found answer (`McpError` with code -32601, walking the
  cause chain) maps to a new catchable `MCPCapabilityNotSupportedError`
  carrying `server` and `capability`. fastmcp-based servers answer
  `resources/list` with an empty list instead, which surfaces as an
  ordinary empty listing.
- Any other failure raises `MCPError` whose message names the server
  and the resource URI or prompt involved; timeouts raise
  `MCPTimeoutError` the same way `create_mcp_tools` does.

## Conversation integration path

Explicit selection only: user code reads/renders through these
functions, composes a `Message` with a provenance header
(`[MCP resource server=... uri=...]`), and sends it via
`conversation.send_message`. The example
(`examples/01_standalone_sdk/60_mcp_resources_prompts/`) demonstrates
the full flow against a local stdio server and needs no LLM.
