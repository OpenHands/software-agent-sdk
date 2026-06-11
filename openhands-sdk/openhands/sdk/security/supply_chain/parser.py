"""Offline, deterministic detection of npm-ecosystem typosquat installs.

Given a shell command an agent is about to run, this module decides whether
it installs (or executes via a package runner) an npm package whose name is
one edit away from a popular package, e.g. ``lodahs`` for ``lodash``. That is
the classic typosquat supply-chain attack: the agent fat-fingers a name or an
attacker plants a near-name package, and the wrong dependency gets installed.

The check is pure: no network, no filesystem, no new dependencies beyond the
tree-sitter-bash view already used elsewhere in the SDK. The typosquat decision
(the OSA distance, the popular-package corpus) and the install/runner subcommand
routing are computed here; the extraction layer runs on a shared
tree-sitter-bash command view (``openhands.sdk.security._shell_ast``) rather than
a hand-rolled char scanner, so command chaining, pipes, path-qualified managers
and subshells parse structurally. The entry point
:func:`find_typosquat_installs` returns one finding per distinct flagged
package; the caller decides what to do (the analyzer raises the risk to
``HIGH`` so a human is asked, never a hard deny).

Hardening:

- enumerate every ``command`` node via ``iter_commands`` -- this surfaces
  sub-commands across ``&&``, ``||``, ``;``, ``|``, ``&``, newline, subshells,
  ``if``/``for``/``while`` bodies and ``$(...)`` substitutions structurally,
  replacing the old separator scanner;
- peel wrapper commands (``sudo``, ``env``, ...) with their leading options and
  env assignments, since the AST does not unwrap them;
- recover obfuscated operands: ``"lodahs"``/``'lodahs'`` and interior-quote or
  backslash forms (``lo""adsh``, ``load"sh"``, ``lo'adsh'``, ``lo\\adsh``,
  ``l"o"a"d"s"h"``) parse opaque, so a bounded shell-literal decode walks the
  single operand word and concatenates its adjacent quoted/escaped/bare
  segments into the one argv token the shell would pass, re-admitting it only if
  it matches a strict package-spec shape (any command substitution, variable
  expansion, embedded space or leftover metacharacter stays opaque);
- expand a static comma-brace operand the way bash does before exec:
  ``lo{a,}dsh`` becomes the two argv tokens ``loadsh`` and ``lodsh``, and EACH
  expansion is checked against the typosquat heuristic, so a near-name hidden in
  a brace alternative is flagged. This is bounded and narrow: it applies only to
  a word that is a concatenation of bare literals and brace/comma punctuation,
  with a hard cap on total expansions (32) and brace groups (8). Ranges
  (``{1..3}``), nested braces (``a{b,{c,d}}``), a brace touching a quote or a
  variable (``lo{a,}d"s"h``, ``lo{a,$X}dsh``), and escaped braces
  (``lo\\{a,\\}dsh``) are OUT OF SCOPE and stay opaque (LOW);

What this module decodes (static forms a real shell collapses to a fixed string
before exec) and what it leaves opaque is an explicit, honest boundary:

- DECODED (can raise to HIGH): quote removal (``"lodahs"``, ``lo'adsh'``),
  backslash unescaping (``lo\\adsh``), interior-quote concatenation
  (``lo""adsh``), ANSI-C ``$'...'`` escapes, line-continuation joining
  (``loa\\<newline>dsh``), and the static comma-brace expansion above.
- OUT OF SCOPE (stays LOW, never decoded): any runtime expansion -- shell
  variables (``$PKG``), command/process substitution (``$(...)``, ```...```,
  ``<(...)``) and arithmetic substitution (``$((...))``); homoglyphs and other
  Unicode confusables (only invisible/zero-width characters and fullwidth NFKC
  folds are normalized, not look-alike letters); and an install nested inside an
  inner command string such as ``bash -c '...'`` (treated as one opaque
  argument). This module flags the typosquats it can prove statically; it does
  not claim to catch every obfuscation.

- dispatch on the package-manager binary POSIX basename (path prefix stripped
  by the AST, ``/usr/bin/npm`` -> ``npm``);
- apply one consistent flag policy everywhere (subcommand skipping, install
  collection, runner collection): a known value-taking flag written bare
  (``--registry``, ``--prefix``, ...) consumes its next token, ``--flag=value``
  is self-contained, and every other flag -- including any unknown long flag --
  is boolean and consumes nothing, matching how npm/yarn/pnpm/bun actually parse;
- collect install targets after the install subcommand;
- collect the single executed package for runners (``npx``/``bunx``/``dlx``):
  the first positional after flag handling is the executed package (the
  typosquat target), trailing tokens are that program's own arguments;
- strip a trailing ``@version`` from a package spec while keeping the scope;
- flag a candidate iff its Optimal-String-Alignment distance to a popular
  name is exactly 1 AND that popular name is longer than four characters.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from tree_sitter import Node

from openhands.sdk.logger import get_logger
from openhands.sdk.security._shell_ast import (
    ShellCommand,
    ShellWord,
    command_basename,
    iter_commands,
    parse_shell_program,
)


logger = get_logger(__name__)

# Generous upper bound on the command length we will parse. The recursive
# tree-sitter-bash walkers in the shared ``_shell_ast`` view (and the operand
# decoders here) descend per nested ``$()`` level or chained operator, so a
# pathologically nested/chained command (hundreds of ``$()`` levels or
# operators) can exhaust the Python recursion stack. A real install command is
# at most a few hundred bytes; this cap is set far above any legitimate command
# (a chained build line, a long scoped-package install) so it never drops a real
# command, only an absurd one whose only purpose is to blow the stack. Even
# above the cap, :func:`find_typosquat_installs` would still not crash (the
# RecursionError below is caught), but skipping the parse avoids the wasted work.
_MAX_COMMAND_LENGTH = 100_000


# ---------------------------------------------------------------------------
# Curated data: popular packages, wrappers, managers, subcommands, flags.
#
# Do not reorder POPULAR_PACKAGES casually: the first matching popular name
# within OSA distance 1 is the one reported, so ordering affects the suggestion
# (not whether a hit occurs).
# ---------------------------------------------------------------------------

POPULAR_PACKAGES: tuple[str, ...] = (
    "react",
    "react-dom",
    "vue",
    "angular",
    "lodash",
    "axios",
    "express",
    "next",
    "tailwindcss",
    "typescript",
    "vite",
    "webpack",
    "eslint",
    "prettier",
    "jest",
    "mocha",
    "chalk",
    "commander",
    "chokidar",
    "moment",
    "dayjs",
    "uuid",
    "yargs",
    "zod",
    "rxjs",
    "ramda",
    "dotenv",
    "cors",
    "body-parser",
    "socket.io",
    "mongoose",
    "sequelize",
    "prisma",
    "redux",
    "react-router",
    "react-router-dom",
    "react-query",
    "framer-motion",
    "styled-components",
    "antd",
    "bootstrap",
    "jquery",
    "underscore",
    "request",
    "node-fetch",
    "got",
    "puppeteer",
    "playwright",
    "cheerio",
    "fs-extra",
    "minimatch",
    "rimraf",
    "semver",
    "debug",
    "winston",
    "pino",
    "morgan",
    "helmet",
    "passport",
    "bcrypt",
    "jsonwebtoken",
    "argon2",
    "mysql2",
    "redis",
    "ioredis",
    "nodemon",
    "concurrently",
)

# Lookup set built once from POPULAR_PACKAGES.
_POPULAR_SET: frozenset[str] = frozenset(POPULAR_PACKAGES)

# Leading binaries that wrap another command. They are skipped (with their
# leading options) before dispatching on the real package-manager binary.
_WRAPPER_COMMANDS: frozenset[str] = frozenset(
    {
        "sudo",
        "doas",
        "env",
        "time",
        "nice",
        "nohup",
        "stdbuf",
        "command",
        "xargs",
    }
)

# Package managers that install dependencies.
_PACKAGE_MANAGERS: frozenset[str] = frozenset({"npm", "npm.cmd", "yarn", "pnpm", "bun"})

# Standalone package runners (npx-like) where the executed package is the
# target.
_RUNNERS: frozenset[str] = frozenset({"npx", "npx.cmd", "bunx", "bunx.cmd"})

# Install subcommands per manager that collect package args.
_INSTALL_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "npm": frozenset({"install", "i", "add"}),
    "yarn": frozenset({"add"}),
    "pnpm": frozenset({"add", "install", "i"}),
    "bun": frozenset({"add", "install", "i"}),
}

# Runner subcommands per manager where only the executed package is the target.
_RUNNER_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "npm": frozenset({"exec", "x"}),
    "yarn": frozenset({"dlx"}),
    "pnpm": frozenset({"dlx"}),
    "bun": frozenset({"x"}),
}

# Every real subcommand keyword each manager recognizes, used by
# ``_find_subcommand_index`` to validate the routing token. Skipping leading
# global flags can land on a stray flag-value (``beta`` after a bare ``--tag``
# we did not enumerate); if that landed token is NOT one of these real
# subcommand keywords, the scan keeps going rather than giving up, so the
# install behind it is still reached. This is the version-independent backstop
# for any value-taking flag missing from the value-flag sets
# (``_SHARED_VALUE_TAKING_FLAGS`` / ``_NPM_ONLY_VALUE_TAKING_FLAGS``). The set is the
# union of the install/runner subcommands above plus the other documented
# subcommands (``npm help``, ``yarn help``, ``pnpm help``, ``bun --help``), so a
# genuine non-install subcommand such as ``run`` still stops the scan and is NOT
# skipped (which would mis-route ``npm run build install x`` into a false
# positive). Curated, not exhaustive across every alias/version, but covers the
# package-flow subcommands and the common ones an agent emits.
_KNOWN_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "npm": frozenset(
        {
            "access",
            "adduser",
            "audit",
            "bugs",
            "cache",
            "ci",
            "completion",
            "config",
            "dedupe",
            "deprecate",
            "diff",
            "dist-tag",
            "docs",
            "doctor",
            "edit",
            "exec",
            "explain",
            "explore",
            "find-dupes",
            "fund",
            "get",
            "help",
            "help-search",
            "init",
            "install",
            "install-ci-test",
            "install-test",
            "link",
            "ll",
            "login",
            "logout",
            "ls",
            "org",
            "outdated",
            "owner",
            "pack",
            "ping",
            "pkg",
            "prefix",
            "profile",
            "prune",
            "publish",
            "query",
            "rebuild",
            "repo",
            "restart",
            "root",
            "run",
            "run-script",
            "sbom",
            "search",
            "set",
            "shrinkwrap",
            "star",
            "stars",
            "start",
            "stop",
            "team",
            "test",
            "token",
            "trust",
            "undeprecate",
            "uninstall",
            "unpublish",
            "unstar",
            "update",
            "version",
            "view",
            "whoami",
            "remove",
            "rm",
            "create",
            "i",
            "in",
            "ins",
            "inst",
            "insta",
            "instal",
            "isnt",
            "isnta",
            "isntal",
            "isntall",
            "add",
            "x",
            "un",
            "up",
            "ln",
            "c",
            "s",
            "se",
            "list",
            "t",
            "tst",
            "ddp",
            "v",
            "r",
            "rb",
        }
    ),
    "yarn": frozenset(
        {
            "access",
            "add",
            "audit",
            "autoclean",
            "bin",
            "cache",
            "check",
            "config",
            "create",
            "dlx",
            "exec",
            "generate-lock-entry",
            "global",
            "help",
            "import",
            "info",
            "init",
            "install",
            "licenses",
            "link",
            "list",
            "login",
            "logout",
            "node",
            "outdated",
            "owner",
            "pack",
            "policies",
            "publish",
            "remove",
            "run",
            "tag",
            "team",
            "unlink",
            "unplug",
            "upgrade",
            "upgrade-interactive",
            "version",
            "versions",
            "why",
            "workspace",
            "workspaces",
            "set",
            "up",
            "patch",
            "patch-commit",
            "dedupe",
            "rebuild",
            "explain",
            "constraints",
        }
    ),
    "pnpm": frozenset(
        {
            "add",
            "audit",
            "bin",
            "config",
            "create",
            "dedupe",
            "deploy",
            "dlx",
            "doctor",
            "env",
            "exec",
            "fetch",
            "import",
            "init",
            "install",
            "i",
            "licenses",
            "link",
            "list",
            "ls",
            "outdated",
            "pack",
            "patch",
            "patch-commit",
            "patch-remove",
            "prune",
            "publish",
            "rebuild",
            "remove",
            "rm",
            "uninstall",
            "un",
            "root",
            "run",
            "server",
            "setup",
            "start",
            "store",
            "test",
            "t",
            "unlink",
            "update",
            "up",
            "why",
            "import-tarball",
            "add-tarball",
            "ci",
        }
    ),
    "bun": frozenset(
        {
            "add",
            "audit",
            "build",
            "create",
            "dev",
            "exec",
            "info",
            "init",
            "install",
            "i",
            "link",
            "outdated",
            "pack",
            "patch",
            "pm",
            "publish",
            "remove",
            "rm",
            "repl",
            "run",
            "test",
            "unlink",
            "update",
            "upgrade",
            "why",
            "x",
            "a",
            "c",
            "ci",
            "why",
        }
    ),
}

# Known value-taking flags across the npm/yarn/pnpm/bun CLIs. This is the ONE
# value-consuming rule shared by subcommand-skipping, install collection, and
# runner collection: when one of these flags is written bare (no ``=``), the
# following token is its value and is skipped. ``--flag=value`` is always
# self-contained. EVERY other flag (including any unknown long flag) is treated
# as boolean and consumes nothing, matching how these CLIs actually parse.
#
# Value-consumption is MANAGER-AWARE: the SAME spelling is a value flag in one
# CLI and a boolean switch in another. The canonical example is ``-w``: in npm
# it is ``--workspace <name>`` (Type String, consumes a value), but in pnpm it
# is ``--workspace-root`` (a boolean "act on the workspace root"), and yarn/bun
# expose no value-taking ``-w`` at all. Treating ``-w`` as value-taking for
# pnpm/yarn/bun is a FALSE NEGATIVE: ``pnpm add -w expres`` would skip
# ``expres``. So the table is split into a shared set valid for every manager
# plus an npm-only set; :func:`_consumes_next_value` takes the manager and ORs
# them only for npm.
#
# Accuracy matters in BOTH directions: a value-taking flag wrongly omitted lets
# an unknown pre-subcommand flag mis-route (false negative -- partly backstopped
# by the subcommand-keyword validation in ``_find_subcommand_index``), while a
# BOOLEAN flag wrongly added here would skip the real package that follows it
# (a new false negative). Every entry below is therefore verified to take a
# scalar value for the manager(s) it applies to, not to be a boolean switch.
#
# SHARED basis: ``npm help 7 config`` flag ``Type:`` (``tag``=String,
# ``audit-level``/``install-strategy``=enum String, ``scope``/``otp``/``ca``/
# ``cert``/``key``=String, ``maxsockets``/``fetch-retries``/``fetch-timeout``=
# Number, ``https-proxy``/``proxy``=URL, ``before``=Date, ``cafile``=Path,
# ``registry``=URL, ``prefix``/``-C``=Path, ``loglevel``=enum,
# ``save-prefix``=String) and the pnpm/yarn/bun help (``--store-dir <dir>``,
# ``--node-linker``, ``-C/--dir <dir>``, ``--cwd <path>``, ``-F/--filter
# <pattern>``, ``--user/global-config``). These spellings are value-taking (or
# simply unused, hence harmless) in every manager.
#
# ``--legacy-peer-deps`` is deliberately EXCLUDED: npm types it ``Boolean``, so
# it must consume nothing.
_SHARED_VALUE_TAKING_FLAGS: frozenset[str] = frozenset(
    {
        "--registry",
        "--prefix",
        "-C",
        "--before",
        "--userconfig",
        "--globalconfig",
        "--cwd",
        "--store-dir",
        "--config",
        "--cache",
        "--cafile",
        "--ca",
        "--cert",
        "--key",
        "--node-linker",
        "--loglevel",
        "--filter",
        "--dir",
        "--save-prefix",
        "--tag",
        "--audit-level",
        "--install-strategy",
        "--scope",
        "--otp",
        "--https-proxy",
        "--proxy",
        "--maxsockets",
        "--fetch-retries",
        "--fetch-timeout",
    }
)

# npm-ONLY value-taking flags. These consume a value under npm but are NOT
# value-taking under pnpm/yarn/bun, so applying them there would skip a real
# package.
#
# - ``-w`` / ``--workspace``: npm ``Type: String`` (a workspace name/path),
#   so npm consumes the next token. pnpm spells ``-w`` as the BOOLEAN
#   ``--workspace-root`` and uses ``--filter`` (shared) for selection; yarn's
#   ``workspace`` is a SUBCOMMAND, not a ``-w`` flag, and yarn/bun ``add`` take
#   no value-taking ``-w``/``--workspace``. So for pnpm/yarn/bun these stay
#   boolean and the package after them is collected.
# - ``--omit`` / ``--include``: npm enum flags (``Type:`` omit/include enum),
#   value-taking under npm. pnpm/yarn/bun do not define them; leaving them out
#   for those managers keeps an unrelated bare ``--omit foo pkg`` from eating
#   ``foo`` there. (npm uses these to select dependency groups.)
_NPM_ONLY_VALUE_TAKING_FLAGS: frozenset[str] = frozenset(
    {
        "--workspace",
        "-w",
        "--omit",
        "--include",
    }
)

# Redirection tokens that are never package specs.
_REDIRECTION_TOKENS: frozenset[str] = frozenset({">", ">>", "<"})


# ---------------------------------------------------------------------------
# Finding type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TyposquatFinding:
    """A single flagged install: a likely typosquat and the name it resembles.

    ``package`` is the normalized (lowercased, version-stripped) candidate as
    written by the agent; ``suggestion`` is the popular package it is one edit
    away from. ``reason`` is a human-readable line suitable for a permission
    prompt.
    """

    package: str
    suggestion: str

    @property
    def reason(self) -> str:
        """Human-readable explanation suitable for a permission prompt."""
        return (
            f"`{self.package}` is one edit away from the "
            f"popular package `{self.suggestion}`"
        )


# ---------------------------------------------------------------------------
# Encoding-evasion normalization
#
# Self-contained and property-based: instead of a hand-maintained table of
# invisible code points, classify each character by its Unicode general
# category and a handful of default-ignorable ranges the category check alone
# misses, then NFKC-fold so fullwidth ``ｎｐｍ`` becomes ASCII ``npm``. This runs
# at the public entry so both the parser entry and the analyzer (which calls it)
# are protected from ``lod<ZWSP>ahs`` and U+0085/U+2028/U+2029 line-splitting
# evasions. Combining marks (Mn) are intentionally NOT stripped; homoglyph
# folding is out of scope.
# ---------------------------------------------------------------------------

# Whitespace that must survive normalization: tabs and the line separators the
# tree-sitter-bash view needs as command boundaries, plus the normal space.
_KEEP_WHITESPACE: frozenset[str] = frozenset({"\t", "\n", "\r", " "})

# General categories whose code points are stripped: format (Cf), control (Cc),
# unassigned (Cn), and the line/paragraph separators (Zl, Zp) -- the latter two
# are what make U+2028/U+2029 split a package name when left in.
_STRIP_CATEGORIES: frozenset[str] = frozenset({"Cf", "Cc", "Cn", "Zl", "Zp"})


def _is_default_ignorable_extra(code_point: int) -> bool:
    """Report default-ignorable code points the category check alone misses.

    Variation selectors (U+FE00..U+FE0F, U+E0100..U+E01EF), Mongolian free
    variation selectors (U+180B..U+180E), and the tag block (U+E0000..U+E007F)
    are assigned non-format categories yet are invisible joiners an attacker can
    splice into a name, so they are stripped explicitly.
    """
    return (
        0xFE00 <= code_point <= 0xFE0F
        or 0xE0100 <= code_point <= 0xE01EF
        or 0x180B <= code_point <= 0x180E
        or 0xE0000 <= code_point <= 0xE007F
    )


def _normalize(command: str) -> str:
    """Strip invisible/zero-width characters then NFKC-fold ``command``.

    Per character: keep tab/newline/carriage-return/space verbatim; drop any
    code point whose Unicode general category is one of ``Cf``/``Cc``/``Cn``/
    ``Zl``/``Zp`` (this removes zero-width spaces, bidi controls, soft hyphens,
    BOM, U+0085/U+2028/U+2029 and unassigned code points) or that falls in a
    default-ignorable range the category check misses (variation selectors,
    Mongolian FVS, tag characters); keep everything else. Then apply NFKC so
    fullwidth/compatibility glyphs collapse to their ASCII form. General
    combining marks are deliberately preserved (homoglyphs are out of scope).
    """
    stripped = [
        ch
        for ch in command
        if ch in _KEEP_WHITESPACE
        or (
            unicodedata.category(ch) not in _STRIP_CATEGORIES
            and not _is_default_ignorable_extra(ord(ch))
        )
    ]
    return unicodedata.normalize("NFKC", "".join(stripped))


# ---------------------------------------------------------------------------
# Line-continuation joining
#
# bash joins an unquoted backslash-newline into nothing before tokenizing, so
# ``loa\<newline>dsh`` is the single word ``loadsh``. tree-sitter-bash does not
# fold this -- it leaves ``loa`` and ``dsh`` as two adjacent words -- so the
# obfuscated package would slip through. We remove the backslash-newline at the
# source level before parsing, exactly the way the shell does. Whether a
# backslash-newline is removed depends on the surrounding quoting context, so we
# track the full bash state with three flags rather than single-quote alone:
#
# - in a single-quoted ``'...'`` span the backslash-newline is LITERAL and kept
#   (``'loa\<newline>dsh'`` stays the literal ``loa\<newline>dsh``);
# - in a double-quoted ``"..."`` span bash DOES remove the backslash-newline
#   (``"loa\<newline>dsh"`` is ``loadsh``), so we join it;
# - in a ``#`` comment the rest of the physical line is discarded by the shell,
#   so a backslash-newline there is not a continuation and we keep the text
#   verbatim (the comment is dropped later by the tree-sitter view anyway).
#
# Tracking only single quotes (the old behavior) desynced on an apostrophe
# inside a double-quoted arg (``"a'b"``) or inside a ``# don't`` comment: the
# stray ``'`` toggled the single-quote flag, so a following real
# backslash-newline continuation hiding a typosquat was wrongly treated as
# inside single quotes and left unjoined (false negative). Modeling all three
# contexts fixes that while preserving the single-quote literal case. We
# deliberately do NOT model ANSI-C ``$'...'`` or here-doc bodies here: a
# ``\<newline>`` inside ``$'...'`` is an escaped newline either way (it does not
# change the package name shape), and a here-doc body is not an operand we
# collect from.
# ---------------------------------------------------------------------------


def _join_line_continuations(command: str) -> str:
    """Remove unquoted ``backslash-newline`` line continuations, as bash does.

    Walks ``command`` once, modeling bash's lexical state with three flags --
    ``in_single_quote``, ``in_double_quote`` and ``in_comment`` -- because
    whether a backslash-newline is a continuation depends on that context:

    - outside all quotes, an unescaped ``'`` opens a single-quoted span and an
      unescaped ``"`` opens a double-quoted span; a ``'`` while in a double-quote
      and a ``"`` while in a single-quote are literal (they do not toggle);
    - a ``#`` that begins a word (start of input or after whitespace, and not
      inside any quote) starts a comment that runs to the next newline;
    - a backslash-newline (optionally a preceding carriage return for CRLF) is
      collapsed ONLY when not in a single-quote and not in a comment -- bash
      removes it outside quotes AND inside double quotes, but keeps it literal
      inside single quotes and ignores it inside a comment.

    Everything else is preserved verbatim, so command boundaries (bare newlines),
    quoted text and comment text are untouched.
    """
    if "\\" not in command:
        return command
    out: list[str] = []
    i = 0
    n = len(command)
    in_single_quote = False
    in_double_quote = False
    in_comment = False
    while i < n:
        ch = command[i]

        # A bare newline ends a comment and resets the line context.
        if ch == "\n":
            in_comment = False
            out.append(ch)
            i += 1
            continue

        if in_comment:
            # Inside a comment everything is verbatim until the newline above; a
            # backslash-newline here is not a continuation (bash discards the
            # rest of the physical line).
            out.append(ch)
            i += 1
            continue

        # Quote toggling: a quote char only opens/closes when not inside the
        # OTHER kind of quote (a ' in "..." and a " in '...' are literal).
        if ch == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            out.append(ch)
            i += 1
            continue
        if ch == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            out.append(ch)
            i += 1
            continue

        # A ``#`` that begins a word, outside all quotes, starts a comment.
        if (
            ch == "#"
            and not in_single_quote
            and not in_double_quote
            and (i == 0 or command[i - 1] in " \t\n")
        ):
            in_comment = True
            out.append(ch)
            i += 1
            continue

        # Line continuation: collapse a backslash-newline unless we are inside a
        # single-quote (literal) -- comments are handled above. bash removes it
        # outside quotes and inside double quotes alike.
        if ch == "\\" and not in_single_quote and i + 1 < n:
            nxt = command[i + 1]
            if nxt == "\n":
                i += 2
                continue
            if nxt == "\r" and i + 2 < n and command[i + 2] == "\n":
                i += 3
                continue

        out.append(ch)
        i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def find_typosquat_installs(command: str) -> list[TyposquatFinding]:
    """Return one finding per distinct likely-typosquat package in ``command``.

    Normalizes ``command`` first (stripping invisible/zero-width characters and
    NFKC-folding fullwidth glyphs) so an attacker cannot hide ``lodahs`` as
    ``lod<ZWSP>ahs`` or split a name with U+0085/U+2028/U+2029, then parses it
    once with the shared tree-sitter-bash view, walks every ``command`` node
    (which surfaces sub-commands across ``&&``, ``||``, ``;``, ``|``, ``&``,
    newline, subshells and ``$(...)``), collects the packages each
    install/runner would fetch, and keeps those exactly one edit away from a
    popular name. Returns an empty list for anything that is not such an install
    (e.g. ``npm run build``, ``git status``, an empty string). De-duplicates by
    normalized package name, preserving first-seen order. Parse uncertainty
    (``has_error``) is tolerated: tree-sitter recovers and still surfaces the
    operands.

    Known limitation (never a crash, always fail-open to "no finding"):
    pathologically nested or chained input -- hundreds of ``$(...)`` levels
    (``x$($($(...)))``) or hundreds of chained operators
    (``ls && ls && ... && ls``) -- builds a parse tree so deep that the
    recursive tree-sitter-bash walkers in the shared ``_shell_ast`` view exhaust
    the Python recursion stack. Such input is NOT analyzed: it is treated as
    "no finding" (returns ``[]``) and never raises. An absurdly large command
    (beyond :data:`_MAX_COMMAND_LENGTH`, far above any legitimate install line)
    is short-circuited to ``[]`` for the same reason, and a ``RecursionError``
    from the parse/walk is caught and turned into ``[]`` as a defensive backstop,
    so nothing escapes this seam. This is a deliberate, documented boundary: not
    analyzing adversarially-nested noise is acceptable; a crash in the security
    seam is not.
    """
    # Generous early guard: an absurdly large command cannot be a real install
    # line and only risks blowing the recursive walkers, so skip it as "no
    # finding". The cap is set far above any legitimate command, so it never
    # drops a real long install (see _MAX_COMMAND_LENGTH).
    if len(command) > _MAX_COMMAND_LENGTH:
        logger.debug(
            "Supply-chain check skipped: command length %d exceeds %d; "
            "treating as no finding.",
            len(command),
            _MAX_COMMAND_LENGTH,
        )
        return []

    findings: list[TyposquatFinding] = []
    seen: set[str] = set()
    try:
        program = parse_shell_program(_join_line_continuations(_normalize(command)))
        for cmd in iter_commands(program):
            for pkg in _collect_packages_from_command(cmd):
                name = _normalize_package_name(pkg)
                suggestion = _find_typosquat_target(name)
                if suggestion is None or name in seen:
                    continue
                seen.add(name)
                findings.append(TyposquatFinding(package=name, suggestion=suggestion))
    except RecursionError:
        # Pathologically nested/chained input (hundreds of $() levels or chained
        # operators) builds a parse tree so deep the recursive walkers in the
        # shared _shell_ast view exhaust the stack. Fail open to "no finding":
        # such adversarial noise is not analyzed, but it never crashes the seam.
        logger.debug(
            "Supply-chain check skipped: command is too deeply nested/chained to "
            "parse without exhausting the recursion stack; treating as no finding."
        )
        return []
    return findings


# ---------------------------------------------------------------------------
# Per-command package collection (AST-driven)
# ---------------------------------------------------------------------------


def _collect_packages_from_command(cmd: ShellCommand) -> list[str]:
    """Dispatch a single parsed command and collect its install/runner targets.

    The tree-sitter view excludes redirects, comments and leading
    ``VAR=val`` assignments from ``cmd.words`` already, so this only has to:
    peel wrapper commands (``sudo``/``env``/...), recover static operand
    values from possibly-quoted words, then route on the binary basename.
    Returns the package specs this command would install or execute via a
    recognized npm-ecosystem manager, or an empty list otherwise.
    """
    base = command_basename(cmd)
    if base is None:
        # The AST flags the command name opaque for two different reasons: a
        # genuine runtime expansion (``$CMD``, ``$(...)``) that is unknowable,
        # or static obfuscation (``n"p"m``, ``\\npm``, ``$'npm'``) that a real
        # shell collapses to a fixed binary name before exec. Try the same
        # bounded literal decode used for operands; a runtime element keeps it
        # opaque (returns None) and the command stays LOW as before.
        base = _decode_command_name(cmd)
        if base is None:
            return []

    words = list(cmd.words)
    base, words = _peel_wrappers(base, words)
    if base is None:
        return []

    # Flatten each word into the argv token(s) the shell would pass. A normal
    # word yields one token; an unknowable word yields a single sentinel that
    # occupies the slot but can never be a flag, redirection, subcommand keyword
    # or package spec; a static comma-brace word (``lo{a,}dsh``) yields one token
    # per expansion (``loadsh``, ``lodsh``), exactly as bash expands it before
    # exec. This keeps the install/runner routing functions operating on a plain
    # ``list[str]`` while preserving positional semantics (an opaque runner
    # target still stops collection with no target).
    recovered: list[str] = []
    for word in words:
        recovered.extend(_recover_word_tokens(word))

    if base in _RUNNERS:
        # Standalone runners are npm/bun flavored. The npm-only value flags
        # (``-w``/``--omit``/``--include``) do not affect a runner target (the
        # first positional), but pass the matching manager so value-flag skipping
        # matches each runner's CLI exactly.
        runner_manager = "bun" if base in ("bunx", "bunx.cmd") else "npm"
        return _collect_runner_target(recovered, runner_manager)

    if base in _PACKAGE_MANAGERS:
        manager = "npm" if base == "npm.cmd" else base
        return _collect_manager_packages(manager, recovered)

    return []


def _decode_command_name(cmd: ShellCommand) -> str | None:
    """Statically decode an obfuscated command-name word to its POSIX basename.

    The shared AST marks a command name opaque whenever it is not a single bare
    ``word``: that covers both runtime expansions (``$CMD``, ``$(echo npm)``)
    and pure static obfuscation (``n"p"m``, ``\\npm``, ``$'npm'``,
    ``/usr/''bin/npm``) that the shell collapses to a fixed binary before exec.
    This applies the same bounded literal decode used for operands
    (:func:`_decode_shell_literal`: quote removal, backslash unescape, ANSI-C,
    concatenation) to the command-name word, then strips the POSIX path prefix
    so ``/usr/bin/npm`` becomes ``npm``.

    Returns the decoded basename, or ``None`` when the name is unknowable: a
    missing name, a runtime element anywhere inside it, or a decoded value that
    does not match the strict package-spec shape (so an embedded space or
    leftover metacharacter stays opaque and the command stays LOW).
    """
    name = cmd.name
    if name is None:
        return None
    # The command_name node wraps exactly one named child (word / string /
    # concatenation / ansi_c_string / simple_expansion ...). Decode that child;
    # fall back to the command_name node itself if the shape is unexpected.
    inner = name.node.named_children
    target = inner[0] if len(inner) == 1 else name.node
    decoded = _decode_shell_literal(target)
    if decoded is None:
        return None
    # Strip the POSIX path prefix first (``/usr/bin/npm`` -> ``npm``) so a
    # path-qualified obfuscated manager is recognized, then re-admit the bare
    # basename only if it matches the strict package-spec shape -- this keeps an
    # embedded space or leftover metacharacter opaque (LOW).
    basename = _strip_path_prefix(decoded)
    if not _PACKAGE_SPEC_RE.match(basename):
        return None
    return basename


# Placeholder for an opaque word in the recovered argv. A single null byte can
# never start with "-", is in no flag/keyword/redirection set, and fails
# ``_is_package_spec`` -- so it is positionally present but never collected.
_OPAQUE_SENTINEL = "\x00"


def _peel_wrappers(
    base: str, words: list[ShellWord]
) -> tuple[str | None, list[ShellWord]]:
    """Strip wrapper binaries (``sudo``/``env``/...) and re-derive the binary.

    The AST does not unwrap ``sudo npm ...`` or ``env FOO=bar npm ...``: the
    command name is ``sudo``/``env`` and the real binary is the first operand.
    While the basename is a wrapper, drop it, then drop any leading ``-`` flags
    and ``KEY=VALUE`` env-assignment-shaped words, and promote the first
    remaining plain word to the new binary (POSIX basename, one quote pair
    stripped). Returns ``(None, [])`` if no real binary follows the wrapper.
    """
    while base in _WRAPPER_COMMANDS:
        i = 0
        n = len(words)
        while i < n and (
            words[i].text.startswith("-")
            or _is_env_assignment(_strip_one_quote_pair(words[i].text))
        ):
            i += 1
        if i >= n:
            return None, []
        binary_word = words[i]
        binary_value = _recover_word(binary_word)
        if binary_value is None:
            # Opaque binary after the wrapper -- unknowable.
            return None, []
        base = _strip_path_prefix(binary_value)
        words = words[i + 1 :]
    return base, words


def _recover_word(word: ShellWord) -> str | None:
    """Recover the static string value of a word, or ``None`` if unknowable.

    A non-opaque word is its own text. An opaque word may still be a single,
    statically-known package operand that was obfuscated with interior quoting
    or backslash escaping -- ``lo""adsh``, ``load"sh"``, ``lo'adsh'``,
    ``lo\\adsh``, ``l"o"a"d"s"h"`` all resolve to the one argv token ``loadsh``
    in a real shell. :func:`_decode_shell_literal` walks the tree-sitter view of
    that one word and concatenates the adjacent quoted/escaped/bare segments
    into the literal string the shell would pass, returning ``None`` the moment
    it meets a runtime element (command substitution, variable expansion,
    backticks, process/arithmetic substitution).

    To stay a *bounded operand-name* decode and never widen what counts as a
    package, the decoded value is re-admitted only if it matches the strict
    package-spec shape :data:`_PACKAGE_SPEC_RE` (optional ``@scope/``, an
    ``@version`` suffix, no whitespace, no leftover metacharacters). So
    ``"lodahs"`` and ``lo""adsh`` are recovered, while ``"$(...)"``, ``$CMD``,
    a glob, or ``"a b"`` (embedded space) decode to ``None`` and are treated as
    unknown, not benign.
    """
    if not word.opaque:
        return word.text
    decoded = _decode_shell_literal(word.node)
    if decoded is not None and _PACKAGE_SPEC_RE.match(decoded):
        return decoded
    return None


def _recover_word_tokens(word: ShellWord) -> list[str]:
    """Recover the argv token(s) a single word would pass to the binary.

    Most words map to exactly one token: a known static value when
    :func:`_recover_word` can recover it, or the single :data:`_OPAQUE_SENTINEL`
    when it cannot (a runtime expansion, an embedded space, a leftover
    metacharacter -- unknowable, so it occupies the slot but is never collected).

    The one word that maps to *several* tokens is a static comma-brace word like
    ``lo{a,}dsh``: bash expands it to ``loadsh`` and ``lodsh`` (two argv tokens)
    before exec, so a typosquat hidden behind a brace alternative would otherwise
    slip through as an opaque concatenation. :func:`_expand_static_braces`
    performs that bounded expansion (comma lists only; ranges, nesting, quotes
    and any runtime element are out of scope and keep the word opaque), and each
    resulting token is re-admitted only if it matches the strict package-spec
    shape -- so ``lo{a,}d sh`` (space) or ``lo{a,}{bc}dsh`` (leftover ``{``)
    contribute nothing. When brace expansion does not apply, this falls back to
    the single-token recovery, so existing behavior is unchanged.
    """
    expansions = _expand_static_braces(word)
    if expansions is not None:
        return [exp for exp in expansions if _PACKAGE_SPEC_RE.match(exp)] or [
            _OPAQUE_SENTINEL
        ]
    value = _recover_word(word)
    return [value if value is not None else _OPAQUE_SENTINEL]


# ---------------------------------------------------------------------------
# Bounded static comma-brace expansion
#
# bash performs brace expansion textually BEFORE other expansions, so
# ``lo{a,}dsh`` becomes the two argv words ``loadsh`` and ``lodsh``.
# tree-sitter parses such a word as a ``concatenation`` of bare ``word``
# segments (``lo`` ``{`` ``a,`` ``}`` ``dsh``) and marks it opaque, so without
# this step a typosquat hidden behind a brace alternative is never checked.
#
# This is a DELIBERATELY NARROW decode. It applies ONLY when the word is a
# ``concatenation`` whose every child is a bare ``word`` -- which structurally
# guarantees there is no quote (``string``/``raw_string``), no runtime element
# (``simple_expansion``/``command_substitution``/...) and no range
# (``brace_expression`` for ``{1..3}``) anywhere in the word. On that pure-static
# text it expands top-level comma lists with a hard cap on total expansions and
# on brace-group count. Everything else stays opaque (LOW):
#
# - ranges ``{1..3}`` and nested braces ``a{b,{c,d}}`` -> bail (return ``None``);
# - a brace touching a quote or a variable (``lo{a,}d"s"h``, ``lo{a,$X}dsh``) ->
#   the concatenation has a non-``word`` child, so the pure-word gate excludes it;
# - escaped braces ``lo\{a,\}dsh`` -> a single ``word`` node, never a brace here;
# - a comma-less group ``{abc}`` -> bash leaves it literal, so do we (the literal
#   brace then fails the package-spec guard and is dropped).
# ---------------------------------------------------------------------------

# Hard caps so a crafted word cannot blow up the expansion (``{a,b}`` repeated):
# total expanded tokens and number of expanding brace groups are both bounded.
_MAX_BRACE_EXPANSIONS = 32
_MAX_BRACE_GROUPS = 8

# Characters allowed in a pure static comma-brace word. Anything else (a
# backslash, whitespace, a shell metacharacter) means the word is not a clean
# static brace operand, so expansion bails and the word stays opaque. Package
# specs use ascii letters/digits and ``. _ - / @``; braces add ``{ } ,``.
_BRACE_WORD_CHARS: frozenset[str] = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-/@{},"
)


def _is_pure_brace_word(word: ShellWord) -> bool:
    """Report whether ``word`` is a ``concatenation`` of only bare ``word`` nodes.

    That structure is the gate for static brace expansion: it guarantees the
    word carries no quote, no runtime expansion and no range ``brace_expression``
    -- only literal text and brace/comma punctuation -- so the joined text can be
    expanded purely textually the way bash does.
    """
    node = word.node
    if node.type != "concatenation":
        return False
    children = node.named_children
    if not children:
        return False
    return all(child.type == "word" for child in children)


def _expand_static_braces(word: ShellWord) -> list[str] | None:
    """Expand a static comma-brace word into its argv tokens, or ``None``.

    Returns ``None`` (meaning "not a clean static comma-brace; leave it to the
    single-token recovery, which keeps it opaque") when the word is not a pure
    bare-``word`` concatenation, contains a character outside
    :data:`_BRACE_WORD_CHARS`, has no actually-expanding brace group, or hits a
    range/nesting/unbalanced/cap condition. Otherwise returns the full list of
    expansions (the cartesian product across every top-level comma group),
    exactly as bash would produce them. The caller validates each token against
    the package-spec shape.
    """
    if not _is_pure_brace_word(word):
        return None
    text = word.text
    if "{" not in text:
        return None
    if any(ch not in _BRACE_WORD_CHARS for ch in text):
        return None

    # segments: (is_literal, value) where value is a literal str or a list of
    # alternatives for an expanding brace group.
    segments: list[tuple[bool, str | list[str]]] = []
    i = 0
    n = len(text)
    groups = 0
    while i < n:
        ch = text[i]
        if ch == "}":
            return None  # unbalanced close brace
        if ch != "{":
            j = i
            while j < n and text[j] not in "{}":
                j += 1
            segments.append((True, text[i:j]))
            i = j
            continue
        # Find the matching close brace; bail on nesting (depth > 1).
        depth = 0
        j = i
        comma_at_top = False
        while j < n:
            c = text[j]
            if c == "{":
                depth += 1
                if depth > 1:
                    return None  # nested brace -> out of scope
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            elif c == "," and depth == 1:
                comma_at_top = True
            j += 1
        if j >= n or depth != 0:
            return None  # unbalanced open brace
        inner = text[i + 1 : j]
        if ".." in inner:
            return None  # numeric/char range -> out of scope
        if not comma_at_top:
            # bash leaves a comma-less ``{abc}`` literal; the leftover brace then
            # fails the package-spec guard, so it is dropped, never collected.
            segments.append((True, text[i : j + 1]))
            i = j + 1
            continue
        groups += 1
        if groups > _MAX_BRACE_GROUPS:
            return None
        segments.append((False, inner.split(",")))
        i = j + 1

    if groups == 0:
        return None  # no expanding group -> not a brace operand we handle

    results = [""]
    for is_literal, value in segments:
        if is_literal:
            assert isinstance(value, str)
            results = [r + value for r in results]
            continue
        assert isinstance(value, list)
        expanded: list[str] = []
        for prefix in results:
            for alt in value:
                expanded.append(prefix + alt)
                if len(expanded) > _MAX_BRACE_EXPANSIONS:
                    return None  # cap exceeded -> bail rather than blow up
        results = expanded
    return results


# Strict npm package-spec shape, case-insensitive. Optional ``@scope/`` prefix,
# a single path segment, and an optional ``@version`` suffix (which may contain
# any non-space characters, e.g. a tag, range or git url). A decoded operand is
# only re-admitted as a package name if it matches this -- this is the
# false-positive guard that keeps embedded spaces, leftover globs, and runtime
# expansions out, no matter how the segments were quoted.
_PACKAGE_SPEC_RE: re.Pattern[str] = re.compile(
    r"^@?[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)?(@[^\s]+)?$",
    re.IGNORECASE,
)

# Tree-sitter node types that introduce a value only known at runtime. If any of
# these appears anywhere inside an operand word, the word is opaque and is never
# decoded into a package name.
_RUNTIME_NODE_TYPES: frozenset[str] = frozenset(
    {
        "simple_expansion",
        "expansion",
        "command_substitution",
        "process_substitution",
        "arithmetic_expansion",
    }
)


def _decode_shell_literal(node: Node) -> str | None:
    """Reconstruct the literal argv token for a single operand word.

    Walks the tree-sitter view of one word and concatenates the value of each
    adjacent segment exactly as the shell would join them into one argument:

    - ``concatenation`` -- decode and join every child segment;
    - ``string`` (double-quoted) -- the joined ``string_content`` with
      double-quote backslash unescaping; any non-content child (an expansion or
      substitution inside the quotes) makes it opaque;
    - ``raw_string`` (single-quoted) -- the verbatim inner text (no expansion,
      no unescaping);
    - ``ansi_c_string`` (``$'...'``) -- the inner text with the common ANSI-C
      escapes (``\\n``/``\\t``/``\\xHH``/octal/``\\uHHHH`` ...) resolved;
    - ``word`` -- the bare text with POSIX unquoted backslash unescaping;
    - any runtime node (``$...``, ``$(...)``, backticks, ``<(...)``) -- opaque.

    Returns the decoded literal, or ``None`` if any segment is opaque. The
    caller is responsible for validating the result against the strict
    package-spec shape; this function only reconstructs, it does not judge.
    """
    node_type = node.type

    if node_type in _RUNTIME_NODE_TYPES:
        return None

    if node_type == "word":
        return _unescape_unquoted(_node_text(node))

    if node_type == "raw_string":
        text = _node_text(node)
        # Strip the surrounding single quotes; content is fully literal.
        return text[1:-1] if len(text) >= 2 else ""

    if node_type == "ansi_c_string":
        return _decode_ansi_c_string(_node_text(node))

    if node_type == "string":
        return _decode_double_quoted(node)

    if node_type == "concatenation":
        parts: list[str] = []
        for child in node.named_children:
            decoded = _decode_shell_literal(child)
            if decoded is None:
                return None
            parts.append(decoded)
        return "".join(parts)

    # Any other node type (string_content handled inline below, numbers, etc.)
    # is unexpected in operand position -- treat as opaque to stay safe.
    return None


def _decode_double_quoted(node: Node) -> str | None:
    """Decode a double-quoted ``string`` node to its literal content.

    Returns ``None`` if the quotes contain any expansion or substitution
    (a non ``string_content`` child). An empty ``""`` decodes to the empty
    string. Backslash escapes are resolved per double-quote rules.
    """
    parts: list[str] = []
    for child in node.named_children:
        if child.type != "string_content":
            return None
        parts.append(_unescape_double_quoted(_node_text(child)))
    return "".join(parts)


def _node_text(node: Node) -> str:
    """Return a tree-sitter node's source text, ``""`` for an empty node.

    ``Node.text`` is typed ``bytes | None`` (None only for a zero-width node),
    so this collapses that to a decoded ``str`` and keeps the decoder total.
    """
    raw = node.text
    return raw.decode() if raw is not None else ""


def _unescape_unquoted(text: str) -> str:
    """Resolve POSIX unquoted backslash escapes: ``\\x`` -> ``x``.

    Outside quotes a backslash quotes the single following character (so
    ``lo\\adsh`` -> ``loadsh``, ``l\\o\\a\\d\\s\\h`` -> ``loadsh``). A trailing
    backslash with no following character is dropped.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            out.append(text[i + 1])
            i += 2
            continue
        if ch == "\\":
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# Inside double quotes a backslash is literal UNLESS it precedes one of these.
_DQ_ESCAPABLE: frozenset[str] = frozenset({"$", "`", '"', "\\", "\n"})


def _unescape_double_quoted(text: str) -> str:
    """Resolve double-quote backslash escapes in ``string_content``.

    Inside ``"..."`` a backslash only escapes ``$``, `````, ``"``, ``\\`` and a
    newline; before anything else the backslash is a literal character. This
    keeps ``lo\\nadsh`` as the literal ``lo\\nadsh`` (not a newline) while still
    folding an escaped quote.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n and text[i + 1] in _DQ_ESCAPABLE:
            out.append(text[i + 1])
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# Single-character ANSI-C escapes (``$'...'``) that map to one literal char.
# Only the common, package-name-relevant set is resolved; anything else falls
# through to the literal-backslash branch in :func:`_decode_ansi_c_string`.
_ANSI_C_SIMPLE_ESCAPES: dict[str, str] = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "\\": "\\",
    "'": "'",
    '"': '"',
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "v": "\v",
    "e": "\x1b",
    "E": "\x1b",
}


def _decode_ansi_c_string(text: str) -> str | None:
    """Decode an ANSI-C ``$'...'`` quoted string to its literal value.

    ``text`` is the full node text including the ``$'`` prefix and closing
    ``'``. Resolves the common escapes a real bash applies inside ``$'...'``:
    ``\\n \\t \\r \\\\ \\' \\"`` and the rarer control escapes, ``\\xHH`` (1-2
    hex digits), ``\\0NN``/``\\NNN`` (octal), and ``\\uHHHH``/``\\UHHHHHHHH``
    (Unicode). An unrecognised ``\\x`` keeps the backslash literal, matching
    bash. A trailing lone backslash is kept literal. Returns the decoded string;
    the caller validates it against the strict package-spec shape, so a control
    character or space simply fails that guard and stays opaque.
    """
    if len(text) < 3 or not text.startswith("$'") or not text.endswith("'"):
        # Not the expected $'...' shape; let the spec guard reject it.
        return text
    inner = text[2:-1]
    out: list[str] = []
    i = 0
    n = len(inner)
    while i < n:
        ch = inner[i]
        if ch != "\\" or i + 1 >= n:
            out.append(ch)
            i += 1
            continue
        nxt = inner[i + 1]
        if nxt in _ANSI_C_SIMPLE_ESCAPES:
            out.append(_ANSI_C_SIMPLE_ESCAPES[nxt])
            i += 2
            continue
        if nxt == "x":
            decoded, consumed = _read_radix_escape(inner, i + 2, 16, 2)
            if consumed:
                out.append(decoded)
                i += 2 + consumed
                continue
            # `\x` with no hex digit: literal backslash, per bash.
            out.append("\\")
            i += 1
            continue
        if nxt in ("u", "U"):
            width = 4 if nxt == "u" else 8
            decoded, consumed = _read_radix_escape(inner, i + 2, 16, width)
            if consumed:
                out.append(decoded)
                i += 2 + consumed
                continue
            out.append("\\")
            i += 1
            continue
        if "0" <= nxt <= "7":
            # `\NNN` and `\0NN`: up to three octal digits after the backslash.
            decoded, consumed = _read_radix_escape(inner, i + 1, 8, 3)
            out.append(decoded)
            i += 1 + consumed
            continue
        # Unknown escape: keep the backslash literal (matches bash).
        out.append("\\")
        i += 1
    return "".join(out)


def _read_radix_escape(
    text: str, start: int, radix: int, max_digits: int
) -> tuple[str, int]:
    """Read up to ``max_digits`` base-``radix`` digits and return ``(char, n)``.

    Returns the decoded single character and how many digits were consumed.
    ``("", 0)`` means no valid digit was present. A code point that is not a
    valid Unicode scalar (e.g. a surrogate) yields the empty string but still
    reports the digits consumed, so the escape is dropped rather than left as a
    raw backslash; the package-spec guard rejects the result regardless.
    """
    digits = "0123456789abcdef"[:radix]
    j = start
    end = min(len(text), start + max_digits)
    while j < end and text[j].lower() in digits:
        j += 1
    consumed = j - start
    if consumed == 0:
        return "", 0
    code_point = int(text[start:j], radix)
    try:
        return chr(code_point), consumed
    except (ValueError, OverflowError):
        return "", consumed


def _strip_one_quote_pair(text: str) -> str:
    """Strip one matched pair of surrounding single or double quotes."""
    if len(text) >= 2:
        first, last = text[0], text[-1]
        if (first == "'" and last == "'") or (first == '"' and last == '"'):
            return text[1:-1]
    return text


def _is_env_assignment(token: str) -> bool:
    """Report whether ``token`` is a ``KEY=VALUE`` assignment.

    The key must be a valid shell identifier: a leading letter or underscore
    followed by letters, digits, or underscores.
    """
    eq = token.find("=")
    if eq <= 0:
        return False
    key = token[:eq]
    for j, c in enumerate(key):
        is_alpha = c == "_" or ("A" <= c <= "Z") or ("a" <= c <= "z")
        is_digit = "0" <= c <= "9"
        if j == 0 and not is_alpha:
            return False
        if j > 0 and not is_alpha and not is_digit:
            return False
    return True


def _strip_path_prefix(token: str) -> str:
    """Strip a POSIX path prefix on a binary name: ``/usr/bin/npm`` -> ``npm``.

    Splits on ``/`` only, matching ``command_basename`` (``posixpath.basename``)
    so the wrapper-peel binary and the direct-dispatch binary derive their name
    under one POSIX rule. Backslash path separators are out of scope: the targets
    are POSIX shells, and treating ``\\`` as a separator would mis-handle an
    operand whose name legitimately contained a backslash.
    """
    idx = token.rfind("/")
    return token[idx + 1 :] if idx >= 0 else token


# ---------------------------------------------------------------------------
# Manager dispatch
# ---------------------------------------------------------------------------


# Transparent prefix subcommands per manager: a routing token that is NOT itself
# an install/runner action but wraps one on the remaining args. ``yarn global
# add expres`` / ``yarn global remove pkg`` run ``add``/``remove`` in the global
# scope: ``global`` is a known yarn subcommand, so the scan stops on it, yet the
# real action (``add``) lives behind it. We advance past the wrapper and
# re-resolve the real subcommand on the remainder, so ``add``/``remove`` route
# correctly (``yarn global add expres`` -> express HIGH; ``yarn global remove
# lodash`` -> not an install sub -> LOW). Only yarn has this form; npm/pnpm/bun
# install globally with a ``-g``/``--global`` flag (boolean, already handled),
# not a wrapping subcommand. A small recursion bound stops a pathological
# ``yarn global global global ...`` from looping.
_TRANSPARENT_PREFIX_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "yarn": frozenset({"global"}),
}

# Max nested transparent-prefix unwraps. One real prefix (``global``) is enough
# in practice; the bound just stops a crafted ``yarn global global ... add`` from
# recursing without limit.
_MAX_PREFIX_UNWRAP: int = 8


def _collect_manager_packages(manager: str, args: list[str]) -> list[str]:
    """Find the subcommand, then collect install packages or the runner target.

    Skips leading global options before the subcommand. Returns an empty list
    for non-install/non-runner subcommands (e.g. ``npm run build``). Transparent
    prefix subcommands (yarn ``global``) are unwrapped so the real action behind
    them (``add``/``remove``) routes.
    """
    install_subs = _INSTALL_SUBCOMMANDS.get(manager, frozenset())
    runner_subs = _RUNNER_SUBCOMMANDS.get(manager, frozenset())
    known_subs = _KNOWN_SUBCOMMANDS.get(manager, frozenset())
    prefix_subs = _TRANSPARENT_PREFIX_SUBCOMMANDS.get(manager, frozenset())

    # Unwrap any transparent prefix wrapper (yarn ``global``) before deciding the
    # action, so ``yarn global add expres`` routes through ``add``.
    for _ in range(_MAX_PREFIX_UNWRAP):
        sub_index = _find_subcommand_index(args, known_subs, manager)
        if sub_index == -1:
            return []
        subcommand = args[sub_index]
        if subcommand in prefix_subs:
            args = args[sub_index + 1 :]
            continue
        break
    else:
        # Exhausted the unwrap budget on nested prefixes; treat as unresolvable.
        return []

    rest = args[sub_index + 1 :]

    if subcommand in install_subs:
        return _collect_install_packages(rest, manager)
    if subcommand in runner_subs:
        return _collect_runner_target(rest, manager)
    return []


def _consumes_next_value(token: str, manager: str) -> bool:
    """Report whether ``token`` consumes the following token as its value.

    The single value-consuming rule shared by subcommand skipping, install
    collection, and runner collection: a bare flag (no ``=``) consumes its next
    token only when it is a known value-taking flag for ``manager``. A
    ``--flag=value`` is self-contained, and every other flag (including any
    unknown long flag) is boolean and consumes nothing -- matching how the
    npm/yarn/pnpm/bun CLIs actually parse.

    Value-consumption is manager-aware: the shared
    :data:`_SHARED_VALUE_TAKING_FLAGS` apply to every manager, while
    :data:`_NPM_ONLY_VALUE_TAKING_FLAGS` (``-w``/``--workspace``/``--omit``/
    ``--include``) apply ONLY to npm. Under pnpm/yarn/bun, ``-w`` is the boolean
    ``--workspace-root`` (pnpm) or simply unused (yarn/bun), so it must consume
    nothing or the package after it is skipped (``pnpm add -w expres``).
    """
    if "=" in token:
        return False
    if token in _SHARED_VALUE_TAKING_FLAGS:
        return True
    return manager == "npm" and token in _NPM_ONLY_VALUE_TAKING_FLAGS


def _find_subcommand_index(
    args: list[str], known_subcommands: frozenset[str], manager: str
) -> int:
    """Return the index of the subcommand, skipping leading global options.

    Applies the shared :func:`_consumes_next_value` rule (manager-aware): a
    known value-taking flag for ``manager`` written bare consumes the following
    token, every other flag is boolean.

    Robustness backstop: after the value-flag skip, the first non-option token is
    accepted as the subcommand only when it is one of ``known_subcommands`` (the
    real subcommand keywords for this manager). If it is NOT -- which happens when
    an UNKNOWN bare value flag we did not enumerate leaked its value into this
    position (``npm --someunknownvalflag X install pkg`` lands on ``X``) -- the
    token is skipped and the scan continues, so the install behind it is still
    reached. A token that genuinely is not a subcommand (e.g. a stray operand
    before any subcommand) is simply skipped too; if no known subcommand is ever
    found, returns -1 and the command stays LOW. Because the known value-taking
    flags consume their own value first, a flag value that happens to equal a
    subcommand keyword (``npm --tag install install pkg``: ``--tag`` eats the
    first ``install``) is handled correctly and the real ``install`` routes.
    """
    i = 0
    n = len(args)
    while i < n:
        token = args[i]
        if token.startswith("-"):
            if _consumes_next_value(token, manager) and i + 1 < n:
                i += 2
            else:
                i += 1
            continue
        if token in known_subcommands:
            return i
        # Not a real subcommand keyword: a leaked value from an unknown bare
        # value flag (or a stray operand). Skip it and keep scanning so an
        # install hidden behind it is not lost.
        i += 1
    return -1


def _collect_install_packages(args: list[str], manager: str) -> list[str]:
    """Collect package specs after an install subcommand.

    Applies the shared :func:`_consumes_next_value` rule (manager-aware): a
    known value-taking flag for ``manager`` written bare also skips its value
    token; every other flag is boolean. So ``pnpm add -w expres`` collects
    ``expres`` (pnpm ``-w`` is boolean), while ``npm install -w pkg loadsh``
    skips the npm ``-w`` value ``pkg`` and collects ``loadsh``. Redirection
    tokens are skipped too. An unknown long flag swallows no value, so
    ``--unknownflag axio lodash`` collects both ``axio`` and ``lodash``.
    """
    packages: list[str] = []
    i = 0
    n = len(args)
    while i < n:
        token = args[i]

        if token in _REDIRECTION_TOKENS:
            i += 1
            continue

        if token.startswith("-"):
            if _consumes_next_value(token, manager):
                i += 1
            i += 1
            continue

        if _is_package_spec(token):
            packages.append(token)
        i += 1

    return packages


def _collect_runner_target(args: list[str], manager: str) -> list[str]:
    """Return the single executed package for a runner invocation.

    Honors ``-p``/``--package`` and ``--package=NAME``/``-p=NAME``, whose value
    is the package to execute. Otherwise it applies the shared
    :func:`_consumes_next_value` rule (manager-aware) to skip known value-taking
    flags and treats every other flag as boolean, then returns the FIRST
    positional token as the executed package -- the typosquat target. Trailing
    positionals are arguments to that program, never install targets
    (``npx create-react-app my-app`` -> ``create-react-app``; an unknown long
    flag swallows no value, so ``npx --some-flag value lodahs`` yields ``value``).
    """
    i = 0
    n = len(args)
    while i < n:
        token = args[i]

        if token.startswith("--package="):
            return [token[len("--package=") :]]
        if token.startswith("-p="):
            return [token[len("-p=") :]]
        if token in ("-p", "--package"):
            if i + 1 < n:
                return [args[i + 1]]
            return []

        if token in _REDIRECTION_TOKENS:
            i += 1
            continue

        if token.startswith("-"):
            if _consumes_next_value(token, manager):
                i += 1
            i += 1
            continue

        if _is_package_spec(token):
            return [token]
        return []

    return []


# ---------------------------------------------------------------------------
# Package-name handling
# ---------------------------------------------------------------------------


def _is_package_spec(token: str) -> bool:
    """Report whether ``token`` can be a package spec.

    True when the first character is ascii-alphanumeric or ``_``, or when it
    is a scoped name starting ``@`` that contains ``/``.
    """
    if not token:
        return False
    first = token[0]
    if first == "_" or first.isascii() and first.isalnum():
        return True
    if first == "@" and "/" in token:
        return True
    return False


def _normalize_package_name(spec: str) -> str:
    """Strip a trailing ``@version`` while keeping the scope; lowercase.

    ``@scope/pkg@1.2.3`` -> ``@scope/pkg``; ``lodash@4`` -> ``lodash``;
    ``@scope/pkg`` -> ``@scope/pkg``.
    """
    name = spec
    if name.startswith("@"):
        slash = name.find("/")
        if slash != -1:
            at = name.find("@", slash)
            if at != -1:
                name = name[:at]
    else:
        at = name.find("@")
        if at > 0:
            name = name[:at]
    return name.lower()


# ---------------------------------------------------------------------------
# Typosquat decision
# ---------------------------------------------------------------------------

# Real, mainstream npm packages that legitimately sit one OSA edit from a
# popular name and would otherwise be flagged as a typosquat of it. The OSA-1
# heuristic cannot tell ``preact`` (a real 80M+/month React-compatible library)
# from ``loadsh`` (a non-existent name one edit off ``lodash``); both are
# distance 1 from a popular package. This is a precision-only allowlist: a name
# here is suppressed because it is itself a genuine, established package, not a
# fat-finger of one. It can ONLY ever suppress its own exact spelling, so it can
# never let an actual typosquat through -- every entry was confirmed to exist on
# the npm registry as a real, non-malicious package with its own repository.
#
# Entries are kept SHORT and each is justified (npm last-month downloads / repo
# verified on the public registry at the time of writing):
#
# - ``preact``    -- distance 1 from ``react``; ~86M downloads/month,
#                    github.com/preactjs/preact (Fast 3kb React-compatible VDOM).
# - ``prettierx`` -- distance 1 from ``prettier``; a real named fork of Prettier,
#                    github.com/brodybits/prettierx, published to npm.
# - ``moments``   -- distance 1 from ``moment``; a real package by egoist,
#                    github.com/egoist/moments, published to npm.
#
# Add a name here ONLY after confirming it is an established, real package on the
# registry; never add a name that does not exist (that would be allowlisting a
# typosquat). Names already in :data:`POPULAR_PACKAGES` need no entry -- they are
# suppressed by the ``candidate in _POPULAR_SET`` check below.
_KNOWN_LEGIT: frozenset[str] = frozenset(
    {
        "preact",
        "prettierx",
        "moments",
    }
)


def _find_typosquat_target(candidate: str) -> str | None:
    """Return the popular package ``candidate`` typosquats, or ``None``.

    Flagged iff ``candidate`` is NOT itself popular, NOT in the curated
    :data:`_KNOWN_LEGIT` allowlist of real packages that happen to sit one edit
    from a popular name, AND its minimum Optimal-String-Alignment distance to
    some popular name is exactly 1 AND that popular name is longer than four
    characters (short names produce too many false positives). The allowlist is
    precision-only: it suppresses only its own exact, registry-confirmed names,
    so it never lets a genuine typosquat through.
    """
    if candidate in _POPULAR_SET or candidate in _KNOWN_LEGIT:
        return None
    for popular in POPULAR_PACKAGES:
        if len(popular) <= 4:
            continue
        if _osa_distance_within(candidate, popular, 1) == 1:
            return popular
    return None


def _osa_distance_within(a: str, b: str, max_distance: int) -> int:
    """Optimal String Alignment distance, capped at ``max_distance + 1``.

    OSA (restricted Damerau-Levenshtein) counts insertion, deletion,
    substitution, and transposition of two ADJACENT characters, with the
    restriction that no substring is edited more than once. The cap lets the
    caller exit early: any returned value greater than ``max_distance`` means
    "farther than max". A two-row rolling buffer keeps memory at O(len(b)).
    """
    la, lb = len(a), len(b)
    if abs(la - lb) > max_distance:
        return max_distance + 1

    prev_prev = [0] * (lb + 1)
    prev = list(range(lb + 1))
    curr = [0] * (lb + 1)

    for i in range(1, la + 1):
        curr[0] = i
        row_min = curr[0]
        ai = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ai == b[j - 1] else 1
            best = prev[j] + 1  # deletion
            ins = curr[j - 1] + 1  # insertion
            if ins < best:
                best = ins
            sub = prev[j - 1] + cost  # substitution
            if sub < best:
                best = sub
            if i > 1 and j > 1 and ai == b[j - 2] and a[i - 2] == b[j - 1]:
                trans = prev_prev[j - 2] + 1
                if trans < best:
                    best = trans
            curr[j] = best
            if best < row_min:
                row_min = best
        if row_min > max_distance:
            return max_distance + 1
        prev_prev, prev, curr = prev, curr, prev_prev

    if prev[lb] > max_distance:
        return max_distance + 1
    return prev[lb]
