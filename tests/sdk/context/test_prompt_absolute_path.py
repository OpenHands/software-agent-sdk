"""Tests for absolute path support in system_prompt_filename."""

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from jinja2 import FileSystemBytecodeCache, TemplateSyntaxError
from jinja2.bccache import bc_magic
from pydantic import SecretStr

from openhands.sdk.agent.agent import Agent
from openhands.sdk.context.prompts import prompt
from openhands.sdk.context.prompts.prompt import render_template
from openhands.sdk.llm import LLM


@pytest.fixture
def isolated_prompt_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "state"))
    prompt._get_env.cache_clear()
    prompt._get_template.cache_clear()
    yield tmp_path / "state" / "cache" / "jinja"
    prompt._get_env.cache_clear()
    prompt._get_template.cache_clear()


def test_render_template_syntax_error(
    tmp_path: Path, isolated_prompt_cache: Path
) -> None:
    template = tmp_path / "broken.j2"
    template.write_text("{% if %}", encoding="utf-8")
    with pytest.raises(TemplateSyntaxError) as exc_info:
        render_template(str(tmp_path), template.name)
    assert str(template) in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, TemplateSyntaxError)


def test_render_template_decode_error(
    tmp_path: Path, isolated_prompt_cache: Path
) -> None:
    template = tmp_path / "broken.j2"
    template.write_bytes(b"\xff")
    with pytest.raises(UnicodeDecodeError):
        render_template(str(tmp_path), template.name)


@pytest.mark.parametrize("payload", [b"", b"invalid pickle"])
def test_render_template_with_corrupt_cache(
    tmp_path: Path, isolated_prompt_cache: Path, payload: bytes
) -> None:
    template = tmp_path / "template.j2"
    template.write_text("Hello {{ name }}", encoding="utf-8")
    assert render_template(str(tmp_path), template.name, name="First") == "Hello First"
    cache_file = next(isolated_prompt_cache.glob("*.cache"))
    cache_file.write_bytes(bc_magic + payload)
    prompt._get_env.cache_clear()
    prompt._get_template.cache_clear()
    assert (
        render_template(str(tmp_path), template.name, name="Second") == "Hello Second"
    )


@pytest.mark.parametrize("create_environment_first", [False, True])
def test_render_template_with_unavailable_cache(
    tmp_path: Path, isolated_prompt_cache: Path, create_environment_first: bool
) -> None:
    if create_environment_first:
        prompt._get_env(str(tmp_path))
        isolated_prompt_cache.rmdir()
    else:
        isolated_prompt_cache.parent.mkdir(parents=True)
    isolated_prompt_cache.write_text("not a directory", encoding="utf-8")
    for name in ["first.j2", "second.j2"]:
        (tmp_path / name).write_text("Hello {{ name }}", encoding="utf-8")
        assert render_template(str(tmp_path), name, name="World") == "Hello World"


@pytest.mark.parametrize("operation", ["load_bytecode", "dump_bytecode"])
def test_render_template_disables_failed_cache(
    tmp_path: Path,
    isolated_prompt_cache: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    calls = []

    def fail_cache(*args: object) -> None:
        calls.append(None)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(FileSystemBytecodeCache, operation, fail_cache)
    for name in ["first.j2", "second.j2"]:
        (tmp_path / name).write_text("Hello {{ name }}", encoding="utf-8")
        assert render_template(str(tmp_path), name, name="World") == "Hello World"
    assert len(calls) == 1


def test_render_template_with_relative_path():
    """Test that render_template resolves a relative name against prompt_dir.

    The built-in .j2 templates were ported into the prompt registry, so this uses a
    throwaway template in a temp dir to exercise relative-path resolution.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        template_name = "relative_template.j2"
        with open(os.path.join(tmp_dir, template_name), "w") as f:
            f.write("Relative render of {{ subject }}.")

        result = render_template(
            prompt_dir=tmp_dir,
            template_name=template_name,
            subject="a template",
        )

    assert result == "Relative render of a template."


def test_render_template_with_absolute_path():
    """Test that render_template works with absolute paths."""
    # Create a temporary template file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".j2", delete=False) as tmp_file:
        tmp_file.write("Hello {{ name }}! This is a test template.")
        tmp_path = tmp_file.name

    try:
        # Render using absolute path
        result = render_template(
            prompt_dir="/unused/dir",  # This should be ignored for absolute paths
            template_name=tmp_path,
            name="World",
        )

        assert result == "Hello World! This is a test template."
    finally:
        # Clean up
        os.unlink(tmp_path)


def test_agent_with_absolute_system_prompt_path():
    """Test that Agent can use an absolute path for system_prompt_filename."""
    # Create a temporary template file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".j2", delete=False) as tmp_file:
        tmp_file.write(
            "You are a test assistant. CLI mode: {{ cli_mode|default(false) }}"
        )
        tmp_path = tmp_file.name

    try:
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )

        # Create agent with absolute path to system prompt
        agent = Agent(
            llm=llm,
            tools=[],
            system_prompt_filename=tmp_path,
            system_prompt_kwargs={"cli_mode": True},
        )

        # Get system message
        system_message = agent.static_system_message

        # Verify the message was rendered correctly
        assert "You are a test assistant" in system_message
        assert "CLI mode: True" in system_message
    finally:
        # Clean up
        os.unlink(tmp_path)


def test_agent_with_relative_system_prompt_path():
    """Test that Agent still works with relative paths (backward compatibility)."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")

    # Create agent with default relative path
    agent = Agent(
        llm=llm,
        tools=[],
        system_prompt_filename="system_prompt.j2",  # Relative path
    )

    # Get system message
    system_message = agent.static_system_message

    # Verify the message was rendered correctly
    assert isinstance(system_message, str)
    assert len(system_message) > 0


def test_render_template_with_nonexistent_absolute_path():
    """Test render_template raises error for nonexistent absolute path."""  # noqa: E501
    nonexistent_path = "/nonexistent/directory/template.j2"

    with pytest.raises(FileNotFoundError, match="Prompt file"):
        render_template(
            prompt_dir="/unused/dir",
            template_name=nonexistent_path,
            name="Test",
        )


def test_render_template_with_nonexistent_relative_path():
    """Test render_template raises error for nonexistent relative path."""  # noqa: E501
    with tempfile.TemporaryDirectory() as tmp_dir:
        with pytest.raises(FileNotFoundError, match="Prompt file"):
            render_template(
                prompt_dir=tmp_dir,
                template_name="nonexistent_template.j2",
                name="Test",
            )
