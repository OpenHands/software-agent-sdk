"""Tool for reading local image files and returning vision content."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, override

from pydantic import Field

from openhands.sdk.llm import ImageContent, TextContent
from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    register_tool,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
DEFAULT_MAX_MB = 10


class ImageReaderAction(Action):
    """Schema for loading an image from disk."""

    path: str = Field(
        description=(
            "Path to the image file. Relative paths are resolved against the current "
            "workspace; absolute paths are also supported."
        )
    )


class ImageReaderObservation(Observation):
    """Observation containing the loaded image and metadata."""

    path: str = Field(description="Resolved absolute path to the image file")
    mime_type: str = Field(description="Detected MIME type of the image")
    size_bytes: int = Field(description="Size of the image file in bytes")
    data_url: str = Field(description="Image encoded as a data URL for the LLM")

    @property
    @override
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        meta = (
            f"Loaded image from {self.path}\n"
            f"MIME type: {self.mime_type}\n"
            f"Size: {self.size_bytes} bytes"
        )
        return [
            TextContent(text=meta),
            ImageContent(image_urls=[self.data_url]),
        ]


IMAGE_READER_DESCRIPTION = (
    "Load a local image file and return it to the LLM as vision content.\n\n"
    "Usage:\n"
    "- Provide an absolute path, or a path relative to the current workspace.\n"
    f"- Supported formats: {', '.join(sorted(IMAGE_EXTENSIONS))}\n"
    f"- Max file size: {DEFAULT_MAX_MB} MB.\n\n"
    "Notes:\n"
    "- Use this when you need to inspect screenshots, design assets, or other "
    "images that were generated or already exist on disk.\n"
    "- This tool is read-only and will not modify files."
)


class ImageReaderTool(ToolDefinition[ImageReaderAction, ImageReaderObservation]):
    """Tool for reading images from disk and returning vision content."""

    @classmethod
    def create(
        cls,
        conv_state: ConversationState,
        max_size_mb: int = DEFAULT_MAX_MB,
    ) -> Sequence[ImageReaderTool]:
        from openhands.tools.image_reader.impl import ImageReaderExecutor

        executor = ImageReaderExecutor(
            workspace_root=conv_state.workspace.working_dir,
            max_size_mb=max_size_mb,
        )

        description = IMAGE_READER_DESCRIPTION
        if not conv_state.agent.llm.vision_is_active():
            description = (
                IMAGE_READER_DESCRIPTION
                + "\nWarning: Current LLM does not have vision enabled."
            )

        return [
            cls(
                action_type=ImageReaderAction,
                observation_type=ImageReaderObservation,
                description=description,
                annotations=ToolAnnotations(
                    title="image_read",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                executor=executor,
            )
        ]


register_tool(ImageReaderTool.name, ImageReaderTool)
