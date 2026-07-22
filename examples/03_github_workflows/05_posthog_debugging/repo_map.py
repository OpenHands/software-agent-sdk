"""Deterministic, allowlisted mapping from an error origin to a target repo.

The sanitized event's ``source`` is a *constant*
(``Literal["openhands-agent-server"]``), so it cannot discriminate repositories.
The only per-repo signal the event carries is ``error_origin_module`` -- the
dotted ``openhands.*`` module of the deepest first-party frame -- together with
``is_first_party``. So the mapping keys on a **longest-matching module prefix**,
and a fingerprint is only ever remediable when it is first-party, maps to an
allowlisted prefix, and resolves to a concrete base commit.

Adding a repository is a deliberate, reviewable act: append one
:class:`RepoTarget` here (or to the ``config.yaml`` overlay) and provision the
scoped credential. Nothing is mapped implicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from fingerprint import FingerprintGroup
from sanitize import safe_token, safe_version


@dataclass(frozen=True, slots=True)
class VerificationProfile:
    """How Job C proves red->green for a target repo.

    The runner is declared, never auto-detected across an arbitrary repo. A
    fingerprint whose repo has no profile is ineligible.
    """

    runner: str = "pytest"
    #: Directory the new regression test must live under (test.patch is
    #: rejected if it writes outside this).
    test_root: str = "tests/"
    #: Globs that identify test files (fix.patch is rejected if it touches one).
    test_globs: tuple[str, ...] = ("tests/**", "test_*.py", "*_test.py")
    result_parser: str = "pytest-json"


@dataclass(frozen=True, slots=True)
class RepoTarget:
    """One allowlisted remediation target."""

    #: Longest-matching ``error_origin_module`` prefix that selects this repo.
    module_prefix: str
    repo: str
    #: Which sanitized field yields the base commit for this repo.
    #: ``build_git_sha`` for agent-server-origin errors, else a semver.
    sha_field: str = "build_git_sha"
    verification: VerificationProfile = field(default_factory=VerificationProfile)


#: The built-in allowlist. ``config.yaml`` may extend or override this; keep the
#: prefixes specific so a broad ``openhands`` never captures an unintended repo.
DEFAULT_TARGETS: Final[tuple[RepoTarget, ...]] = (
    RepoTarget(
        module_prefix="openhands.agent_server",
        repo="OpenHands/software-agent-sdk",
        sha_field="build_git_sha",
    ),
    RepoTarget(
        module_prefix="openhands.tools",
        repo="OpenHands/software-agent-sdk",
        sha_field="build_git_sha",
    ),
    RepoTarget(
        module_prefix="openhands.sdk",
        repo="OpenHands/software-agent-sdk",
        sha_field="build_git_sha",
    ),
)


@dataclass(frozen=True, slots=True)
class Eligibility:
    """Why a fingerprint may or may not be remediated. Both paths are logged."""

    eligible: bool
    reason: str
    target: RepoTarget | None = None
    base_sha: str = ""


def _match_target(
    module: str | None, targets: tuple[RepoTarget, ...]
) -> RepoTarget | None:
    """Longest-prefix match so ``openhands.sdk.llm`` binds the sdk target."""
    if not module:
        return None
    best: RepoTarget | None = None
    for target in targets:
        prefix = target.module_prefix
        if module == prefix or module.startswith(prefix + "."):
            if best is None or len(prefix) > len(best.module_prefix):
                best = target
    return best


def resolve_target(
    module: str | None, targets: tuple[RepoTarget, ...] = DEFAULT_TARGETS
) -> RepoTarget | None:
    return _match_target(module, targets)


def evaluate(
    group: FingerprintGroup,
    *,
    targets: tuple[RepoTarget, ...] = DEFAULT_TARGETS,
    min_count: int = 1,
) -> Eligibility:
    """Decide whether one fingerprint group may enter remediation.

    Fails closed on every uncertainty: third-party origin, unknown module, no
    mapped repo, or no resolvable base commit all yield ``eligible=False`` with
    a human-readable reason (safe to log -- contains only validated tokens).
    """
    if not group.is_first_party:
        return Eligibility(False, "third-party origin (is_first_party is false)")

    module = group.error_origin_module
    if not module or module == "unknown":
        return Eligibility(False, "origin module is unknown")

    target = _match_target(module, targets)
    if target is None:
        return Eligibility(
            False, f"module '{safe_token(module, default='unknown')}' not in allowlist"
        )

    base_sha = safe_version(group.latest_sha, default="")
    if not base_sha:
        return Eligibility(
            False, "no resolvable base commit (build_git_sha absent)", target=target
        )

    if group.count < min_count:
        return Eligibility(
            False,
            f"below occurrence threshold ({group.count} < {min_count})",
            target=target,
            base_sha=base_sha,
        )

    return Eligibility(True, "eligible", target=target, base_sha=base_sha)
