from openhands.tools.file_editor import FileEditorTool


def test_to_mcp_tool_detailed_type_validation_editor(mock_conversation_state):
    """Test detailed type validation for MCP tool schema generation."""

    file_editor_tool = FileEditorTool.create(conv_state=mock_conversation_state)
    assert len(file_editor_tool) == 1
    file_editor_tool = file_editor_tool[0]
    assert isinstance(file_editor_tool, FileEditorTool)

    # Test file_editor tool schema
    str_editor_mcp = file_editor_tool.to_mcp_tool()
    str_editor_schema = str_editor_mcp["inputSchema"]
    str_editor_props = str_editor_schema["properties"]

    assert "command" in str_editor_props
    assert "path" in str_editor_props
    assert "file_text" in str_editor_props
    assert "old_str" in str_editor_props
    assert "new_str" in str_editor_props
    assert "insert_line" in str_editor_props
    assert "view_range" in str_editor_props
    # security_risk should NOT be in the schema after #341
    assert "security_risk" not in str_editor_props

    view_range_schema = str_editor_props["view_range"]
    assert "anyOf" not in view_range_schema
    assert view_range_schema["type"] == "array"
    assert view_range_schema["items"]["type"] == "integer"

    assert "description" in view_range_schema
    assert "Optional parameter of `view` command" in view_range_schema["description"]

    command_schema = str_editor_props["command"]
    assert "enum" in command_schema
    expected_commands = ["view", "create", "str_replace", "insert", "undo_edit"]
    assert set(command_schema["enum"]) == set(expected_commands)

    path_schema = str_editor_props["path"]
    assert path_schema["type"] == "string"
    assert "path" in str_editor_schema["required"]
