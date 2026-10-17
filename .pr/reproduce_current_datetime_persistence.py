"""Exercise FileSettingsStore with existing v3/v6 settings files."""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal

from openhands.agent_server.persistence.models import PersistedSettings
from openhands.agent_server.persistence.store import FileSettingsStore


STALE_DATETIME = "2024-03-15T14:30:00Z"


def _current_datetime(settings: PersistedSettings) -> datetime | str | None:
    context = settings.agent_settings.agent_context
    assert context is not None
    return context.current_datetime


def _display(value: datetime | str | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else value


def _run_case(
    name: str,
    agent_settings: dict[str, object],
    expect_runtime: Literal["persisted", "omitted"],
) -> None:
    input_payload = {"schema_version": 3, "agent_settings": agent_settings}
    with tempfile.TemporaryDirectory() as directory:
        settings_path = Path(directory) / "settings.json"
        settings_path.write_text(json.dumps(input_payload), encoding="utf-8")

        store = FileSettingsStore(directory)
        loaded = store.load()
        assert loaded is not None
        loaded_datetime = _current_datetime(loaded)

        store.update(lambda current: current)

        saved_payload = json.loads(settings_path.read_text(encoding="utf-8"))
        saved_context = saved_payload["agent_settings"]["agent_context"]
        reloaded = store.load()
        assert reloaded is not None
        reloaded_datetime = _current_datetime(reloaded)
        reloaded_context = reloaded.agent_settings.agent_context
        assert reloaded_context is not None
        formatted_datetime = reloaded_context.get_formatted_datetime()

        result = {
            "case": name,
            "input_file_schema": input_payload["schema_version"],
            "input_agent_schema": agent_settings["schema_version"],
            "saved_file_schema": saved_payload["schema_version"],
            "saved_agent_schema": saved_payload["agent_settings"]["schema_version"],
            "saved_current_datetime": saved_context.get("current_datetime", "<absent>"),
            "loaded_current_datetime": _display(loaded_datetime),
            "reloaded_current_datetime": _display(reloaded_datetime),
            "formatted_current_datetime": formatted_datetime,
        }

        if name == "OpenHands":
            if expect_runtime == "persisted":
                assert "current_datetime" in saved_context
                assert formatted_datetime.startswith("2024-03-15T14:30")
            else:
                assert "current_datetime" not in saved_context
                assert isinstance(reloaded_datetime, datetime)
                assert reloaded_datetime.year != 2024
        else:
            assert saved_context["current_datetime"] is None
            assert reloaded_datetime is None
            assert formatted_datetime is None

        print(json.dumps(result, default=str, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--expect-runtime",
        choices=("persisted", "omitted"),
        required=True,
    )
    args = parser.parse_args()

    _run_case(
        "OpenHands",
        {
            "schema_version": 6,
            "agent_kind": "openhands",
            "llm": {"model": "test-model"},
            "agent_context": {"current_datetime": STALE_DATETIME},
        },
        args.expect_runtime,
    )
    _run_case(
        "ACP",
        {
            "schema_version": 6,
            "agent_kind": "acp",
            "acp_server": "codex",
            "agent_context": {"current_datetime": None},
        },
        args.expect_runtime,
    )


if __name__ == "__main__":
    main()
