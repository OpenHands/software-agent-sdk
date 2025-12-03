"""Executor implementation for the image reader tool."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from openhands.sdk.tool import ToolExecutor
from openhands.tools.image_reader.definition import (
    DEFAULT_MAX_MB,
    IMAGE_EXTENSIONS,
    ImageReaderAction,
    ImageReaderObservation,
)


class ImageReaderExecutor(ToolExecutor[ImageReaderAction, ImageReaderObservation]):
    """Load local images and present them as LLM vision content."""

    _workspace_root: Path | None
    _max_size_bytes: int

    def __init__(
        self,
        workspace_root: str | None = None,
        max_size_mb: int = DEFAULT_MAX_MB,
    ):
        self._workspace_root = Path(workspace_root).resolve() if workspace_root else None
        self._max_size_bytes = max_size_mb * 1024 * 1024

    def __call__(self, *, path: str) -> ImageReaderObservation:
        resolved = self._resolve_path(path)

        if not resolved.exists():
            raise FileNotFoundError(f"Image not found: {resolved}")
        if not resolved.is_file():
            raise IsADirectoryError(f"Path is not a file: {resolved}")

        if resolved.suffix.lower() not in IMAGE_EXTENSIONS:
            supported = ", ".join(sorted(IMAGE_EXTENSIONS))
            raise ValueError(
                f"Unsupported image format for {resolved}. "
                f"Supported extensions: {supported}"
            )

        size_bytes = resolved.stat().st_size
        if size_bytes > self._max_size_bytes:
            raise ValueError(
                f"Image size {size_bytes} bytes exceeds limit of "
                f"{self._max_size_bytes} bytes"
            )

        image_bytes = resolved.read_bytes()
        mime_type, _ = mimetypes.guess_type(str(resolved))
        mime_type = mime_type or "image/png"
        data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode()}"

        return ImageReaderObservation(
            path=str(resolved),
            mime_type=mime_type,
            size_bytes=size_bytes,
            data_url=data_url,
        )

    def _resolve_path(self, path: str) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            if self._workspace_root:
                candidate = (self._workspace_root / candidate).resolve()
            else:
                candidate = candidate.resolve()
        else:
            candidate = candidate.resolve()

        if self._workspace_root and not str(candidate).startswith(
            str(self._workspace_root)
        ):
            raise PermissionError(
                f"Path {candidate} is outside the workspace root {self._workspace_root}"
            )
        return candidate
