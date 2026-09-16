import subprocess
from pathlib import Path

import pytest

from openhands.tools.browser_use import recording_exporter
from openhands.tools.browser_use.recording_exporter import (
    BrowserRecordingExportError,
    RecordingExportOptions,
    build_rrweb_player_html,
    convert_webm_to_gif,
    convert_webm_to_mp4,
    export_rrweb_recording,
    find_latest_recording,
    infer_export_format,
    load_recording_events,
)


def _write_events(path: Path, timestamps: list[int]) -> None:
    path.write_text(
        "["
        + ",".join(
            f'{{"type":3,"timestamp":{timestamp},"data":{{}}}}'
            for timestamp in timestamps
        )
        + "]"
    )


def test_load_recording_events_from_directory_sorts_events(tmp_path: Path):
    recording_dir = tmp_path / "recording-20260101-000000-000000"
    recording_dir.mkdir()
    _write_events(recording_dir / "2.json", [300])
    _write_events(recording_dir / "1.json", [200, 100])

    events = load_recording_events(recording_dir)

    assert [event["timestamp"] for event in events] == [100, 200, 300]


def test_load_recording_events_accepts_wrapped_events(tmp_path: Path):
    recording_file = tmp_path / "events.json"
    recording_file.write_text(
        '{"events":[{"type":3,"timestamp":1000,"data":{"source":1}}]}'
    )

    events = load_recording_events(recording_file)

    assert events == [{"type": 3, "timestamp": 1000, "data": {"source": 1}}]


def test_load_recording_events_rejects_empty_recording(tmp_path: Path):
    recording_dir = tmp_path / "recording-20260101-000000-000000"
    recording_dir.mkdir()

    with pytest.raises(BrowserRecordingExportError, match="No rrweb events"):
        load_recording_events(recording_dir)


def test_find_latest_recording_uses_newest_directory_with_json(tmp_path: Path):
    old_recording = tmp_path / "recording-20260101-000000-000000"
    new_recording = tmp_path / "recording-20260101-000001-000000"
    ignored_recording = tmp_path / "recording-20260101-000002-000000"
    old_recording.mkdir()
    new_recording.mkdir()
    ignored_recording.mkdir()
    _write_events(old_recording / "events.json", [100])
    _write_events(new_recording / "events.json", [200])

    assert find_latest_recording(tmp_path) == new_recording


def test_infer_export_format_from_output_suffix():
    assert infer_export_format("recording.gif", None) == "gif"
    assert infer_export_format("recording.mp4", None) == "mp4"
    assert infer_export_format(None, None) == "mp4"


@pytest.mark.parametrize("requested", ["webm", "mov", ""])
def test_infer_export_format_rejects_unsupported_formats(requested: str):
    with pytest.raises(BrowserRecordingExportError, match="Unsupported export format"):
        infer_export_format(None, requested)


def test_build_rrweb_player_html_base64_encodes_event_payload():
    html = build_rrweb_player_html(
        [
            {
                "type": 3,
                "timestamp": 1000,
                "data": {"text": "</script><script>alert('nope')</script>"},
            }
        ],
        RecordingExportOptions(width=640, height=360),
    )

    assert "encodedEvents" in html
    assert "alert('nope')" not in html
    assert "width: 640px" in html
    assert "height: 360px" in html


def test_convert_webm_to_mp4_invokes_ffmpeg_with_faststart(monkeypatch, tmp_path: Path):
    commands: list[list[str]] = []

    def fake_which(command: str) -> str | None:
        return "/usr/bin/ffmpeg" if command == "ffmpeg" else None

    def fake_run(command: list[str], **kwargs):
        commands.append(command)
        assert kwargs == {"check": True, "capture_output": True, "text": True}
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(recording_exporter.shutil, "which", fake_which)
    monkeypatch.setattr(recording_exporter.subprocess, "run", fake_run)

    output = convert_webm_to_mp4(tmp_path / "recording.webm", tmp_path / "out.mp4")

    assert output == tmp_path / "out.mp4"
    assert commands == [
        [
            "/usr/bin/ffmpeg",
            "-y",
            "-i",
            str(tmp_path / "recording.webm"),
            "-an",
            "-vf",
            "fps=30",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(tmp_path / "out.mp4"),
        ]
    ]


def test_convert_webm_to_gif_invokes_ffmpeg_with_palette(monkeypatch, tmp_path: Path):
    commands: list[list[str]] = []

    def fake_which(command: str) -> str | None:
        return "/usr/bin/ffmpeg" if command == "ffmpeg" else None

    def fake_run(command: list[str], **kwargs):
        commands.append(command)
        assert kwargs == {"check": True, "capture_output": True, "text": True}
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(recording_exporter.shutil, "which", fake_which)
    monkeypatch.setattr(recording_exporter.subprocess, "run", fake_run)

    output = convert_webm_to_gif(
        tmp_path / "recording.webm",
        tmp_path / "out.gif",
        fps=8,
        width=480,
    )

    assert output == tmp_path / "out.gif"
    command = commands[0]
    assert command[:4] == [
        "/usr/bin/ffmpeg",
        "-y",
        "-i",
        str(tmp_path / "recording.webm"),
    ]
    filter_graph = command[command.index("-vf") + 1]
    assert "fps=8,scale=480:-1:flags=lanczos" in filter_graph
    assert "palettegen" in filter_graph
    assert command[-1] == str(tmp_path / "out.gif")


def test_export_rrweb_recording_renders_then_converts(monkeypatch, tmp_path: Path):
    recording_dir = tmp_path / "recording-20260101-000000-000000"
    recording_dir.mkdir()
    _write_events(recording_dir / "events.json", [100, 200])
    rendered_events: list[dict] = []

    def fake_render(events, output_path, options):
        rendered_events.extend(events)
        Path(output_path).write_text("webm")
        assert isinstance(options, RecordingExportOptions)
        return Path(output_path)

    def fake_convert(webm_path, output_path, fps):
        assert Path(webm_path).read_text() == "webm"
        assert fps == 24
        Path(output_path).write_text("mp4")
        return Path(output_path)

    monkeypatch.setattr(
        recording_exporter,
        "render_rrweb_events_to_webm",
        fake_render,
    )
    monkeypatch.setattr(recording_exporter, "convert_webm_to_mp4", fake_convert)

    output = export_rrweb_recording(
        recording_dir,
        output_path=tmp_path / "out.mp4",
        options=RecordingExportOptions(fps=24),
    )

    assert output == tmp_path / "out.mp4"
    assert output.read_text() == "mp4"
    assert [event["timestamp"] for event in rendered_events] == [100, 200]
