"""Helpers for populating ``ToolShieldLLMSecurityAnalyzer.safety_experiences``.

These helpers integrate with the ``toolshield`` PyPI package (install via the
``[toolshield]`` optional extra). They expose three usage patterns:

1. :func:`default_safety_experiences` -- seed with terminal + filesystem
   experiences we ship by default.
2. :func:`load_safety_experiences` -- load an explicit list of tool
   experiences.
3. :func:`auto_detect_safety_experiences` -- probe localhost for active MCP
   servers, load experiences for the tools that are actually running.

All three return a rendered string ready to plug into
``ToolShieldLLMSecurityAnalyzer(safety_experiences=...)``. Users who want to
inject their own hand-authored experiences can skip these helpers and pass
an arbitrary string directly.

Example:
    >>> from openhands.sdk.security import ToolShieldLLMSecurityAnalyzer
    >>> from openhands.sdk.security.toolshield_helpers import (
    ...     default_safety_experiences,
    ...     auto_detect_safety_experiences,
    ... )
    >>>
    >>> # Default seed
    >>> analyzer = ToolShieldLLMSecurityAnalyzer(
    ...     llm=guardrail_llm,
    ...     safety_experiences=default_safety_experiences(),
    ... )
    >>>
    >>> # Auto-detect whatever MCP servers are running locally
    >>> analyzer = ToolShieldLLMSecurityAnalyzer(
    ...     llm=guardrail_llm,
    ...     safety_experiences=auto_detect_safety_experiences(),
    ... )
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from openhands.sdk.logger import get_logger


if TYPE_CHECKING:
    # Only for type hints; keep the real import lazy so the SDK doesn't
    # require toolshield to be installed.
    from toolshield import ExperienceStore  # noqa: F401


logger = get_logger(__name__)


# Tools seeded by default. These are the ones we have bundled experiences for
# and that cover the tool surface evaluated in the linked issue.
DEFAULT_TOOL_NAMES: list[str] = ["terminal-mcp", "filesystem-mcp"]


# Default port range for auto-detection. Matches toolshield's ``mcp_scan``
# default, which probes localhost:8000-10000 for anything speaking MCP.
# Narrow this for faster scans in known deployments.
DEFAULT_SCAN_PORT_RANGE: tuple[int, int] = (8000, 10000)


# Tools that don't have a port to probe (terminal is local exec). We include
# them unconditionally in auto-detect results.
ALWAYS_ACTIVE_TOOLS: list[str] = ["terminal-mcp"]


def _require_toolshield():
    """Import the toolshield package or raise a helpful ImportError."""
    try:
        import toolshield  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "toolshield is not installed. Install via "
            "`pip install openhands-sdk[toolshield]` to use these helpers, "
            "or pass a custom string to "
            "ToolShieldLLMSecurityAnalyzer(safety_experiences=...)."
        ) from e
    return toolshield


def load_safety_experiences(
    tool_names: list[str],
    model: str = "claude-sonnet-4.5",
) -> str:
    """Load experiences for an explicit list of tool names.

    Args:
        tool_names: Tool experience identifiers (e.g. ``"terminal-mcp"``).
            Must match a file bundled in the ``toolshield`` package for the
            given ``model`` subdirectory.
        model: Which pre-generated experience set to use. Defaults to
            ``"claude-sonnet-4.5"``.

    Returns:
        A rendered string ready for ``safety_experiences=``.
    """
    ts = _require_toolshield()
    experiences = ts.load_experiences(tool_names, model=model)
    return experiences.format_for_prompt()


def default_safety_experiences(model: str = "claude-sonnet-4.5") -> str:
    """Default seed: terminal + filesystem experiences.

    This is the starting point that covers the tool surface evaluated in the
    linked issue. Callers with different tool surfaces should use
    :func:`load_safety_experiences` or :func:`auto_detect_safety_experiences`
    instead.
    """
    return load_safety_experiences(DEFAULT_TOOL_NAMES, model=model)


def _experience_name_from_server_name(server_name: str) -> str:
    """Derive a bundled-experience filename stem from an MCP server's
    self-reported ``serverInfo.name``.

    Mirrors the convention ``toolshield``'s ``auto_discover`` uses:
    ``tool_name = server["name"].lower(); exp_file = f"{tool_name}-mcp.json"``.
    E.g. server name ``"filesystem"`` -> experience ``"filesystem-mcp"``.
    If the server name already ends in ``-mcp``, it's used as-is.
    """
    slug = server_name.lower().strip().replace(" ", "-").replace("_", "-")
    if slug.endswith("-mcp") or slug.endswith("mcp"):
        return slug if slug.endswith("-mcp") else slug[:-3] + "-mcp"
    return f"{slug}-mcp"


def detect_active_mcp_tools(
    port_range: tuple[int, int] = DEFAULT_SCAN_PORT_RANGE,
    verbose: bool = False,
) -> list[str]:
    """Scan localhost for MCP servers and return matching experience names.

    Uses ``toolshield.mcp_scan`` to perform a full MCP JSON-RPC handshake
    (``initialize`` over SSE) against each open port in the range. This is
    ground-truth detection: we learn each server's self-reported name and
    version, not just "something responds on port 9090". Requires the
    ``toolshield`` optional extra.

    Tools in :data:`ALWAYS_ACTIVE_TOOLS` (terminal) are returned
    unconditionally since they're local exec rather than network services.

    Args:
        port_range: Inclusive ``(start, end)`` localhost port range to scan.
            Default matches toolshield's convention (``8000-10000``).
        verbose: Pass through to ``toolshield.mcp_scan`` to log per-port
            probe attempts.

    Returns:
        Experience identifiers (e.g. ``"terminal-mcp"``, ``"filesystem-mcp"``)
        corresponding to tools whose servers responded to the MCP handshake.
        Always-active tools appear first.
    """
    _require_toolshield()
    from toolshield.mcp_scan import main as _scan_main  # type: ignore[import-not-found]

    start_port, end_port = port_range
    try:
        # ``mcp_scan.main`` is async; safe to run here because we're not
        # already inside an event loop (the analyzer is constructed from
        # sync code). If a caller IS in an async context, they can wrap
        # this helper in ``asyncio.to_thread`` themselves.
        found = asyncio.run(_scan_main(start_port, end_port, verbose=verbose))
    except RuntimeError as e:
        # Typical cause: called from within a running event loop.
        logger.warning(
            f"MCP scan failed ({e}); returning always-active tools only"
        )
        return list(ALWAYS_ACTIVE_TOOLS)

    active = list(ALWAYS_ACTIVE_TOOLS)
    for server in found or []:
        name = server.get("name", "") or ""
        if not name or name == "unknown":
            logger.debug(
                f"MCP server at {server.get('url')} reported no name; skipping"
            )
            continue
        exp_name = _experience_name_from_server_name(name)
        if exp_name in active:
            continue
        active.append(exp_name)
        logger.debug(
            f"MCP server {name!r} at {server.get('url')} -> experience {exp_name!r}"
        )
    return active


def auto_detect_safety_experiences(
    port_range: tuple[int, int] = DEFAULT_SCAN_PORT_RANGE,
    verbose: bool = False,
    model: str = "claude-sonnet-4.5",
    fallback_to_default: bool = True,
) -> str:
    """Scan localhost for active MCP servers and load matching experiences.

    Uses toolshield's full MCP JSON-RPC handshake (via
    ``toolshield.mcp_scan``) rather than blind TCP probes, so we only
    credit experiences for tools whose servers *actually* respond as MCP
    and self-report a name.

    "Detection" requires at least one *networked* MCP server to respond
    -- the unconditionally-included always-active tools (e.g. terminal)
    don't count as detection signal. When no networked tool is detected,
    falls back to :func:`default_safety_experiences` (terminal +
    filesystem), unless ``fallback_to_default=False`` in which case
    returns an empty string so the caller's no-op path doesn't quietly
    require ``toolshield`` to be installed.

    Detected servers whose derived experience name (e.g. server
    ``"filesystem"`` -> ``"filesystem-mcp"``) has no bundled file for
    ``model`` are skipped with a log line. Operators can drop in their
    own JSON under ``toolshield/experiences/<model>/`` to extend coverage.

    Args:
        port_range: Inclusive ``(start, end)`` localhost port range.
            Default ``(8000, 10000)`` matches toolshield's scanner.
        verbose: Log per-port probe attempts.
        model: Experience-set subdirectory. Defaults to
            ``"claude-sonnet-4.5"``.
        fallback_to_default: If no MCP servers are detected, return the
            default seed (terminal + filesystem) instead of empty.

    Returns:
        A rendered string ready for ``safety_experiences=``.
    """
    active = detect_active_mcp_tools(port_range=port_range, verbose=verbose)
    networked_detected = [t for t in active if t not in ALWAYS_ACTIVE_TOOLS]

    if networked_detected:
        logger.info(f"Auto-detected active MCP tools: {active}")
        # Keep only tools with a bundled experience for ``model``; log
        # the misses so the operator knows coverage gaps.
        from toolshield import ExperienceStore  # lazy; _require_toolshield above
        available = set(ExperienceStore.list_bundled(model))
        runnable = [t for t in active if t in available]
        missing = [t for t in active if t not in available]
        if missing:
            logger.info(
                f"Detected tools without bundled {model!r} experiences "
                f"(skipping): {missing}"
            )
        if runnable:
            return load_safety_experiences(runnable, model=model)

    if fallback_to_default:
        logger.info(
            "No networked MCP tools detected; falling back to default seed "
            f"({DEFAULT_TOOL_NAMES})"
        )
        return default_safety_experiences(model=model)

    logger.warning(
        "No networked MCP tools detected and fallback_to_default=False; "
        "returning empty safety_experiences"
    )
    return ""
