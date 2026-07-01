"""Tests for SCA section parser."""
import pytest
from openhands.sdk.subagent.section_parser import (
    ALWAYS_ACTIVE_SENTINEL,
    parse_sections,
    _is_foundational,
    _tokenize,
    _header_triggers,
)


SAMPLE_AGENTS_MD = """\
## Docker Setup
Use Docker for all containerized services.
Run with: docker-compose up

## Building and Testing
Run tests with ./gradlew test
Build artifacts: ./gradlew build

## API Conventions
All endpoints return JSON.
Use REST conventions for resource naming.
"""


class TestParseSections:
    def test_returns_empty_for_no_headers(self):
        assert parse_sections("No headers here, just prose.") == []

    def test_returns_one_agent_per_section(self):
        agents = parse_sections(SAMPLE_AGENTS_MD)
        assert len(agents) == 3

    def test_agent_names_use_sca_prefix_and_slug(self):
        agents = parse_sections(SAMPLE_AGENTS_MD)
        names = {a.name for a in agents}
        assert "sca_docker_setup" in names
        assert "sca_building_and_testing" in names
        assert "sca_api_conventions" in names

    def test_system_prompt_contains_header_and_body(self):
        agents = parse_sections(SAMPLE_AGENTS_MD)
        docker = next(a for a in agents if a.name == "sca_docker_setup")
        assert "## Docker Setup" in docker.system_prompt
        assert "docker-compose up" in docker.system_prompt

    def test_description_is_header_text_without_hashes(self):
        agents = parse_sections(SAMPLE_AGENTS_MD)
        docker = next(a for a in agents if a.name == "sca_docker_setup")
        assert docker.description == "Docker Setup"

    def test_source_path_stored(self):
        agents = parse_sections(SAMPLE_AGENTS_MD, source_path="/repo/AGENTS.md")
        assert all(a.source == "/repo/AGENTS.md" for a in agents)

    def test_header_words_appear_in_triggers(self):
        agents = parse_sections(SAMPLE_AGENTS_MD)
        docker = next(a for a in agents if a.name == "sca_docker_setup")
        assert "docker" in docker.triggers
        assert "setup" in docker.triggers

    def test_foundational_section_gets_always_active_sentinel(self):
        agents = parse_sections(SAMPLE_AGENTS_MD)
        build = next(a for a in agents if a.name == "sca_building_and_testing")
        assert ALWAYS_ACTIVE_SENTINEL in build.triggers

    def test_non_foundational_section_lacks_always_active_sentinel(self):
        agents = parse_sections(SAMPLE_AGENTS_MD)
        docker = next(a for a in agents if a.name == "sca_docker_setup")
        assert ALWAYS_ACTIVE_SENTINEL not in docker.triggers

    def test_synonym_expansion_test_to_testing(self):
        content = "## Testing Guide\nRun test suite daily."
        agents = parse_sections(content)
        assert len(agents) == 1
        triggers = agents[0].triggers
        assert "testing" in triggers or "tests" in triggers

    def test_invalid_header_level_raises(self):
        with pytest.raises(ValueError, match="header_level must be"):
            parse_sections("content", header_level="#")

    def test_hash_level_three_option(self):
        content = "### Sub Section\nBody text here for the sub section."
        agents = parse_sections(content, header_level="###")
        assert len(agents) == 1
        assert agents[0].name == "sca_sub_section"

    def test_triggers_are_non_empty_for_normal_sections(self):
        agents = parse_sections(SAMPLE_AGENTS_MD)
        for agent in agents:
            assert len(agent.triggers) > 0, f"{agent.name} has no triggers"

    def test_single_section_file(self):
        content = "## Docker Setup\nRun with docker compose."
        agents = parse_sections(content)
        assert len(agents) == 1


class TestIsFoundational:
    def test_testing_is_foundational(self):
        assert _is_foundational("## Testing Guidelines")

    def test_build_is_foundational(self):
        assert _is_foundational("## Building and Testing")

    def test_commit_is_foundational(self):
        assert _is_foundational("## Commit Conventions")

    def test_docker_is_not_foundational(self):
        assert not _is_foundational("## Docker Setup")

    def test_api_is_not_foundational(self):
        assert not _is_foundational("## API Conventions")


class TestTokenize:
    def test_strips_stop_words(self):
        tokens = _tokenize("the docker image and container")
        assert "the" not in tokens
        assert "and" not in tokens
        assert "docker" in tokens
        assert "image" in tokens

    def test_min_length_filter(self):
        tokens = _tokenize("a be it at")
        assert tokens == []

    def test_short_allowlist_passes(self):
        tokens = _tokenize("run the ci pipeline")
        assert "ci" in tokens

    def test_lowercases_input(self):
        tokens = _tokenize("Docker Image Container")
        assert "docker" in tokens
        assert "Docker" not in tokens


class TestHeaderTriggers:
    def test_single_word_header(self):
        triggers = _header_triggers("## Docker")
        assert "docker" in triggers

    def test_multi_word_produces_phrase(self):
        triggers = _header_triggers("## Docker Setup")
        assert "docker" in triggers
        assert "setup" in triggers
        assert "docker setup" in triggers
