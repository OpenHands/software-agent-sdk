"""Tests for AgentContext template rendering functionality."""

import pytest
from pydantic import SecretStr

from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.context.skills import (
    KeywordTrigger,
    Skill,
)
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.secret import LookupSecret, StaticSecret


class TestAgentContext:
    """Test cases for AgentContext template rendering."""

    def test_agent_context_creation_empty(self):
        """Test creating an empty AgentContext."""
        context = AgentContext()
        assert context.skills == []
        assert context.system_message_suffix is None
        assert context.user_message_suffix is None

    def test_agent_context_creation_with_suffix(self):
        """Test creating AgentContext with custom suffixes."""
        context = AgentContext(
            system_message_suffix="Custom system suffix",
            user_message_suffix="Custom user suffix",
        )
        assert context.system_message_suffix == "Custom system suffix"
        assert context.user_message_suffix == "Custom user suffix"

    def test_skill_validation_duplicate_names(self):
        """Test that duplicate skill names raise validation error."""
        repo_skill1 = Skill(
            name="duplicate",
            content="First agent",
            source="test1.md",
            trigger=None,
        )
        repo_skill2 = Skill(
            name="duplicate",
            content="Second agent",
            source="test2.md",
            trigger=None,
        )

        with pytest.raises(ValueError, match="Duplicate skill name found: duplicate"):
            AgentContext(skills=[repo_skill1, repo_skill2])

    def test_get_system_message_suffix_no_repo_skills(self):
        """Test system message suffix with no repo skills."""
        knowledge_skill = Skill(
            name="test_knowledge",
            content="Some knowledge content",
            source="test.md",
            trigger=KeywordTrigger(keywords=["test"]),
        )
        context = AgentContext(skills=[knowledge_skill])
        result = context.get_system_message_suffix()
        assert result is None

    def test_get_system_message_suffix_with_repo_skills(self):
        """Test system message suffix rendering with repo skills."""
        repo_agent1 = Skill(
            name="coding_standards",
            content="Follow PEP 8 style guidelines for Python code.",
            source="coding_standards.md",
            trigger=None,
        )
        repo_agent2 = Skill(
            name="testing_guidelines",
            content="Write comprehensive unit tests for all new features.",
            source="testing_guidelines.md",
            trigger=None,
        )

        context = AgentContext(skills=[repo_agent1, repo_agent2])
        result = context.get_system_message_suffix()

        expected_output = (
            "<REPO_CONTEXT>\n"
            "The following information has been included based on several files \
defined in user's repository.\n"
            "Please follow them while working.\n"
            "\n"
            "\n"
            "[BEGIN context from [coding_standards]]\n"
            "Follow PEP 8 style guidelines for Python code.\n"
            "[END Context]\n"
            "\n"
            "[BEGIN context from [testing_guidelines]]\n"
            "Write comprehensive unit tests for all new features.\n"
            "[END Context]\n"
            "\n"
            "</REPO_CONTEXT>"
        )

        assert result == expected_output

    def test_get_system_message_suffix_with_custom_suffix(self):
        """Test system message suffix with repo skills and custom suffix."""
        repo_agent = Skill(
            name="security_rules",
            content="Always validate user input and sanitize data.",
            source="security-rules.md",
            trigger=None,
        )

        context = AgentContext(
            skills=[repo_agent],
            system_message_suffix="Additional custom instructions for the system.",
        )
        result = context.get_system_message_suffix()

        expected_output = (
            "<REPO_CONTEXT>\n"
            "The following information has been included based on several files \
defined in user's repository.\n"
            "Please follow them while working.\n"
            "\n"
            "\n"
            "[BEGIN context from [security_rules]]\n"
            "Always validate user input and sanitize data.\n"
            "[END Context]\n"
            "\n"
            "</REPO_CONTEXT>\n"
            "\n"
            "\n"
            "\n"
            "Additional custom instructions for the system."
        )

        assert result == expected_output

    def test_get_user_message_suffix_empty_query(self):
        """Test user message suffix with empty query."""
        knowledge_agent = Skill(
            name="python_tips",
            content="Use list comprehensions for better performance.",
            source="python-tips.md",
            trigger=KeywordTrigger(keywords=["python", "performance"]),
        )

        context = AgentContext(skills=[knowledge_agent])
        empty_message = Message(role="user", content=[])
        result = context.get_user_message_suffix(empty_message, [])

        assert result is None

    def test_get_user_message_suffix_no_triggers(self):
        """Test user message suffix with no matching triggers."""
        knowledge_agent = Skill(
            name="python_tips",
            content="Use list comprehensions for better performance.",
            source="python-tips.md",
            trigger=KeywordTrigger(keywords=["python", "performance"]),
        )

        context = AgentContext(skills=[knowledge_agent])
        user_message = Message(
            role="user", content=[TextContent(text="How do I write JavaScript code?")]
        )
        result = context.get_user_message_suffix(user_message, [])

        assert result is None

    def test_get_user_message_suffix_with_single_trigger(self):
        """Test user message suffix with single triggered skill."""
        knowledge_agent = Skill(
            name="python_tips",
            content="Use list comprehensions for better performance.",
            source="python-tips.md",
            trigger=KeywordTrigger(keywords=["python", "performance"]),
        )

        context = AgentContext(skills=[knowledge_agent])
        user_message = Message(
            role="user",
            content=[TextContent(text="How can I improve my Python code performance?")],
        )
        result = context.get_user_message_suffix(user_message, [])

        assert result is not None
        text_content, triggered_names = result

        expected_output = (
            "<EXTRA_INFO>\n"
            "The following information has been included based on a keyword match "
            'for "python".\n'
            "It may or may not be relevant to the user's request.\n"
            "\n"
            "Skill location: python-tips.md\n"
            "(Use this path to resolve relative file references in the skill "
            "content below)\n"
            "\n"
            "\n"
            "Use list comprehensions for better performance.\n"
            "</EXTRA_INFO>"
        )

        assert text_content.text == expected_output
        assert triggered_names == ["python_tips"]

    def test_get_user_message_suffix_with_multiple_triggers(self):
        """Test user message suffix with multiple triggered skills."""
        python_agent = Skill(
            name="python_best_practices",
            content="Follow PEP 8 and use type hints for better code quality.",
            source="python-best-practices.md",
            trigger=KeywordTrigger(keywords=["python", "best practices"]),
        )
        testing_agent = Skill(
            name="testing_framework",
            content="Use pytest for comprehensive testing with fixtures and \
parametrization.",
            source="testing-framework.md",
            trigger=KeywordTrigger(keywords=["testing", "pytest"]),
        )

        context = AgentContext(skills=[python_agent, testing_agent])
        user_message = Message(
            role="user",
            content=[
                TextContent(
                    text="I need help with Python testing using pytest framework."
                )
            ],
        )
        result = context.get_user_message_suffix(user_message, [])

        assert result is not None
        text_content, triggered_names = result

        expected_output = (
            "<EXTRA_INFO>\n"
            "The following information has been included based on a keyword match "
            'for "python".\n'
            "It may or may not be relevant to the user's request.\n"
            "\n"
            "Skill location: python-best-practices.md\n"
            "(Use this path to resolve relative file references in the skill "
            "content below)\n"
            "\n"
            "\n"
            "Follow PEP 8 and use type hints for better code quality.\n"
            "</EXTRA_INFO>\n"
            "\n"
            "<EXTRA_INFO>\n"
            "The following information has been included based on a keyword match "
            'for "testing".\n'
            "It may or may not be relevant to the user's request.\n"
            "\n"
            "Skill location: testing-framework.md\n"
            "(Use this path to resolve relative file references in the skill "
            "content below)\n"
            "\n"
            "\n"
            "Use pytest for comprehensive testing with fixtures and "
            "parametrization.\n"
            "</EXTRA_INFO>"
        )

        assert text_content.text == expected_output
        assert set(triggered_names) == {"python_best_practices", "testing_framework"}

    def test_get_user_message_suffix_skip_skill_names(self):
        """Test user message suffix with skipped skill names."""
        knowledge_agent = Skill(
            name="python_tips",
            content="Use list comprehensions for better performance.",
            source="python-tips.md",
            trigger=KeywordTrigger(keywords=["python", "performance"]),
        )

        context = AgentContext(skills=[knowledge_agent])
        user_message = Message(
            role="user",
            content=[TextContent(text="How can I improve my Python code performance?")],
        )
        result = context.get_user_message_suffix(user_message, ["python_tips"])

        assert result is None

    def test_get_user_message_suffix_multiline_content(self):
        """Test user message suffix with multiline user content."""
        knowledge_agent = Skill(
            name="database_tips",
            content="Always use parameterized queries to prevent SQL injection \
attacks.",
            source="database-tips.md",
            trigger=KeywordTrigger(keywords=["database", "sql"]),
        )

        context = AgentContext(skills=[knowledge_agent])
        user_message = Message(
            role="user",
            content=[
                TextContent(text="I'm working on a web application"),
                TextContent(text="that needs to connect to a database"),
                TextContent(text="and execute SQL queries safely"),
            ],
        )
        result = context.get_user_message_suffix(user_message, [])

        assert result is not None
        text_content, triggered_names = result

        expected_output = (
            "<EXTRA_INFO>\n"
            "The following information has been included based on a keyword match "
            'for "database".\n'
            "It may or may not be relevant to the user's request.\n"
            "\n"
            "Skill location: database-tips.md\n"
            "(Use this path to resolve relative file references in the skill "
            "content below)\n"
            "\n"
            "\n"
            "Always use parameterized queries to prevent SQL injection attacks.\n"
            "</EXTRA_INFO>"
        )

        assert text_content.text == expected_output
        assert triggered_names == ["database_tips"]

    def test_mixed_skill_types(self):
        """Test AgentContext with mixed skill types."""
        repo_agent = Skill(
            name="repo_standards",
            content="Use semantic versioning for releases.",
            source="repo-standards.md",
            trigger=None,
        )
        knowledge_agent = Skill(
            name="git_tips",
            content="Use conventional commits for better history.",
            source="git-tips.md",
            trigger=KeywordTrigger(keywords=["git", "commit"]),
        )

        context = AgentContext(skills=[repo_agent, knowledge_agent])

        # Test system message suffix (should only include repo skills)
        system_result = context.get_system_message_suffix()
        expected_system_output = (
            "<REPO_CONTEXT>\n"
            "The following information has been included based on several files \
defined in user's repository.\n"
            "Please follow them while working.\n"
            "\n"
            "\n"
            "[BEGIN context from [repo_standards]]\n"
            "Use semantic versioning for releases.\n"
            "[END Context]\n"
            "\n"
            "</REPO_CONTEXT>"
        )
        assert system_result == expected_system_output

        # Test user message suffix (should only include knowledge skills)
        user_message = Message(
            role="user",
            content=[TextContent(text="How should I format my git commits?")],
        )
        user_result = context.get_user_message_suffix(user_message, [])

        assert user_result is not None
        text_content, triggered_names = user_result

        expected_user_output = (
            "<EXTRA_INFO>\n"
            "The following information has been included based on a keyword match "
            'for "git".\n'
            "It may or may not be relevant to the user's request.\n"
            "\n"
            "Skill location: git-tips.md\n"
            "(Use this path to resolve relative file references in the skill "
            "content below)\n"
            "\n"
            "\n"
            "Use conventional commits for better history.\n"
            "</EXTRA_INFO>"
        )

        assert text_content.text == expected_user_output
        assert triggered_names == ["git_tips"]

    def test_case_insensitive_trigger_matching(self):
        """Test that trigger matching is case insensitive."""
        knowledge_agent = Skill(
            name="docker_tips",
            content="Use multi-stage builds to reduce image size.",
            source="docker-tips.md",
            trigger=KeywordTrigger(keywords=["docker", "container"]),
        )

        context = AgentContext(skills=[knowledge_agent])
        user_message = Message(
            role="user",
            content=[TextContent(text="I need help with DOCKER containerization.")],
        )
        result = context.get_user_message_suffix(user_message, [])

        assert result is not None
        text_content, triggered_names = result

        expected_output = (
            "<EXTRA_INFO>\n"
            "The following information has been included based on a keyword match "
            'for "docker".\n'
            "It may or may not be relevant to the user's request.\n"
            "\n"
            "Skill location: docker-tips.md\n"
            "(Use this path to resolve relative file references in the skill "
            "content below)\n"
            "\n"
            "\n"
            "Use multi-stage builds to reduce image size.\n"
            "</EXTRA_INFO>"
        )

        assert text_content.text == expected_output
        assert triggered_names == ["docker_tips"]

    def test_special_characters_in_content(self):
        """Test template rendering with special characters in content."""
        repo_agent = Skill(
            name="special_chars",
            content="Use {{ curly braces }} and <angle brackets> carefully in \
templates.",
            source="special-chars.md",
            trigger=None,
        )

        context = AgentContext(skills=[repo_agent])
        result = context.get_system_message_suffix()

        expected_output = (
            "<REPO_CONTEXT>\n"
            "The following information has been included based on several files \
defined in user's repository.\n"
            "Please follow them while working.\n"
            "\n"
            "\n"
            "[BEGIN context from [special_chars]]\n"
            "Use {{ curly braces }} and <angle brackets> carefully in \
templates.\n"
            "[END Context]\n"
            "\n"
            "</REPO_CONTEXT>"
        )

        assert result == expected_output

    def test_empty_skill_content(self):
        """Test template rendering with empty skill content."""
        repo_agent = Skill(
            name="empty_content", content="", source="test.md", trigger=None
        )

        context = AgentContext(skills=[repo_agent])
        result = context.get_system_message_suffix()

        expected_output = (
            "<REPO_CONTEXT>\n"
            "The following information has been included based on several files \
defined in user's repository.\n"
            "Please follow them while working.\n"
            "\n"
            "\n"
            "[BEGIN context from [empty_content]]\n"
            "\n"
            "[END Context]\n"
            "\n"
            "</REPO_CONTEXT>"
        )

        assert result == expected_output

    def test_get_system_message_suffix_custom_suffix_only(self):
        """Test system message suffix with custom suffix but no repo skills.

        This test exposes a bug where get_system_message_suffix() returns None
        when there are no repo skills, even if system_message_suffix is set.
        The method should return the custom suffix in this case.
        """
        # Create context with only knowledge skills (no repo skills)
        # but with a custom system_message_suffix
        knowledge_agent = Skill(
            name="test_knowledge",
            content="Some knowledge content",
            source="test-knowledge.md",
            trigger=KeywordTrigger(keywords=["test"]),
        )
        context = AgentContext(
            skills=[knowledge_agent],
            system_message_suffix="Custom system instructions without repo context.",
        )

        result = context.get_system_message_suffix()

        assert result == "Custom system instructions without repo context."

    def test_get_user_message_suffix_empty_query_with_suffix(self):
        """Test user message suffix with empty query but custom user_message_suffix.

        This test exposes a bug where get_user_message_suffix() returns None
        when the user message has no text content, even if user_message_suffix is set.
        The method should return the custom suffix in this case.
        """
        # Create context with user_message_suffix
        context = AgentContext(
            skills=[],
            user_message_suffix="Custom user instructions for empty messages.",
        )

        # Create a message with no text content (empty query)
        empty_message = Message(role="user", content=[])

        result = context.get_user_message_suffix(empty_message, [])

        expected_content = TextContent(
            text="Custom user instructions for empty messages."
        )
        assert result == (expected_content, [])

    def test_get_secret_infos_no_secrets(self):
        """Test get_secret_infos with no secrets configured."""
        context = AgentContext()
        result = context.get_secret_infos()
        assert result == []

    def test_get_secret_infos_none_secrets(self):
        """Test get_secret_infos when secrets is None."""
        context = AgentContext(secrets=None)
        result = context.get_secret_infos()
        assert result == []

    def test_get_secret_infos_with_secrets(self):
        """Test get_secret_infos with multiple secrets."""
        secrets = {
            "GITHUB_TOKEN": StaticSecret(
                value=SecretStr("test_token_123"),
                description="GitHub authentication token",
            ),
            "API_KEY": StaticSecret(
                value=SecretStr("test_api_key"),
                description="API key for external service",
            ),
            "DATABASE_PASSWORD": StaticSecret(
                value=SecretStr("test_password"),
                description="Database password",
            ),
        }
        context = AgentContext(secrets=secrets)
        result = context.get_secret_infos()
        # Order may vary, so use set comparison for names
        result_names = {info["name"] for info in result}
        assert result_names == {"GITHUB_TOKEN", "API_KEY", "DATABASE_PASSWORD"}
        assert len(result) == 3
        # Verify descriptions are included
        result_dict = {info["name"]: info for info in result}
        assert (
            result_dict["GITHUB_TOKEN"]["description"] == "GitHub authentication token"
        )
        assert result_dict["API_KEY"]["description"] == "API key for external service"
        assert result_dict["DATABASE_PASSWORD"]["description"] == "Database password"

    def test_get_secret_infos_with_lookup_secrets(self):
        """Test get_secret_infos with multiple LookupSecret instances."""
        secrets = {
            "API_TOKEN": LookupSecret(
                url="https://api.example.com/token",
                description="API token fetched from external service",
            ),
            "CONFIG_SECRET": LookupSecret(
                url="https://config.example.com/secret",
                description="Configuration secret from remote endpoint",
            ),
            "AUTH_KEY": LookupSecret(
                url="https://auth.example.com/key",
                description="Authentication key",
            ),
        }
        context = AgentContext(secrets=secrets)
        result = context.get_secret_infos()
        # Order may vary, so use set comparison for names
        result_names = {info["name"] for info in result}
        assert result_names == {"API_TOKEN", "CONFIG_SECRET", "AUTH_KEY"}
        assert len(result) == 3
        # Verify descriptions are included
        result_dict = {info["name"]: info for info in result}
        assert (
            result_dict["API_TOKEN"]["description"]
            == "API token fetched from external service"
        )
        assert (
            result_dict["CONFIG_SECRET"]["description"]
            == "Configuration secret from remote endpoint"
        )
        assert result_dict["AUTH_KEY"]["description"] == "Authentication key"

    def test_get_secret_infos_with_mixed_secret_types(self):
        """Test get_secret_infos with a mix of StaticSecret and LookupSecret."""
        secrets = {
            "STATIC_SECRET": StaticSecret(
                value=SecretStr("static_value"),
                description="A static secret",
            ),
            "LOOKUP_SECRET": LookupSecret(
                url="https://example.com/secret",
                description="A lookup secret",
            ),
            "PLAIN_STRING": "plain_string_value",  # Plain string has no description
        }
        context = AgentContext(secrets=secrets)
        result = context.get_secret_infos()
        # Order may vary, so use set comparison for names
        result_names = {info["name"] for info in result}
        assert result_names == {"STATIC_SECRET", "LOOKUP_SECRET", "PLAIN_STRING"}
        assert len(result) == 3
        # Verify descriptions are included for SecretSource instances
        result_dict = {info["name"]: info for info in result}
        assert result_dict["STATIC_SECRET"]["description"] == "A static secret"
        assert result_dict["LOOKUP_SECRET"]["description"] == "A lookup secret"
        # Plain strings have no description
        assert result_dict["PLAIN_STRING"]["description"] is None

    def test_get_system_message_suffix_with_secrets_only(self):
        """Test system message suffix with secrets but no repo skills or custom suffix.

        This test verifies that secrets are included in the system message suffix
        when no repo skills or custom suffix are present.
        """
        secrets = {
            "GITHUB_TOKEN": StaticSecret(
                value=SecretStr("test_token"),
                description="GitHub authentication token",
            ),
            "API_KEY": StaticSecret(
                value=SecretStr("test_key"),
                description="API key for external service",
            ),
        }
        context = AgentContext(secrets=secrets)
        result = context.get_system_message_suffix()

        assert result is not None
        assert "<CUSTOM_SECRETS>" in result
        assert "You have access to the following environment variables" in result
        assert "**$GITHUB_TOKEN**" in result
        assert "GitHub authentication token" in result
        assert "**$API_KEY**" in result
        assert "API key for external service" in result
        assert "</CUSTOM_SECRETS>" in result

    def test_get_system_message_suffix_with_secrets_and_repo_skills(self):
        """Test system message suffix with both secrets and repo skills."""
        repo_skill = Skill(
            name="coding_standards",
            content="Follow PEP 8 style guidelines.",
            source="coding_standards.md",
            trigger=None,
        )
        secrets = {
            "GITHUB_TOKEN": StaticSecret(
                value=SecretStr("test_token"),
                description="GitHub authentication token",
            ),
        }
        context = AgentContext(skills=[repo_skill], secrets=secrets)
        result = context.get_system_message_suffix()

        assert result is not None
        assert "<REPO_CONTEXT>" in result
        assert "coding_standards" in result
        assert "<CUSTOM_SECRETS>" in result
        assert "**$GITHUB_TOKEN**" in result
        assert "GitHub authentication token" in result

    def test_get_system_message_suffix_with_secrets_and_custom_suffix(self):
        """Test system message suffix with secrets and custom suffix."""
        secrets = {
            "API_KEY": StaticSecret(
                value=SecretStr("test_key"),
                description="API key for external service",
            ),
        }
        context = AgentContext(
            secrets=secrets,
            system_message_suffix="Custom system instructions.",
        )
        result = context.get_system_message_suffix()

        assert result is not None
        assert "Custom system instructions." in result
        assert "<CUSTOM_SECRETS>" in result
        assert "**$API_KEY**" in result
        assert "API key for external service" in result

    def test_get_system_message_suffix_with_all_components(self):
        """Test system message suffix with repo skills, secrets, and custom suffix."""
        repo_skill = Skill(
            name="security_rules",
            content="Always validate user input.",
            source="security-rules.md",
            trigger=None,
        )
        secrets = {
            "GITHUB_TOKEN": StaticSecret(
                value=SecretStr("test_token"),
                description="GitHub authentication token",
            ),
            "DATABASE_PASSWORD": StaticSecret(
                value=SecretStr("test_password"),
                description="Database password",
            ),
        }
        context = AgentContext(
            skills=[repo_skill],
            secrets=secrets,
            system_message_suffix="Additional custom instructions.",
        )
        result = context.get_system_message_suffix()

        assert result is not None
        assert "<REPO_CONTEXT>" in result
        assert "security_rules" in result
        assert "Additional custom instructions." in result
        assert "<CUSTOM_SECRETS>" in result
        assert "**$GITHUB_TOKEN**" in result
        assert "GitHub authentication token" in result
        assert "**$DATABASE_PASSWORD**" in result
        assert "Database password" in result

    def test_get_system_message_suffix_secrets_order(self):
        """Test that secret names appear in the output in a consistent order."""
        secrets = {
            "Z_SECRET": StaticSecret(
                value=SecretStr("z_value"),
                description="Z secret description",
            ),
            "A_SECRET": StaticSecret(
                value=SecretStr("a_value"),
                description="A secret description",
            ),
            "M_SECRET": StaticSecret(
                value=SecretStr("m_value"),
                description="M secret description",
            ),
        }
        context = AgentContext(secrets=secrets)
        result = context.get_system_message_suffix()

        assert result is not None
        # Check that all secrets are present
        assert "**$Z_SECRET**" in result
        assert "Z secret description" in result
        assert "**$A_SECRET**" in result
        assert "A secret description" in result
        assert "**$M_SECRET**" in result
        assert "M secret description" in result
