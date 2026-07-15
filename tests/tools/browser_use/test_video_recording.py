from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openhands.tools.browser_use.video_recording import BrowserVideoRecorder


@pytest.mark.asyncio
async def test_video_recorder_returns_absolute_webm_path(tmp_path, monkeypatch):
    monkeypatch.setenv("DISPLAY", ":1")
    monkeypatch.setenv("VNC_GEOMETRY", "1280x800")
    process = MagicMock()
    process.returncode = None
    process.wait = AsyncMock(return_value=0)
    output_path: Path | None = None

    async def create_process(*args, **_kwargs):
        nonlocal output_path
        output_path = Path(args[-1])
        return process

    recorder = BrowserVideoRecorder(str(tmp_path))
    with (
        patch("shutil.which", return_value="/usr/bin/ffmpeg"),
        patch("asyncio.create_subprocess_exec", side_effect=create_process),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        start_result = await recorder.start()
        assert output_path is not None
        output_path.write_bytes(b"webm")
        stop_result = await recorder.stop()

    assert str(output_path.resolve()) in start_result
    assert str(output_path.resolve()) in stop_result
    process.send_signal.assert_called_once()


@pytest.mark.asyncio
async def test_video_recorder_requires_ffmpeg(tmp_path):
    recorder = BrowserVideoRecorder(str(tmp_path))

    with patch("shutil.which", return_value=None):
        result = await recorder.start()

    assert result == "Error: ffmpeg is required for browser video recording"


@pytest.mark.asyncio
async def test_video_recorder_terminates_process_after_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("DISPLAY", ":1")
    process = MagicMock()
    process.returncode = None
    process.wait = AsyncMock(return_value=0)

    recorder = BrowserVideoRecorder(str(tmp_path))
    with (
        patch("shutil.which", return_value="/usr/bin/ffmpeg"),
        patch("asyncio.create_subprocess_exec", return_value=process),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        await recorder.start()

    output_path = recorder._output_path
    assert output_path is not None
    output_path.write_bytes(b"webm")
    process.wait = AsyncMock(side_effect=[TimeoutError, 0])

    result = await recorder.stop()

    assert result.startswith("Browser video recording saved:")
    process.terminate.assert_called_once()
