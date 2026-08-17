"""Shared observation behaviour for the Gemini tools that write files.

``edit`` and ``write_file`` both report a file that was either created or
changed, and both render the result as a diff. The only part that differs is
the line announcing a change to an existing file, so the rendering lives here
and each tool supplies that one line.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from pydantic import PrivateAttr
from rich.text import Text

from openhands.sdk.tool import Observation


class FileChangeObservation(Observation, ABC):
    """Base for observations reporting a created or rewritten file.

    Subclasses declare ``file_path``, ``is_new_file``, ``old_content`` and
    ``new_content`` themselves so that each tool keeps its own field
    descriptions, and implement :meth:`change_summary`.
    """

    if TYPE_CHECKING:
        file_path: str | None
        is_new_file: bool
        old_content: str | None
        new_content: str | None

    _diff_cache: Text | None = PrivateAttr(default=None)

    @abstractmethod
    def change_summary(self) -> str:
        """Return the line shown when an existing file was changed."""

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this observation."""
        if self.is_error:
            return super().visualize

        text = Text()
        if self.file_path:
            if self.is_new_file:
                text.append("✨ ", style="green bold")
                text.append(f"Created: {self.file_path}\n", style="green")
            else:
                text.append("✏️  ", style="yellow bold")
                text.append(self.change_summary(), style="yellow")

            if self.old_content is not None and self.new_content is not None:
                from openhands.tools.file_editor.utils.diff import visualize_diff

                if not self._diff_cache:
                    self._diff_cache = visualize_diff(
                        self.file_path,
                        self.old_content,
                        self.new_content,
                        n_context_lines=2,
                        change_applied=True,
                    )
                text.append(self._diff_cache)
        return text
