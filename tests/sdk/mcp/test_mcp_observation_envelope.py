import mcp.types

from openhands.sdk.mcp.definition import MCPToolObservation


def test_mcp_observation_preserves_embedded_resource_and_structured_content():
    embedded = mcp.types.EmbeddedResource(
        type="resource",
        resource=mcp.types.TextResourceContents(
            uri="file:///manifest/VB-101.json",
            mime_type="application/json",
            text='{"id": "VB-101", "status": "verified"}',
        ),
    )
    result = mcp.types.CallToolResult(
        content=[embedded],
        structured_content={"id": "VB-101", "status": "verified"},
        is_error=False,
    )

    obs = MCPToolObservation.from_call_tool_result("inspect_bundle", result)
    assert obs.structured_content == {"id": "VB-101", "status": "verified"}
    assert len(obs.mcp_content_blocks) == 1
    assert obs.mcp_content_blocks[0]["type"] == "resource"
    assert "VB-101" in obs.text
    assert "file:///manifest/VB-101.json" in obs.text


def test_mcp_observation_surfaces_structured_content_when_content_empty():
    result = mcp.types.CallToolResult(
        content=[],
        structured_content={"verdict": "accepted", "receipt": "RCPT-42"},
        is_error=False,
    )
    obs = MCPToolObservation.from_call_tool_result("submit_verdict", result)
    assert obs.structured_content == {"verdict": "accepted", "receipt": "RCPT-42"}
    assert "RCPT-42" in obs.text
