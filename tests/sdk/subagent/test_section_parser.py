"""Tests for SCA section parser."""
import pytest
from openhands.sdk.subagent.section_parser import (
    ALWAYS_ACTIVE_SENTINEL,
    parse_sections,
    parse_xml_sections,
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


class TestParseSectionsRunsXmlAndMarkdownTogether:
    """Regression tests: XML tags and ## headers are no longer mutually
    exclusive fallbacks — both are recognized in the same pass."""

    def test_xml_tag_inside_markdown_section_becomes_own_section(self):
        """An XML tag following a ## header must not be absorbed into that
        header's body — it should become its own section."""
        content = (
            "## Docker Setup\n"
            "Use docker-compose.\n\n"
            "<TESTING>\n"
            "Run pytest before committing.\n"
            "</TESTING>\n"
        )
        agents = parse_sections(content)
        names = {a.name for a in agents}
        assert "sca_docker_setup" in names
        assert "sca_testing" in names

        docker = next(a for a in agents if a.name == "sca_docker_setup")
        testing = next(a for a in agents if a.name == "sca_testing")
        # The XML block's content must not leak into the preceding section.
        assert "Run pytest before committing." not in docker.system_prompt
        assert "Run pytest before committing." in testing.system_prompt

    def test_xml_tagged_section_gets_always_active_when_foundational(self):
        content = (
            "## Docker Setup\n"
            "Use docker-compose.\n\n"
            "<TESTING>\n"
            "Run pytest before committing.\n"
            "</TESTING>\n"
        )
        agents = parse_sections(content)
        testing = next(a for a in agents if a.name == "sca_testing")
        docker = next(a for a in agents if a.name == "sca_docker_setup")
        assert ALWAYS_ACTIVE_SENTINEL in testing.triggers
        assert ALWAYS_ACTIVE_SENTINEL not in docker.triggers

    def test_pure_xml_content_no_markdown_headers(self):
        """parse_sections finds XML sections even with zero ## headers —
        callers no longer need a separate parse_xml_sections fallback."""
        content = "<ROLE>\nYou are a helpful agent.\n</ROLE>\n"
        agents = parse_sections(content)
        assert len(agents) == 1
        assert agents[0].name == "sca_role"

    def test_multiple_xml_tags_before_and_after_markdown_section(self):
        content = (
            "<ROLE>\n"
            "You are a helpful agent.\n"
            "</ROLE>\n\n"
            "## Docker Setup\n"
            "Use docker-compose.\n\n"
            "<TESTING>\n"
            "Run pytest.\n"
            "</TESTING>\n"
        )
        agents = parse_sections(content)
        names = {a.name for a in agents}
        assert names == {"sca_role", "sca_docker_setup", "sca_testing"}

    def test_parse_xml_sections_unaffected_by_refactor(self):
        """parse_xml_sections keeps its own public contract unchanged."""
        content = "<DOCKER_SETUP>\nUse docker-compose.\n</DOCKER_SETUP>\n"
        agents = parse_xml_sections(content)
        assert len(agents) == 1
        assert agents[0].name == "sca_docker_setup"

    def test_preamble_before_first_marker_still_dropped(self):
        """Known open issue (see KNOWN_ISSUES.md): content that is neither
        inside an XML tag nor under a ## header is still discarded. This
        test documents current behavior, not desired behavior."""
        content = (
            "# Core Engineering Principles\n"
            "Always write tests.\n\n"
            "## Docker Setup\n"
            "Use docker-compose.\n"
        )
        agents = parse_sections(content)
        all_text = " ".join(a.system_prompt for a in agents)
        assert "Always write tests." not in all_text


class TestSlugCollisionMerge:
    """Regression tests: colliding slugs merge instead of silently dropping."""

    def test_colliding_slugs_merge_into_one_agent(self):
        content = (
            "## API Conventions\n"
            "Use REST for all endpoints.\n\n"
            "## API-Conventions\n"
            "Use GraphQL for internal tooling.\n"
        )
        agents = parse_sections(content)
        assert len(agents) == 1
        assert agents[0].name == "sca_api_conventions"

    def test_merge_preserves_both_bodies(self):
        content = (
            "## API Conventions\n"
            "Use REST for all endpoints.\n\n"
            "## API-Conventions\n"
            "Use GraphQL for internal tooling.\n"
        )
        agents = parse_sections(content)
        assert "Use REST for all endpoints." in agents[0].system_prompt
        assert "Use GraphQL for internal tooling." in agents[0].system_prompt

    def test_merge_does_not_duplicate_identical_body(self):
        content = (
            "## API Conventions\n"
            "Use REST for all endpoints.\n\n"
            "## API-Conventions\n"
            "Use REST for all endpoints.\n"
        )
        agents = parse_sections(content)
        assert len(agents) == 1
        # Identical body text must appear only once, not duplicated.
        assert agents[0].system_prompt.count("Use REST for all endpoints.") == 1

    def test_merge_unions_triggers(self):
        content = (
            "## Docker Setup\n"
            "Use docker-compose for services.\n\n"
            "## Docker-Setup\n"
            "Kubernetes is an alternative.\n"
        )
        agents = parse_sections(content)
        assert len(agents) == 1
        assert "docker" in agents[0].triggers
        assert "kubernetes" in agents[0].triggers


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

    def test_pull_request_is_foundational(self):
        """Regression test: multi-word phrase with no single-word fallback.

        _is_foundational previously only matched single tokens split on "_",
        so "pull_request" (stored as one underscored string) could never
        match — "pull" and "request" alone aren't in the keyword set.
        """
        assert _is_foundational("## Pull Request Guidelines")

    def test_builds_is_not_foundational(self):
        """"builds" is not "build" — the token match must not substring-match."""
        assert not _is_foundational("## Builds Overview")


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
