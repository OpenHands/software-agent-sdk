from openhands.sdk.tool.builtins import BUILT_IN_TOOLS


def test_all_tools_property():
    # BUILT_IN_TOOLS contains tool classes, so we need to instantiate them
    for tool_class in BUILT_IN_TOOLS:
        # Create tool instances using .create() method
        tool_instances = tool_class.create()
        assert len(tool_instances) > 0, (
            f"{tool_class.__name__}.create() should return at least one tool"
        )

        # Check properties for all instances (usually just one)
        for tool in tool_instances:
            assert tool.description is not None
            assert tool.executor is not None
            assert tool.annotations is not None
            # Annotations should have specific hints
            # Builtin tools should have all these properties
            assert tool.annotations.readOnlyHint
            assert not tool.annotations.destructiveHint
            assert tool.annotations.idempotentHint
            assert not tool.annotations.openWorldHint
