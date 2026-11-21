import random
import shutil
import subprocess

import pytest

from openhands.tools.terminal.utils.command import (
    escape_bash_special_chars,
    find_unterminated_heredoc,
    needs_heredoc_completion_boundary,
    split_bash_commands,
)


def test_split_commands_util():
    cmds = [
        "ls -l",
        'echo -e "hello\nworld"',
        """
echo -e "hello it\\'s me"
""".strip(),
        """
echo \\
    -e 'hello' \\
    -v
""".strip(),
        """
echo -e 'hello\\nworld\\nare\\nyou\\nthere?'
""".strip(),
        """
echo -e 'hello
world
are
you\\n
there?'
""".strip(),
        """
echo -e 'hello
world "
'
""".strip(),
        """
mkdir -p _modules && \
for month in {01..04}; do
    for day in {01..05}; do
        touch "_modules/2024-${month}-${day}-sample.md"
    done
done
""".strip(),
        """
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: busybox-sleep
spec:
  containers:
  - name: busybox
    image: busybox:1.28
    args:
    - sleep
    - "1000000"
EOF
""".strip(),
    ]
    joined_cmds = "\n".join(cmds)
    split_cmds = split_bash_commands(joined_cmds)
    for i in range(len(cmds)):
        assert split_cmds[i].strip() == cmds[i].strip(), (
            f"At index {i}: {split_cmds[i]} != {cmds[i]}."
        )


@pytest.mark.parametrize(
    "input_command, expected_output",
    [
        ("ls -l", ["ls -l"]),
        ("echo 'Hello, world!'", ["echo 'Hello, world!'"]),
        ("cd /tmp && touch test.txt", ["cd /tmp && touch test.txt"]),
        ("test -f missing || echo absent", ["test -f missing || echo absent"]),
        ("sleep 0 & wait", ["sleep 0 & wait"]),
        ("echo -e 'line1\\nline2\\nline3'", ["echo -e 'line1\\nline2\\nline3'"]),
        (
            "grep 'pattern' file.txt | sort | uniq",
            ["grep 'pattern' file.txt | sort | uniq"],
        ),
        ("for i in {1..5}; do echo $i; done", ["for i in {1..5}; do echo $i; done"]),
        (
            "echo 'Single quotes don\\'t escape'",
            ["echo 'Single quotes don\\'t escape'"],
        ),
        (
            'echo "Double quotes \\"do\\" escape"',
            ['echo "Double quotes \\"do\\" escape"'],
        ),
    ],
)
def test_single_commands(input_command, expected_output):
    assert split_bash_commands(input_command) == expected_output


@pytest.mark.parametrize(
    "input_commands",
    [
        "cat <<EOF\nmultiline\ntext\nEOF\necho done",
        "cat <<'EOF'\n$VALUE\nEOF\nprintf done",
        "cat <<-EOF\n\tindented\n\tEOF\nprintf done",
        "cat <<EOF | sed 's/text/replaced/'\ntext\nEOF\nprintf done",
        "value=$(cat <<EOF\ntext\nEOF\n) && printf '%s' \"$value\"",
        (
            "printf start; python - <<'PY'\n"
            "print('first')\n"
            "PY\n"
            "printf middle; python - <<'PY'\n"
            "print('second')\n"
            "PY\n"
            "printf done"
        ),
    ],
)
def test_heredoc_script_is_not_split(input_commands):
    assert split_bash_commands(input_commands) == [input_commands]


@pytest.mark.parametrize(
    "command",
    [
        "cat <<'EOF'\nbody\nEOF\nsleep 1; echo done",
        "cat <<A <<B\na\nA\nb\nB\necho done",
        'cat <<E"OF"\nbody\nEOF\necho done',
        "cat <<EOF &\nbody\nEOF\nwait",
    ],
)
def test_heredoc_scripts_need_completion_boundary(command):
    assert needs_heredoc_completion_boundary(command)


def test_single_heredoc_does_not_need_completion_boundary():
    command = "cat <<'EOF'\nbody\nEOF\n"

    assert not needs_heredoc_completion_boundary(command)


def test_incomplete_heredoc_does_not_need_completion_boundary():
    assert not needs_heredoc_completion_boundary("cat <<EOF\nbody")


def test_completion_boundary_is_platform_independent():
    """The guard must not depend on a `bash` executable being on PATH.

    On Windows tree-sitter reports parse errors for these forms, so the
    decision is made from the tree alone and must match on every platform.
    """
    assert needs_heredoc_completion_boundary(
        "cat <<'EOF'\nbody\nEOF\nsleep 1; echo done"
    )
    assert needs_heredoc_completion_boundary("cat <<A <<B\na\nA\nb\nB\necho done")
    assert needs_heredoc_completion_boundary('cat <<E"OF"\nbody\nEOF\necho done')
    assert needs_heredoc_completion_boundary("cat <<EOF &\nbody\nEOF\nwait")
    assert needs_heredoc_completion_boundary(
        "cat <<-EOF\n\tindented\n\tEOF\nprintf done"
    )
    assert not needs_heredoc_completion_boundary("cat <<EOF\nbody")
    assert not needs_heredoc_completion_boundary("cat <<EOF\nbody\nEOF\n")
    assert not needs_heredoc_completion_boundary("cat <<'EOF'\nbody\nEOF\n")


@pytest.mark.parametrize(
    "input_commands, expected_output",
    [
        ("echo first\necho second", ["echo first", "echo second"]),
        ("cat <<< text\necho second", ["cat <<< text", "echo second"]),
        (
            "cat <<EOF\ntext\nEOF\necho second\necho third",
            ["cat <<EOF\ntext\nEOF\necho second", "echo third"],
        ),
        (
            "cat <<EOF\nunterminated\necho second",
            ["cat <<EOF\nunterminated\necho second"],
        ),
        # A heredoc *nested* in a compound statement or substitution does not
        # end the statement: tree-sitter leaves the closing token (`fi`, `)`)
        # unnamed, so the following newline must still split.
        (
            "if true; then\n  cat <<EOF\nx\nEOF\nfi\necho after",
            ["if true; then\n  cat <<EOF\nx\nEOF\nfi", "echo after"],
        ),
        (
            "x=$(cat <<EOF\nhi\nEOF\n)\nrm -rf /tmp/x",
            ["x=$(cat <<EOF\nhi\nEOF\n)", "rm -rf /tmp/x"],
        ),
        (
            "( cat <<EOF\nx\nEOF\n)\necho after",
            ["( cat <<EOF\nx\nEOF\n)", "echo after"],
        ),
        (
            "for i in 1 2; do cat <<EOF\nx\nEOF\ndone\necho after",
            ["for i in 1 2; do cat <<EOF\nx\nEOF\ndone", "echo after"],
        ),
    ],
)
def test_non_heredoc_newlines_still_split(input_commands, expected_output):
    assert split_bash_commands(input_commands) == expected_output


def test_backslash_continuation():
    input_commands = """
echo "This is a long \
command that spans \
multiple lines"
echo "Next command"
"""
    expected_output = [
        'echo "This is a long command that spans multiple lines"',
        'echo "Next command"',
    ]
    assert split_bash_commands(input_commands) == expected_output


def test_comments():
    input_commands = """
echo "Hello" # This is a comment
# This is another comment
ls -l
"""
    expected_output = [
        'echo "Hello" # This is a comment\n# This is another comment',
        "ls -l",
    ]
    assert split_bash_commands(input_commands) == expected_output


def test_complex_quoting():
    input_commands = """
echo "This is a \\"quoted\\" string"
echo 'This is a '\''single-quoted'\'' string'
echo "Mixed 'quotes' in \\"double quotes\\""
"""
    expected_output = [
        'echo "This is a \\"quoted\\" string"',
        "echo 'This is a '''single-quoted''' string'",
        'echo "Mixed \'quotes\' in \\"double quotes\\""',
    ]
    assert split_bash_commands(input_commands) == expected_output


def test_invalid_syntax():
    invalid_inputs = [
        'echo "Unclosed quote',
        "echo 'Unclosed quote",
        "cat <<EOF\nUnclosed heredoc",
    ]
    for input_command in invalid_inputs:
        # it will fall back to return the original input
        assert split_bash_commands(input_command) == [input_command]


def test_unclosed_backtick():
    # This test reproduces issue #7391
    # The issue occurs when parsing a command with an unclosed backtick
    # which causes a TypeError: ParsingError.__init__() missing 2 required
    # positional arguments: 's' and 'position'
    command = "echo `unclosed backtick"

    # Should not raise TypeError
    try:
        result = split_bash_commands(command)
        # If we get here, the error was handled properly
        assert result == [command]
    except TypeError as e:
        # This is the error we're trying to fix
        raise e

    # Also test with the original command from the issue (with placeholder org/repo)
    curl_command = (
        'curl -X POST "https://api.github.com/repos/example-org/example-repo/pulls" \\ '
        '-H "Authorization: Bearer $GITHUB_TOKEN" \\ '
        '-H "Accept: application/vnd.github.v3+json" \\ '
        '-d \'{ "title": "XXX", "head": "XXX", "base": "main", "draft": false }\' '
        "`echo unclosed"
    )

    try:
        result = split_bash_commands(curl_command)
        assert result == [curl_command]
    except TypeError as e:
        raise e


def test_over_escaped_command():
    # This test reproduces issue #8369 Example 1
    # The issue occurs when parsing a command with over-escaped quotes
    over_escaped_command = (
        r"# 0. Setup directory\\nrm -rf /workspace/repro_sphinx_bug && "
        r"mkdir -p /workspace/repro_sphinx_bug && cd /workspace/repro_sphinx_bug\\n\\n"
        r"# 1. Run sphinx-quickstart\\nsphinx-quickstart --no-sep --project myproject "
        r"--author me -v 0.1.0 --release 0.1.0 --language en . -q\\n\\n"
        r"# 2. Create index.rst\\necho -e \'Welcome\\\\\\\\n=======\\\\\\\\n\\\\\\\\n"
        r".. toctree::\\\\n   :maxdepth: 2\\\\\\\\n\\\\\\\\n   "
        r"mypackage_file\\\\\\\\n\' > index.rst"
    )

    # Should not raise any exception
    try:
        result = split_bash_commands(over_escaped_command)
        # If parsing fails, it should return the original command
        assert result == [over_escaped_command]
    except Exception as e:
        # This is the error we're trying to fix
        pytest.fail(f"split_bash_commands raised {type(e).__name__} unexpectedly: {e}")


@pytest.fixture
def sample_commands():
    return [
        "ls -l",
        'echo "Hello, world!"',
        "cd /tmp && touch test.txt",
        'echo -e "line1\\nline2\\nline3"',
        'grep "pattern" file.txt | sort | uniq',
        "for i in {1..5}; do echo $i; done",
        "cat <<EOF\nmultiline\ntext\nEOF",
        'echo "Escaped \\"quotes\\""',
        "echo 'Single quotes don\\'t escape'",
        'echo "Command with a trailing backslash \\\n  and continuation"',
    ]


def test_split_single_commands(sample_commands):
    for cmd in sample_commands:
        result = split_bash_commands(cmd)
        assert len(result) == 1, f"Expected single command, got: {result}"


def test_split_commands_with_heredoc():
    input_commands = """
cat <<EOF
multiline
text
EOF
echo "Done"
"""
    assert split_bash_commands(input_commands) == [input_commands.strip()]


@pytest.mark.parametrize(
    ("input_commands", "expected_output"),
    [
        pytest.param(
            '\necho "This is a long command that spans multiple lines"\n'
            'echo "Next command"\n',
            [
                'echo "This is a long command that spans multiple lines"',
                'echo "Next command"',
            ],
            id="backslash_continuation",
        ),
        pytest.param(
            '\nls -l\n\necho "Hello"\n\ncd /tmp\n',
            ["ls -l", 'echo "Hello"', "cd /tmp"],
            id="empty_lines",
        ),
        pytest.param(
            '\necho "Hello" # This is a comment\n# This is another comment\nls -l\n',
            ['echo "Hello" # This is a comment\n# This is another comment', "ls -l"],
            id="comments",
        ),
        pytest.param(
            '\necho "This is a \\"quoted\\" string"\n'
            'echo "Mixed \'quotes\' in \\"double quotes\\""\n',
            [
                'echo "This is a \\"quoted\\" string"',
                'echo "Mixed \'quotes\' in \\"double quotes\\""',
            ],
            id="complex_quoting",
        ),
    ],
)
def test_split_commands(input_commands, expected_output):
    result = split_bash_commands(input_commands)
    assert result == expected_output, f"Expected {expected_output}, got {result}"


def test_split_commands_with_invalid_input():
    invalid_inputs = [
        'echo "Unclosed quote',
        "echo 'Unclosed quote",
        "cat <<EOF\nUnclosed heredoc",
    ]
    for input_command in invalid_inputs:
        # it will fall back to return the original input
        assert split_bash_commands(input_command) == [input_command]


def test_escape_bash_special_chars():
    test_cases = [
        # Basic cases - use raw strings (r'') to avoid Python escape sequence warnings
        ("echo test \\; ls", "echo test \\\\; ls"),
        ("grep pattern \\| sort", "grep pattern \\\\| sort"),
        ("cmd1 \\&\\& cmd2", "cmd1 \\\\&\\\\& cmd2"),
        ("cat file \\> output.txt", "cat file \\\\> output.txt"),
        ("cat \\< input.txt", "cat \\\\< input.txt"),
        # Quoted strings should remain unchanged
        ('echo "test \\; unchanged"', 'echo "test \\; unchanged"'),
        ("echo 'test \\| unchanged'", "echo 'test \\| unchanged'"),
        # Mixed quoted and unquoted
        (
            'echo "quoted \\;" \\; "more" \\| grep',
            'echo "quoted \\;" \\\\; "more" \\\\| grep',
        ),
        # Multiple escapes in sequence
        ("cmd1 \\;\\|\\& cmd2", "cmd1 \\\\;\\\\|\\\\& cmd2"),
        # Commands with other backslashes
        ("echo test\\ntest", "echo test\\ntest"),
        ('echo "test\\ntest"', 'echo "test\\ntest"'),
        # Edge cases
        ("", ""),  # Empty string
        ("\\\\", "\\\\"),  # Double backslash
        ('\\"', '\\"'),  # Escaped quote
    ]

    for input_cmd, expected in test_cases:
        result = escape_bash_special_chars(input_cmd)
        assert result == expected, (
            f'Failed on input "{input_cmd}"\nExpected: "{expected}"\nGot: "{result}"'
        )


def test_escape_bash_special_chars_with_invalid_syntax():
    invalid_inputs = [
        'echo "unclosed quote',
        "echo 'unclosed quote",
        "cat <<EOF\nunclosed heredoc",
    ]
    for input_cmd in invalid_inputs:
        # Should return original input when parsing fails
        result = escape_bash_special_chars(input_cmd)
        assert result == input_cmd, f"Failed to handle invalid input: {input_cmd}"


def test_escape_bash_special_chars_with_heredoc():
    input_cmd = r"""cat <<EOF
line1 \; not escaped
line2 \| not escaped
EOF"""
    # Heredoc content should not be escaped
    expected = input_cmd
    result = escape_bash_special_chars(input_cmd)
    assert result == expected, (
        f"Failed to handle heredoc correctly\nExpected: {expected}\nGot: {result}"
    )


def test_escape_bash_special_chars_with_parameter_expansion():
    test_cases = [
        # Parameter expansion should be preserved
        ("echo $HOME", "echo $HOME"),
        ("echo ${HOME}", "echo ${HOME}"),
        ("echo ${HOME:-default}", "echo ${HOME:-default}"),
        # Mixed with special chars
        ("echo $HOME \\; ls", "echo $HOME \\\\; ls"),
        ("echo ${PATH} \\| grep bin", "echo ${PATH} \\\\| grep bin"),
        # Quoted parameter expansion
        ('echo "$HOME"', 'echo "$HOME"'),
        ('echo "${HOME}"', 'echo "${HOME}"'),
        # Complex parameter expansions
        ("echo ${var:=default} \\; ls", "echo ${var:=default} \\\\; ls"),
        ("echo ${!prefix*} \\| sort", "echo ${!prefix*} \\\\| sort"),
    ]

    for input_cmd, expected in test_cases:
        result = escape_bash_special_chars(input_cmd)
        assert result == expected, (
            f'Failed on input "{input_cmd}"\nExpected: "{expected}"\nGot: "{result}"'
        )


def test_escape_bash_special_chars_with_command_substitution():
    test_cases = [
        # Basic command substitution
        ("echo $(pwd)", "echo $(pwd)"),
        ("echo `pwd`", "echo `pwd`"),
        # Mixed with special chars
        ("echo $(pwd) \\; ls", "echo $(pwd) \\\\; ls"),
        ("echo `pwd` \\| grep home", "echo `pwd` \\\\| grep home"),
        # Nested command substitution
        ("echo $(echo `pwd`)", "echo $(echo `pwd`)"),
        # Complex command substitution
        ('echo $(find . -name "*.txt" \\; ls)', 'echo $(find . -name "*.txt" \\; ls)'),
        # Mixed with quotes
        ('echo "$(pwd)"', 'echo "$(pwd)"'),
        ('echo "`pwd`"', 'echo "`pwd`"'),
    ]

    for input_cmd, expected in test_cases:
        result = escape_bash_special_chars(input_cmd)
        assert result == expected, (
            f'Failed on input "{input_cmd}"\nExpected: "{expected}"\nGot: "{result}"'
        )


def test_escape_bash_special_chars_mixed_nodes():
    test_cases = [
        # Mix of parameter expansion and command substitution
        ("echo $HOME/$(pwd)", "echo $HOME/$(pwd)"),
        # Mix with special chars
        ("echo $HOME/$(pwd) \\; ls", "echo $HOME/$(pwd) \\\\; ls"),
        # Complex mixed cases
        (
            'echo "${HOME}/$(basename `pwd`) \\; next"',
            'echo "${HOME}/$(basename `pwd`) \\; next"',
        ),
        (
            "VAR=${HOME} \\; echo $(pwd)",
            "VAR=${HOME} \\\\; echo $(pwd)",
        ),
        # Real-world examples
        (
            'find . -name "*.txt" -exec grep "${PATTERN:-default}" {} \\;',
            'find . -name "*.txt" -exec grep "${PATTERN:-default}" {} \\\\;',
        ),
        (
            'echo "Current path: ${PWD}/$(basename `pwd`)" \\| grep home',
            'echo "Current path: ${PWD}/$(basename `pwd`)" \\\\| grep home',
        ),
    ]

    for input_cmd, expected in test_cases:
        result = escape_bash_special_chars(input_cmd)
        assert result == expected, (
            f'Failed on input "{input_cmd}"\nExpected: "{expected}"\nGot: "{result}"'
        )


def test_escape_bash_special_chars_with_chained_commands():
    test_cases = [
        # Basic chained commands
        ("ls && pwd", "ls && pwd"),
        ('echo "hello" && ls', 'echo "hello" && ls'),
        # Chained commands with special chars
        ("ls \\; pwd && echo test", "ls \\\\; pwd && echo test"),
        ("echo test && grep pattern \\| sort", "echo test && grep pattern \\\\| sort"),
        # Complex chained cases
        ("echo ${HOME} && ls \\; pwd", "echo ${HOME} && ls \\\\; pwd"),
        (
            'echo "$(pwd)" && cat file \\> out.txt',
            'echo "$(pwd)" && cat file \\\\> out.txt',
        ),
        # Multiple chains
        ("cmd1 && cmd2 && cmd3", "cmd1 && cmd2 && cmd3"),
        (
            "cmd1 \\; ls && cmd2 \\| grep && cmd3",
            "cmd1 \\\\; ls && cmd2 \\\\| grep && cmd3",
        ),
    ]

    for input_cmd, expected in test_cases:
        result = escape_bash_special_chars(input_cmd)
        assert result == expected, (
            f'Failed on input "{input_cmd}"\nExpected: "{expected}"\nGot: "{result}"'
        )


@pytest.mark.parametrize(
    "command",
    [
        "cat <<EOF\nbody\nEOF",
        "cat <<EOF\nbody\nEOF\n",
        "cat <<EOF\nEOF",
        "cat <<'EOF'\nbody\nEOF",
        'cat <<"EOF"\nbody\nEOF',
        "cat <<\\EOF\nbody\nEOF",
        'cat <<E"OF"\nbody\nEOF',
        "cat << EOF\nbody\nEOF",
        "cat <<-EOF\n\tbody\n\t\tEOF",
        "cat <<EOF | grep b\nbody\nEOF",
        "cat <<EOF > out.txt && echo ok\nbody\nEOF",
        "cat <<EOF # it's a comment\nbody\nEOF",
        "cat <<EOF \\\n -n\nbody\nEOF",
        "cat <<EOF 'a\nb'\nbody\nEOF",
        "cat <<A <<B\na\nA\nb\nB",
        "cat <<EOF\nbody\nEOF\ncat <<END\nmore\nEND",
        "cat <<EOF\ncat <<INNER\nEOF",
        "cat <<EOF\nEOFX\n EOF\nEOF",
        "cat <<'EOF'\ntrailing \\\nEOF",
        # A lone backslash joins to the terminator line and leaves it intact.
        "cat <<EOF\n\\\nEOF",
        "cat <<$'EOF'\nbody\nEOF",
        "x=$(cat <<EOF\nbody\nEOF\n)",
        "if true; then\ncat <<EOF\nbody\nEOF\nfi",
        "python - <<'PY'\nprint('<<EOF')\nPY",
        "git commit -m \"$(cat <<'EOF'\nfix: thing\n\nbody << text\nEOF\n)\"",
        "cat > f.c <<'EOF'\nint x = 1 << 2;\nchar *s = \"<<EOF\";\nEOF\ngcc f.c",
        "ssh host 'bash -s' <<'REMOTE'\ncat <<LOCAL\nx\nLOCAL\nREMOTE",
        "cat <<EOF > a.txt; cat <<EOF2 > b.txt\none\nEOF\ntwo\nEOF2",
    ],
)
def test_closed_heredoc_is_not_reported(command):
    assert find_unterminated_heredoc(command) is None


@pytest.mark.parametrize(
    "command, delimiter",
    [
        ("cat <<EOF", "EOF"),
        ("cat <<EOF\n", "EOF"),
        ("cat <<EOF\nbody", "EOF"),
        ("cat <<'EOF'\nbody", "EOF"),
        ('cat <<E"OF"\nbody', "EOF"),
        ("cat > out.txt <<EOF && echo ok\nbody", "EOF"),
        # The terminator must be the whole line: these are the ways it goes
        # missing while the command still looks complete.
        ("cat <<EOF\n  body\n  EOF", "EOF"),
        ("cat <<EOF\nbody\nEOF ", "EOF"),
        ("cat <<EOF\nbody\nEOFX", "EOF"),
        ("cat <<EOF\nbody\nEOF;", "EOF"),
        ("cat <<EOF\nbody\neof", "EOF"),
        ("cat <<-EOF\n\tbody\n    EOF", "EOF"),
        ("cat <<EOF\ntrailing \\\nEOF", "EOF"),
        ("cat <<A <<B\na\nA\nb", "B"),
        ("cat <<EOF\nbody\nEOF\ncat <<END\nmore", "END"),
        ("x=$(cat <<EOF\nbody\n)", "EOF"),
        ("if true; then\ncat <<EOF\nbody\nfi", "EOF"),
        ("git commit -m \"$(cat <<'EOF'\nfix: thing\n   EOF\n)\"", "EOF"),
        ("ssh host 'bash -s' <<'REMOTE'\ncat <<LOCAL\nx\nLOCAL", "REMOTE"),
        ("cat <<EOF > a.txt; cat <<EOF2 > b.txt\none\nEOF\ntwo", "EOF2"),
    ],
)
def test_unterminated_heredoc_reports_its_delimiter(command, delimiter):
    assert find_unterminated_heredoc(command) == delimiter


@pytest.mark.parametrize(
    "command",
    [
        "echo hello",
        "cat <<< 'here string'",
        "echo $((1<<2))",
        "(( x = 1 << 3 ))",
        "for ((i=1<<2; i<9; i++)); do echo $i; done",
        "echo '<<EOF'",
        'echo "a <<EOF b"',
        "echo \\<\\<EOF",
        "# cat <<EOF",
        "echo a # <<EOF",
        'python -c "x = 1 << 3\nprint(x)"',
        "echo ${x:-<<EOF}\necho next",
        # An unclosed quote is a different error; it must not be reported as
        # a heredoc problem.
        "cat <<EOF 'unclosed\nbody",
    ],
)
def test_commands_without_a_heredoc_are_not_reported(command):
    assert find_unterminated_heredoc(command) is None


def _generate_heredoc_command(rng: random.Random) -> str:
    """Build a command around one or two heredocs, closed or not."""
    delimiters = ["EOF", "END", "PY", "E_O-F"]
    body_lines = [
        "hello",
        "",
        "  indented",
        "\ttabbed",
        "it's a quote",
        'say "hi"',
        "$(echo sub)",
        "cat <<INNER",
        "INNER",
        "a # b",
        "x << y",
        "trailing \\",
        "\\",
        "x\\\\",
        "fi",
        ")",
    ]
    same_line = [
        "",
        " | grep h",
        " && echo ok",
        " > f.txt",
        " # it's a comment",
        " ; echo after",
        " 'q'",
        " \\\n -n",
        " &",
    ]
    lookalikes = [
        "echo $((1<<2))",
        "cat <<< 'here string'",
        "echo '<<EOF'",
        'echo "a <<EOF b"',
        "# cat <<EOF",
        "(( x = 1 << 3 ))",
        "echo \\<\\<EOF",
    ]

    def heredoc() -> tuple[str, list[str]]:
        delimiter = rng.choice(delimiters)
        operator = rng.choice(["<<", "<<", "<<-"])
        word = rng.choice(
            [
                delimiter,
                f"'{delimiter}'",
                f'"{delimiter}"',
                f"\\{delimiter}",
                f'{delimiter[0]}"{delimiter[1:]}"',
                f"$'{delimiter}'",
                f" {delimiter}",
            ]
        )
        lines = [rng.choice(body_lines) for _ in range(rng.randint(0, 4))]
        if rng.random() < 0.6:
            tabs = "\t" * rng.randint(0, 2) if operator == "<<-" else ""
            lines.append(tabs + delimiter)
        elif rng.random() < 0.7:
            lines.append(
                rng.choice(
                    [
                        f" {delimiter}",
                        f"{delimiter} ",
                        f"{delimiter}X",
                        f"    {delimiter}",
                        f"{delimiter};",
                        f"{delimiter})",
                        f"'{delimiter}'",
                    ]
                )
            )
        return operator + word, lines

    if rng.random() < 0.1:
        return rng.choice(lookalikes)
    heredocs = [heredoc() for _ in range(1 if rng.random() < 0.8 else 2)]
    command = (
        rng.choice(["cat", "cat > out.txt", "python -", "grep h"])
        + " "
        + " ".join(operator for operator, _ in heredocs)
        + rng.choice(same_line)
    )
    body = "\n".join("\n".join(lines) for _, lines in heredocs)
    if body or rng.random() < 0.7:
        command += "\n" + body
    wrapper = rng.random()
    if wrapper < 0.1:
        command = f"x=$({command}\n)"
    elif wrapper < 0.2:
        command = f"if true; then\n{command}\nfi"
    elif wrapper < 0.25:
        command = f"f() {{\n{command}\n}}"
    elif wrapper < 0.3:
        command = f"{rng.choice(lookalikes)}; {command}"
    return command + rng.choice(["", "\n", "\necho done", "\n# trailing"])


def _bash_reports_unterminated_heredocs() -> bool:
    """Whether ``bash -n`` warns about a heredoc cut off by end of input.

    bash 4.0 added the warning. Older versions, including the 3.2 that macOS
    ships as ``/bin/bash``, accept such input silently and so cannot serve as
    the reference.
    """
    if shutil.which("bash") is None:
        return False
    probe = subprocess.run(
        ["bash", "-n"], input=b"cat <<EOF\nbody", capture_output=True, check=False
    )
    return b"delimited by end-of-file" in probe.stderr


@pytest.mark.skipif(
    not _bash_reports_unterminated_heredocs(),
    reason="needs bash >= 4, which warns about an unterminated heredoc",
)
def test_unterminated_heredoc_detection_agrees_with_bash():
    """bash itself is the reference for what an unterminated heredoc is.

    ``bash -n`` warns "here-document ... delimited by end-of-file" exactly when
    an interactive shell would go on waiting for the terminator. The detector
    refuses commands, so it must never report one bash considers complete, and
    it should not miss one either.
    """
    rng = random.Random(7048)
    commands = sorted({_generate_heredoc_command(rng) for _ in range(400)})

    wrongly_refused = []
    missed = []
    for command in commands:
        check = subprocess.run(
            ["bash", "-n"], input=command.encode(), capture_output=True, check=False
        )
        expected = b"delimited by end-of-file" in check.stderr
        reported = find_unterminated_heredoc(command) is not None
        if reported and not expected and b"syntax error" in check.stderr:
            # `bash -n` stops at the first syntax error, so it never reached
            # the heredoc further down. An interactive shell runs line by line
            # and would. bash gives no verdict here.
            continue
        if reported and not expected:
            wrongly_refused.append(command)
        if expected and not reported:
            missed.append(command)

    assert wrongly_refused == []
    assert missed == []
