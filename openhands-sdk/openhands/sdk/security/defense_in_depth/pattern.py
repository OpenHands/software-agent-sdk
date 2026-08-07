"""Classify agent actions by matching content against known threat signatures.

When an agent is about to run ``rm -rf /``, you want to catch it. When
the agent merely *thinks about* ``rm -rf /`` while running ``ls /tmp``,
you do not. This module solves that with two scanning corpora:

- **Executable corpus** (tool_name, tool_call arguments): scanned for
  shell-destructive, code-execution, and network-to-exec patterns.
- **All-field corpus** (executable + thought/reasoning/summary): scanned
  for injection and social-engineering patterns that are dangerous
  wherever they appear.

Each pattern carries a stable detector ID for telemetry readiness.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import Field, PrivateAttr

from openhands.sdk.event import ActionEvent
from openhands.sdk.logger import get_logger
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.defense_in_depth.utils import (
    _extract_content,
    _extract_exec_segments,
    _extract_exec_content,
    _has_rm_recursive_force,
    _normalize,
)
from openhands.sdk.security.risk import SecurityRisk


logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Stable detector IDs -- do not change between releases without documentation.
# Format: DET_{CORPUS}_{FAMILY}_{SPECIFIC}
# ---------------------------------------------------------------------------

DET_EXEC_DESTRUCT_RM_RF = "exec.destruct.rm_rf"
DET_EXEC_DESTRUCT_SUDO_RM = "exec.destruct.sudo_rm"
DET_EXEC_DESTRUCT_MKFS = "exec.destruct.mkfs"
DET_EXEC_DESTRUCT_DD = "exec.destruct.dd_raw_disk"
DET_EXEC_CODE_EVAL = "exec.code.eval_call"
DET_EXEC_CODE_EXEC = "exec.code.exec_call"
DET_EXEC_CODE_OS_SYSTEM = "exec.code.os_system"
DET_EXEC_CODE_SUBPROCESS = "exec.code.subprocess"
DET_EXEC_NET_CURL_EXEC = "exec.net.curl_pipe_exec"
DET_EXEC_NET_WGET_EXEC = "exec.net.wget_pipe_exec"
DET_EXEC_NET_CURL = "exec.net.curl"
DET_EXEC_NET_WGET = "exec.net.wget"
DET_INJECT_OVERRIDE = "inject.override"
DET_INJECT_MODE_SWITCH = "inject.mode_switch"
DET_INJECT_IDENTITY = "inject.identity"

# ---------------------------------------------------------------------------
# Pattern definitions
#
# Format: (regex_pattern, description, detector_id)
#
# Pattern design constraints:
# - No unbounded .* or .+ around alternations (catastrophic backtracking)
# - Risky spans are bounded ({0,N}) to prevent ReDoS
# - \s* and \w+ are acceptable in non-alternation positions
# - \b-anchored to avoid substring matches
# - IGNORECASE compiled in
# ---------------------------------------------------------------------------

DEFAULT_HIGH_PATTERNS: list[tuple[str, str, str]] = [
    # Destructive filesystem operations
    (r"\bsudo\s+rm\b", "Privileged file deletion", DET_EXEC_DESTRUCT_SUDO_RM),
    (r"\bmkfs\.\w+", "Filesystem format command", DET_EXEC_DESTRUCT_MKFS),
    (r"\bdd\b.{0,100}of=/dev/", "Raw disk write", DET_EXEC_DESTRUCT_DD),
    # Code invocation via dynamic interpreters
    (r"\beval\s*\(", "Dynamic code evaluation", DET_EXEC_CODE_EVAL),
    (r"\bexec\s*\(", "Dynamic code execution", DET_EXEC_CODE_EXEC),
    (r"\bos\.system\s*\(", "OS-level command execution", DET_EXEC_CODE_OS_SYSTEM),
    (
        r"\bsubprocess\.(?:call|run|Popen|check_output|check_call)\s*\(",
        "Subprocess invocation",
        DET_EXEC_CODE_SUBPROCESS,
    ),
    # Download-and-run
    (
        r"\bcurl\b[^|]{0,200}\|\s*(?:ba)?sh\b",
        "Download and run (curl | sh)",
        DET_EXEC_NET_CURL_EXEC,
    ),
    (
        r"\bwget\b[^|]{0,200}\|\s*(?:ba)?sh\b",
        "Download and run (wget | sh)",
        DET_EXEC_NET_WGET_EXEC,
    ),
]

DEFAULT_MEDIUM_PATTERNS: list[tuple[str, str, str]] = [
    # Network access without invocation pipe
    (r"\bcurl\b.{0,100}https?://", "HTTP request via curl", DET_EXEC_NET_CURL),
    (r"\bwget\b.{0,100}https?://", "Download via wget", DET_EXEC_NET_WGET),
]

# Injection patterns: scanned against ALL fields (invocation + reasoning).
# These are textual attacks targeting instruction-following, not the OS.

DEFAULT_INJECTION_HIGH_PATTERNS: list[tuple[str, str, str]] = [
    (
        r"\b(?:ignore|disregard|forget|override|bypass)\s+(?:all\s+)?"
        r"(?:previous|prior|above)\s+(?:instructions?|prompts?|rules?|directives?)\b",
        "Instruction override attempt",
        DET_INJECT_OVERRIDE,
    ),
]

DEFAULT_INJECTION_MEDIUM_PATTERNS: list[tuple[str, str, str]] = [
    (
        r"\byou\s+are\s+now\s+(?:in\s+)?(?:\w+\s+)?mode\b",
        "Mode switching attempt",
        DET_INJECT_MODE_SWITCH,
    ),
    (
        r"\bpretend\s+(?:you\s+are|to\s+be)\s+(?:a\s+)?different\b",
        "Identity manipulation",
        DET_INJECT_IDENTITY,
    ),
]


# ---------------------------------------------------------------------------
# PatternSecurityAnalyzer
# ---------------------------------------------------------------------------


class PatternSecurityAnalyzer(SecurityAnalyzerBase):
    """Catch dangerous agent actions through deterministic signature scanning.

    Use this when you want fast, local, no-network threat detection at the
    action boundary. It returns ``SecurityRisk.HIGH``, ``MEDIUM``, or ``LOW``
    -- pair it with ``ConfirmRisky`` to decide what gets confirmed.

    The key design choice: shell-destructive patterns only scan what the
    agent will *execute* (tool arguments), never what it *thought about*
    (reasoning text). Injection patterns scan everything, because
    "ignore all previous instructions" is dangerous wherever it appears.

    Normalization is always on -- invisible characters and fullwidth
    substitutions are collapsed before matching.

    Example::

        from openhands.sdk.security import PatternSecurityAnalyzer, ConfirmRisky

        analyzer = PatternSecurityAnalyzer()
        policy = ConfirmRisky(threshold=SecurityRisk.MEDIUM)
    """

    high_patterns: list[tuple[str, str, str]] = Field(
        default_factory=lambda: list(DEFAULT_HIGH_PATTERNS),
        description="HIGH patterns scanned against executable fields only",
    )
    medium_patterns: list[tuple[str, str, str]] = Field(
        default_factory=lambda: list(DEFAULT_MEDIUM_PATTERNS),
        description="MEDIUM patterns scanned against executable fields only",
    )
    injection_high_patterns: list[tuple[str, str, str]] = Field(
        default_factory=lambda: list(DEFAULT_INJECTION_HIGH_PATTERNS),
        description="HIGH patterns scanned against all fields",
    )
    injection_medium_patterns: list[tuple[str, str, str]] = Field(
        default_factory=lambda: list(DEFAULT_INJECTION_MEDIUM_PATTERNS),
        description="MEDIUM patterns scanned against all fields",
    )

    _compiled_high: list[tuple[re.Pattern[str], str, str]] = PrivateAttr(
        default_factory=list,
    )
    _compiled_medium: list[tuple[re.Pattern[str], str, str]] = PrivateAttr(
        default_factory=list,
    )
    _compiled_injection_high: list[tuple[re.Pattern[str], str, str]] = PrivateAttr(
        default_factory=list,
    )
    _compiled_injection_medium: list[tuple[re.Pattern[str], str, str]] = PrivateAttr(
        default_factory=list,
    )

    def model_post_init(self, __context: Any) -> None:
        """Compile regex patterns after model initialization."""
        self._compiled_high = [
            (re.compile(p, re.IGNORECASE), d, det_id)
            for p, d, det_id in self.high_patterns
        ]
        self._compiled_medium = [
            (re.compile(p, re.IGNORECASE), d, det_id)
            for p, d, det_id in self.medium_patterns
        ]
        self._compiled_injection_high = [
            (re.compile(p, re.IGNORECASE), d, det_id)
            for p, d, det_id in self.injection_high_patterns
        ]
        self._compiled_injection_medium = [
            (re.compile(p, re.IGNORECASE), d, det_id)
            for p, d, det_id in self.injection_medium_patterns
        ]

    def security_risk(self, action: ActionEvent) -> SecurityRisk:
        """Evaluate security risk via two-corpus pattern matching."""
        exec_segments = [_normalize(s) for s in _extract_exec_segments(action)]
        exec_content = _normalize(_extract_exec_content(action))
        all_content = _normalize(_extract_content(action))

        if not exec_content and not all_content:
            return SecurityRisk.LOW

        # HIGH: rm recursive+force detection uses token parsing, not regex.
        if any(_has_rm_recursive_force(seg) for seg in exec_segments):
            logger.debug("Pattern matched: %s -> HIGH", DET_EXEC_DESTRUCT_RM_RF)
            return SecurityRisk.HIGH

        # HIGH: patterns on executable fields only
        for pattern, _desc, det_id in self._compiled_high:
            if pattern.search(exec_content):
                logger.debug("Pattern matched: %s -> HIGH", det_id)
                return SecurityRisk.HIGH

        # HIGH: injection patterns on all fields
        for pattern, _desc, det_id in self._compiled_injection_high:
            if pattern.search(all_content):
                logger.debug("Pattern matched: %s -> HIGH", det_id)
                return SecurityRisk.HIGH

        # MEDIUM: patterns on executable fields only
        for pattern, _desc, det_id in self._compiled_medium:
            if pattern.search(exec_content):
                logger.debug("Pattern matched: %s -> MEDIUM", det_id)
                return SecurityRisk.MEDIUM

        # MEDIUM: injection patterns on all fields
        for pattern, _desc, det_id in self._compiled_injection_medium:
            if pattern.search(all_content):
                logger.debug("Pattern matched: %s -> MEDIUM", det_id)
                return SecurityRisk.MEDIUM

        return SecurityRisk.LOW
