"""Tests for the offline npm typosquat parser.

Covers every install/runner shape, the hardening cases (chaining, env/wrapper
prefixes, quoting, value-flag skips, path prefixes), the consistent flag policy,
and every false-positive guard.
"""

from __future__ import annotations

import dataclasses

import pytest

from openhands.sdk.security._shell_ast import iter_commands, parse_shell_program
from openhands.sdk.security.supply_chain.parser import (
    POPULAR_PACKAGES,
    TyposquatFinding,
    _expand_static_braces,
    _is_env_assignment,
    _normalize_package_name,
    _osa_distance_within,
    find_typosquat_installs,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def assert_flags(command: str, target: str) -> None:
    """Assert a command flags at least one typosquat resembling ``target``."""
    findings = find_typosquat_installs(command)
    assert findings, f'expected "{command}" to be flagged'
    assert any(f.suggestion == target for f in findings), (
        f'expected "{command}" to mention popular package "{target}", '
        f"got: {[f.reason for f in findings]}"
    )


def assert_clean(command: str) -> None:
    """Assert a command flags nothing."""
    findings = find_typosquat_installs(command)
    assert findings == [], (
        f'expected "{command}" to be clean, got: {[f.reason for f in findings]}'
    )


# ---------------------------------------------------------------------------
# npm install detection
# ---------------------------------------------------------------------------


class TestNpmInstallDetection:
    def test_flags_one_edit_typosquat_of_lodash(self):
        assert_flags("npm install lodahs", "lodash")

    def test_flags_typosquats_behind_short_flags(self):
        assert_flags("npm i -D lodahs", "lodash")

    def test_flags_typosquats_quoted_in_double_quotes(self):
        assert_flags('npm install "lodahs"', "lodash")

    def test_flags_with_global_flag_before_subcommand(self):
        assert_flags("npm -g install lodahs", "lodash")

    def test_flags_with_value_taking_flag_before_subcommand(self):
        assert_flags("npm --prefix ui install lodahs", "lodash")

    def test_flags_after_env_assignment(self):
        assert_flags("FOO=bar npm install lodahs", "lodash")

    def test_flags_after_sudo_wrapper(self):
        assert_flags("sudo npm install lodahs", "lodash")

    def test_flags_after_env_wrapper_with_inline_assignment(self):
        assert_flags("env FOO=bar npm install lodahs", "lodash")

    def test_flags_typosquat_of_express(self):
        assert_flags("npm install expres", "express")

    def test_flags_typosquat_of_typescript_missing_letter(self):
        assert_flags("npm install typscript", "typescript")


# ---------------------------------------------------------------------------
# Chained commands
# ---------------------------------------------------------------------------


class TestChainedCommands:
    def test_parses_install_segment_after_and(self):
        # evil is not a typosquat, but it must parse without crash or false hit.
        assert_clean("cd x && npm install evil")

    def test_flags_typosquat_after_and(self):
        assert_flags("cd x && npm install lodahs", "lodash")

    def test_flags_typosquat_after_newline(self):
        assert_flags("cd ui\nnpm install lodahs", "lodash")

    def test_flags_typosquat_after_pipe(self):
        assert_flags("echo hi | npm install lodahs", "lodash")

    def test_flags_typosquat_after_or(self):
        assert_flags("false || npm install lodahs", "lodash")

    def test_flags_typosquat_after_background(self):
        assert_flags("sleep 1 & npm install lodahs", "lodash")


# ---------------------------------------------------------------------------
# Runners (npx / dlx / bunx)
# ---------------------------------------------------------------------------


class TestRunners:
    def test_targets_executed_package_never_trailing_arg(self):
        # create-react-app and my-app are both clean; the invariant is that
        # my-app is never collected as the target.
        assert_clean("npx create-react-app my-app")

    def test_flags_typosquat_runner_target(self):
        assert_flags("npx expres", "express")

    def test_honors_package_equals_for_runner_target(self):
        assert_flags("npx --package=lodahs some-bin", "lodash")

    def test_honors_p_name_for_runner_target(self):
        assert_flags("npx -p lodahs some-bin", "lodash")

    def test_flags_bunx_runner_targets(self):
        assert_flags("bunx expres", "express")

    def test_flags_npm_exec_runner_target(self):
        assert_flags("npm exec expres", "express")

    def test_flags_bun_x_runner_target(self):
        assert_flags("bun x expres", "express")


# ---------------------------------------------------------------------------
# Non-install subcommands are ignored
# ---------------------------------------------------------------------------


class TestNonInstallSubcommands:
    def test_ignores_npm_run_build(self):
        assert_clean("npm run build")

    def test_ignores_bare_npm_install(self):
        assert_clean("npm install")

    def test_ignores_script_named_add(self):
        assert_clean("npm run add lodahs")


# ---------------------------------------------------------------------------
# False-positive guards
# ---------------------------------------------------------------------------


class TestFalsePositiveGuards:
    def test_does_not_flag_legitimate_lodash(self):
        assert_clean("npm install lodash")

    def test_does_not_flag_multiple_legitimate_packages(self):
        assert_clean("npm install react react-dom")

    def test_value_taking_flag_argument_is_not_a_package(self):
        # axio (one edit from axios) is the --prefix value, must NOT flag;
        # lodash is legit, so the command is clean overall.
        assert_clean("npm install --prefix axio lodash")

    def test_does_not_flag_short_popular_names(self):
        # 'vu' is one edit from 'vue' but vue.length == 3, so it must not flag.
        assert_clean("npm install vu")

    def test_does_not_flag_two_edits_away(self):
        assert_clean("npm install lodaaash")

    def test_does_not_flag_unrelated_long_package(self):
        assert_clean("npm install some-internal-tool")

    def test_does_not_flag_registry_value(self):
        # the --registry value must be treated as a value, not a package.
        assert_clean("npm install --registry expres lodash")


# ---------------------------------------------------------------------------
# Known-legit allowlist
#
# Real, mainstream npm packages that happen to sit one OSA edit from a popular
# name must NOT be flagged. The allowlist is precision-only: it suppresses only
# its own exact, registry-confirmed names, so a genuine typosquat that is one
# edit from the SAME popular name (e.g. loadsh/lodahs vs lodash) must still flag.
# ---------------------------------------------------------------------------


class TestKnownLegitAllowlist:
    def test_does_not_flag_preact(self):
        # preact is one edit from react but a real ~86M/month package.
        assert_clean("npm install preact")

    def test_does_not_flag_prettierx(self):
        # prettierx is one edit from prettier but a real published fork.
        assert_clean("npm install prettierx")

    def test_does_not_flag_moments(self):
        # moments is one edit from moment but a real published package.
        assert_clean("npm install moments")

    def test_allowlisted_package_clean_across_managers_and_runners(self):
        # The suppression is on the resolved name, so it holds everywhere a name
        # is collected: every install manager and every runner shape.
        assert_clean("npm install preact")
        assert_clean("yarn add preact")
        assert_clean("pnpm add preact")
        assert_clean("bun add preact")
        assert_clean("npx preact")
        assert_clean("npm install preact@10.29.2")

    def test_allowlist_still_flags_real_typosquats_of_same_popular_name(self):
        # Allowlisting preact must NOT suppress a genuine typosquat of react,
        # nor a genuine typosquat of any other popular name.
        assert_flags("npm install loadsh", "lodash")
        assert_flags("npm install lodahs", "lodash")
        assert_flags("npm install expres", "express")
        assert_flags("npm install momnt", "moment")

    def test_allowlisted_and_typosquat_in_same_command(self):
        # preact is suppressed, lodahs still flags; only the real typosquat hits.
        findings = find_typosquat_installs("npm install preact lodahs")
        assert [f.suggestion for f in findings] == ["lodash"]


# ---------------------------------------------------------------------------
# Consistent flag policy (#3/#4)
#
# One rule across subcommand-skipping, install collection and runner collection:
# a KNOWN value-taking flag written bare consumes its next token; ``--flag=value``
# is self-contained; EVERY other flag (including an unknown long flag) is boolean
# and swallows nothing. This is how npm/yarn/pnpm/bun actually parse, so an
# unknown flag never eats the package that follows it.
# ---------------------------------------------------------------------------


class TestFlagConsistency:
    def test_unknown_install_flag_swallows_no_value(self):
        # --unknownflag is boolean -> axio (one edit from axios) is still a real
        # install target and flags HIGH; lodash is legitimate.
        assert_flags("npm install --unknownflag axio lodash", "axios")

    def test_known_value_flag_before_subcommand_consumes_its_value(self):
        # --registry is value-taking -> http://x is its value (skipped), and the
        # real package lodahs flags as lodash.
        assert_flags("npm install --registry http://x lodahs", "lodash")

    def test_known_value_flag_consumes_a_typosquat_value(self):
        # --before is value-taking -> it consumes lodahs as its value (not a
        # package); axio remains and flags as axios.
        assert_flags("npm install --before lodahs axio", "axios")

    def test_equals_form_value_flag_is_self_contained(self):
        # --omit=dev carries its own value -> lodahs stays a package and flags.
        assert_flags("npm install --omit=dev lodahs", "lodash")

    def test_unknown_global_flag_value_is_skipped_to_reach_install(self):
        # --foo is boolean; 'bar' is the next token but is NOT a real npm
        # subcommand keyword. The robust subcommand scan skips 'bar' and keeps
        # going, reaching the real 'install' so the typosquat behind it (lodahs)
        # is still flagged -- closing the value-flag-before-subcommand gap even
        # for a flag we did not enumerate.
        assert_flags("npm --foo bar install lodahs", "lodash")

    def test_non_subcommand_leading_token_without_install_stays_clean(self):
        # When nothing after the skipped non-subcommand token is a real
        # install/runner subcommand, the scan finds no subcommand and stays LOW.
        assert_clean("npm --foo bar baz lodahs")

    def test_runner_unknown_flag_makes_next_positional_the_target(self):
        # Per npx semantics, --some-flag is boolean, so the FIRST positional
        # 'value' is the executed package (the runner target); 'lodahs' is a
        # trailing argument to that program and is never an install target.
        # 'value' is not a typosquat, so the command is clean. (Policy-pinned.)
        assert_clean("npx --some-flag value lodahs")

    def test_runner_known_value_flag_is_skipped_before_target(self):
        # --registry is value-taking -> http://x skipped, leaving expres as the
        # first positional runner target, which flags as express.
        assert_flags("npx --registry http://x expres", "express")


# ---------------------------------------------------------------------------
# Value-taking flag BEFORE the install subcommand
#
# A real npm/yarn/pnpm/bun value-taking flag written bare before the install
# subcommand consumes its own value; the subcommand and the package behind it
# must still be reached. Previously these mis-routed to LOW because the flags
# were absent from the value-taking set; the expanded set plus the robust
# subcommand scan close the gap.
# ---------------------------------------------------------------------------


class TestValueFlagBeforeSubcommand:
    @pytest.mark.parametrize(
        "command",
        [
            "npm --tag beta install loadsh",
            "npm --audit-level high install loadsh",
            "npm --install-strategy nested install loadsh",
            "npm --scope @myscope install loadsh",
            "npm --otp 123456 install loadsh",
            "npm --registry http://x install loadsh",
            "npm --https-proxy http://p install loadsh",
            "npm --proxy http://p install loadsh",
            "npm --maxsockets 5 install loadsh",
            "npm --fetch-retries 3 install loadsh",
            "npm --fetch-timeout 1000 install loadsh",
            "npm --cafile /tmp/ca.pem install loadsh",
            "npm --ca somecert install loadsh",
            "npm --cert somecert install loadsh",
            "npm --key somekey install loadsh",
            "npm --before 2020-01-01 install loadsh",
            "npm --loglevel silent install loadsh",
            "npm --omit dev install loadsh",
            "npm --include optional install loadsh",
        ],
    )
    def test_npm_value_flag_before_subcommand_flags(self, command: str):
        assert_flags(command, "lodash")

    def test_yarn_cwd_value_flag_before_add(self):
        assert_flags("yarn --cwd /tmp add loadsh", "lodash")

    def test_pnpm_filter_value_flag_before_add(self):
        assert_flags("pnpm --filter foo add loadsh", "lodash")

    def test_pnpm_store_dir_value_flag_before_install(self):
        assert_flags("pnpm --store-dir /s install loadsh", "lodash")

    def test_bun_cwd_value_flag_before_add(self):
        assert_flags("bun --cwd /tmp add loadsh", "lodash")

    def test_unknown_value_flag_backstop_still_reaches_install(self):
        # --someunknownvalflag is NOT enumerated, so its value 'X' is not
        # consumed as a value; but 'X' is not a real subcommand keyword either,
        # so the robust scan skips it and still reaches 'install', flagging the
        # typosquat. This is the version-independent backstop.
        assert_flags("npm --someunknownvalflag X install loadsh", "lodash")

    def test_legacy_peer_deps_is_boolean_not_value_taking(self):
        # --legacy-peer-deps is a Boolean npm flag, so it must NOT consume the
        # following token. loadsh stays a real install target and flags.
        assert_flags("npm install --legacy-peer-deps loadsh", "lodash")

    def test_value_flag_swallows_its_typosquat_value_not_a_package(self):
        # --tag is value-taking, so 'loadsh' is its value (not an install
        # target). The only real package, lodash, is legitimate -> clean.
        assert_clean("npm --tag loadsh install lodash")


# ---------------------------------------------------------------------------
# Manager-aware -w / --workspace value consumption
#
# The SAME spelling is a value flag in one CLI and a boolean switch in another:
# npm ``-w``/``--workspace`` takes a workspace NAME (Type String), but pnpm
# ``-w`` is the boolean ``--workspace-root``, and yarn/bun expose no value-taking
# ``-w`` for ``add`` (yarn ``workspace`` is a subcommand). Treating ``-w`` as
# value-taking everywhere was a false negative: ``pnpm add -w expres`` skipped
# ``expres``. Value consumption is now manager-aware.
#
# The npm-only ``--omit``/``--include`` enum flags are likewise scoped to npm:
# under npm they consume their value; under pnpm/yarn/bun they are boolean.
# ---------------------------------------------------------------------------


class TestManagerAwareWorkspaceFlag:
    def test_pnpm_w_is_boolean_so_package_after_it_flags(self):
        # BUG #5 repro: pnpm '-w' is the boolean --workspace-root, so 'expres'
        # is the install target, not the flag's value. Previously [] (skipped).
        assert_flags("pnpm add -w expres", "express")

    def test_pnpm_w_boolean_typosquat_of_lodash(self):
        assert_flags("pnpm add -w lodahs", "lodash")

    def test_bun_w_is_boolean_so_package_after_it_flags(self):
        # bun 'add' has no value-taking -w, so it consumes nothing.
        assert_flags("bun add -w expres", "express")

    def test_yarn_w_is_boolean_so_package_after_it_flags(self):
        # yarn 'add' has no value-taking -w (workspace is a subcommand), so -w
        # consumes nothing and lodahs is the install target.
        assert_flags("yarn add -w lodahs", "lodash")

    def test_pnpm_long_workspace_root_is_boolean(self):
        assert_flags("pnpm add --workspace-root lodahs", "lodash")

    def test_npm_w_takes_workspace_value_then_flags_package(self):
        # NO REGRESSION: npm -w consumes the workspace value 'pkg'; the real
        # package 'loadsh' still flags.
        assert_flags("npm install -w pkg loadsh", "lodash")

    def test_npm_w_consumes_lone_typosquat_as_workspace_value(self):
        # npm -w is value-taking: with only one token after it, that token is the
        # workspace NAME (not a package), so nothing installs -> clean. This is
        # the correct npm semantics, not a regression.
        assert_clean("npm install -w lodahs")

    def test_npm_long_workspace_takes_value_then_flags_package(self):
        assert_flags("npm install --workspace ui loadsh", "lodash")

    def test_npm_omit_is_value_taking_then_flags_package(self):
        # npm --omit consumes 'dev' (enum value); 'loadsh' still flags.
        assert_flags("npm install --omit dev loadsh", "lodash")

    def test_pnpm_omit_is_boolean_so_package_after_it_flags(self):
        # --omit/--include are npm-only; under pnpm they are boolean and consume
        # nothing, so 'loadsh' is the install target.
        assert_flags("pnpm add --omit loadsh", "lodash")


# ---------------------------------------------------------------------------
# Yarn 'global' transparent prefix wrapper
#
# BUG #6: 'global' is a known yarn subcommand, so the subcommand scan stopped on
# it and never reached the real action ('add'/'remove') behind it. 'yarn global'
# runs that action in the global scope, so it is a transparent prefix: we
# advance past it and re-resolve the real subcommand on the remainder.
# npm/pnpm/bun install globally with a boolean -g/--global flag (already
# handled), not a wrapping subcommand.
# ---------------------------------------------------------------------------


class TestYarnGlobalPrefix:
    def test_yarn_global_add_routes_to_add(self):
        # BUG #6 repro: previously [] because the scan stopped at 'global'.
        assert_flags("yarn global add expres", "express")

    def test_yarn_global_add_typosquat_of_lodash(self):
        assert_flags("yarn global add lodahs", "lodash")

    def test_yarn_global_add_legit_package_is_clean(self):
        assert_clean("yarn global add lodash")

    def test_yarn_global_remove_is_not_an_install_subcommand(self):
        # NO REGRESSION: 'remove' is not an install/runner subcommand, so even
        # the typosquat-shaped argument behind it stays clean.
        assert_clean("yarn global remove lodash")

    def test_yarn_global_remove_typosquat_arg_still_clean(self):
        assert_clean("yarn global remove lodahs")

    def test_yarn_global_add_with_w_boolean_flag(self):
        # Under yarn, -w is boolean (no value), so the package after it flags
        # even behind the global prefix.
        assert_flags("yarn global add -w expres", "express")

    def test_yarn_global_bin_is_not_install(self):
        # 'yarn global bin' is a non-install action -> clean.
        assert_clean("yarn global bin lodahs")


# ---------------------------------------------------------------------------
# Flag value equal to a subcommand keyword
#
# A known value-taking flag must consume its value even when that value spells a
# subcommand keyword: ``npm --tag install install loadsh`` -> ``--tag`` eats the
# first ``install`` (its tag value), and the SECOND ``install`` is the real
# subcommand, so loadsh flags. The flag value is never mistaken for the route.
# ---------------------------------------------------------------------------


class TestFlagValueEqualsSubcommandKeyword:
    def test_tag_value_equal_to_install_keyword_routes_correctly(self):
        assert_flags("npm --tag install install loadsh", "lodash")

    def test_scope_value_equal_to_add_keyword_routes_correctly(self):
        assert_flags("npm --scope add install loadsh", "lodash")

    def test_value_equal_to_keyword_does_not_become_subcommand_when_no_install(
        self,
    ):
        # --tag eats the 'install' value; nothing real follows, so no package is
        # collected and the command stays clean (the eaten 'install' is a value,
        # never a route).
        assert_clean("npm --tag install lodash")


# ---------------------------------------------------------------------------
# Package name normalization
# ---------------------------------------------------------------------------


class TestPackageNameNormalization:
    def test_strips_trailing_version_keeps_name(self):
        assert_flags("npm install lodahs@4.17.21", "lodash")

    def test_does_not_crash_on_scoped_packages_keeps_scope(self):
        assert_clean("npm install @scope/pkg@1.2.3")

    def test_normalize_scoped_version(self):
        assert _normalize_package_name("@scope/pkg@1.2.3") == "@scope/pkg"

    def test_normalize_unscoped_version(self):
        assert _normalize_package_name("lodash@4") == "lodash"

    def test_normalize_scoped_no_version(self):
        assert _normalize_package_name("@scope/pkg") == "@scope/pkg"

    def test_normalize_lowercases(self):
        assert _normalize_package_name("LoDaHs") == "lodahs"


# ---------------------------------------------------------------------------
# Other package managers
# ---------------------------------------------------------------------------


class TestOtherPackageManagers:
    def test_flags_yarn_add_typosquats(self):
        assert_flags("yarn add lodahs", "lodash")

    def test_flags_pnpm_add_typosquats(self):
        assert_flags("pnpm add lodahs", "lodash")

    def test_flags_bun_add_typosquats(self):
        assert_flags("bun add lodahs", "lodash")

    def test_flags_pnpm_dlx_runner_targets(self):
        assert_flags("pnpm dlx expres", "express")

    def test_flags_yarn_dlx_runner_targets(self):
        assert_flags("yarn dlx expres", "express")

    def test_ignores_yarn_add_with_no_packages(self):
        assert_clean("yarn add")

    def test_pnpm_install_short_alias(self):
        assert_flags("pnpm i lodahs", "lodash")


# ---------------------------------------------------------------------------
# Multiple hits
# ---------------------------------------------------------------------------


class TestMultipleHits:
    def test_collects_multiple_typosquats(self):
        findings = find_typosquat_installs("npm install lodahs expres")
        suggestions = {f.suggestion for f in findings}
        assert suggestions == {"lodash", "express"}
        assert len(findings) == 2

    def test_de_duplicates_repeated_typosquat(self):
        findings = find_typosquat_installs("npm install lodahs lodahs")
        assert len(findings) == 1

    def test_de_duplicates_across_sub_commands(self):
        findings = find_typosquat_installs("npm install lodahs && npm install lodahs")
        assert len(findings) == 1


# ---------------------------------------------------------------------------
# Path-prefixed binaries
# ---------------------------------------------------------------------------


class TestPathPrefixedBinaries:
    def test_strips_path_prefix_on_binary(self):
        assert_flags("/usr/bin/npm install lodahs", "lodash")

    def test_strips_path_prefix_on_runner(self):
        assert_flags("/usr/local/bin/npx expres", "express")


# ---------------------------------------------------------------------------
# Empty / non-install commands
# ---------------------------------------------------------------------------


class TestEmptyOrUnrelated:
    def test_clean_for_empty_command(self):
        assert_clean("")

    def test_clean_for_unrelated_command(self):
        assert_clean("git status")

    def test_clean_for_pip_install(self):
        # pip is not an npm-ecosystem manager; out of scope, must not flag.
        assert_clean("pip install requestss")


# ---------------------------------------------------------------------------
# Reason wording
# ---------------------------------------------------------------------------


class TestReasonWording:
    def test_reason_wording(self):
        findings = find_typosquat_installs("npm install lodahs")
        assert len(findings) == 1
        assert findings[0].reason == (
            "`lodahs` is one edit away from the popular package `lodash`"
        )

    def test_finding_is_frozen(self):
        finding = TyposquatFinding(package="lodahs", suggestion="lodash")
        with pytest.raises(dataclasses.FrozenInstanceError):
            finding.package = "x"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# OSA distance unit tests
# ---------------------------------------------------------------------------


class TestOsaDistance:
    @pytest.mark.parametrize(
        "a,b,expected",
        [
            ("lodash", "lodash", 0),
            ("lodahs", "lodash", 1),  # adjacent transposition
            ("expres", "express", 1),  # deletion
            ("typscript", "typescript", 1),  # insertion
            ("axio", "axios", 1),  # deletion
            ("lodaaash", "lodash", 2),  # two edits -> capped to 2 (> max)
        ],
    )
    def test_distance_values(self, a: str, b: str, expected: int):
        assert _osa_distance_within(a, b, 1) == expected

    def test_length_gap_short_circuits(self):
        # Length difference exceeds max -> returns max + 1 without full DP.
        assert _osa_distance_within("a", "abcdef", 1) == 2


# ---------------------------------------------------------------------------
# Sub-command traversal (now via the tree-sitter AST view).
#
# The hand-rolled _split_sub_commands char scanner was replaced by
# iter_commands over the parsed shell tree; these assertions pin the resulting
# behavior end-to-end instead of poking at the deleted splitter.
# ---------------------------------------------------------------------------


class TestSubCommandTraversal:
    def test_quoted_operators_are_not_separators(self):
        # The ';' lives inside a double-quoted echo arg; the install after &&
        # is still surfaced, and the quoted ';' does not create a spurious one.
        assert_flags('echo "a ; b" && npm install lodahs', "lodash")

    def test_double_operator_chaining(self):
        # Both halves are parsed; the install after && is flagged.
        assert_flags("true && npm install lodahs", "lodash")

    def test_semicolon_separated_commands(self):
        assert_flags("echo done; npm i lodahs", "lodash")

    def test_repeated_empty_separators_are_harmless(self):
        assert_flags(";;; npm install lodahs ;;;", "lodash")

    def test_or_separated_commands(self):
        assert_flags("cat foo || npm install lodahs", "lodash")

    def test_background_separated_commands(self):
        assert_flags("sleep 1 & npm install lodahs", "lodash")


# ---------------------------------------------------------------------------
# AST structural cases (the correctness upgrade over the char scanner).
# ---------------------------------------------------------------------------


class TestAstStructural:
    def test_path_qualified_manager_via_posix_basename(self):
        assert_flags("/bin/npm install loadsh", "lodash")

    def test_collapsed_odd_spacing(self):
        assert_flags("npm install   loadsh", "lodash")

    def test_single_quote_raw_string_recovery(self):
        assert_flags("npm install 'loadsh'", "lodash")

    def test_double_quote_string_recovery(self):
        assert_flags('npm install "loadsh"', "lodash")

    def test_command_substitution_nested_command_surfaced(self):
        # iter_commands descends into $(...), so the inner install is scanned.
        assert_flags("echo $(npm install loadsh)", "lodash")

    def test_runner_via_ast(self):
        assert_flags("npx loadsh", "lodash")

    def test_whole_install_inside_double_quoted_string_is_inert(self):
        # The entire install is one echo argument (a string), not a command.
        assert_clean('echo "npm install lodahs"')

    def test_bash_dash_c_inner_string_is_opaque(self):
        # Known coverage limitation: the inner -c string is an opaque argument
        # to bash and is not surfaced as a command by the AST view.
        assert_clean('bash -c "npm install lodahs"')

    def test_opaque_outer_command_name_is_skipped(self):
        # The outer command name $(echo npm) is opaque -> that command is
        # skipped; the inner 'echo npm' yields no manager. Clean overall.
        assert_clean("$(echo npm) install lodahs")

    def test_parse_error_still_yields_operand(self):
        # Unterminated quote -> program has_error, but tree-sitter recovers and
        # the lodahs operand is still surfaced.
        assert_flags('npm install lodahs "unterminated', "lodash")


# ---------------------------------------------------------------------------
# Interior-quote / backslash obfuscation of the operand name
#
# A real shell collapses interior quoting and backslash escapes inside one
# argv token: `npm install lo""adsh` runs `npm install loadsh`. The bounded
# operand-literal decode reconstructs that single token and re-admits it only
# when it matches a strict package-spec shape, so the obfuscated typosquats
# below now flag while runtime expansions and embedded spaces stay opaque.
# ---------------------------------------------------------------------------


class TestOperandObfuscation:
    @pytest.mark.parametrize(
        "command,desc",
        [
            ('npm install lo""adsh', "empty double-quote in the middle"),
            ('npm install load"sh"', "trailing double-quoted segment"),
            ("npm install lo'adsh'", "trailing single-quoted raw_string segment"),
            ("npm install lo\\adsh", "single backslash escape"),
            ('npm install l"o"a"d"s"h"', "every other char double-quoted"),
            ("npm install lo'a'dsh", "interior single-quoted segment"),
            ("npm install lo''adsh", "empty single-quote in the middle"),
            ("npm install l\\o\\a\\d\\s\\h", "every char backslash-escaped"),
            ('npm install "load"sh', "leading double-quoted then bare concat"),
            ("npm install 'load'sh", "leading single-quoted then bare concat"),
            ("npm i lo\\adsh", "obfuscation behind the i alias"),
            ("npx lo\\adsh", "obfuscated runner target"),
            ("sudo npm install lo''adsh", "obfuscation behind a wrapper"),
        ],
        ids=lambda v: v if " " not in v else None,
    )
    def test_obfuscated_operand_is_decoded_and_flagged(self, command: str, desc: str):
        assert_flags(command, "lodash")

    @pytest.mark.parametrize(
        "command,desc",
        [
            ("npm install $(echo loadsh)", "command substitution stays opaque"),
            ("npm install `echo loadsh`", "backticks stay opaque"),
            ("npm install $CMD", "bare variable stays opaque"),
            ("npm install lo$xadsh", "interior variable stays opaque"),
            ("npm install lo*dsh", "glob stays opaque"),
            ('npm install "a b"', "embedded space is not a single package name"),
            ('npm install lo"$x"adsh', "variable inside a quoted segment is opaque"),
            (
                'npm install lo"$(id)"adsh',
                "command substitution inside a quoted segment is opaque",
            ),
        ],
        ids=lambda v: v if " " not in v else None,
    )
    def test_runtime_or_split_operand_stays_opaque(self, command: str, desc: str):
        assert_clean(command)

    def test_exact_popular_name_with_interior_quotes_stays_clean(self):
        # Decoding must not turn a legitimate popular package into a hit just
        # because it was quoted: `lo""dash` decodes to the popular `lodash`.
        assert_clean('npm install lo""dash')

    def test_scoped_name_via_single_quotes_is_recovered_not_flagged(self):
        # A quoted scoped name decodes to @scope/pkg and is a valid spec, but it
        # is not a typosquat, so the command stays clean (proves decode + the
        # strict spec accept scoped/@version shapes without false positives).
        assert_clean("npm install '@scope/pkg'")


# ---------------------------------------------------------------------------
# Command-NAME obfuscation
#
# A real shell collapses interior quoting, backslash escapes and ANSI-C quoting
# in the COMMAND NAME too: `n"p"m install loadsh`, `\npm ...`, `$'npm' ...` all
# exec the binary `npm`. The same bounded literal decode used for operands is
# applied to the command-name word (then the POSIX path prefix is stripped)
# before dispatch, so these obfuscated managers/runners are recognized. A
# runtime expansion in the name (`$CMD`, `$(echo npm)`) stays opaque and LOW.
# ---------------------------------------------------------------------------


class TestCommandNameObfuscation:
    @pytest.mark.parametrize(
        "command,desc",
        [
            ('n"p"m install loadsh', "interior double-quote in npm"),
            ("n'p'm install loadsh", "interior single-quote in npm"),
            (r"\npm install loadsh", "leading backslash escape"),
            (r"n\p\m install loadsh", "interior backslash escapes"),
            ("$'npm' install loadsh", "ANSI-C quoted npm"),
            ('np"m" install loadsh', "trailing double-quoted segment"),
            ("/usr/''bin/npm install loadsh", "path-qualified with empty quote"),
            ("sudo $'npm' install loadsh", "ANSI-C name behind a wrapper"),
        ],
        ids=lambda v: v if " " not in v else None,
    )
    def test_obfuscated_manager_name_is_decoded_and_flagged(
        self, command: str, desc: str
    ):
        assert_flags(command, "lodash")

    @pytest.mark.parametrize(
        "command,desc",
        [
            ('n"p"x loadsh', "interior double-quote in npx runner"),
            (r"\npx loadsh", "backslash-escaped npx runner"),
            ("$'npx' loadsh", "ANSI-C quoted npx runner"),
        ],
        ids=lambda v: v if " " not in v else None,
    )
    def test_obfuscated_runner_name_is_decoded_and_flagged(
        self, command: str, desc: str
    ):
        assert_flags(command, "lodash")

    @pytest.mark.parametrize(
        "command,desc",
        [
            ("$CMD install loadsh", "bare variable command name stays opaque"),
            ("$(echo npm) install loadsh", "command substitution name stays opaque"),
            ("${NPM} install loadsh", "brace-expansion name stays opaque"),
            ("`echo npm` install loadsh", "backtick name stays opaque"),
        ],
        ids=lambda v: v if " " not in v else None,
    )
    def test_runtime_command_name_stays_opaque(self, command: str, desc: str):
        assert_clean(command)

    def test_obfuscated_name_of_popular_stays_clean(self):
        # Decoding the name must not invent a hit: `$'npm' install lodash`
        # installs the popular package, so the command stays clean.
        assert_clean("$'npm' install lodash")

    def test_obfuscated_non_manager_name_stays_clean(self):
        # An obfuscated name that decodes to a non-manager binary must not be
        # routed into install collection.
        assert_clean("$'echo' npm install loadsh")


# ---------------------------------------------------------------------------
# ANSI-C ($'...') operand decoding
#
# bash decodes `$'...'` escapes before passing the argument: `$'loadsh'` is the
# token `loadsh`, and `$'loa\x64sh'` is also `loadsh` (\x64 == 'd'). The literal
# decoder resolves the common ANSI-C escapes and re-admits the result only when
# it matches the strict package-spec shape, so a control char or space stays
# opaque (LOW).
# ---------------------------------------------------------------------------


class TestAnsiCOperandDecoding:
    @pytest.mark.parametrize(
        "command,desc",
        [
            ("npm install $'loadsh'", "plain ANSI-C operand"),
            (r"npm install $'loa\x64sh'", "hex escape backslash-x64 -> d"),
            (r"npm install $'loa\144sh'", "octal escape backslash-144 -> d"),
            (r"npm install $'loa\u0064sh'", "unicode escape backslash-u0064 -> d"),
            (r"npm install $'\x6coadsh'", "leading hex escape backslash-x6c -> l"),
            ("npx $'loadsh'", "ANSI-C runner target"),
            (r"npm install lo$'a'dsh", "ANSI-C segment concatenated with bare"),
        ],
        ids=lambda v: v if " " not in v else None,
    )
    def test_ansi_c_operand_is_decoded_and_flagged(self, command: str, desc: str):
        assert_flags(command, "lodash")

    @pytest.mark.parametrize(
        "command,desc",
        [
            (r"npm install $'a b'", "embedded space is not one package name"),
            (r"npm install $'lo\tadsh'", "embedded tab control char stays opaque"),
            (r"npm install $'lo\nadsh'", "embedded newline stays opaque"),
        ],
        ids=lambda v: v if " " not in v else None,
    )
    def test_ansi_c_operand_with_metachar_stays_opaque(self, command: str, desc: str):
        assert_clean(command)

    def test_ansi_c_of_popular_stays_clean(self):
        # `$'lodash'` decodes to the popular package -> not a typosquat.
        assert_clean("npm install $'lodash'")


# ---------------------------------------------------------------------------
# Backslash-newline line continuation
#
# bash joins an unquoted backslash-newline into nothing before tokenizing, so
# `npm install loa\<newline>dsh` runs `npm install loadsh`. tree-sitter-bash
# does not fold it, so the join is done at the source level before parsing,
# matching the shell -- but a backslash-newline INSIDE single quotes is literal
# in bash and is preserved (so it does NOT become `loadsh`).
# ---------------------------------------------------------------------------


class TestLineContinuation:
    def test_line_continuation_in_operand_joins_to_package(self):
        assert_flags("npm install loa\\\ndsh", "lodash")

    def test_line_continuation_mid_operand_joins(self):
        assert_flags("npm install lo\\\nadsh", "lodash")

    def test_line_continuation_in_command_name_joins(self):
        assert_flags("np\\\nm install loadsh", "lodash")

    def test_crlf_line_continuation_joins(self):
        assert_flags("npm install loa\\\r\ndsh", "lodash")

    def test_line_continuation_does_not_merge_separate_commands(self):
        # A bare newline (no backslash) still separates commands; only the
        # backslash-newline is removed.
        assert_flags("cd ui\nnpm install lodahs", "lodash")

    def test_backslash_newline_inside_single_quotes_is_literal(self):
        # bash keeps the backslash-newline literal inside single quotes, so the
        # package is literally `loa\<newline>dsh`, never `loadsh` -- stays clean.
        assert_clean("npm install 'loa\\\ndsh'")

    def test_backslash_newline_inside_double_quotes_joins(self):
        # bash DOES remove a backslash-newline inside double quotes, so
        # `npm install "loa\<newline>dsh"` runs `npm install loadsh`.
        assert_flags('npm install "loa\\\ndsh"', "lodash")

    def test_apostrophe_in_double_quoted_arg_does_not_desync_continuation(self):
        # An apostrophe inside a double-quoted arg (`"a'b"`) is a LITERAL quote,
        # not a single-quote opener. The old joiner tracked only single-quote
        # state and flipped on this `'`, so the following real backslash-newline
        # continuation was wrongly treated as inside single quotes and left
        # unjoined -- a false negative. The typosquat must now be flagged.
        quote = chr(39)
        command = "npm install " + quote.join(['"a', 'b"']) + " loa\\\ndsh"
        assert command == 'npm install "a' + chr(39) + 'b" loa\\\ndsh'
        assert_flags(command, "lodash")

    def test_apostrophe_in_comment_does_not_desync_continuation(self):
        # An apostrophe inside a `# comment` is consumed by the comment, not a
        # single-quote opener. The old single-quote-only joiner flipped on this
        # `'`, desyncing the state for the NEXT command's backslash-newline
        # continuation and hiding the typosquat. Both must now be flagged.
        quote = chr(39)
        command = "npm install pkg # don" + quote + "t\nnpm install loa\\\ndsh"
        assert (
            command == "npm install pkg # don" + chr(39) + "t\nnpm install loa\\\ndsh"
        )
        assert_flags(command, "lodash")

    def test_double_quote_inside_single_quotes_is_literal(self):
        # A `"` inside single quotes must NOT open a double-quote span; the
        # backslash-newline inside the single-quoted arg stays literal -> clean.
        assert_clean("npm install 'a\"b loa\\\ndsh'")


# ---------------------------------------------------------------------------
# Encoding-evasion normalization at the parser entry
#
# The public entry point normalizes before parsing, stripping invisible /
# zero-width / line-separator code points and NFKC-folding fullwidth glyphs.
# These regressions pin that the parser itself (not just the analyzer) defeats
# the evasion -- including U+0085/U+2028/U+2029, which previously split a
# package name when splitlines() ran before invisibles were stripped.
# ---------------------------------------------------------------------------


class TestEncodingEvasionNormalization:
    @pytest.mark.parametrize(
        "command,desc",
        [
            ("npm install lod\u0085ahs", "U+0085 NEL line separator split"),
            ("npm install lod\u2028ahs", "U+2028 line separator split"),
            ("npm install lod\u2029ahs", "U+2029 paragraph separator split"),
            ("npm install lod\u200bahs", "zero-width space split"),
            ("npm install load\u200bsh", "zero-width split of loadsh"),
            ("ｎｐｍ install lodahs", "fullwidth npm folds to ascii"),
        ],
        ids=lambda v: v if " " not in v else None,
    )
    def test_invisible_or_fullwidth_evasion_is_flagged(self, command: str, desc: str):
        assert_flags(command, "lodash")

    def test_normalize_keeps_newlines_as_command_boundaries(self):
        # A real newline must survive normalization so the install after it is a
        # separate command the AST view surfaces.
        assert_flags("cd ui\nnpm install lodahs", "lodash")

    def test_normalize_does_not_invent_typosquats(self):
        # Legitimate lodash with an embedded zero-width still decodes to lodash
        # (popular) -- it must stay clean, not become a hit.
        assert_clean("npm install lod​ash")


# ---------------------------------------------------------------------------
# Env-assignment unit tests
# ---------------------------------------------------------------------------


class TestEnvAssignment:
    @pytest.mark.parametrize(
        "token,expected",
        [
            ("FOO=bar", True),
            ("_X=1", True),
            ("A1_B=v", True),
            ("=bar", False),
            ("1FOO=bar", False),
            ("FO-O=bar", False),
            ("npm", False),
        ],
    )
    def test_env_assignment(self, token: str, expected: bool):
        assert _is_env_assignment(token) is expected


# ---------------------------------------------------------------------------
# Data integrity
# ---------------------------------------------------------------------------


class TestDataIntegrity:
    def test_no_popular_package_flags_itself(self):
        # Every curated popular name must be treated as legitimate, never a
        # typosquat of another popular name.
        for pkg in POPULAR_PACKAGES:
            assert_clean(f"npm install {pkg}")


# ---------------------------------------------------------------------------
# Static comma-brace expansion of the operand name
#
# bash expands a comma-brace word textually before exec, so `npm install
# lo{a,}dsh` actually runs `npm install loadsh lodsh` (two argv words). Without
# expansion that word parses as one opaque concatenation and the `loadsh`
# typosquat slips through. The bounded expander below decodes ONLY the pure
# static comma-brace form (no ranges, no nesting, no quotes, no runtime element),
# checks every expansion against the typosquat heuristic, and caps total
# expansions so a crafted word cannot blow up.
# ---------------------------------------------------------------------------


def _brace_word(command: str):
    """Return the first command word whose text contains a brace, for unit tests."""
    program = parse_shell_program(command)
    for cmd in iter_commands(program):
        for word in cmd.words:
            if "{" in word.text:
                return word
    raise AssertionError(f"no brace word found in {command!r}")


class TestBraceExpansion:
    @pytest.mark.parametrize(
        "command,desc",
        [
            ("npm install lo{a,}dsh", "reviewer case: loadsh alternative"),
            ("npm install {axio,lodash}", "first alt axio is a typosquat of axios"),
            ("npm i {react,lodahs}", "second alt lodahs is a typosquat of lodash"),
            ("npm install lod{a,}sh", "lodash core with empty alt yields lodash+lodsh"),
            ("npx {lodahs,foo}", "runner first positional brace target"),
            ("yarn add {expres,foo}", "yarn add brace alternative typosquat"),
        ],
        ids=lambda v: v if " " not in v else None,
    )
    def test_static_brace_alternative_is_expanded_and_flagged(
        self, command: str, desc: str
    ):
        # Each command has at least one brace alternative one edit off a popular
        # package; expansion must surface it.
        findings = find_typosquat_installs(command)
        assert findings, f'expected "{command}" ({desc}) to be flagged'

    def test_reviewer_case_flags_loadsh_for_lodash(self):
        # The exact reviewer report: `npm install lo{a,}dsh` -> bash argv
        # `loadsh lodsh`; `loadsh` is one edit from `lodash`.
        assert_flags("npm install lo{a,}dsh", "lodash")
        findings = find_typosquat_installs("npm install lo{a,}dsh")
        assert any(f.package == "loadsh" for f in findings), (
            f"expected the loadsh expansion, got {[f.package for f in findings]}"
        )

    @pytest.mark.parametrize(
        "command,desc",
        [
            ("npm install a{x,y}b", "neither axb nor ayb resembles a popular name"),
            ("npm install pre{,fix}", "pre/prefix are not typosquats"),
            ("npm install {react,lodash}", "both alternatives are legitimate popular"),
            ("npm install {vue,react}{,-dom}", "vue/react/react-dom all popular"),
        ],
        ids=lambda v: v if " " not in v else None,
    )
    def test_benign_brace_expansion_stays_clean(self, command: str, desc: str):
        assert_clean(command)

    @pytest.mark.parametrize(
        "command,desc",
        [
            ("npm install lo{a,$X}dsh", "runtime variable inside brace stays opaque"),
            ("npm install pkg{1..3}", "numeric range is out of scope"),
            ("npm install lo{a,{b,c}}dsh", "nested brace is out of scope"),
            ('npm install "lo{a,}dsh"', "quoted brace is not expanded by bash"),
            ('npm install lo{a,}d"s"h', "brace touching a quote stays opaque"),
            ("npm install lo\\{a,\\}dsh", "escaped braces are literal, not expanded"),
            ("npm install {abc}sh", "comma-less brace is literal in bash"),
        ],
        ids=lambda v: v if " " not in v else None,
    )
    def test_out_of_scope_brace_forms_stay_low(self, command: str, desc: str):
        # These would each expand to a typosquat IF we handled them, but they are
        # deliberately out of scope; the analyzer must stay LOW, not guess.
        assert_clean(command)

    def test_runtime_brace_does_not_invent_a_typosquat(self):
        # `lo{a,$X}dsh` could be `loadsh` at runtime, but $X is unknowable; we
        # must not flag the static `loa...` alternative as if it were the value.
        assert_clean("npm install lo{a,$X}dsh")

    def test_expansion_cap_bails_instead_of_blowing_up(self):
        # 4 * 4 * 4 = 64 combinations exceeds the 32 cap, so the expander returns
        # None (treated as opaque) rather than materializing 64 tokens.
        word = _brace_word("x p{a,b,c,d}{a,b,c,d}{a,b,c,d}sh")
        assert _expand_static_braces(word) is None
        # And end to end it stays clean (no blow-up, no finding).
        assert_clean("npm install p{a,b,c,d}{a,b,c,d}{a,b,c,d}sh")

    def test_group_count_cap_bails(self):
        # Nine expanding groups exceeds the 8-group cap -> opaque.
        word = _brace_word("x " + "{a,b}" * 9)
        assert _expand_static_braces(word) is None

    def test_expansion_matches_bash_order_and_values(self):
        # Cartesian product across two groups, in bash's order.
        word = _brace_word("x {a,b}{c,d}")
        assert _expand_static_braces(word) == ["ac", "ad", "bc", "bd"]

    def test_plain_word_is_not_treated_as_brace(self):
        # A word with no brace must return None so the normal single-token
        # recovery path runs unchanged.
        word = _brace_word("x lod{a,}sh")  # has brace, sanity that helper works
        assert _expand_static_braces(word) == ["lodash", "lodsh"]


# ---------------------------------------------------------------------------
# Pathologically nested / chained input (DoS hardening)
#
# The recursive tree-sitter-bash walkers in the shared `_shell_ast` view descend
# per nested `$()` level and per chained operator, so hundreds of either can
# exhaust the Python recursion stack and raise RecursionError. `find_typosquat_
# installs` must NEVER let that escape: it catches the RecursionError (and
# short-circuits absurdly large commands) and returns [] -- "no finding" -- so a
# crafted command can only ever fail to be analyzed, not crash the seam. This is
# a documented limitation, not a guess: such adversarial noise stays clean.
# ---------------------------------------------------------------------------


class TestPathologicalInputDoesNotCrash:
    def test_deeply_chained_operators_return_empty_without_raising(self):
        # 1500 `&&`-chained commands build a deep right-leaning parse tree.
        command = " && ".join("ls" for _ in range(1500))
        # No exception, and nothing flagged (none of these is an install).
        assert find_typosquat_installs(command) == []

    def test_deeply_nested_command_substitution_returns_empty(self):
        # 200 levels of `$( ... )` nesting; the walkers would otherwise recurse
        # past the stack limit. Must return [] without raising.
        command = "x" + "$(" * 200 + "echo" + ")" * 200
        assert find_typosquat_installs(command) == []

    def test_absurdly_large_command_is_short_circuited(self):
        # Beyond _MAX_COMMAND_LENGTH the command is skipped as "no finding"
        # before parsing, so even a huge input cannot blow the walkers.
        command = "ls " * 50_000  # well over the 100k-char guard
        assert find_typosquat_installs(command) == []

    def test_moderately_chained_real_install_still_flags(self):
        # The guard must NOT drop a legitimate long command: a real typosquat
        # install behind dozens of chained steps is still flagged HIGH.
        prefix = " && ".join(f"echo step{i}" for i in range(50))
        command = f"{prefix} && npm install loadsh"
        findings = find_typosquat_installs(command)
        assert any(f.suggestion == "lodash" for f in findings), (
            f"expected lodash typosquat, got {[f.reason for f in findings]}"
        )

    def test_real_install_in_short_chain_still_flags(self):
        # Sanity that the try/except wrapper did not change normal behavior.
        assert_flags("true && npm i loadsh", "lodash")
        assert_flags("a && b && npm install loadsh", "lodash")
