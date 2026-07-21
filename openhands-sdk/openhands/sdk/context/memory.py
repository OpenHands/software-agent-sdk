"""Two-tier persistent-memory loader (issue #2037).

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
_TRUNCATION_NOTICE: Final[str] = "[earlier memory truncated]\n"


def _read_index(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(f"Failed to read memory index {path}: {e}")
        return None
    return text or None


def load_memory(
    working_dir: str | Path, char_budget: int = MEMORY_CHAR_BUDGET
) -> str | None:
    """Load the combined memory-index text for ``working_dir``.

    User tier first, project tier second (the later position gets more model
    attention). Returns ``None`` when neither index has content. Over-budget
    text is truncated from the top, keeping the most recent tail -- the
    maintenance instructions tell the agent to append.
    """
    tiers: list[str] = []
    user_index = _read_index(Path.home() / MEMORY_INDEX_RELPATH)
    if user_index is not None:
        tiers.append(f"# User memory (~/{MEMORY_INDEX_RELPATH})\n{user_index}")
    project_index = _read_index(Path(working_dir) / MEMORY_INDEX_RELPATH)
    if project_index is not None:
        tiers.append(f"# Project memory ({MEMORY_INDEX_RELPATH})\n{project_index}")
    if not tiers:
        return None
    combined = "\n\n".join(tiers)
    if len(combined) > char_budget:
        combined = _TRUNCATION_NOTICE + combined[-char_budget:]
    return combined
