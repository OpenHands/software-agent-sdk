"""Two-tier persistent-memory loader.

Reads the agent-maintained ``MEMORY.md`` indexes -- ``~/.openhands/memory/``
(user tier) and ``<workspace>/.openhands/memory/`` (project tier) -- into one
prompt-ready string. LocalConversation resolves this on the first
``send_message()`` / ``run()`` (the workspace path is unknown when AgentContext
validates); AgentContext only carries the resolved text. Daily logs
(``YYYY-MM-DD.md``) in the same directories are deliberately NOT loaded -- the
agent reads them on demand.
"""

from pathlib import Path
from typing import Final

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

__all__ = ["MEMORY_CHAR_BUDGET", "MEMORY_INDEX_RELPATH", "load_memory"]

MEMORY_INDEX_RELPATH: Final[str] = ".openhands/memory/MEMORY.md"
MEMORY_CHAR_BUDGET: Final[int] = 6000
_TRUNCATION_NOTICE: Final[str] = "[earlier memory truncated]"


def _read_index(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"Failed to read memory index {path}: {e}")
        return None
    return text or None


def _truncate_top(body: str, budget: int) -> str:
    """Drop whole lines from the top of ``body`` until it fits ``budget``.

    A truncated body starts with the truncation notice (which counts toward
    the budget); partial lines never survive.
    """
    if len(body) <= budget:
        return body
    lines = body.splitlines()
    while lines:
        del lines[0]
        candidate = "\n".join([_TRUNCATION_NOTICE, *lines])
        if len(candidate) <= budget:
            return candidate
    return _TRUNCATION_NOTICE


def load_memory(
    working_dir: str | Path, char_budget: int = MEMORY_CHAR_BUDGET
) -> str | None:
    """Load the combined memory-index text for ``working_dir``.

    User tier first, project tier second (the later position gets more model
    attention). Returns ``None`` when neither index has content. Over-budget
    tiers are truncated line-wise from the top, keeping the most recent tail
    -- the maintenance instructions tell the agent to append -- while tier
    headers (and truncation notices) always survive. That skeleton is the
    budget's effective floor: ``char_budget`` is honored whenever it covers
    the headers plus one notice per tier (~150 chars for both tiers).
    """
    tiers: list[tuple[str, str]] = []
    user_index = _read_index(Path.home() / MEMORY_INDEX_RELPATH)
    if user_index is not None:
        tiers.append((f"# User memory (~/{MEMORY_INDEX_RELPATH})", user_index))
    project_index = _read_index(Path(working_dir) / MEMORY_INDEX_RELPATH)
    if project_index is not None:
        tiers.append((f"# Project memory ({MEMORY_INDEX_RELPATH})", project_index))
    if not tiers:
        return None

    combined = "\n\n".join(f"{header}\n{body}" for header, body in tiers)
    if len(combined) <= char_budget:
        return combined

    # Headers and blank-line separators are always emitted; the remaining
    # budget is split evenly across tier bodies, a short tier's unused share
    # rolling over to the other.
    overhead = sum(len(header) + 1 for header, _ in tiers) + 2 * (len(tiers) - 1)
    body_budget = char_budget - overhead
    fair_share = body_budget // len(tiers)
    budgets = [min(len(body), fair_share) for _, body in tiers]
    leftover = body_budget - sum(budgets)
    for i, (_, body) in enumerate(tiers):
        extra = min(leftover, len(body) - budgets[i])
        budgets[i] += extra
        leftover -= extra
    return "\n\n".join(
        f"{header}\n{_truncate_top(body, budget)}"
        for (header, body), budget in zip(tiers, budgets)
    )
