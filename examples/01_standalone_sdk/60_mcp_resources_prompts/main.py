"""Use MCP Resources and Prompts from an OpenHands-native MCP config.

MCP servers can advertise more than tools: resources (static or templated
content, read on demand) and prompts (reusable message templates rendered
server-side). This example lists and reads both against a local stdio
server, renders a prompt with arguments, and shows the documented
integration path: composing the selected content into a conversation
message with its provenance.

No LLM is needed to run this example.

Usage:
    uv run python examples/01_standalone_sdk/60_mcp_resources_prompts/main.py
"""

import sys
from pathlib import Path

from openhands.sdk import Message, TextContent
from openhands.sdk.mcp import (
    MCPServer,
    list_mcp_prompts,
    list_mcp_resources,
    read_mcp_resource,
    render_mcp_prompt,
)


mcp_config: dict[str, MCPServer] = {
    "kanban": MCPServer(
        command=sys.executable,
        args=[str(Path(__file__).parent / "server.py")],
    )
}

# 1. List resources and templates. Every item carries its server key.
listings = list_mcp_resources(mcp_config)
listing = listings["kanban"]
for resource in listing.resources:
    print(f"resource   {resource.uri}  ({resource.mime_type})  {resource.name}")
for template in listing.templates:
    print(f"template   {template.uri_template}  parameters={template.parameters}")

# 2. Read a static resource on demand.
usage = read_mcp_resource(mcp_config, "kanban", "docs://usage")[0]
print(f"\nread docs://usage -> {usage.text}")

# 3. Fill in a template's parameters and read the resolved instance.
resolved = read_mcp_resource(mcp_config, "kanban", "docs://projects/acme-website")[0]
print(f"read docs://projects/acme-website -> {resolved.text}")

# 4. List prompts with their declared arguments.
prompts = list_mcp_prompts(mcp_config)
for prompt in prompts:
    arguments = ", ".join(
        f"{a.name}{' required' if a.required else ' optional'}"
        for a in prompt.arguments
    )
    print(f"\nprompt     {prompt.name}({arguments})")

# 5. Render a prompt server-side; messages keep their roles and content
#    types.
rendered = render_mcp_prompt(
    mcp_config,
    "kanban",
    "plan_task",
    {"task": "migrate the contact form", "project": "acme-website"},
)
for message in rendered.messages:
    print(f"rendered   [{message.role}] {message.content.text}")

# 6. Documented integration path: compose the explicitly selected content
#    into one user message (with provenance) and send it to a
#    conversation:
#
#     conversation.send_message(message)
prompt_lines: list[str] = []
for message in rendered.messages:
    text = message.content.text
    assert text is not None
    prompt_lines.append(text)

message_text = TextContent(
    text=(
        f"[MCP resource server=kanban uri={resolved.uri}]\n"
        f"{resolved.text}\n\n"
        f"[MCP prompt server={rendered.server} name={rendered.name}]\n"
        + "\n".join(prompt_lines)
    )
)
message = Message(role="user", content=[message_text])
print(f"\nconversation message:\n{message_text.text}")

print("EXAMPLE_COST: 0")
