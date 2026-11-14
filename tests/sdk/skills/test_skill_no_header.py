import pytest

from openhands.sdk.context import Skill


def test_load_markdown_without_frontmatter(tmp_path):
    """Test loading a markdown file without frontmatter."""
    content = "# Test Content\nThis is a test markdown file without frontmatter."
    path = tmp_path / "test.md"
    path.write_text(content)

    skill = Skill.load(path=path)

    # Verify it's loaded as a repo skill with default values
    assert skill.trigger is None
    assert skill.name == "test"  # Name comes from path.stem
    assert skill.content == content


@pytest.mark.parametrize(
    ("frontmatter", "expected_name"),
    [
        pytest.param("---\n---\n", "test", id="empty"),
        pytest.param("---\nname: custom_name\n---\n", "custom_name", id="partial"),
        pytest.param(
            "---\nname: test_agent\ntype: repo\nagent: CustomAgent\n"
            "version: 2.0.0\n---\n",
            "test_agent",
            id="full",
        ),
    ],
)
def test_load_markdown_with_frontmatter(tmp_path, frontmatter, expected_name):
    body = "# Test Content\nThis is a test markdown file with frontmatter."
    path = tmp_path / "test.md"
    path.write_text(frontmatter + body)

    skill = Skill.load(path=path)

    assert skill.trigger is None
    assert skill.name == expected_name
    assert skill.content == body
