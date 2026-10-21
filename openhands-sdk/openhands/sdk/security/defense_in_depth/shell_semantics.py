"""Shared AST-backed shell command semantics for security analyzers.

Resolves command names and arguments against the shared tree-sitter
``_shell_ast`` view (issue #2721). Policy rails reuse these facts for pipeline,
wrapper, raw-disk, delete, and nested-script analysis. The pattern analyzer
uses the same view for recursive force-delete detection. Injection and
Python-code patterns stay as regex in ``pattern.py`` because they are not shell
syntax.

The destructive-command scanner reports ``uncertain`` when it cannot verify a
relevant structure. The analyzer can then emit ``UNKNOWN`` rather than a false
``LOW``:

- the destructive flag shape appears on a command whose verb cannot be
  resolved;
- a dynamic argument or unsupported option group prevents safe selection of
  directly executed shell source;
- an ERROR/MISSING parse node intersects a command span the detector uses; or
- the nested-runner depth bound is hit with a script operand still unscanned.

Shell parse errors outside these spans stay ``LOW``. Arbitrary non-shell text
can contain parse errors, so mapping each error to ``UNKNOWN`` would produce too
many uncertain results. Detector IDs are owned by ``pattern.py`` and passed in
unchanged.
"""

from __future__ import annotations

import contextlib
import posixpath
from dataclasses import dataclass, replace
from fnmatch import fnmatchcase
from typing import Final

from tree_sitter import Node

from openhands.sdk.security import _shell_ast as ast_view
from openhands.sdk.security._shell_ast import (
    ShellCommand,
    ShellProgram,
    ShellWord,
)


# Command basenames whose script operand is a nested shell program.
# Resolving and re-parsing that operand closes the nested-runner bypass
# class without hardcoding payloads.
_SHELL_RUNNERS: Final[frozenset[str]] = frozenset(
    {"sh", "bash", "dash", "zsh", "ksh", "ash"}
)

# Basename of the recursive force-delete command resolved structurally.
_RM_BASENAME: Final[str] = "rm"

# The short-option character that makes a runner's first operand a script.
_SCRIPT_FLAG_CHAR: Final[str] = "c"

# End-of-options marker that terminates option parsing (POSIX).
_END_OF_OPTIONS: Final[str] = "--"

# A lone dash: the historical end-of-options synonym POSIX sh still accepts.
_BARE_DASH: Final[str] = "-"

# Leading characters that mark a runner argv word as an option group. POSIX
# sh accepts both ``-x``-style and ``+x``-style groups.
_OPTION_PREFIXES: Final[frozenset[str]] = frozenset({"-", "+"})

# Runner short options that consume the next word as their argument (set -o /
# shopt -O names). Without this, ``bash -o errexit -c ...`` would misread the
# option argument as the first operand and stop looking for the script flag.
_SHORT_OPTS_WITH_ARG: Final[frozenset[str]] = frozenset({"o", "O"})

# Runner long options that consume the next word as their argument. An
# unrecognized long option makes command selection uncertain.
_LONG_OPTS_WITH_ARG: Final[frozenset[str]] = frozenset({"--rcfile", "--init-file"})

_LONG_OPTS_WITHOUT_ARG: Final[frozenset[str]] = frozenset(
    {
        "--debugger",
        "--login",
        "--noediting",
        "--noprofile",
        "--norc",
        "--posix",
        "--restricted",
        "--verbose",
    }
)

_SHORT_RUNNER_OPTIONS: Final[frozenset[str]] = frozenset("abefhkmnptuvxBCDHPcilrsoO")

# Long-flag names mapped onto their short-flag characters below.
_LONG_RECURSIVE: Final[str] = "recursive"
_LONG_FORCE: Final[str] = "force"

# Node types that this analyzer cannot resolve to literal text. Most are
# dynamic. ANSI-C strings are static, but decoding their escape language is
# outside this analyzer's bounded literal resolver.
_UNRESOLVED_LITERAL_NODE_TYPES: Final[frozenset[str]] = frozenset(
    {
        "command_substitution",
        "process_substitution",
        "expansion",
        "simple_expansion",
        "arithmetic_expansion",
        "ansi_c_string",
    }
)

# How deep nested script operands are followed. Bounds work on adversarial
# deeply-nested runner arguments; hitting the bound with an operand still
# unscanned is surfaced as uncertain, never a silent LOW.
_MAX_NESTING_DEPTH: Final[int] = 8

# Characters a backslash escapes inside double quotes (POSIX 2.2.3). Before
# any other character the backslash is literal and must be retained.
_DOUBLE_QUOTE_ESCAPABLE: Final[frozenset[str]] = frozenset('$`"\\')

# These unresolved node types stay in one argv position. Quoted Bash ``@``
# expansions are handled separately below. Other unresolved shapes can split.
_SINGLE_ARGUMENT_UNRESOLVED_NODE_TYPES: Final[frozenset[str]] = frozenset(
    {
        "string",
        "raw_string",
        "ansi_c_string",
        "arithmetic_expansion",
        "process_substitution",
    }
)

_DEFINITE_NON_OPTION_NODE_TYPES: Final[frozenset[str]] = frozenset(
    {"arithmetic_expansion", "process_substitution"}
)

_RM_OPTION_PATTERNS: Final[tuple[tuple[str, frozenset[str]], ...]] = (
    ("-r", frozenset("r")),
    ("-R", frozenset("R")),
    ("-f", frozenset("f")),
    ("-rf", frozenset("rf")),
    ("-fr", frozenset("rf")),
    ("-Rf", frozenset("Rf")),
    ("-fR", frozenset("Rf")),
    ("--recursive", frozenset("r")),
    ("--force", frozenset("f")),
)


@dataclass(frozen=True, slots=True)
class CommandSemantics:
    """Static argv facts for one resolved command."""

    basename: str | None
    arguments: tuple[str | None, ...]
    argument_words: tuple[ShellWord, ...]
    uncertain: bool = False
    can_invoke_shell_builtin: bool = True
    can_invoke_shell_keyword: bool = True


@dataclass(frozen=True, slots=True)
class ShellScanResult:
    """Outcome of an AST-backed shell scan.

    ``detector_id`` is the matched pattern stable ID when ``matched`` is True,
    else None. ``uncertain`` is True when the scan cannot resolve a relevant
    command or shell-runner boundary, cannot trust a required parse span, or
    reaches the nesting depth bound. The caller then emits ``UNKNOWN`` instead
    of ``LOW``.
    """

    matched: bool
    detector_id: str | None = None
    uncertain: bool = False


@dataclass(frozen=True, slots=True)
class SignalOutput:
    """Outcome of scanning one program or command for a destructive verb.

    ``matched`` is True once a recursive force delete is resolved concretely.
    ``uncertain`` is True when the scan cannot verify a destructive shape or a
    relevant shell-runner boundary. The caller turns this into ``UNKNOWN``
    rather than ``LOW``.

    A concrete match always outranks uncertainty, so ``matched`` and
    ``uncertain`` are never both True.
    """

    matched: bool
    uncertain: bool


_NO_SIGNAL: Final[SignalOutput] = SignalOutput(matched=False, uncertain=False)
_MATCH_SIGNAL: Final[SignalOutput] = SignalOutput(matched=True, uncertain=False)
_UNCERTAIN_SIGNAL: Final[SignalOutput] = SignalOutput(matched=False, uncertain=True)


def scan_shell_command(command: str, rm_detector_id: str) -> ShellScanResult:
    """Scan ``command`` for AST-resolvable destructive shell commands.

    Resolves each simple command name through quotes and path prefixes,
    descends into directly executed shell source, and reports a match for the
    recursive force-delete family.

    Uncertainty is reported narrowly. It applies when the scan cannot verify a
    destructive command, a relevant shell-runner option boundary, a required
    parse span, or a nested script before the depth limit. In each case the
    caller emits UNKNOWN rather than a false LOW. An unrelated parse error
    stays LOW.
    """
    program = _safe_parse(command)
    if program is None:
        # Encoding failure (e.g. lone surrogate). The regex layer already
        # scanned the flattened text; report neither match nor uncertainty.
        return ShellScanResult(matched=False)

    signal = _program_signal(program, depth=0)
    if signal.matched:
        return ShellScanResult(matched=True, detector_id=rm_detector_id)
    return ShellScanResult(matched=False, uncertain=signal.uncertain)


def _safe_parse(command: str) -> ShellProgram | None:
    with contextlib.suppress(UnicodeEncodeError, ValueError):
        return ast_view.parse_shell_program(command)
    return None


def resolve_command(command: ShellCommand) -> CommandSemantics:
    """Resolve a command name and its literal arguments."""
    name_word = command.name
    argument_words = command.words
    if (
        name_word is not None
        and name_word.text.isdecimal()
        and argument_words
        and any(
            name_word.node.end_byte == redirect.start_byte
            for redirect, _ in _command_redirects(command)
        )
    ):
        name_word = argument_words[0]
        argument_words = argument_words[1:]
    name_literal = (
        _resolve_node_literal(name_word.node) if name_word is not None else None
    )
    name_arity_uncertain = name_word is not None and argument_arity_is_uncertain(
        name_literal,
        name_word,
    )
    return CommandSemantics(
        basename=(
            posixpath.basename(name_literal)
            if name_literal is not None and not name_arity_uncertain
            else None
        ),
        arguments=tuple(_resolve_node_literal(word.node) for word in argument_words),
        argument_words=argument_words,
        uncertain=(
            name_word is not None and (name_literal is None or name_arity_uncertain)
        ),
        can_invoke_shell_builtin=(name_literal is not None and "/" not in name_literal),
        can_invoke_shell_keyword=(
            name_literal is not None
            and name_word is not None
            and name_word.text == name_literal
            and "/" not in name_literal
        ),
    )


def has_recursive_force_flags(command: CommandSemantics) -> bool:
    """Return whether a resolved command has recursive and force flags."""
    flags = _collect_literal_flags(command.arguments)
    return ("r" in flags or "R" in flags) and "f" in flags


def static_shell_script(command: CommandSemantics) -> tuple[bool, str | None, bool]:
    """Return the script and option-boundary uncertainty for a shell runner."""
    if not is_shell_runner(command):
        return False, None, False
    assert command.basename is not None
    return _extract_script_operand(
        command.arguments,
        command.argument_words,
        command.basename,
    )


def is_shell_runner(command: CommandSemantics) -> bool:
    """Return whether a command resolves to a supported shell runner."""
    return command.basename in _SHELL_RUNNERS


def static_executable_shell_source(
    syntax: ShellCommand,
    command: CommandSemantics,
) -> tuple[bool, str | None, bool]:
    """Return shell source that the command executes directly."""
    runner_command = _without_redirect_descriptor_words(syntax, command)
    has_script, script, uncertain = static_shell_script(runner_command)
    if has_script or uncertain:
        return has_script, script, uncertain

    eval_command, eval_uncertain = _effective_eval_command(runner_command)
    if eval_uncertain:
        return True, None, True
    if eval_command is not None:
        arguments = eval_command.arguments
        words = eval_command.argument_words
        if arguments and arguments[0] == _END_OF_OPTIONS:
            arguments = arguments[1:]
            words = words[1:]
        elif arguments and arguments[0] is not None and arguments[0].startswith("-"):
            return False, None, False
        if not arguments:
            return False, None, False
        if any(
            argument is None or argument_arity_is_uncertain(argument, word)
            for argument, word in zip(
                arguments,
                words,
                strict=True,
            )
        ):
            return True, None, True
        literal_arguments = [argument for argument in arguments if argument is not None]
        return True, " ".join(literal_arguments), False

    if not is_shell_runner(runner_command):
        return False, None, False
    has_source, source, source_uncertain = _static_standard_input_source(syntax)
    if not has_source:
        return False, None, False
    reads_standard_input = _runner_reads_standard_input(runner_command)
    if reads_standard_input is None:
        return True, None, True
    if not reads_standard_input:
        return False, None, False
    return True, source, source_uncertain


def static_pipeline_output(
    syntax: ShellCommand,
    command: CommandSemantics,
    enclosing_node: Node,
) -> tuple[bool, str | None, bool]:
    """Return output that a command writes as the next pipeline stage's input."""
    reaches_pipeline = standard_output_reaches_pipeline(syntax, enclosing_node)
    if reaches_pipeline is False:
        return False, None, False
    if reaches_pipeline is None or command.uncertain:
        return True, None, True

    if command.basename == "echo":
        return _static_echo_output(command)
    if command.basename == "printf":
        return _static_printf_output(command)
    if command.basename == "cat" and not command.arguments:
        has_source, source, uncertain = _static_standard_input_source(
            syntax,
            enclosing_node=enclosing_node,
        )
        if has_source:
            return has_source, source, uncertain
    return True, None, True


def static_piped_shell_source(
    syntax: ShellCommand,
    command: CommandSemantics,
    pipeline_source: tuple[bool, str | None, bool],
    enclosing_node: Node,
) -> tuple[bool, str | None, bool]:
    """Return pipeline input that a shell runner executes as source."""
    if not pipeline_source[0]:
        return False, None, False

    runner_command = _without_redirect_descriptor_words(syntax, command)
    has_script, _, script_uncertain = static_shell_script(runner_command)
    if has_script:
        return False, None, False
    if script_uncertain:
        return True, None, True
    if not is_shell_runner(runner_command):
        return False, None, False

    reads_standard_input = _runner_reads_standard_input(runner_command)
    if reads_standard_input is False:
        return False, None, False

    has_source, source, source_uncertain = static_standard_input_source(
        syntax,
        pipeline_source,
        enclosing_node,
    )
    if not has_source:
        return False, None, False
    if reads_standard_input is None or source_uncertain:
        return True, None, True
    return True, source, False


def static_standard_input_source(
    syntax: ShellCommand,
    initial_source: tuple[bool, str | None, bool],
    enclosing_node: Node,
) -> tuple[bool, str | None, bool]:
    """Return the standard-input source after applying command redirects."""
    return _static_standard_input_source(
        syntax,
        initial_source,
        enclosing_node,
    )


def _static_echo_output(
    command: CommandSemantics,
) -> tuple[bool, str | None, bool]:
    if any(
        argument is None
        or argument_arity_is_uncertain(argument, word)
        or "\\" in argument
        for argument, word in zip(
            command.arguments,
            command.argument_words,
            strict=True,
        )
    ):
        return True, None, True
    arguments = [argument for argument in command.arguments if argument is not None]
    if arguments and arguments[0].startswith("-"):
        return True, None, True
    return True, " ".join(arguments) + "\n", False


def _static_printf_output(
    command: CommandSemantics,
) -> tuple[bool, str | None, bool]:
    if not command.arguments:
        return True, "", False
    format_index = 1 if command.arguments[0] == _END_OF_OPTIONS else 0
    if format_index >= len(command.arguments):
        return True, "", False
    format_string = command.arguments[format_index]
    if format_string is None or argument_arity_is_uncertain(
        format_string,
        command.argument_words[format_index],
    ):
        return True, None, True
    if format_index == 0 and format_string.startswith("-"):
        return True, None, True
    if "\\" in format_string:
        return True, None, True
    values = [
        (
            argument
            if argument is not None and not argument_arity_is_uncertain(argument, word)
            else None
        )
        for argument, word in zip(
            command.arguments[format_index + 1 :],
            command.argument_words[format_index + 1 :],
            strict=True,
        )
    ]
    output = _render_simple_printf(format_string, values)
    return True, output, output is None


def _render_simple_printf(
    format_string: str,
    values: list[str | None],
) -> str | None:
    output: list[str] = []
    value_index = 0
    consumes_values = False
    index = 0
    while index < len(format_string):
        if format_string[index] != "%":
            output.append(format_string[index])
            index += 1
            continue
        if index + 1 >= len(format_string):
            return None
        conversion = format_string[index + 1]
        if conversion == "%":
            output.append("%")
        elif conversion == "s" and value_index < len(values):
            value = values[value_index]
            if value is None:
                return None
            consumes_values = True
            output.append(value)
            value_index += 1
        else:
            return None
        index += 2
    if consumes_values and value_index != len(values):
        return None
    return "".join(output)


def standard_output_reaches_pipeline(
    command: ShellCommand,
    enclosing_node: Node | None = None,
) -> bool | None:
    """Return whether the command's standard output still reaches its pipe."""
    pipe_output: dict[str, bool | None] = {"1": True, "2": False}
    for redirect, descriptor in _command_redirects(command, enclosing_node):
        target_fd = _normalize_descriptor(descriptor)
        redirect_text = _decode(redirect.text).lstrip("0123456789")
        if redirect_text.startswith(("&>", "&>>")):
            destination = _redirect_destination(redirect)
            alias = _file_descriptor_alias(destination)
            if alias is not None:
                redirected_output = pipe_output.get(alias, False)
            else:
                redirected_output = None if destination is None else False
            pipe_output["1"] = redirected_output
            pipe_output["2"] = redirected_output
            continue
        if redirect_text.startswith((">&", "<&")):
            destination = _redirect_destination(redirect)
            if destination == "-":
                pipe_output[target_fd] = False
            elif destination is not None and destination.isdecimal():
                pipe_output[target_fd] = pipe_output.get(
                    _normalize_descriptor(destination),
                    False,
                )
            else:
                pipe_output[target_fd] = None
            continue
        destination = _redirect_destination(redirect)
        alias = _file_descriptor_alias(destination)
        if alias is not None:
            pipe_output[target_fd] = pipe_output.get(alias, False)
        else:
            pipe_output[target_fd] = None if destination is None else False
    return pipe_output["1"]


def _effective_eval_command(
    command: CommandSemantics,
) -> tuple[CommandSemantics | None, bool]:
    for depth in range(_MAX_NESTING_DEPTH + 1):
        if not command.can_invoke_shell_builtin:
            return None, False
        if command.basename == "eval":
            return command, False
        if command.basename not in {"builtin", "command"}:
            return None, False
        if depth == _MAX_NESTING_DEPTH:
            return None, True

        index = 0
        if command.basename == "command":
            while index < len(command.arguments) and command.arguments[index] == "-p":
                index += 1
            if (
                index < len(command.arguments)
                and command.arguments[index] == _END_OF_OPTIONS
            ):
                index += 1
        elif command.arguments and command.arguments[0] == _END_OF_OPTIONS:
            index = 1
        if index >= len(command.arguments):
            return None, False

        target = command.arguments[index]
        target_word = command.argument_words[index]
        if target is None or argument_arity_is_uncertain(target, target_word):
            return None, True
        if target.startswith("-") or "/" in target:
            return None, False
        command = replace(
            command,
            basename=target,
            arguments=command.arguments[index + 1 :],
            argument_words=command.argument_words[index + 1 :],
            can_invoke_shell_keyword=False,
        )
    return None, True


def argument_arity_is_uncertain(
    literal: str | None,
    word: ShellWord,
) -> bool:
    """Return whether one source word can change the number of argv words."""
    if literal is not None:
        return _contains_unquoted_arity_syntax(word.text)
    return _node_arity_is_uncertain(word.node)


def static_word_prefix(word: ShellWord) -> str:
    """Return the literal prefix before the first dynamic node in a shell word."""
    prefix, _ = _static_node_prefix(word.node)
    return prefix


def _static_node_prefix(node: Node) -> tuple[str, bool]:
    if node.type in _UNRESOLVED_LITERAL_NODE_TYPES:
        return "", False
    if node.type in {"string", "command_name", "concatenation"}:
        parts: list[str] = []
        for child in node.named_children:
            prefix, complete = _static_node_prefix(child)
            parts.append(prefix)
            if not complete:
                return "".join(parts), False
        return "".join(parts), True
    literal = _resolve_node_literal(node)
    return (literal, True) if literal is not None else ("", False)


def _node_arity_is_uncertain(node: Node) -> bool:
    if node.type == "string":
        return _contains_multi_argument_expansion(node)
    if node.type in _SINGLE_ARGUMENT_UNRESOLVED_NODE_TYPES or node.type in {
        "word",
        "number",
    }:
        return False
    if node.type == "concatenation":
        return _contains_unquoted_arity_syntax(_decode(node.text)) or any(
            _node_arity_is_uncertain(child) for child in node.named_children
        )
    return True


def _contains_multi_argument_expansion(node: Node) -> bool:
    if node.type in {"expansion", "simple_expansion"}:
        if any(
            child.type == "special_variable_name" and _decode(child.text) == "@"
            for child in node.named_children
        ):
            return True
        if node.type == "expansion":
            subscript = next(
                (child for child in node.named_children if child.type == "subscript"),
                None,
            )
            if subscript is not None:
                index = subscript.child_by_field_name("index")
                if index is not None and _decode(index.text) == "@":
                    return True
            child_types = [child.type for child in node.children]
            if "!" in child_types and child_types[-2:] == ["@", "}"]:
                return True
    return any(
        _contains_multi_argument_expansion(child) for child in node.named_children
    )


def _contains_unquoted_arity_syntax(text: str) -> bool:
    """Detect unquoted pathname and brace expansion syntax in one shell word."""
    return any(_unquoted_expansion_syntax(text))


def _unquoted_expansion_syntax(text: str) -> tuple[bool, bool]:
    """Return whether a word contains pathname or brace expansion syntax."""
    bracket_open = False
    brace_expands: list[bool] = []
    pathname_expands = False
    has_brace_expansion = False
    quote: str | None = None
    index = 0
    while index < len(text):
        character = text[index]
        if quote == "'":
            if character == quote:
                quote = None
            index += 1
            continue
        if quote == '"':
            if (
                character == "\\"
                and index + 1 < len(text)
                and (
                    text[index + 1] in _DOUBLE_QUOTE_ESCAPABLE
                    or text[index + 1] == "\n"
                )
            ):
                index += 2
                continue
            if character == quote:
                quote = None
            index += 1
            continue
        if character == "\\":
            index += 2
            continue
        if character in {"'", '"'}:
            quote = character
        elif character in {"*", "?"}:
            pathname_expands = True
        elif character == "[":
            bracket_open = True
        elif character == "]" and bracket_open:
            pathname_expands = True
            bracket_open = False
        elif character == "{":
            brace_expands.append(False)
        elif character == "}" and brace_expands:
            if brace_expands.pop():
                has_brace_expansion = True
        elif brace_expands and (character == "," or text.startswith("..", index)):
            brace_expands[-1] = True
        index += 1
    return pathname_expands, has_brace_expansion


def _program_signal(program: ShellProgram, depth: int) -> SignalOutput:
    """Return the scan signal for a parsed program.

    ``matched`` is True when a recursive force delete is resolved. ``uncertain``
    is True when a relevant command, flag shape, parse span, or nested execution
    boundary cannot be resolved statically.
    """
    uncertain = False
    for command in ast_view.iter_commands(program):
        resolved = resolve_command(command)
        if has_recursive_force_flags(resolved):
            if resolved.basename == _RM_BASENAME:
                return _MATCH_SIGNAL
            if resolved.basename is None or command.has_error:
                # Dangerous flag shape on a verb we cannot vouch for:
                # either the name is unresolvable, or an ERROR/MISSING
                # parse node intersects this command, so the resolved verb
                # may not be the verb the shell would run. Keep scanning
                # for a concrete match first.
                uncertain = True
        elif resolved.basename == _RM_BASENAME and _has_uncertain_recursive_force_flags(
            resolved
        ):
            uncertain = True
        nested = _nested_signal(command, resolved, depth)
        if nested.matched:
            return _MATCH_SIGNAL
        uncertain = uncertain or nested.uncertain
    return SignalOutput(matched=False, uncertain=uncertain)


def _collect_literal_flags(arguments: tuple[str | None, ...]) -> set[str]:
    """Collect short-flag chars and recognised long flags.

    Option words are resolved through the same quote-removal path as the
    command name. POSIX quote removal makes ``rm "-rf" /`` and ``rm -r"f" /``
    argv-identical to ``rm -rf /``, so treating a quoted option word as opaque
    -- and therefore flagless -- would let the same catastrophic delete through
    under a different spelling while the de-quoted verb still resolved to
    ``rm``. Resolving the verb but not its options is the asymmetry that made
    that bypass possible.

    Unresolved words do not add literal flags. A separate check marks them
    uncertain when they can expand to recursive and force flags.

    The end-of-options marker terminates flag parsing, and it is compared
    against the de-quoted literal too -- after ``rm "--"`` the shell really
    does treat a following ``-rf`` as a filename. Long flags are mapped onto
    their short-flag character so ordering and mixing work.
    """
    flags: set[str] = set()
    for literal in arguments:
        if literal is None:
            continue
        if literal == _END_OF_OPTIONS:
            break
        flags |= ast_view.split_short_flags(literal)
        if ast_view.is_long_flag(literal, _LONG_RECURSIVE):
            flags.add("r")
        elif ast_view.is_long_flag(literal, _LONG_FORCE):
            flags.add("f")
    return flags


def _has_uncertain_recursive_force_flags(command: CommandSemantics) -> bool:
    flags = _collect_literal_flags(command.arguments)
    for literal, word in zip(
        command.arguments,
        command.argument_words,
        strict=True,
    ):
        if literal == _END_OF_OPTIONS:
            break
        if literal is None or not argument_arity_is_uncertain(literal, word):
            continue
        pathname_expands, brace_expands = _unquoted_expansion_syntax(word.text)
        brace_can_start_option, brace_flags = _leading_brace_option_flags(literal)
        can_start_option = (
            literal.startswith("-")
            or (pathname_expands and _pathname_can_start_option(word.text))
            or (brace_expands and brace_can_start_option)
        )
        if pathname_expands and can_start_option:
            flags.update(_pathname_option_flags(literal))
        elif literal.startswith("-"):
            flags.update(character for character in literal if character in "rRf")
        elif brace_expands:
            flags.update(brace_flags)
    return ("r" in flags or "R" in flags) and "f" in flags


def _pathname_can_start_option(text: str) -> bool:
    quote: str | None = None
    index = 0
    while index < len(text):
        character = text[index]
        if quote is not None:
            if character == quote:
                quote = None
                index += 1
                continue
            return character == "-"
        if character == "\\" and index + 1 < len(text):
            return text[index + 1] == "-"
        if character in {"'", '"'}:
            quote = character
            index += 1
            continue
        return character in {"-", "*", "?", "["}
    return False


def _pathname_option_flags(pattern: str) -> set[str]:
    if pattern.startswith("-"):
        return set("rRf")
    if "[^" in pattern or "[[:" in pattern:
        return set("rRf")
    flags: set[str] = set()
    for option, option_flags in _RM_OPTION_PATTERNS:
        if fnmatchcase(option, pattern):
            flags.update(option_flags)
    return flags


def _leading_brace_option_flags(
    literal: str,
    recursion_depth: int = 0,
) -> tuple[bool, set[str]]:
    if not literal.startswith("{"):
        return False, set()
    if recursion_depth >= _MAX_NESTING_DEPTH:
        return "-" in literal, {
            character for character in literal if character in "rRf"
        }
    brace_depth = 0
    for index, character in enumerate(literal[1:], start=1):
        if character == "{":
            brace_depth += 1
        elif character == "}":
            if brace_depth == 0:
                alternatives = literal[1:index]
                suffix = literal[index + 1 :]
                return _brace_option_flags(
                    alternatives,
                    suffix,
                    recursion_depth,
                )
            brace_depth -= 1
    return False, set()


def _brace_option_flags(
    alternatives: str,
    suffix: str,
    recursion_depth: int,
) -> tuple[bool, set[str]]:
    can_start_option = False
    flags: set[str] = set()
    brace_depth = 0
    start = 0
    for index in range(len(alternatives) + 1):
        character = alternatives[index] if index < len(alternatives) else ","
        if character == "{":
            brace_depth += 1
        elif character == "}":
            brace_depth -= 1
        elif character == "," and brace_depth == 0:
            candidate = alternatives[start:index] + suffix
            if candidate.startswith("-"):
                can_start_option = True
                flags.update(character for character in candidate if character in "rRf")
            elif candidate.startswith("{"):
                nested_can_start, nested_flags = _leading_brace_option_flags(
                    candidate,
                    recursion_depth + 1,
                )
                can_start_option = can_start_option or nested_can_start
                flags.update(nested_flags)
            start = index + 1
    return can_start_option, flags


def _nested_signal(
    command: ShellCommand,
    resolved: CommandSemantics,
    depth: int,
) -> SignalOutput:
    """Descend into directly executed shell source and re-parse it.

    Closes the nested-runner bypass class, including quoted verbs inside the
    script that no outer pattern can see.

    Fail-safe ordering: the operand scan runs first so a concrete match
    always wins; only when no match is found does a parse error on the
    source command's node force uncertainty -- an ERROR/MISSING there (e.g. an
    unclosed quote truncating the script operand) means the argv spans the
    scan just read cannot be trusted, so a silent LOW would vouch for
    something never actually seen.
    """
    source_command = is_shell_runner(resolved) or resolved.basename in {
        "eval",
        "builtin",
        "command",
    }
    has_script, inner, uncertain = static_executable_shell_source(command, resolved)
    if has_script and inner is not None:
        if depth >= _MAX_NESTING_DEPTH:
            return _UNCERTAIN_SIGNAL
        inner_program = _safe_parse(inner)
        if inner_program is not None:
            nested = _program_signal(inner_program, depth + 1)
            if nested.matched:
                return _MATCH_SIGNAL
            if nested.uncertain:
                return _UNCERTAIN_SIGNAL
    if uncertain:
        return _UNCERTAIN_SIGNAL
    if source_command and command.has_error:
        return _UNCERTAIN_SIGNAL
    return _NO_SIGNAL


def _runner_reads_standard_input(command: CommandSemantics) -> bool | None:
    stdin_selected = False
    noexec = False
    dump_strings = False
    options_ended = False
    index = 0
    while index < len(command.arguments):
        literal = command.arguments[index]
        word = command.argument_words[index]
        if literal is None or argument_arity_is_uncertain(literal, word):
            if options_ended:
                return (
                    False
                    if noexec or dump_strings
                    else (True if stdin_selected else None)
                )
            return None
        if not options_ended:
            if literal in {_END_OF_OPTIONS, _BARE_DASH}:
                options_ended = True
                index += 1
                continue
            if _is_option_word(literal):
                if _is_definitely_invalid_option_word(literal):
                    return False
                value_count = _runner_option_value_count(literal, command.basename)
                if value_count is None:
                    return None
                stdin_selected = stdin_selected or _sets_stdin_flag(literal)
                noexec = _updated_noexec(noexec, literal)
                dump_strings = dump_strings or _sets_dump_strings(
                    command.basename,
                    literal,
                )
                if value_count and index + value_count >= len(command.arguments):
                    return False
                index += value_count + 1
                continue
        return False if noexec or dump_strings else stdin_selected
    return not (noexec or dump_strings)


def _static_standard_input_source(
    command: ShellCommand,
    initial_source: tuple[bool, str | None, bool] = (False, None, False),
    enclosing_node: Node | None = None,
) -> tuple[bool, str | None, bool]:
    sources = {"0": initial_source} if initial_source[0] else {}
    for redirect, descriptor in _command_redirects(command, enclosing_node):
        target_fd = _normalize_descriptor(descriptor)
        redirect_text = _decode(redirect.text).lstrip("0123456789")
        if redirect_text.startswith(("<&", ">&")):
            destination = _redirect_destination(redirect)
            if destination == "-":
                sources.pop(target_fd, None)
                continue
            if destination is not None and destination.isdecimal():
                source_fd = _normalize_descriptor(destination)
                if source_fd in sources:
                    sources[target_fd] = sources[source_fd]
                else:
                    sources.pop(target_fd, None)
                continue
            if target_fd == "0" or sources:
                sources[target_fd] = (True, None, True)
            continue
        if redirect_text.startswith(("&>", "&>>")):
            destination = _redirect_destination(redirect)
            alias = _file_descriptor_alias(destination)
            if alias is not None and alias in sources:
                sources["1"] = sources[alias]
                sources["2"] = sources[alias]
            elif destination is None and sources:
                sources["1"] = (True, None, True)
                sources["2"] = (True, None, True)
            else:
                sources.pop("1", None)
                sources.pop("2", None)
            continue
        if _is_herestring_redirect(redirect, redirect_text):
            sources[target_fd] = _static_herestring_source(redirect)
            continue
        if redirect.type == "heredoc_redirect":
            sources[target_fd] = _static_heredoc_source(redirect)
            continue
        destination = _redirect_destination(redirect)
        alias = _file_descriptor_alias(destination)
        if alias is not None and alias in sources:
            sources[target_fd] = sources[alias]
        elif destination is None and (target_fd == "0" or sources):
            sources[target_fd] = (True, None, True)
        else:
            sources.pop(target_fd, None)
    return sources.get("0", (False, None, False))


def _static_herestring_source(redirect: Node) -> tuple[bool, str | None, bool]:
    values = [
        child
        for child in redirect.named_children
        if child.type not in {"file_descriptor", "ERROR"}
        and "redirect" not in child.type
    ]
    if not values:
        return True, None, True
    source = _resolve_node_literal(values[0])
    return True, source, source is None


def _is_herestring_redirect(redirect: Node, text: str) -> bool:
    if redirect.type == "herestring_redirect" or text.startswith("<<<"):
        return True
    previous = redirect.prev_named_sibling
    return (
        previous is not None
        and previous.type == "ERROR"
        and previous.end_byte == redirect.start_byte
        and _decode(previous.text) == "<<"
        and text.startswith("<")
    )


def _redirect_destination(redirect: Node) -> str | None:
    for index, child in enumerate(redirect.children):
        if child.is_named and redirect.field_name_for_child(index) == "destination":
            return _resolve_node_literal(child)
    return None


def _file_descriptor_alias(path: str | None) -> str | None:
    aliases = {
        "/dev/stdin": "0",
        "/dev/stdout": "1",
        "/dev/stderr": "2",
    }
    if path in aliases:
        return aliases[path]
    if path is None:
        return None
    for prefix in ("/dev/fd/", "/proc/self/fd/"):
        descriptor = path.removeprefix(prefix)
        if descriptor != path and descriptor.isdecimal():
            return _normalize_descriptor(descriptor)
    return None


def _normalize_descriptor(descriptor: str) -> str:
    if descriptor.isdecimal():
        return str(int(descriptor))
    return descriptor


def _static_heredoc_source(redirect: Node) -> tuple[bool, str | None, bool]:
    start = next(
        (child for child in redirect.named_children if child.type == "heredoc_start"),
        None,
    )
    body = next(
        (child for child in redirect.named_children if child.type == "heredoc_body"),
        None,
    )
    end = next(
        (child for child in redirect.named_children if child.type == "heredoc_end"),
        None,
    )
    if start is None or body is None or end is None:
        return True, None, True

    body_text = _decode(body.text)
    delimiter_is_quoted = _decode(start.text) != _decode(end.text)
    if not delimiter_is_quoted and (body.named_children or "\\" in body_text):
        return True, None, True
    return True, body_text, False


def _without_redirect_descriptor_words(
    syntax: ShellCommand,
    command: CommandSemantics,
) -> CommandSemantics:
    redirects = _command_redirects(syntax)
    retained = [
        (argument, word)
        for argument, word in zip(
            command.arguments,
            command.argument_words,
            strict=True,
        )
        if not (
            argument is not None
            and argument.isdecimal()
            and word.text.isdecimal()
            and _is_redirect_descriptor_word(argument, word, redirects)
        )
    ]
    if len(retained) == len(command.arguments):
        return command
    return replace(
        command,
        arguments=tuple(argument for argument, _ in retained),
        argument_words=tuple(word for _, word in retained),
    )


def _is_redirect_descriptor_word(
    argument: str,
    word: ShellWord,
    redirects: list[tuple[Node, str]],
) -> bool:
    for redirect, descriptor in redirects:
        if _normalize_descriptor(argument) != _normalize_descriptor(descriptor):
            continue
        if word.node.end_byte == redirect.start_byte:
            return True
        previous = redirect.prev_named_sibling
        if (
            previous is not None
            and previous.type == "ERROR"
            and _decode(previous.text) == "<<"
            and word.node.end_byte == previous.start_byte
            and previous.end_byte == redirect.start_byte
        ):
            return True
    return False


def _command_redirects(
    command: ShellCommand,
    enclosing_node: Node | None = None,
) -> list[tuple[Node, str]]:
    redirects: list[tuple[Node, str]] = []
    container = command.node
    reached_enclosing_node = enclosing_node is None
    while True:
        for child in container.named_children:
            if "redirect" in child.type and child.type != "redirected_statement":
                _append_redirect_tree(redirects, command, child)
        if container == enclosing_node:
            reached_enclosing_node = True
        parent = container.parent
        if not reached_enclosing_node and parent is not None:
            container = parent
            continue
        if parent is not None and parent.type == "redirected_statement":
            container = parent
            continue
        if (
            parent is not None
            and parent.type == "pipeline"
            and parent.named_children
            and parent.named_children[-1] == container
        ):
            container = parent
            continue
        break

    parent = command.node.parent
    sibling = parent.next_named_sibling if parent is not None else None
    if parent is not None and parent.type == "ERROR" and sibling is not None:
        descriptor = _adjacent_trailing_descriptor(command, parent, sibling)
        if descriptor is not None:
            for child in parent.named_children:
                if "redirect" in child.type:
                    _append_redirect_tree(redirects, command, child)
            for child in sibling.named_children:
                if "redirect" in child.type:
                    _append_redirect_tree(redirects, command, child, descriptor)

    redirects.sort(key=lambda item: item[0].start_byte)
    return redirects


def _adjacent_trailing_descriptor(
    command: ShellCommand,
    parent: Node,
    sibling: Node,
) -> str | None:
    if sibling.start_byte != parent.end_byte:
        return None
    suffix = (parent.text or b"")[command.node.end_byte - parent.start_byte :]
    fields = suffix.rsplit(maxsplit=1)
    if not fields or not fields[-1].isdigit():
        return None
    return fields[-1].decode()


def _append_redirect_tree(
    redirects: list[tuple[Node, str]],
    command: ShellCommand,
    redirect: Node,
    descriptor: str | None = None,
) -> None:
    for index, nested in enumerate(ast_view.iter_redirect_nodes(redirect)):
        nested_descriptor = (
            descriptor
            if index == 0 and descriptor is not None
            else _redirect_descriptor(command, nested)
        )
        redirects.append((nested, nested_descriptor))


def _redirect_descriptor(command: ShellCommand, redirect: Node) -> str:
    text = _decode(redirect.text).lstrip()
    descriptor_length = len(text) - len(text.lstrip("0123456789"))
    if descriptor_length:
        return text[:descriptor_length]
    previous = redirect.prev_named_sibling
    if previous is not None and previous.end_byte == redirect.start_byte:
        fields = (previous.text or b"").rsplit(maxsplit=1)
        if fields and fields[-1].isdigit():
            return fields[-1].decode()
    for word in command.words:
        if word.node.end_byte == redirect.start_byte and word.text.isdecimal():
            return word.text
    return "0" if text[descriptor_length:].startswith("<") else "1"


def _extract_script_operand(
    arguments: tuple[str | None, ...],
    argument_words: tuple[ShellWord, ...],
    basename: str,
) -> tuple[bool, str | None, bool]:
    """Locate the runner script operand with the supported shell option rules.

    Short options combine, so the script flag counts at any position in a
    group (``-xc``, ``-cx``). A supported argument-taking option consumes the
    next word when it is last in its group. Other placements remain uncertain.
    ``--`` (or the historical lone ``-``) terminates option parsing. The first
    operand then ends the option list. With the script flag set, that operand
    is the script. Without it, that operand is a script file. Later flags
    belong to the script and must not trigger a nested scan.

    Returns ``(has_flag, operand, uncertain)``. ``uncertain`` is True when a
    dynamic word or unsupported option group prevents safe selection of the
    script operand.
    """
    has_script_flag = False
    noexec = False
    dump_strings = False
    options_ended = False
    index = 0
    while index < len(arguments):
        literal = arguments[index]
        word = argument_words[index]
        if argument_arity_is_uncertain(literal, word):
            if options_ended and (noexec or dump_strings):
                return False, None, False
            if options_ended and not has_script_flag:
                return False, None, False
            return has_script_flag, None, True
        if literal is None:
            if has_script_flag:
                return (
                    (False, None, False)
                    if noexec or dump_strings
                    else (True, None, False)
                )
            if options_ended:
                return False, None, False
            if word.node_type in _DEFINITE_NON_OPTION_NODE_TYPES:
                return False, None, False
            return False, None, index + 1 < len(arguments)
        if not options_ended:
            if literal == _END_OF_OPTIONS or literal == _BARE_DASH:
                options_ended = True
                index += 1
                continue
            if _is_option_word(literal):
                if _is_definitely_invalid_option_word(literal):
                    return False, None, False
                value_count = _runner_option_value_count(literal, basename)
                if value_count is None:
                    return has_script_flag, None, True
                if _sets_script_flag(literal):
                    has_script_flag = True
                noexec = _updated_noexec(noexec, literal)
                dump_strings = dump_strings or _sets_dump_strings(basename, literal)
                if value_count:
                    value_index = index + 1
                    if value_index >= len(arguments):
                        return False, None, False
                    if (
                        argument_words[value_index].node_type
                        in _DEFINITE_NON_OPTION_NODE_TYPES
                        or argument_arity_is_uncertain(
                            arguments[value_index],
                            argument_words[value_index],
                        )
                    ):
                        return has_script_flag, None, True
                index += value_count + 1
                continue
        # First operand reached.
        if has_script_flag:
            return (
                (False, None, False)
                if noexec or dump_strings
                else (True, literal, False)
            )
        return False, None, False
    return (
        (False, None, False)
        if noexec or dump_strings
        else (has_script_flag, None, False)
    )


def _is_option_word(literal: str) -> bool:
    """Return whether a resolved argv word is an option group."""
    return len(literal) > 1 and literal[0] in _OPTION_PREFIXES


def _sets_script_flag(literal: str) -> bool:
    """Return whether a short-option group contains the script flag.

    A single ``-`` or ``+`` prefix starts a short-option group. Repeated
    prefixes do not.
    """
    return _is_short_option_group(literal) and _SCRIPT_FLAG_CHAR in literal[1:]


def _is_short_option_group(literal: str) -> bool:
    return (
        len(literal) > 1
        and literal[0] in _OPTION_PREFIXES
        and literal[1] not in _OPTION_PREFIXES
    )


def _sets_stdin_flag(literal: str) -> bool:
    return _is_short_option_group(literal) and literal[0] == "-" and "s" in literal[1:]


def _updated_noexec(current: bool, literal: str) -> bool:
    if not _is_short_option_group(literal) or "n" not in literal[1:]:
        return current
    return literal.startswith("-")


def _sets_dump_strings(basename: str | None, literal: str) -> bool:
    return basename == "bash" and _is_short_option_group(literal) and "D" in literal[1:]


def _runner_option_value_count(literal: str, basename: str | None) -> int | None:
    if literal.startswith(_END_OF_OPTIONS) and basename != "bash":
        return None
    if literal in _LONG_OPTS_WITH_ARG:
        return 1
    if literal in _LONG_OPTS_WITHOUT_ARG:
        return 0
    if literal.startswith(_END_OF_OPTIONS):
        return None
    flags = literal[1:]
    if basename != "bash" and "D" in flags:
        return None
    if any(flag not in _SHORT_RUNNER_OPTIONS for flag in flags):
        return None
    if any(flag in _SHORT_OPTS_WITH_ARG for flag in flags[:-1]):
        return None
    return int(flags[-1] in _SHORT_OPTS_WITH_ARG)


def _is_definitely_invalid_option_word(literal: str) -> bool:
    return literal.startswith(("++", "-+", "+-"))


def _resolve_node_literal(node: Node) -> str | None:
    """Concatenate literal text, or return None when it cannot be resolved.

    Walks the concatenation/string/raw_string/word structure that
    tree-sitter-bash produces for command names and arguments. A dynamic or
    unsupported child, including an ANSI-C string, yields None.
    """
    parts = _collect_literal_parts(node)
    if parts is None:
        return None
    return "".join(parts)


def _collect_literal_parts(node: Node) -> list[str] | None:
    """Return literal fragments, or None when a node cannot be resolved.

    None means the subtree is not statically resolvable. Fragments are
    escape-normalized to the effective literal the shell would produce after
    quote removal: tree-sitter keeps backslash escapes inline in ``word`` and
    ``string_content`` text, so comparing raw text would let ``r\\m`` (which
    the shell runs as ``rm``) slip past the basename check.
    """
    if node.type in _UNRESOLVED_LITERAL_NODE_TYPES:
        return None

    match node.type:
        case "word" | "number":
            return [_unescape_unquoted(_decode(node.text))]

        case "raw_string":
            # Single-quoted operand: strip the quotes; contents are literal
            # and backslash has no special meaning inside single quotes.
            text = _decode(node.text)
            return [text[1:-1] if len(text) >= 2 else ""]

        case "string_content":
            return [_unescape_double_quoted(_decode(node.text))]

        # "string" is the double-quoted case: recursing catches an embedded
        # expansion as dynamic while string_content fragments contribute
        # literally. command_name and concatenation recurse for the same
        # reason, so all three share one branch.
        case "string" | "command_name" | "concatenation":
            parts: list[str] = []
            for child in node.named_children:
                child_parts = _collect_literal_parts(child)
                if child_parts is None:
                    return None
                parts.extend(child_parts)
            return parts

        # Unknown / unhandled node type in a name position: treat as dynamic
        # so we never fabricate a resolved name we are not sure about.
        case _:
            return None


def _unescape_unquoted(text: str) -> str:
    """Strip backslash escapes from an unquoted word (POSIX 2.2.1).

    Outside quotes a backslash makes the next character literal, so the
    effective value drops the backslash (``r\\m`` -> ``rm``); a
    backslash-newline pair is a line continuation and disappears entirely.
    """
    return _strip_backslash_escapes(text, escapable=None)


def _unescape_double_quoted(text: str) -> str:
    """Strip the escapes double quotes honour (POSIX 2.2.3).

    Inside double quotes a backslash only escapes ``$``, `````, ``"``,
    ``\\`` and newline; before any other character the backslash itself is
    literal and must be retained (``"r\\m"`` stays ``r\\m``).
    """
    return _strip_backslash_escapes(text, escapable=_DOUBLE_QUOTE_ESCAPABLE)


def _strip_backslash_escapes(text: str, escapable: frozenset[str] | None) -> str:
    """Return ``text`` with backslash escapes reduced to their literals.

    ``escapable`` is the set of characters whose escapes are honoured, or
    None to honour every character. Backslash-newline is always a line
    continuation and yields nothing. A trailing lone backslash is kept.
    """
    parts: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\\" and index + 1 < length:
            follower = text[index + 1]
            if follower == "\n":
                index += 2
                continue
            if escapable is None or follower in escapable:
                parts.append(follower)
                index += 2
                continue
        parts.append(char)
        index += 1
    return "".join(parts)


def _decode(raw: bytes | None) -> str:
    return raw.decode() if raw is not None else ""
