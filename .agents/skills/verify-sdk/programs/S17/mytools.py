"""Custom tools a program defines, the way example 02 does: an Action, an
Observation, a ToolExecutor and a ToolDefinition, registered at import time.

- `WordCountTool` (name derived from the class: `word_count`): counts words in
  a file of the conversation workspace.
- `NotebookToolSet` (`notebook_tool_set`): one `create()` returning two tools,
  `add_note` and `list_notes`, sharing one in-memory notebook. `add_note` is
  not read-only and declares the resource key `notebook:<name>`; `list_notes`
  is read-only and declares no resources. Each call sleeps `delay` seconds
  so a program can see which calls ran in parallel.
- `ShoutTool` sets `name = "yell"` explicitly.
- `GatedTool` (`gated`) is usable only when `GATED_TOOL_USABLE=1`;
  `BrokenCheckTool` (`broken_check`) raises in `is_usable()`.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from openhands.sdk import Action, ImageContent, Observation, TextContent, ToolDefinition
from openhands.sdk.tool import (
    DeclaredResources,
    ToolAnnotations,
    ToolExecutor,
    register_tool,
)


# --- word_count -------------------------------------------------------------


class WordCountAction(Action):
    path: str = Field(description="File to count, relative to the workspace")


class WordCountObservation(Observation):
    words: int = 0
    path: str = ""

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        if self.content:  # built with from_text (errors)
            return super().to_llm_content
        return [TextContent(text=f"{self.path} has {self.words} words")]


class WordCountExecutor(ToolExecutor[WordCountAction, WordCountObservation]):
    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def __call__(
        self,
        action: WordCountAction,
        conversation=None,  # noqa: ARG002
    ) -> WordCountObservation:
        target = self.root / action.path
        if not target.is_file():
            return WordCountObservation.from_text(
                text=f"No such file: {action.path}", is_error=True
            )
        return WordCountObservation(
            words=len(target.read_text().split()), path=action.path
        )


class WordCountTool(ToolDefinition[WordCountAction, WordCountObservation]):
    """Counts the words in a workspace file."""

    @classmethod
    def create(cls, conv_state, **params) -> Sequence[WordCountTool]:
        if params:
            raise ValueError(f"word_count takes no params, got {sorted(params)}")
        return [
            cls(
                description="Count the words in a file of the workspace.",
                action_type=WordCountAction,
                observation_type=WordCountObservation,
                annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
                executor=WordCountExecutor(conv_state.workspace.working_dir),
            )
        ]


# --- notebook tool set --------------------------------------------------------


class Section(BaseModel):
    """A nested, self-referencing structure (a reference cycle in the schema)."""

    title: str
    children: list[Section] = Field(default_factory=list)


class AddNoteAction(Action):
    text: str = Field(description="The note")
    priority: Literal["low", "high"] = Field(default="low", description="How urgent")
    tags: list[str] = Field(default_factory=list, description="Labels")
    outline: Section | None = Field(default=None, description="Optional outline")


class ListNotesAction(Action):
    contains: str = Field(default="", description="Only notes containing this text")


class NotesObservation(Observation):
    notes: list[str] = Field(default_factory=list)

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [
            TextContent(text=f"{len(self.notes)} note(s): " + " | ".join(self.notes))
        ]


class Notebook:
    """State shared by the two tools of the set."""

    def __init__(self, name: str, delay: float) -> None:
        self.name = name
        self.delay = delay
        self.notes: list[str] = []
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.calls: list[tuple[str, float, float]] = []

    def enter(self, label: str):
        notebook = self

        class _Span:
            def __enter__(self):
                with notebook.lock:
                    notebook.active += 1
                    notebook.max_active = max(notebook.max_active, notebook.active)
                self.start = time.monotonic()

            def __exit__(self, *exc):
                time.sleep(0)
                with notebook.lock:
                    notebook.active -= 1
                    notebook.calls.append((label, self.start, time.monotonic()))

        return _Span()


class AddNoteExecutor(ToolExecutor[AddNoteAction, NotesObservation]):
    def __init__(self, notebook: Notebook) -> None:
        self.notebook = notebook

    def __call__(self, action: AddNoteAction, conversation=None) -> NotesObservation:  # noqa: ARG002
        with self.notebook.enter("add_note"):
            time.sleep(self.notebook.delay)
            note = f"[{action.priority}] {action.text}"
            if action.tags:
                note += " #" + " #".join(action.tags)
            if action.outline:
                note += (
                    f" (outline: {action.outline.title}/{len(action.outline.children)})"
                )
            with self.notebook.lock:
                self.notebook.notes.append(note)
                notes = list(self.notebook.notes)
        return NotesObservation(notes=notes)


class ListNotesExecutor(ToolExecutor[ListNotesAction, NotesObservation]):
    def __init__(self, notebook: Notebook) -> None:
        self.notebook = notebook

    def __call__(self, action: ListNotesAction, conversation=None) -> NotesObservation:  # noqa: ARG002
        with self.notebook.enter("list_notes"):
            time.sleep(self.notebook.delay)
            with self.notebook.lock:
                notes = [n for n in self.notebook.notes if action.contains in n]
        return NotesObservation(notes=notes)


class AddNoteTool(ToolDefinition[AddNoteAction, NotesObservation]):
    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence[AddNoteTool]:  # noqa: ARG003
        raise NotImplementedError("created by NotebookToolSet")

    def declared_resources(self, action: Action) -> DeclaredResources:  # noqa: ARG002
        notebook = self.executor.notebook if self.executor else None
        return DeclaredResources(
            keys=(f"notebook:{getattr(notebook, 'name', '')}",), declared=True
        )


class ListNotesTool(ToolDefinition[ListNotesAction, NotesObservation]):
    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence[ListNotesTool]:  # noqa: ARG003
        raise NotImplementedError("created by NotebookToolSet")

    def declared_resources(self, action: Action) -> DeclaredResources:  # noqa: ARG002
        return DeclaredResources(keys=(), declared=True)


NOTEBOOKS: dict[str, Notebook] = {}


class NotebookToolSet(ToolDefinition[Action, Observation]):
    """`create(conv_state, name=..., delay=...)` returns add_note and list_notes
    bound to one notebook."""

    @classmethod
    def create(
        cls,
        conv_state,  # noqa: ARG003
        name: str = "default",
        delay: float = 0.0,
    ) -> Sequence[ToolDefinition]:
        notebook = NOTEBOOKS.setdefault(name, Notebook(name, float(delay)))
        return [
            AddNoteTool(
                description="Add a note to the notebook.",
                action_type=AddNoteAction,
                observation_type=NotesObservation,
                annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False),
                executor=AddNoteExecutor(notebook),
            ),
            ListNotesTool(
                description="List the notes in the notebook.",
                action_type=ListNotesAction,
                observation_type=NotesObservation,
                annotations=ToolAnnotations(readOnlyHint=True),
                executor=ListNotesExecutor(notebook),
            ),
        ]


# --- naming and usability ------------------------------------------------------


class ShoutTool(ToolDefinition[WordCountAction, WordCountObservation]):
    name = "yell"

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence[ShoutTool]:  # noqa: ARG003
        return []


class GatedTool(ToolDefinition[WordCountAction, WordCountObservation]):
    @classmethod
    def is_usable(cls) -> bool:
        return os.environ.get("GATED_TOOL_USABLE") == "1"

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence[GatedTool]:  # noqa: ARG003
        return []


class BrokenCheckTool(ToolDefinition[WordCountAction, WordCountObservation]):
    @classmethod
    def is_usable(cls) -> bool:
        raise RuntimeError("probe failed")

    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence[BrokenCheckTool]:  # noqa: ARG003
        return []


register_tool(WordCountTool.name, WordCountTool)
register_tool(NotebookToolSet.name, NotebookToolSet)
register_tool(ShoutTool.name, ShoutTool)
register_tool(GatedTool.name, GatedTool)
register_tool(BrokenCheckTool.name, BrokenCheckTool)
