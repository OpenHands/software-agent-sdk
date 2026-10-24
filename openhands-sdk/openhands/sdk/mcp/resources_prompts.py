"""MCP Resources and Prompts access for OpenHands-native MCP configs.

Listing, reading, and rendering happens on demand and per server: each
call connects to the one server it addresses, so every result carries
that server's key plus the server's original URIs and prompt names (no
composite-client prefixing). Servers that do not advertise the requested
capability raise :class:`MCPCapabilityNotSupportedError`.

The returned content is meant for explicit selection by user code: build
a conversation message from it and send it to a conversation. Nothing is
loaded into a conversation implicitly.
"""

import re
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any, Literal

import mcp.types
from mcp.shared.exceptions import McpError
from pydantic import BaseModel, Field

from openhands.sdk.logger import get_logger
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.config import MCPServer, enabled_mcp_servers
from openhands.sdk.mcp.exceptions import (
    MCPCapabilityNotSupportedError,
    MCPError,
    MCPTimeoutError,
)


logger = get_logger(__name__)

_TEMPLATE_PARAMETER_RE = re.compile(r"\{([^}/#&?]+)\}")


def _uri_parameters(uri_template: str) -> list[str]:
    return _TEMPLATE_PARAMETER_RE.findall(uri_template)


class MCPResourceInfo(BaseModel):
    """One resource advertised by an MCP server."""

    server: str = Field(description="Server key the resource belongs to.")
    uri: str = Field(description="Resource URI as advertised by the server.")
    name: str | None = None
    description: str | None = None
    mime_type: str | None = None
    size: int | None = None


class MCPResourceTemplateInfo(BaseModel):
    """One resource template advertised by an MCP server."""

    server: str = Field(description="Server key the template belongs to.")
    uri_template: str = Field(description="RFC 6570 URI template string.")
    name: str | None = None
    description: str | None = None
    mime_type: str | None = None
    parameters: list[str] = Field(
        default_factory=list,
        description="Variable names parsed from the URI template.",
    )


class MCPResourceListing(BaseModel):
    """Resources and resource templates advertised by one server."""

    resources: list[MCPResourceInfo] = Field(default_factory=list)
    templates: list[MCPResourceTemplateInfo] = Field(default_factory=list)


class MCPResourceContent(BaseModel):
    """Content read from one resource or resolved template instance."""

    server: str = Field(description="Server key the content was read from.")
    uri: str = Field(description="URI the content was read from.")
    mime_type: str | None = None
    text: str | None = None
    blob_b64: str | None = Field(
        default=None,
        description="Binary content, base64-encoded as transported by MCP.",
    )


class MCPPromptArgumentInfo(BaseModel):
    """One declared argument of an MCP prompt."""

    name: str
    description: str | None = None
    required: bool | None = None


class MCPPromptInfo(BaseModel):
    """One prompt advertised by an MCP server."""

    server: str = Field(description="Server key the prompt belongs to.")
    name: str
    description: str | None = None
    arguments: list[MCPPromptArgumentInfo] = Field(default_factory=list)


class MCPPromptContent(BaseModel):
    """One content block of a rendered prompt message, preserving its type."""

    type: Literal["text", "image", "audio", "resource"]
    text: str | None = None
    mime_type: str | None = None
    data_b64: str | None = Field(
        default=None,
        description="Base64 payload for image and audio blocks.",
    )
    resource: MCPResourceContent | None = Field(
        default=None,
        description="Embedded resource for resource blocks.",
    )


class MCPPromptMessage(BaseModel):
    """One message of a rendered prompt, preserving its role and content."""

    role: str
    content: MCPPromptContent


class MCPRenderedPrompt(BaseModel):
    """A prompt rendered server-side with caller-supplied arguments."""

    server: str = Field(description="Server key the prompt was rendered by.")
    name: str
    description: str | None = None
    messages: list[MCPPromptMessage] = Field(default_factory=list)


def _enabled_servers(
    mcp_config: dict[str, MCPServer],
) -> dict[str, MCPServer]:
    requested = mcp_config
    enabled = enabled_mcp_servers(mcp_config)
    if requested and not enabled:
        raise ValueError(
            "All configured MCP servers are disabled: "
            f"{', '.join(sorted(requested))}. Enable at least one, or skip "
            "the call entirely."
        )
    return enabled


def _targets(
    mcp_config: dict[str, MCPServer], server: str | None
) -> list[tuple[str, MCPServer]]:
    if server is not None:
        enabled = _enabled_servers(mcp_config)
        if server not in enabled:
            raise ValueError(
                f"MCP server '{server}' is not configured. Configured "
                f"servers: {', '.join(sorted(enabled))}."
            )
        return [(server, enabled[server])]
    return sorted(_enabled_servers(mcp_config).items())


def _is_method_not_found(exc: BaseException) -> bool:
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if (
            isinstance(current, McpError)
            and current.error.code == mcp.types.METHOD_NOT_FOUND
        ):
            return True
        if current.__cause__ is not None:
            stack.append(current.__cause__)
        if current.__context__ is not None:
            stack.append(current.__context__)
    return False


def _run_on_server(
    server: str,
    server_config: MCPServer,
    operation: Callable[[MCPClient], Awaitable[Any]],
    *,
    capability: str,
    action: str,
    timeout: float,
) -> Any:
    """Run one async operation against a single-server MCP client."""
    # Imported here to keep this module importable from the package
    # ``__init__``: ``utils`` pulls in the tool layer, which pulls in
    # ``openhands.sdk.llm`` and back into this partially initialized package.
    from openhands.sdk.mcp.utils import _prepare_mcp_config

    config = _prepare_mcp_config({server: server_config})
    client = MCPClient(config)
    try:

        async def _call() -> Any:
            await client.connect()
            return await operation(client)

        return client.call_async_from_sync(_call, timeout=timeout)
    except MCPCapabilityNotSupportedError:
        raise
    except TimeoutError as e:
        raise MCPTimeoutError(
            f"MCP {action} on server '{server}' timed out after {timeout} seconds.",
            timeout=timeout,
            config=config.model_dump(),
        ) from e
    except BaseException as e:
        if _is_method_not_found(e):
            raise MCPCapabilityNotSupportedError(server, capability) from e
        raise MCPError(f"MCP {action} on server '{server}' failed: {e}") from e
    finally:
        client.sync_close()


def _to_resource_content(
    server: str, content: mcp.types.ResourceContents
) -> MCPResourceContent:
    common = {"server": server, "uri": str(content.uri), "mime_type": content.mimeType}
    if isinstance(content, mcp.types.TextResourceContents):
        return MCPResourceContent(**common, text=content.text)
    if isinstance(content, mcp.types.BlobResourceContents):
        return MCPResourceContent(**common, blob_b64=content.blob)
    raise MCPError(
        f"Server '{server}' returned an unsupported resource content type: "
        f"{type(content).__name__}"
    )


def _to_prompt_content(
    server: str,
    content: mcp.types.ContentBlock,
) -> MCPPromptContent:
    if isinstance(content, mcp.types.TextContent):
        return MCPPromptContent(type="text", text=content.text)
    if isinstance(content, mcp.types.ImageContent):
        return MCPPromptContent(
            type="image", data_b64=content.data, mime_type=content.mimeType
        )
    if isinstance(content, mcp.types.AudioContent):
        return MCPPromptContent(
            type="audio", data_b64=content.data, mime_type=content.mimeType
        )
    if isinstance(content, mcp.types.EmbeddedResource):
        return MCPPromptContent(
            type="resource",
            resource=_to_resource_content(server, content.resource),
        )
    raise MCPError(
        f"Server '{server}' returned an unsupported prompt content type: "
        f"{type(content).__name__}"
    )


async def _list_resources_op(client: MCPClient, server: str) -> MCPResourceListing:
    raw_resources = await client.list_resources()
    raw_templates = await client.list_resource_templates()
    return MCPResourceListing(
        resources=[
            MCPResourceInfo(
                server=server,
                uri=str(r.uri),
                name=r.name,
                description=r.description,
                mime_type=r.mimeType,
                size=r.size,
            )
            for r in raw_resources
        ],
        templates=[
            MCPResourceTemplateInfo(
                server=server,
                uri_template=str(t.uriTemplate),
                name=t.name,
                description=t.description,
                mime_type=t.mimeType,
                parameters=_uri_parameters(str(t.uriTemplate)),
            )
            for t in raw_templates
        ],
    )


async def _read_resource_op(
    client: MCPClient, server: str, uri: str
) -> list[MCPResourceContent]:
    contents = await client.read_resource(uri)
    return [_to_resource_content(server, c) for c in contents]


async def _list_prompts_op(client: MCPClient, server: str) -> list[MCPPromptInfo]:
    raw_prompts = await client.list_prompts()
    return [
        MCPPromptInfo(
            server=server,
            name=p.name,
            description=p.description,
            arguments=[
                MCPPromptArgumentInfo(
                    name=a.name,
                    description=a.description,
                    required=a.required,
                )
                for a in p.arguments or []
            ],
        )
        for p in raw_prompts
    ]


async def _render_prompt_op(
    client: MCPClient, server: str, name: str, arguments: dict[str, Any] | None
) -> MCPRenderedPrompt:
    result = await client.get_prompt(name, arguments)
    return MCPRenderedPrompt(
        server=server,
        name=name,
        description=result.description,
        messages=[
            MCPPromptMessage(
                role=m.role,
                content=_to_prompt_content(server, m.content),
            )
            for m in result.messages
        ],
    )


def list_mcp_resources(
    mcp_config: dict[str, MCPServer],
    server: str | None = None,
    *,
    timeout: float = 30.0,
) -> dict[str, MCPResourceListing]:
    """List resources and resource templates of enabled MCP servers.

    Args:
        mcp_config: OpenHands-native MCP server settings.
        server: Restrict the listing to this server key. ``None`` lists
            every enabled server.
        timeout: Per-server timeout in seconds.

    Returns:
        ``{server key: MCPResourceListing}``, one entry per addressed
        server. A server that advertises no resources yields an empty
        listing; a server that does not implement the capability raises
        :class:`MCPCapabilityNotSupportedError`.
    """
    results: dict[str, MCPResourceListing] = {}
    for server_key, server_config in _targets(mcp_config, server):
        results[server_key] = _run_on_server(
            server_key,
            server_config,
            partial(_list_resources_op, server=server_key),
            capability="resources",
            action="resource listing",
            timeout=timeout,
        )
    return results


def read_mcp_resource(
    mcp_config: dict[str, MCPServer],
    server: str,
    uri: str,
    *,
    timeout: float = 30.0,
) -> list[MCPResourceContent]:
    """Read one resource or resolved template instance from a server.

    Args:
        mcp_config: OpenHands-native MCP server settings.
        server: Server key to read from.
        uri: Resource URI, or a template URI with parameters filled in.
        timeout: Timeout in seconds.

    Returns:
        The content blocks returned by the server, each carrying the
        server key, URI, and MIME type.
    """
    return _run_on_server(
        *_targets(mcp_config, server)[0],
        partial(_read_resource_op, server=server, uri=uri),
        capability="resources",
        action=f"read of resource '{uri}'",
        timeout=timeout,
    )


def list_mcp_prompts(
    mcp_config: dict[str, MCPServer],
    server: str | None = None,
    *,
    timeout: float = 30.0,
) -> list[MCPPromptInfo]:
    """List prompts of enabled MCP servers.

    Args:
        mcp_config: OpenHands-native MCP server settings.
        server: Restrict the listing to this server key. ``None`` lists
            every enabled server.
        timeout: Per-server timeout in seconds.

    Returns:
        One :class:`MCPPromptInfo` per advertised prompt, each carrying
        its server key. Servers that do not implement the capability
        raise :class:`MCPCapabilityNotSupportedError`.
    """
    prompts: list[MCPPromptInfo] = []
    for server_key, server_config in _targets(mcp_config, server):
        prompts.extend(
            _run_on_server(
                server_key,
                server_config,
                partial(_list_prompts_op, server=server_key),
                capability="prompts",
                action="prompt listing",
                timeout=timeout,
            )
        )
    return prompts


def render_mcp_prompt(
    mcp_config: dict[str, MCPServer],
    server: str,
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    timeout: float = 30.0,
) -> MCPRenderedPrompt:
    """Render one prompt server-side with caller-supplied arguments.

    Args:
        mcp_config: OpenHands-native MCP server settings.
        server: Server key that advertises the prompt.
        name: Prompt name as listed by :func:`list_mcp_prompts`.
        arguments: Prompt arguments.
        timeout: Timeout in seconds.

    Returns:
        The rendered messages, preserving each message's role and
        content type and carrying the server key.
    """
    return _run_on_server(
        *_targets(mcp_config, server)[0],
        partial(_render_prompt_op, server=server, name=name, arguments=arguments),
        capability="prompts",
        action=f"render of prompt '{name}'",
        timeout=timeout,
    )
