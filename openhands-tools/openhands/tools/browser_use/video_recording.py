"""Visible desktop video recording for browser QA evidence."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
from datetime import UTC, datetime
from pathlib import Path


VIDEO_OUTPUT_DIR = "browser_videos"


class BrowserVideoRecorder:
    """Record the X11 desktop containing the headed browser to WebM."""

    def __init__(self, output_root: str | None) -> None:
        self._output_root = output_root
        self._process: asyncio.subprocess.Process | None = None
        self._output_path: Path | None = None

    @property
    def is_recording(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> str:
        if self.is_recording:
            assert self._output_path is not None
            return f"Error: Video recording is already active at {self._output_path}"

        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            return "Error: ffmpeg is required for browser video recording"

        display = os.getenv("DISPLAY")
        if display is None:
            return "Error: DISPLAY is required for visible browser video recording"

        geometry = os.getenv("VNC_GEOMETRY", "1280x800")
        if not self._valid_geometry(geometry):
            return f"Error: Invalid VNC_GEOMETRY value: {geometry}"

        output_root = (
            Path(self._output_root)
            if self._output_root is not None
            else Path.cwd() / ".agent_tmp" / "browser_observations"
        )
        output_dir = output_root / VIDEO_OUTPUT_DIR
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        self._output_path = (output_dir / f"browser-{timestamp}.webm").resolve()

        self._process = await asyncio.create_subprocess_exec(
            ffmpeg,
            "-y",
            "-f",
            "x11grab",
            "-framerate",
            "15",
            "-video_size",
            geometry,
            "-i",
            f"{display}.0",
            "-an",
            "-c:v",
            "libvpx-vp9",
            "-deadline",
            "realtime",
            "-cpu-used",
            "8",
            "-b:v",
            "1M",
            str(self._output_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.sleep(0.5)
        if self._process.returncode is not None:
            returncode = self._process.returncode
            self._reset()
            return f"Error: ffmpeg exited before recording started ({returncode})"

        return f"Browser video recording started: {self._output_path}"

    async def stop(self) -> str:
        if not self.is_recording:
            return "Error: Browser video recording is not active"

        assert self._process is not None
        assert self._output_path is not None
        process = self._process
        output_path = self._output_path
        process.send_signal(signal.SIGINT)
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except TimeoutError:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()
        self._reset()

        if not output_path.is_file() or output_path.stat().st_size == 0:
            return "Error: Browser video recording did not produce a file"
        return f"Browser video recording saved: {output_path}"

    @staticmethod
    def _valid_geometry(geometry: str) -> bool:
        width, separator, height = geometry.partition("x")
        return (
            separator == "x"
            and width.isdigit()
            and height.isdigit()
            and int(width) > 0
            and int(height) > 0
        )

    def _reset(self) -> None:
        self._process = None
        self._output_path = None
