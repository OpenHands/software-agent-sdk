# Issue #4957 technical design

## Problem and current behavior

`TerminalSession.execute()` calls `split_bash_commands()` before sending a Unix shell command to the persistent terminal. The validator rejects more than one returned element so an LLM cannot submit unrelated newline-separated shell actions in one tool call. Explicit Bash operators (`;`, `&&`, `||`, `|`, and `&`) keep commands in one element.

The splitter already uses the shared tree-sitter-bash parser introduced by #3237 and centralized by #3578. The bug is in boundary classification, not parsing. tree-sitter represents a heredoc as a top-level `redirected_statement` whose byte range ends at the heredoc terminator. A command after that terminator is another root statement, and the current generic “newline between root statements” rule splits there. This rejects a syntactically valid submitted shell script such as:

```bash
cd /repo && command; python - <<'PY'
print('one')
PY
next-command; python - <<'PY'
print('two')
PY
```

Issue #2181/#2182 addressed PTY write buffering for long heredocs, not validation. Issue #2721 led to the tree-sitter parser work, so no new parser or dependency is needed. No open PR duplicates #4957.

## Contract: one tool action versus one shell script

The terminal tool accepts one action per tool call. On Unix, that action may be one syntactically valid Bash script containing explicitly chained commands. The preflight splitter remains a shape guard: ordinary independent top-level commands separated only by newlines remain multiple actions and are rejected.

A completed heredoc changes the lexical layout. Its body and terminator must occupy following lines even though operators and redirections that belong to the command occur on its opening line. A newline immediately after a completed top-level heredoc is therefore classified as part of the submitted heredoc script rather than as a generic action boundary. This exception may coalesce multiple sequential heredocs and commands after their terminators. It does not globally permit arbitrary newline-separated commands: inputs without a completed top-level heredoc preserve the current rejection behavior.

“Multiple tool actions” remains unsupported: this change does not execute multiple `TerminalAction` objects concurrently or bypass the tool framework’s resource serialization. It only stops dividing one Bash script at heredoc layout boundaries.

## Bash syntax classification

| Construct | Classification |
|---|---|
| Unquoted heredoc (`<<EOF`) | One script through the completed `heredoc_end`; a following top-level statement remains attached. |
| Quoted delimiter (`<<'EOF'`, `<<"EOF"`) | Same; tree-sitter identifies `heredoc_end` structurally, without interpreting delimiter text. |
| Indented heredoc (`<<-EOF`) | Same; tab-indented body and terminator are represented by the same AST node types. |
| Multiple sequential heredocs | Accepted as one script; each completed heredoc suppresses the otherwise generic newline split after its terminator. |
| Command after a terminator | Accepted in the same submitted script, including the reported sequence. |
| Heredoc in command substitution | Already one root statement because `command_substitution` is nested; no special boundary is introduced. |
| Heredoc feeding/inside a pipeline | Already one redirected/pipeline root statement; remains one script. |
| `&&`, `;`, `||`, `|`, `&` | Existing behavior remains: operators structurally join commands into one top-level Bash statement. Operators on a line after a heredoc terminator are invalid Bash and continue through the existing parse-error fallback rather than being specially accepted. |
| Here-string (`<<<`) | Not a heredoc: it has `herestring_redirect`, no `heredoc_end`, and receives no exception. A following bare newline still splits/rejects. |
| Incomplete/malformed heredoc | Existing parse-error fallback remains unchanged. The validator does not invent a terminator or partially classify malformed input. |
| Ordinary newline-separated commands | Still split and rejected. |

## Architecture and implementation

Keep `split_bash_commands()` and its shared tree-sitter parse. Replace only the boundary predicate:

1. Iterate the existing top-level named statements.
2. A newline between adjacent statements normally creates a split boundary.
3. Do not create that boundary when the preceding statement structurally contains a `heredoc_end` that reaches the statement’s end.

Use an AST walk/helper rather than delimiter regexes or source masking. This handles quoted delimiters, `<<-`, nested redirected constructs, arbitrary body text, and multiple heredocs according to the parser grammar. It also avoids adding another shell parser abstraction when #2721/#3237/#3578 already established tree-sitter as the shared parser.

The helper should be small and local to terminal command splitting because this is terminal action-boundary policy, not a general security parser feature.

## Security and confirmation invariants

`split_bash_commands()` is not the command security analyzer. Security analysis and confirmation operate on the complete `TerminalAction` before tool execution; this change does not alter or truncate the action command, its declared risk, analyzer input, confirmation policy, or execution command. Every command before, inside, and after a heredoc remains visible in the original action string to those layers.

The validator must not globally accept arbitrary unchained newline injection. Negative tests will retain rejection for ordinary newline-separated commands and for a here-string followed by a bare-newline command. The new allowance is tied to a completed structural `heredoc_end`, not user-controlled delimiter matching via regex.

Malformed parse behavior remains conservative and backward compatible: the current parser-error fallback returns the original input as one element. Changing that policy is outside #4957 and could affect existing recovery/security behavior.

## Compatibility and platforms

- Public APIs and schemas do not change.
- Existing valid commands and ordinary multi-command rejection are unchanged.
- The behavioral change is intentionally more permissive only for completed Bash heredoc scripts.
- Unix terminal backends share this validator, so subprocess and tmux gain the fix.
- PowerShell uses separate parsing/execution behavior; Bash heredoc grammar is not applied there.
- No dependency, packaging, persisted-state, REST, TypeScript, or agent-server contract changes are required.

## Test and evidence plan

1. Add table-driven parser tests for unquoted/quoted/`<<-` heredocs, pipelines, command substitutions, multiple sequential heredocs, commands after terminators, here-strings, and ordinary newlines.
2. Add a real `TerminalSession.execute()` regression using the reported multi-heredoc shape and verify output/artifacts, without mocks.
3. Preserve negative integration coverage showing ordinary newline-separated commands are rejected.
4. Before implementation, run the new focused tests on `origin/main` and capture the expected failure/rejection.
5. After implementation, run focused parser/session tests, the terminal suite, relevant SDK security tests, pre-commit/lint/type checks, and the broadest practical suite.
6. Run the same real terminal entry-point script on base and head, recording base rejection and head execution.
