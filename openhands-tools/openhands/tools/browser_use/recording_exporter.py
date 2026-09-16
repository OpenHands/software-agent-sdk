"""Export rrweb browser recordings to watchable media files."""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Literal


RecordingExportFormat = Literal["mp4", "gif"]

_RRWEB_PLAYER_VERSION = "2.0.0-alpha.17"
_DEFAULT_PLAYER_SCRIPT_URL = (
    f"https://unpkg.com/rrweb-player@{_RRWEB_PLAYER_VERSION}/dist/rrweb-player.umd.cjs"
)
_DEFAULT_PLAYER_STYLE_URL = (
    f"https://cdn.jsdelivr.net/npm/rrweb-player@{_RRWEB_PLAYER_VERSION}/dist/style.css"
)
_SUPPORTED_FORMATS: tuple[RecordingExportFormat, ...] = ("mp4", "gif")


class BrowserRecordingExportError(RuntimeError):
    """Raised when an rrweb recording cannot be exported."""


@dataclass(frozen=True)
class RecordingExportOptions:
    """Options for rendering an rrweb recording."""

    width: int = 1280
    height: int = 720
    fps: int = 30
    speed: float = 1.0
    playback_extra_ms: int = 1000
    rrweb_player_script_url: str = _DEFAULT_PLAYER_SCRIPT_URL
    rrweb_player_style_url: str = _DEFAULT_PLAYER_STYLE_URL
    chromium_executable_path: str | None = None

    def validate(self) -> None:
        if self.width <= 0:
            raise BrowserRecordingExportError("Export width must be greater than 0")
        if self.height <= 0:
            raise BrowserRecordingExportError("Export height must be greater than 0")
        if self.fps <= 0:
            raise BrowserRecordingExportError("Export fps must be greater than 0")
        if self.speed <= 0:
            raise BrowserRecordingExportError("Export speed must be greater than 0")
        if self.playback_extra_ms < 0:
            raise BrowserRecordingExportError("Playback extra time cannot be negative")


def find_latest_recording(
    root_dir: str | os.PathLike[str] = ".agent_tmp/browser_observations",
) -> Path:
    """Return the newest recording directory under the browser observations root."""
    root = Path(root_dir)
    if not root.exists():
        raise BrowserRecordingExportError(f"Recording root does not exist: {root}")

    candidates = sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and path.name.startswith("recording-")
    )
    for candidate in reversed(candidates):
        if list(candidate.glob("*.json")):
            return candidate

    raise BrowserRecordingExportError(f"No recording directories found in: {root}")


def load_recording_events(recording_path: str | os.PathLike[str]) -> list[dict]:
    """Load rrweb events from a recording JSON file or recording directory."""
    path = Path(recording_path)
    if path.is_dir():
        json_paths = sorted(path.glob("*.json"))
    elif path.is_file():
        json_paths = [path]
    else:
        raise BrowserRecordingExportError(f"Recording path does not exist: {path}")

    events: list[dict] = []
    for json_path in json_paths:
        try:
            payload = json.loads(json_path.read_text())
        except json.JSONDecodeError as error:
            raise BrowserRecordingExportError(
                f"Recording file is not valid JSON: {json_path}"
            ) from error

        if isinstance(payload, list):
            chunk = payload
        elif isinstance(payload, dict) and isinstance(payload.get("events"), list):
            chunk = payload["events"]
        else:
            raise BrowserRecordingExportError(
                f"Recording file must contain an rrweb event list: {json_path}"
            )

        if not all(isinstance(event, dict) for event in chunk):
            raise BrowserRecordingExportError(
                f"Recording file contains non-object events: {json_path}"
            )
        events.extend(chunk)

    if not events:
        raise BrowserRecordingExportError(f"No rrweb events found in: {path}")

    return sorted(events, key=lambda event: event.get("timestamp", 0))


def recording_duration_ms(events: Sequence[dict]) -> int:
    """Return the recording duration in milliseconds, with a small minimum."""
    timestamps = [
        timestamp
        for event in events
        if isinstance(timestamp := event.get("timestamp"), int | float)
    ]
    if len(timestamps) < 2:
        return 1000
    return max(1000, int(max(timestamps) - min(timestamps)))


def infer_export_format(
    output_path: str | os.PathLike[str] | None,
    output_format: str | None,
) -> RecordingExportFormat:
    """Infer and validate the requested export format."""
    requested = output_format
    if requested is None and output_path is not None:
        suffix = Path(output_path).suffix.lower().removeprefix(".")
        requested = suffix or None
    if requested is None:
        requested = "mp4"

    if requested not in _SUPPORTED_FORMATS:
        raise BrowserRecordingExportError(
            "Unsupported export format "
            f"'{requested}'. Expected one of: {', '.join(_SUPPORTED_FORMATS)}"
        )
    return requested


def build_rrweb_player_html(
    events: Sequence[dict],
    options: RecordingExportOptions | None = None,
) -> str:
    """Build a self-contained rrweb-player page for rendering captured events."""
    resolved_options = options or RecordingExportOptions()
    resolved_options.validate()

    encoded_events = base64.b64encode(
        json.dumps(list(events), separators=(",", ":")).encode()
    ).decode()
    duration_ms = recording_duration_ms(events)
    playback_timeout_ms = int(
        duration_ms / resolved_options.speed + resolved_options.playback_extra_ms
    )

    return f"""<!doctype html>
<html>
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <link rel=\"stylesheet\" href=\"{resolved_options.rrweb_player_style_url}\" />
    <style>
      html, body {{
        margin: 0;
        width: {resolved_options.width}px;
        height: {resolved_options.height}px;
        overflow: hidden;
        background: #111;
      }}
      #player {{
        width: {resolved_options.width}px;
        height: {resolved_options.height}px;
      }}
    </style>
  </head>
  <body>
    <div id=\"player\"></div>
    <script src=\"{resolved_options.rrweb_player_script_url}\"></script>
    <script>
      window.__rrwebExportReady = false;
      window.__rrwebExportDone = false;
      window.__rrwebExportError = null;

      const encodedEvents = \"{encoded_events}\";
      const binaryEvents = atob(encodedEvents);
      const eventBytes = Uint8Array.from(binaryEvents, (char) => char.charCodeAt(0));
      const events = JSON.parse(new TextDecoder().decode(eventBytes));

      function markExportDone() {{
        window.setTimeout(() => {{
          window.__rrwebExportDone = true;
        }}, {playback_timeout_ms});
      }}

      function resolvePlayerConstructor() {{
        return window.rrwebPlayer?.default || window.rrwebPlayer || window.RRWebPlayer;
      }}

      function play(player) {{
        if (typeof player.play === \"function\") {{
          player.play();
          return;
        }}
        if (
          typeof player.getReplayer === \"function\" &&
          typeof player.getReplayer().play === \"function\"
        ) {{
          player.getReplayer().play();
          return;
        }}
        throw new Error(\"rrweb-player did not expose a play method\");
      }}

      window.addEventListener(\"load\", () => {{
        try {{
          const Player = resolvePlayerConstructor();
          if (!Player) {{
            throw new Error(\"rrweb-player failed to load\");
          }}
          const player = new Player({{
            target: document.getElementById(\"player\"),
            props: {{
              events,
              width: {resolved_options.width},
              height: {resolved_options.height},
              autoPlay: false,
              showController: false,
              skipInactive: false,
              speed: {resolved_options.speed},
            }},
          }});
          window.__rrwebExportPlayer = player;
          window.__startRrwebExportPlayback = () => {{
            try {{
              play(player);
              markExportDone();
            }} catch (error) {{
              window.__rrwebExportError = String(error);
            }}
          }};
          window.__rrwebExportReady = true;
        }} catch (error) {{
          window.__rrwebExportError = String(error);
        }}
      }});
    </script>
  </body>
</html>
"""


def _default_chromium_executable() -> str | None:
    for executable in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        path = shutil.which(executable)
        if path:
            return path
    return None


def _chromium_launch_args() -> list[str]:
    getuid = getattr(os, "getuid", None)
    if getuid is not None and getuid() == 0:
        return ["--no-sandbox", "--disable-setuid-sandbox"]
    return []


def render_rrweb_events_to_webm(
    events: Sequence[dict],
    output_path: str | os.PathLike[str],
    options: RecordingExportOptions | None = None,
) -> Path:
    """Render rrweb events through rrweb-player and save a WebM capture."""
    resolved_options = options or RecordingExportOptions()
    resolved_options.validate()

    try:
        sync_api = import_module("playwright.sync_api")
    except ImportError as error:
        raise BrowserRecordingExportError(
            "Exporting browser recordings requires Playwright. Install it with "
            "`pip install playwright` or `uv pip install playwright`."
        ) from error
    sync_playwright = sync_api.sync_playwright

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    html = build_rrweb_player_html(events, resolved_options)
    playback_timeout_ms = int(
        recording_duration_ms(events) / resolved_options.speed
        + resolved_options.playback_extra_ms
        + 15_000
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        html_path = temp_path / "recording-player.html"
        html_path.write_text(html)
        video_dir = temp_path / "video"
        video_dir.mkdir()

        try:
            with sync_playwright() as playwright:
                executable_path = (
                    resolved_options.chromium_executable_path
                    or _default_chromium_executable()
                )
                if executable_path:
                    browser = playwright.chromium.launch(
                        headless=True,
                        args=_chromium_launch_args(),
                        executable_path=executable_path,
                    )
                else:
                    browser = playwright.chromium.launch(
                        headless=True,
                        args=_chromium_launch_args(),
                    )
                context = browser.new_context(
                    viewport={
                        "width": resolved_options.width,
                        "height": resolved_options.height,
                    },
                    record_video_dir=str(video_dir),
                    record_video_size={
                        "width": resolved_options.width,
                        "height": resolved_options.height,
                    },
                )
                page = context.new_page()
                video = page.video
                if video is None:
                    context.close()
                    browser.close()
                    raise BrowserRecordingExportError(
                        "Playwright video capture did not start"
                    )

                try:
                    page.goto(html_path.as_uri(), wait_until="networkidle")
                    page.wait_for_function(
                        "window.__rrwebExportReady === true || "
                        "Boolean(window.__rrwebExportError)",
                        timeout=15_000,
                    )
                    error = page.evaluate("window.__rrwebExportError")
                    if error:
                        raise BrowserRecordingExportError(str(error))

                    page.evaluate("window.__startRrwebExportPlayback()")
                    page.wait_for_function(
                        "window.__rrwebExportDone === true || "
                        "Boolean(window.__rrwebExportError)",
                        timeout=playback_timeout_ms,
                    )
                    error = page.evaluate("window.__rrwebExportError")
                    if error:
                        raise BrowserRecordingExportError(str(error))
                finally:
                    context.close()
                    browser.close()

                generated_video = Path(video.path())
                shutil.copyfile(generated_video, destination)
        except BrowserRecordingExportError:
            raise
        except Exception as error:
            raise BrowserRecordingExportError(
                "Playwright failed while rendering the rrweb recording. "
                "If video capture support is missing, run `playwright install ffmpeg`. "
                f"Original error: {error}"
            ) from error

    return destination


def _require_ffmpeg() -> str:
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path is None:
        raise BrowserRecordingExportError(
            "Exporting browser recordings to mp4/gif requires ffmpeg on PATH."
        )
    return ffmpeg_path


def _run_ffmpeg(command: list[str]) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        stderr = (error.stderr or "").strip()
        if len(stderr) > 2000:
            stderr = stderr[-2000:]
        message = "ffmpeg failed while exporting browser recording"
        if stderr:
            message = f"{message}: {stderr}"
        raise BrowserRecordingExportError(message) from error


def convert_webm_to_mp4(
    webm_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    fps: int = 30,
) -> Path:
    """Convert a WebM recording to streamable MP4."""
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            _require_ffmpeg(),
            "-y",
            "-i",
            str(webm_path),
            "-an",
            "-vf",
            f"fps={fps}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(destination),
        ]
    )
    return destination


def convert_webm_to_gif(
    webm_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    fps: int = 12,
    width: int = 960,
) -> Path:
    """Convert a WebM recording to an inline-friendly GIF."""
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    filter_graph = (
        f"fps={fps},scale={width}:-1:flags=lanczos,split[s0][s1];"
        "[s0]palettegen[p];[s1][p]paletteuse"
    )
    _run_ffmpeg(
        [
            _require_ffmpeg(),
            "-y",
            "-i",
            str(webm_path),
            "-vf",
            filter_graph,
            str(destination),
        ]
    )
    return destination


def _default_output_path(
    recording_path: str | os.PathLike[str],
    output_format: RecordingExportFormat,
) -> Path:
    path = Path(recording_path)
    directory = path if path.is_dir() else path.parent
    return directory / f"recording.{output_format}"


def export_rrweb_recording(
    recording_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str] | None = None,
    output_format: str | None = None,
    options: RecordingExportOptions | None = None,
) -> Path:
    """Export an existing rrweb recording directory or JSON file to MP4/GIF."""
    resolved_format = infer_export_format(output_path, output_format)
    destination = (
        Path(output_path)
        if output_path is not None
        else _default_output_path(recording_path, resolved_format)
    )
    resolved_options = options or RecordingExportOptions()
    events = load_recording_events(recording_path)

    with tempfile.TemporaryDirectory() as temp_dir:
        webm_path = Path(temp_dir) / "recording.webm"
        render_rrweb_events_to_webm(events, webm_path, resolved_options)
        if resolved_format == "mp4":
            return convert_webm_to_mp4(webm_path, destination, fps=resolved_options.fps)
        return convert_webm_to_gif(
            webm_path,
            destination,
            fps=min(resolved_options.fps, 12),
            width=min(resolved_options.width, 960),
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export saved rrweb browser recording JSON to MP4 or GIF."
    )
    parser.add_argument(
        "recording_path",
        help="Recording directory or JSON file produced by browser_stop_recording.",
    )
    parser.add_argument(
        "-o",
        "--output",
        dest="output_path",
        help="Output media path. Defaults to recording.<format> in the recording dir.",
    )
    parser.add_argument(
        "--format",
        choices=_SUPPORTED_FORMATS,
        dest="output_format",
        help="Output format. Inferred from --output when omitted; defaults to mp4.",
    )
    parser.add_argument("--width", type=int, default=RecordingExportOptions.width)
    parser.add_argument("--height", type=int, default=RecordingExportOptions.height)
    parser.add_argument("--fps", type=int, default=RecordingExportOptions.fps)
    parser.add_argument("--speed", type=float, default=RecordingExportOptions.speed)
    parser.add_argument(
        "--playback-extra-ms",
        type=int,
        default=RecordingExportOptions.playback_extra_ms,
    )
    parser.add_argument(
        "--chromium-executable-path",
        help="Chromium/Chrome executable for Playwright to launch.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for exporting rrweb browser recordings."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    options = RecordingExportOptions(
        width=args.width,
        height=args.height,
        fps=args.fps,
        speed=args.speed,
        playback_extra_ms=args.playback_extra_ms,
        chromium_executable_path=args.chromium_executable_path,
    )

    try:
        output = export_rrweb_recording(
            args.recording_path,
            output_path=args.output_path,
            output_format=args.output_format,
            options=options,
        )
    except BrowserRecordingExportError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(f"Exported browser recording to: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
