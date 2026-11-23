"""Block obviously dangerous composed actions before pattern scanning runs.

Some threats are structural, not lexical: ``curl ... | command`` is
dangerous because of the *combination* of fetch + pipeline consumer, not
because either token is dangerous alone. Rails express these composed
conditions as deterministic rules evaluated per-segment, so that
tokens from different executable fields cannot
accidentally satisfy a composed condition.

v1 ships three rails: fetch-to-exec, raw-disk-op, catastrophic-delete.
A matching rail maps to ``SecurityRisk.HIGH`` at the SDK boundary. Relevant
unresolved shell structure maps to ``SecurityRisk.UNKNOWN``. The confirmation
policy decides whether to prompt the user.
"""

from __future__ import annotations

import posixpath
from bisect import bisect_left
from dataclasses import dataclass, replace

from tree_sitter import Node

from openhands.sdk.event import ActionEvent
from openhands.sdk.logger import get_logger
from openhands.sdk.security import _shell_ast as ast_view
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.defense_in_depth.shell_semantics import (
    _MAX_NESTING_DEPTH,
    CommandSemantics,
    _safe_parse,
    argument_arity_is_uncertain,
    has_recursive_force_flags,
    is_shell_runner,
    resolve_command,
    standard_output_reaches_pipeline,
    static_executable_shell_source,
    static_piped_shell_source,
    static_pipeline_output,
    static_standard_input_source,
    static_word_prefix,
)
from openhands.sdk.security.defense_in_depth.utils import _extract_exec_segments
from openhands.sdk.security.risk import SecurityRisk


logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Stable rail IDs -- do not change between releases without documentation.
# ---------------------------------------------------------------------------

RAIL_FETCH_TO_EXEC = "fetch-to-exec"
RAIL_RAW_DISK_OP = "raw-disk-op"
RAIL_CATASTROPHIC_DELETE = "catastrophic-delete"


# ---------------------------------------------------------------------------
# Rail types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RailDecision:
    """Result of a policy rail evaluation.

    ``outcome`` is ``HIGH`` when a rail fires and ``UNKNOWN`` when a relevant
    shell structure cannot be resolved. ``LOW`` means that no configured rail
    matched and no relevant uncertainty remained. It is not a general safety
    or authorization result. ``reason`` preserves observability for logging
    and debugging.
    """

    outcome: SecurityRisk
    rule_name: str = ""
    reason: str = ""


_PASS = RailDecision(outcome=SecurityRisk.LOW)

_FETCH_COMMANDS = frozenset({"curl", "wget"})
_FETCH_OUTPUT_FORWARDERS = frozenset({"echo"})
_CRITICAL_ROOTS = ("/etc", "/usr", "/var", "/home", "/boot")
_WRAPPER_OPTIONS = {
    "env": {
        "-i": 0,
        "--ignore-environment": 0,
        "-u": 1,
        "--unset": 1,
        "-C": 1,
        "--chdir": 1,
    },
    "sudo": {
        "-E": 0,
        "-H": 0,
        "-i": 0,
        "-n": 0,
        "-S": 0,
        "-u": 1,
        "--user": 1,
        "-g": 1,
        "--group": 1,
        "-D": 1,
        "--chdir": 1,
    },
    "builtin": {},
    "command": {"-p": 0, "-v": 0, "-V": 0},
    "time": {"-p": 0},
    "timeout": {
        "--foreground": 0,
        "--preserve-status": 0,
        "--verbose": 0,
        "-k": 1,
        "--kill-after": 1,
        "-s": 1,
        "--signal": 1,
    },
    "exec": {"-a": 1, "-c": 0, "-l": 0},
    "nice": {"-n": 1, "--adjustment": 1},
    "nohup": {},
    "chroot": {"--userspec": 1, "--groups": 1, "--skip-chdir": 0},
    "busybox": {},
    "toybox": {},
    "setsid": {"-f": 0, "--fork": 0, "-c": 0, "--ctty": 0, "-w": 0, "--wait": 0},
    "stdbuf": {"-i": 1, "--input": 1, "-o": 1, "--output": 1, "-e": 1, "--error": 1},
    "strace": {"-f": 0, "-o": 1, "-e": 1, "-p": 1, "-s": 1},
}
_ASSIGNMENT_WRAPPERS = frozenset({"env", "sudo"})
_PREFIX_OPERAND_WRAPPERS = {"timeout": 1, "chroot": 1}
_BUILTIN_WRAPPER_TARGETS = frozenset({"builtin", "command", "exec"})


@dataclass
class _RailSignals:
    fetch_to_exec: bool = False
    raw_disk_reason: str = ""
    catastrophic_delete: bool = False
    uncertain: bool = False


# ---------------------------------------------------------------------------
# Rail evaluation
# ---------------------------------------------------------------------------


def _evaluate_rail_segments(segments: list[str]) -> RailDecision:
    """Evaluate deterministic policy rails against per-segment content.

    Per-segment evaluation prevents cross-field false positives. Fetch-to-exec
    requires fetch output to reach a consumer in one pipeline inside one
    executable segment. Text from separate executable fields cannot form one
    match.
    """
    uncertain: RailDecision | None = None
    for seg in segments:
        decision = _evaluate_segment(seg)
        if decision.outcome == SecurityRisk.HIGH:
            return decision
        if decision.outcome == SecurityRisk.UNKNOWN and uncertain is None:
            uncertain = decision

    return uncertain or _PASS


def _evaluate_segment(segment: str) -> RailDecision:
    program = _safe_parse(segment)
    if program is None:
        return RailDecision(
            SecurityRisk.UNKNOWN,
            reason="Shell segment could not be parsed",
        )

    signals = _RailSignals()
    _scan_program(program, depth=0, signals=signals)

    if signals.fetch_to_exec:
        return RailDecision(
            SecurityRisk.HIGH,
            RAIL_FETCH_TO_EXEC,
            "Network fetch piped to another command",
        )
    if signals.raw_disk_reason:
        return RailDecision(
            SecurityRisk.HIGH,
            RAIL_RAW_DISK_OP,
            signals.raw_disk_reason,
        )
    if signals.catastrophic_delete:
        return RailDecision(
            SecurityRisk.HIGH,
            RAIL_CATASTROPHIC_DELETE,
            "Recursive force-delete targeting critical path",
        )
    if signals.uncertain:
        return RailDecision(
            SecurityRisk.UNKNOWN,
            reason="Shell segment could not be fully evaluated",
        )
    return _PASS


def _scan_program(
    program: ast_view.ShellProgram,
    depth: int,
    signals: _RailSignals,
    inherited_input: tuple[bool, str | None, bool] | None = None,
) -> None:
    commands = []
    for command in ast_view.iter_commands(program):
        resolved = resolve_command(command)
        effective = _resolve_wrappers(resolved)
        if resolved.basename in _WRAPPER_OPTIONS and effective.uncertain:
            signals.uncertain = True
        commands.append((command, effective))
    if _has_unmodeled_compound_source(program, commands):
        signals.uncertain = True
    _scan_fetch_pipelines(program, commands, depth, signals)
    if inherited_input is not None:
        _scan_inherited_shell_input(
            program,
            commands,
            inherited_input,
            depth,
            signals,
        )

    for command, semantics in commands:
        _scan_raw_disk(semantics, signals)
        _scan_catastrophic_delete(semantics, signals)

        has_script, script, script_uncertain = static_executable_shell_source(
            command,
            semantics,
        )
        if script_uncertain:
            signals.uncertain = True
        if not has_script:
            continue
        nested_input = None
        if inherited_input is not None and len(commands) == 1:
            candidate = static_standard_input_source(
                command,
                inherited_input,
                program.parse_result.tree.root_node,
            )
            if candidate[0]:
                nested_input = candidate
        _scan_nested_source(
            script,
            depth,
            signals,
            inherited_input=nested_input,
        )


def _scan_inherited_shell_input(
    program: ast_view.ShellProgram,
    commands: list[tuple[ast_view.ShellCommand, CommandSemantics]],
    source: tuple[bool, str | None, bool],
    depth: int,
    signals: _RailSignals,
) -> None:
    root = program.parse_result.tree.root_node
    executable_sources = []
    for command, semantics in commands:
        has_source, script, uncertain = static_piped_shell_source(
            command,
            semantics,
            source,
            root,
        )
        if uncertain:
            signals.uncertain = True
        if has_source:
            executable_sources.append(script)

    if not executable_sources:
        return
    if len(commands) != 1 or len(executable_sources) != 1:
        signals.uncertain = True
        return
    _scan_nested_source(executable_sources[0], depth, signals)


def _scan_nested_source(
    source: str | None,
    depth: int,
    signals: _RailSignals,
    inherited_input: tuple[bool, str | None, bool] | None = None,
) -> None:
    if source is None or depth >= _MAX_NESTING_DEPTH:
        signals.uncertain = True
        return
    nested = _safe_parse(source)
    if nested is None:
        signals.uncertain = True
        return
    _scan_program(
        nested,
        depth=depth + 1,
        signals=signals,
        inherited_input=inherited_input,
    )


def _resolve_wrappers(command: CommandSemantics) -> CommandSemantics:
    while command.basename in _WRAPPER_OPTIONS:
        if (
            command.basename in {"builtin", "command"}
            and not command.can_invoke_shell_builtin
        ):
            break
        wrapper = command.basename
        options = _WRAPPER_OPTIONS[command.basename]
        index = 0
        while index < len(command.arguments):
            argument = command.arguments[index]
            word = command.argument_words[index]
            if argument_arity_is_uncertain(argument, word):
                return replace(command, uncertain=True)
            if argument is None:
                prefix = static_word_prefix(word)
                if (
                    command.basename in _ASSIGNMENT_WRAPPERS
                    and not prefix.startswith("-")
                    and "=" in prefix
                ):
                    break
                if (
                    prefix.startswith("--")
                    and "=" in prefix
                    and _wrapper_option_value_count(prefix, options) == 0
                ):
                    index += 1
                    continue
                return replace(command, uncertain=True)
            if argument == "--":
                index += 1
                break
            if not argument.startswith("-") or argument == "-":
                break
            value_count = _wrapper_option_value_count(argument, options)
            if value_count is None or index + value_count >= len(command.arguments):
                return replace(command, uncertain=True)
            if wrapper == "command" and any(
                flag in argument[1:] for flag in ("v", "V")
            ):
                return command
            if any(
                argument_arity_is_uncertain(
                    command.arguments[value_index],
                    command.argument_words[value_index],
                )
                for value_index in range(index + 1, index + value_count + 1)
            ):
                return replace(command, uncertain=True)
            index += value_count + 1

        if (
            command.basename == "env"
            and index < len(command.arguments)
            and command.arguments[index] == "-"
        ):
            index += 1

        if command.basename in _ASSIGNMENT_WRAPPERS:
            while index < len(command.arguments):
                argument = command.arguments[index]
                word = command.argument_words[index]
                if argument_arity_is_uncertain(
                    argument,
                    word,
                ):
                    return replace(command, uncertain=True)
                prefix = argument if argument is not None else static_word_prefix(word)
                if "=" not in prefix:
                    if argument is None:
                        return replace(command, uncertain=True)
                    break
                index += 1
        prefix_count = _PREFIX_OPERAND_WRAPPERS.get(command.basename, 0)
        if any(
            argument_arity_is_uncertain(value, word)
            for value, word in zip(
                command.arguments[index : index + prefix_count],
                command.argument_words[index : index + prefix_count],
                strict=True,
            )
        ):
            return replace(command, uncertain=True)
        index += prefix_count

        if index >= len(command.arguments):
            return command
        target = command.arguments[index]
        if target is None or argument_arity_is_uncertain(
            target,
            command.argument_words[index],
        ):
            return replace(command, uncertain=True)
        if wrapper == "builtin" and target not in _BUILTIN_WRAPPER_TARGETS:
            return command
        command = replace(
            command,
            basename=posixpath.basename(target),
            arguments=command.arguments[index + 1 :],
            argument_words=command.argument_words[index + 1 :],
            can_invoke_shell_builtin=(
                command.can_invoke_shell_builtin
                and (
                    wrapper in {"builtin", "command"}
                    or (wrapper == "time" and command.can_invoke_shell_keyword)
                )
                and "/" not in target
            ),
            can_invoke_shell_keyword=(
                command.can_invoke_shell_keyword
                and wrapper == "time"
                and "/" not in target
                and command.argument_words[index].text == target
            ),
        )
    return command


def _wrapper_option_value_count(
    argument: str,
    options: dict[str, int],
) -> int | None:
    value_count = options.get(argument)
    if value_count is not None:
        return value_count
    if argument.startswith("--"):
        option, separator, _ = argument.partition("=")
        if separator and option in options:
            return 0
        return None
    if not argument.startswith("-") or len(argument) < 3:
        return None

    for index, flag in enumerate(argument[1:]):
        value_count = options.get(f"-{flag}")
        if value_count is None:
            return None
        if value_count:
            return 0 if index < len(argument) - 2 else value_count
    return 0


def _scan_fetch_pipelines(
    program: ast_view.ShellProgram,
    commands: list[tuple[ast_view.ShellCommand, CommandSemantics]],
    depth: int,
    signals: _RailSignals,
) -> None:
    command_starts = [command.node.start_byte for command, _ in commands]
    for pipeline in ast_view.iter_pipelines(program):
        stages, stage_commands = _pipeline_stages(
            pipeline,
            commands,
            command_starts,
        )
        _scan_piped_shell_sources(stages, stage_commands, depth, signals)
        for index, source_commands in enumerate(stage_commands[:-1]):
            stage = stages[index]
            has_rhs = any(
                command.name is not None and bool(command.name.text)
                for later_commands in stage_commands[index + 1 :]
                for command, _ in later_commands
            )
            if not has_rhs:
                if (pipeline.has_error or not pipeline.complete) and any(
                    _is_fetch(semantics) for _, semantics in source_commands
                ):
                    signals.uncertain = True
                continue
            direct_command = _direct_stage_command(stage, source_commands)
            for command, semantics in source_commands:
                role = _pipeline_command_role(command.node, stage)
                if role == "captured":
                    continue
                if role == "indirect":
                    if _is_fetch(semantics):
                        forwarding = _forwards_substitution(
                            direct_command,
                            command.node,
                        )
                        if forwarding:
                            signals.fetch_to_exec = True
                            return
                        if forwarding is None:
                            signals.uncertain = True
                    continue
                has_script, _, script_uncertain = static_executable_shell_source(
                    command,
                    semantics,
                )
                if has_script or script_uncertain:
                    signals.uncertain = True
                if _is_fetch(semantics):
                    signals.fetch_to_exec = True
                    return
                if semantics.uncertain:
                    signals.uncertain = True


def _pipeline_stages(
    pipeline: ast_view.ShellPipeline,
    commands: list[tuple[ast_view.ShellCommand, CommandSemantics]],
    command_starts: list[int],
) -> tuple[
    list[Node],
    list[list[tuple[ast_view.ShellCommand, CommandSemantics]]],
]:
    stages = list(pipeline.node.named_children)
    stage_commands = []
    for stage in stages:
        index = bisect_left(command_starts, stage.start_byte)
        group = []
        while index < len(commands):
            pair = commands[index]
            if pair[0].node.start_byte >= stage.end_byte:
                break
            if pair[0].node.end_byte <= stage.end_byte:
                group.append(pair)
            index += 1
        stage_commands.append(group)

    leading_source = _leading_heredoc_pipeline_source(pipeline.node, commands)
    if leading_source is not None:
        stages.insert(0, leading_source[0].node)
        stage_commands.insert(0, [leading_source])
    return stages, stage_commands


def _leading_heredoc_pipeline_source(
    pipeline: Node,
    commands: list[tuple[ast_view.ShellCommand, CommandSemantics]],
) -> tuple[ast_view.ShellCommand, CommandSemantics] | None:
    """Recover a heredoc source kept outside a leading-pipe AST node."""
    if not (pipeline.text or b"").lstrip().startswith(b"|"):
        return None
    redirect = pipeline.parent
    statement = redirect.parent if redirect is not None else None
    if (
        redirect is None
        or redirect.type != "heredoc_redirect"
        or statement is None
        or statement.type != "redirected_statement"
    ):
        return None
    body = statement.child_by_field_name("body")
    if body is None or body.type != "command":
        return None
    return next((pair for pair in commands if pair[0].node == body), None)


def _direct_stage_command(
    stage: Node,
    commands: list[tuple[ast_view.ShellCommand, CommandSemantics]],
) -> tuple[ast_view.ShellCommand, CommandSemantics] | None:
    direct_commands = _direct_stage_commands(stage, commands)
    return direct_commands[0] if direct_commands else None


def _direct_stage_commands(
    stage: Node,
    commands: list[tuple[ast_view.ShellCommand, CommandSemantics]],
) -> list[tuple[ast_view.ShellCommand, CommandSemantics]]:
    return [
        (command, semantics)
        for command, semantics in commands
        if _pipeline_command_role(command.node, stage) == "direct"
        and command.name is not None
    ]


def _scan_piped_shell_sources(
    stages: list[Node],
    stage_commands: list[list[tuple[ast_view.ShellCommand, CommandSemantics]]],
    depth: int,
    signals: _RailSignals,
) -> None:
    for index in range(len(stage_commands) - 1):
        producers = _direct_stage_commands(stages[index], stage_commands[index])
        consumers = _direct_stage_commands(
            stages[index + 1],
            stage_commands[index + 1],
        )
        if not consumers:
            continue
        if len(producers) != 1:
            if any(_could_be_shell_runner(semantics) for _, semantics in consumers):
                signals.uncertain = True
            continue
        producer = producers[0]
        output = static_pipeline_output(
            producer[0],
            producer[1],
            stages[index],
        )
        if not output[0]:
            continue
        if len(consumers) != 1:
            if any(_could_be_shell_runner(semantics) for _, semantics in consumers):
                signals.uncertain = True
            continue
        consumer = consumers[0]
        if not is_shell_runner(consumer[1]) and _could_be_shell_runner(consumer[1]):
            signals.uncertain = True
            continue
        has_source, source, source_uncertain = static_piped_shell_source(
            consumer[0],
            consumer[1],
            output,
            stages[index + 1],
        )
        if source_uncertain:
            signals.uncertain = True
        if not has_source:
            _scan_piped_shell_script(
                consumer,
                output,
                stages[index + 1],
                depth,
                signals,
            )
            continue
        _scan_nested_source(source, depth, signals)


def _scan_piped_shell_script(
    consumer: tuple[ast_view.ShellCommand, CommandSemantics],
    pipeline_source: tuple[bool, str | None, bool],
    stage: Node,
    depth: int,
    signals: _RailSignals,
) -> None:
    has_script, script, script_uncertain = static_executable_shell_source(*consumer)
    if script_uncertain:
        signals.uncertain = True
    if not has_script:
        return
    standard_input = static_standard_input_source(
        consumer[0],
        pipeline_source,
        stage,
    )
    if not standard_input[0]:
        return
    _scan_nested_source(
        script,
        depth,
        signals,
        inherited_input=standard_input,
    )


def _could_be_shell_runner(command: CommandSemantics) -> bool:
    return is_shell_runner(command) or command.uncertain or command.basename is None


def _pipeline_command_role(node: Node, stage: Node) -> str:
    if node == stage:
        return "direct"
    in_substitution = False
    in_assignment = False
    parent = node.parent
    while parent is not None:
        if parent.type == "variable_assignment" and in_substitution:
            in_assignment = True
        if parent.type in {"command_substitution", "process_substitution"}:
            in_substitution = True
        if parent.type == "command":
            if in_assignment and not any(
                child.type == "command_name" for child in parent.named_children
            ):
                return "captured"
            return "indirect"
        if parent == stage:
            if in_assignment:
                return "captured"
            return "indirect" if in_substitution else "direct"
        parent = parent.parent
    return "indirect"


def _forwards_substitution(
    direct: tuple[ast_view.ShellCommand, CommandSemantics] | None,
    nested_node: Node,
) -> bool | None:
    if direct is None:
        return None
    direct_syntax, direct_command = direct
    if direct_command.basename not in _FETCH_OUTPUT_FORWARDERS | {"printf"}:
        return None

    argument_index = next(
        (
            index
            for index, word in enumerate(direct_command.argument_words)
            if word.node.start_byte <= nested_node.start_byte
            and nested_node.end_byte <= word.node.end_byte
        ),
        None,
    )
    if argument_index is None:
        return False
    output_reaches_pipeline = standard_output_reaches_pipeline(direct_syntax)
    if output_reaches_pipeline is not True:
        return output_reaches_pipeline
    if direct_command.basename in _FETCH_OUTPUT_FORWARDERS:
        return True
    if argument_index == 0 or not direct_command.arguments:
        return False
    format_string = direct_command.arguments[0]
    if format_string is None or "\\" in format_string:
        return None
    return _printf_format_consumes_argument(format_string)


def _printf_format_consumes_argument(format_string: str) -> bool:
    index = 0
    while index < len(format_string):
        if format_string[index] != "%":
            index += 1
            continue
        if index + 1 >= len(format_string):
            return False
        if format_string[index + 1] != "%":
            return True
        index += 2
    return False


def _has_unmodeled_compound_source(
    program: ast_view.ShellProgram,
    commands: list[tuple[ast_view.ShellCommand, CommandSemantics]],
) -> bool:
    stack = [program.parse_result.tree.root_node]
    while stack:
        node = stack.pop()
        if node.type == "redirected_statement":
            body = node.child_by_field_name("body")
            if (
                body is not None
                and body.type != "command"
                and (
                    b"<<<" in program.source_bytes[body.end_byte : node.end_byte]
                    or any(
                        _redirect_has_inline_source(child)
                        for child in node.named_children
                        if "redirect" in child.type
                    )
                )
                and any(
                    body.start_byte <= command.node.start_byte
                    and command.node.end_byte <= body.end_byte
                    and is_shell_runner(semantics)
                    for command, semantics in commands
                )
            ):
                return True
        stack.extend(node.named_children)
    return False


def _redirect_has_inline_source(redirect: Node) -> bool:
    text = (redirect.text or b"").decode().lstrip("0123456789")
    if redirect.type in {"heredoc_redirect", "herestring_redirect"} or text.startswith(
        "<<<"
    ):
        return True
    return any(
        _redirect_has_inline_source(child)
        for child in redirect.named_children
        if "redirect" in child.type
    )


def _is_fetch(command: CommandSemantics) -> bool:
    return (command.basename or "").casefold() in _FETCH_COMMANDS


def _scan_raw_disk(command: CommandSemantics, signals: _RailSignals) -> None:
    basename = command.basename.casefold() if command.basename is not None else None
    if basename == "mkfs" or (basename is not None and basename.startswith("mkfs.")):
        signals.raw_disk_reason = "Filesystem format via mkfs"
        return
    for argument, word in zip(command.arguments, command.argument_words, strict=True):
        if argument is None:
            if basename == "dd":
                signals.uncertain = True
            continue
        key, separator, value = argument.partition("=")
        is_device_output = (
            bool(separator) and key.casefold() == "of" and _is_device_path(value)
        )
        if basename == "dd" and is_device_output:
            signals.raw_disk_reason = "Raw disk write via dd"
            return
        if basename == "dd" and argument_arity_is_uncertain(argument, word):
            signals.uncertain = True
        if command.uncertain and (is_device_output or _is_device_path(argument)):
            signals.uncertain = True


def _scan_catastrophic_delete(
    command: CommandSemantics,
    signals: _RailSignals,
) -> None:
    options_ended = False
    has_critical_target = False
    has_unresolved_argument = False
    for argument, word in zip(command.arguments, command.argument_words, strict=True):
        if argument is None:
            has_unresolved_argument = True
            continue
        if argument_arity_is_uncertain(argument, word):
            has_unresolved_argument = True
        if not options_ended and argument == "--":
            options_ended = True
            continue
        if not options_ended and argument.startswith("-") and argument != "-":
            continue
        if _is_critical_target(argument, word):
            has_critical_target = True

    has_flags = has_recursive_force_flags(command)
    is_rm = command.basename is not None and command.basename.casefold() == "rm"
    if not has_critical_target:
        if has_unresolved_argument and has_flags and (is_rm or command.uncertain):
            signals.uncertain = True
        return
    if is_rm and has_flags:
        signals.catastrophic_delete = True
    elif command.uncertain and (has_flags or has_unresolved_argument):
        signals.uncertain = True
    elif is_rm and has_unresolved_argument:
        signals.uncertain = True


def _is_device_path(path: str) -> bool:
    normalized = _normalize_absolute_path(path)
    return normalized is not None and normalized.startswith("/dev/")


def _is_critical_target(path: str, word: ast_view.ShellWord) -> bool:
    folded = path.casefold()
    if word.text.startswith("~"):
        if folded == "~":
            return True
        if folded.startswith("~/"):
            relative = posixpath.normpath(folded[2:])
            if relative in {".", ".."} or relative.startswith("../"):
                return True

    normalized = _normalize_absolute_path(folded)
    if normalized in {"/", "/*"}:
        return True
    if normalized is None:
        return False
    return any(
        normalized == root
        or normalized.startswith(f"{root}/")
        or normalized.startswith(f"{root}*")
        for root in _CRITICAL_ROOTS
    )


def _normalize_absolute_path(path: str) -> str | None:
    if not path.startswith("/"):
        return None
    return "/" + posixpath.normpath(path).lstrip("/")


def _evaluate_rail(content: str) -> RailDecision:
    """Evaluate rails against a single string (convenience wrapper).

    Parses the original shell source so text normalization cannot change its
    execution structure. This matches ``PolicyRailSecurityAnalyzer``.
    """
    return _evaluate_rail_segments([content])


# ---------------------------------------------------------------------------
# PolicyRailSecurityAnalyzer
# ---------------------------------------------------------------------------


class PolicyRailSecurityAnalyzer(SecurityAnalyzerBase):
    """Catch threats defined by shell command structure.

    Named rails use shell syntax to detect dangerous operations, such as a
    network fetch piped to a downstream command. Per-segment evaluation keeps
    the threat model interpretable and prevents text from separate fields from
    forming one match.

    Evaluates original executable segments only -- reasoning text is never
    scanned.

    Returns ``SecurityRisk.HIGH`` when a rail fires and ``UNKNOWN`` when a
    relevant shell structure cannot be resolved. ``LOW`` means that no
    configured rail matched and no relevant uncertainty remained. It does not
    establish general safety or authorization. Pair with ``ConfirmRisky`` and
    compose via ``EnsembleSecurityAnalyzer``.

    v1 rails: fetch-to-exec, raw-disk-op, catastrophic-delete.

    Example::

        from openhands.sdk.security import PolicyRailSecurityAnalyzer

        analyzer = PolicyRailSecurityAnalyzer()
        # risk = analyzer.security_risk(action)
    """

    def security_risk(self, action: ActionEvent) -> SecurityRisk:
        """Evaluate policy rails on original executable segments."""
        rail = _evaluate_rail_segments(_extract_exec_segments(action))
        if rail.outcome != SecurityRisk.LOW:
            logger.debug(
                "Policy rail result: %s (%s) -> %s",
                rail.rule_name or "unresolved-shell",
                rail.reason,
                rail.outcome.name,
            )
        return rail.outcome
