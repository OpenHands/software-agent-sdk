"""Tests for structured truncation metadata and spillover persistence (#6994)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.llm import TextContent
from openhands.sdk.utils import TruncationMetadata, truncate_with_metadata
from openhands.tools.terminal.constants import MAX_CMD_OUTPUT_SIZE
from openhands.tools.terminal.definition import TerminalAction, TerminalObservation
from openhands.tools.terminal.impl import TerminalExecutor
from openhands.tools.terminal.metadata import CmdOutputMetadata


def test_truncate_with_metadata_populated_on_spillover(tmp_path: Path):
    lines = [f"ROW-{idx:04d}: payload={'x' * 40}" for idx in range(1, 401)]
    content = "\n".join(lines)
    res = truncate_with_metadata(
        content,
        truncate_after=1200,
        save_dir=str(tmp_path),
        tool_prefix="terminal",
    )

    assert res.metadata.truncated is True
    assert res.metadata.original_chars == len(content)
    assert res.metadata.original_lines == 400
    assert res.metadata.omitted_chars > 0
    assert res.metadata.kept_chars + res.metadata.omitted_chars == len(content)
    assert res.metadata.head_end_line is not None and res.metadata.head_end_line > 0
    assert (
        res.metadata.tail_start_line is not None
        and res.metadata.tail_start_line > res.metadata.head_end_line
    )
    assert res.metadata.spillover_path is not None
    spill = Path(res.metadata.spillover_path)
    assert spill.is_file()
    assert spill.read_text(encoding="utf-8") == content


def test_terminal_executor_persists_full_output_when_save_dir_configured(
    tmp_path: Path,
):
    obs_dir = tmp_path / "observations"
    executor = TerminalExecutor(
        working_dir=str(tmp_path),
        terminal_type="subprocess",
        full_output_save_dir=str(obs_dir),
    )
    try:
        obs = executor(
            TerminalAction(
                command=(
                    'python3 -c "for i in range(1, 1501): '
                    "print(f'LINE-{i:04d}-' + 'Z' * 30)\""
                )
            )
        )
        assert isinstance(obs.truncation, TruncationMetadata)
        assert obs.truncation.truncated is True
        assert obs.truncation.original_chars > MAX_CMD_OUTPUT_SIZE
        assert obs.truncation.original_lines >= 1500
        assert obs.truncation.spillover_path is not None

        spill_file = Path(obs.truncation.spillover_path)
        assert spill_file.is_file()
        full_saved = spill_file.read_text(encoding="utf-8")
        assert "LINE-0750-" in full_saved
        assert "LINE-0001-" in full_saved
        assert "LINE-1500-" in full_saved

        llm_blocks = obs.to_llm_content
        llm_text = "\n".join(
            block.text for block in llm_blocks if isinstance(block, TextContent)
        )
        assert obs.truncation.spillover_path in llm_text
    finally:
        executor.close()


def test_terminal_executor_persistence_off_writes_no_files_but_reports_metadata(
    tmp_path: Path,
):
    executor = TerminalExecutor(
        working_dir=str(tmp_path),
        terminal_type="subprocess",
        full_output_save_dir=None,
    )
    try:
        obs = executor(
            TerminalAction(
                command=(
                    'python3 -c "for i in range(1, 1501): '
                    "print(f'LINE-{i:04d}-' + 'Z' * 30)\""
                )
            )
        )
        assert isinstance(obs.truncation, TruncationMetadata)
        assert obs.truncation.truncated is True
        assert obs.truncation.spillover_path is None
        assert list(tmp_path.iterdir()) == []
    finally:
        executor.close()


def test_terminal_executor_masks_secrets_in_spillover_file(tmp_path: Path):
    secret_value = "sk-super-secret-spillover-token-998877"
    registry = SecretRegistry()
    registry.update_secrets({"API_TOKEN": secret_value})
    _ = registry.get_secrets_as_env_vars("echo $API_TOKEN")
    fake_conv = SimpleNamespace(state=SimpleNamespace(secret_registry=registry))

    obs_dir = tmp_path / "observations"
    executor = TerminalExecutor(
        working_dir=str(tmp_path),
        terminal_type="subprocess",
        full_output_save_dir=str(obs_dir),
    )
    try:
        obs = executor(
            TerminalAction(
                command=(
                    'python3 -c "import os; '
                    "print('HEAD-' * 4000); "
                    "print('MID:' + os.environ.get('API_TOKEN', '')); "
                    "print('TAIL-' * 4000)\""
                )
            ),
            conversation=fake_conv,  # type: ignore[arg-type]
        )
        assert obs.truncation is not None
        assert obs.truncation.spillover_path is not None
        spill_content = Path(obs.truncation.spillover_path).read_text(encoding="utf-8")
        assert secret_value not in obs.text
        assert secret_value not in spill_content
    finally:
        executor.close()


def test_direct_terminal_observation_populates_truncation_metadata():
    metadata = CmdOutputMetadata(working_dir="/tmp", exit_code=0, pid=1)
    obs = TerminalObservation(
        command="echo long",
        content=[TextContent(text="X" * (MAX_CMD_OUTPUT_SIZE + 2500))],
        metadata=metadata,
    )
    _ = obs.to_llm_content
    assert obs.truncation is not None
    assert obs.truncation.truncated is True
    assert obs.truncation.omitted_chars > 2000
