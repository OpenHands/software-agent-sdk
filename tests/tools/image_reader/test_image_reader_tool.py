from __future__ import annotations

import base64

import pytest

from openhands.sdk.llm import ImageContent
from openhands.tools.image_reader.definition import ImageReaderAction
from openhands.tools.image_reader.impl import ImageReaderExecutor


SAMPLE_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNg"
    "YAAAAAMAAWgmWQ0AAAAASUVORK5CYII="
)


def test_image_reader_loads_image(tmp_path):
    img_bytes = base64.b64decode(SAMPLE_PNG_BASE64)
    img_path = tmp_path / "sample.png"
    img_path.write_bytes(img_bytes)

    executor = ImageReaderExecutor(workspace_root=str(tmp_path))
    obs = executor(ImageReaderAction(path="sample.png"))

    assert obs.path == str(img_path.resolve())
    assert obs.mime_type == "image/png"
    assert obs.size_bytes == len(img_bytes)

    contents = obs.to_llm_content
    assert any(isinstance(c, ImageContent) for c in contents)
    image_blocks = [c for c in contents if isinstance(c, ImageContent)]
    assert image_blocks[0].image_urls[0].startswith("data:image/png;base64,")


def test_image_reader_rejects_outside_workspace(tmp_path):
    img_bytes = base64.b64decode(SAMPLE_PNG_BASE64)
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(img_bytes)

    executor = ImageReaderExecutor(workspace_root=str(tmp_path))
    with pytest.raises(PermissionError):
        executor(ImageReaderAction(path=str(outside)))
