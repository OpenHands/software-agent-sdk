from openhands.sdk.tool.builtins import BUILT_IN_TOOLS


def test_all_tools_property():
    for tool in BUILT_IN_TOOLS:
        assert tool.description is not None
        assert tool.executor is not None
        assert tool.annotations is not None
        # Annotations should have specific hints
        # Builtin tools should have all these properties
        assert tool.annotations.readOnlyHint
        assert not tool.annotations.destructiveHint
        assert tool.annotations.idempotentHint
        assert not tool.annotations.openWorldHint
