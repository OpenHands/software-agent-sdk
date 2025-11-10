"""Tests to verify consistency between ripgrep and fallback implementations."""

import tempfile
from pathlib import Path

import pytest

from openhands.tools.grep.definition import GrepAction
from openhands.tools.grep.impl import GrepExecutor
from openhands.tools.utils import _check_ripgrep_available


# ruff: noqa


@pytest.mark.skipif(
    not _check_ripgrep_available(),
    reason="ripgrep not available - consistency tests require ripgrep",
)
class TestGrepConsistency:
    """Test that ripgrep and fallback methods produce consistent results."""

    @pytest.fixture
    def temp_dir_with_content(self):
        """Create a temporary directory with test files containing searchable content."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create test files with more complex content
            test_files = {
                # Root level files
                "app.py": "def main():\n    print('Hello World')\n    return 0\n# TODO: add error handling",
                "main.py": "import sys\ndef hello():\n    print('Hello from main')\n# FIXME: refactor this",
                "test.py": (
                    "import unittest\nclass TestApp(unittest.TestCase):\n    # TODO: write tests\n    pass"
                ),
                "setup.py": "from setuptools import setup\n# Configuration for package",
                "config.json": '{"name": "test", "version": "1.0", "hello": "world", "debug": true}',
                "config.yaml": "name: test\nversion: 1.0\nhello: world\n",
                "readme.md": "# Hello World\nThis is a test project.\n## TODO\n- Add features",
                "README.MD": "# Alternative README\nHELLO WORLD\n",
                ".env": "API_KEY=secret123\nDEBUG=true\n",
                ".gitignore": "*.pyc\n__pycache__/\n.env\n",
                # Source directory
                "src/utils.py": "def helper():\n    return 'Hello from helper'\n\ndef error_handler():\n    raise Exception('Error!')",
                "src/models.py": (
                    "class User:\n    def __init__(self, name):\n"
                    "        self.name = name\n\nclass Admin(User):\n    pass"
                ),
                "src/api.py": "import requests\n\ndef fetch_data():\n    # TODO: add retry logic\n    return requests.get('http://example.com')",
                "src/__init__.py": "# Package initialization\n",
                "src/core/engine.py": "class Engine:\n    def __init__(self):\n        self.running = False\n    # FIXME: memory leak",
                "src/core/parser.py": "import re\n\ndef parse(text):\n    # TODO: handle edge cases\n    return re.findall(r'\\w+', text)",
                "src/core/__init__.py": "",
                "src/plugins/auth.py": "def authenticate(user, password):\n    # Security: hash passwords\n    return user == 'admin'",
                "src/plugins/db.py": "class Database:\n    def connect(self):\n        # TODO: add connection pooling\n        pass",
                "src/plugins/__init__.py": "",
                # Tests directory
                "tests/test_utils.py": "def test_helper():\n    # TODO: add assertions\n    pass",
                "tests/test_models.py": "def test_user():\n    assert True  # FIXME: real test",
                "tests/integration/test_api.py": "def test_api():\n    # Integration test\n    pass",
                "tests/integration/__init__.py": "",
                "tests/unit/test_engine.py": "def test_engine():\n    # Unit test for engine\n    pass",
                "tests/unit/test_parser.py": "def test_parser():\n    # TODO: test edge cases\n    pass",
                "tests/unit/__init__.py": "",
                # Documentation
                "docs/guide.md": "# User Guide\nSay hello to get started.\n\n## Examples\nTODO: add examples",
                "docs/api.md": "# API Reference\n\n## Authentication\nUse API keys for auth.",
                "docs/tutorial.rst": "Tutorial\n========\n\nHello from tutorial\n\nTODO: complete sections",
                "docs/CHANGELOG.md": "# Changelog\n\n## v1.0.0\n- Initial release\n\n## TODO\n- Add v2.0 features",
                # Configuration files
                "config/settings.json": '{"debug": true, "timeout": 30, "retries": 3}',
                "config/database.yaml": "host: localhost\nport: 5432\nuser: admin\n",
                "config/logging.ini": "[loggers]\nkeys=root\n\n[handlers]\nkeys=console",
                "config/secrets.env": "API_KEY=secret\nDB_PASSWORD=pass123\n",
                # Scripts
                "scripts/deploy.sh": "#!/bin/bash\necho 'Deploying...'\n# TODO: add validation",
                "scripts/build.py": "import subprocess\n# Build script\nsubprocess.run(['make', 'build'])",
                "scripts/test.py": "import pytest\n# Run tests\npytest.main(['-v'])",
                # Build artifacts
                "build/output.js": "console.log('Hello from build');\n// TODO: minify",
                "build/styles.css": "body { margin: 0; }\n/* TODO: add dark mode */",
                # Hidden directory
                ".github/workflows/ci.yml": "name: CI\non: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest",
                ".github/workflows/deploy.yml": "name: Deploy\n# TODO: add production deploy",
                # Deep nesting
                "deep/level1/level2/level3/file.py": "# Deep nested file\ndef deep_function():\n    return 'hello from deep'",
                "deep/level1/level2/level3/data.json": '{"deep": "nested", "hello": "world"}',
                # Special characters in content
                "special.txt": "Line with ERROR: something failed\nLine with WARNING: be careful\nLine with INFO: all good",
                "patterns.txt": "email@example.com\n192.168.1.1\nhttp://example.com\n",
                # Binary-like file (won't match text searches)
                "data.bin": "\x00\x01\x02\x03\x04",
                # Empty file
                "empty.txt": "",
            }

            for file_path, content in test_files.items():
                full_path = Path(temp_dir) / file_path
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(content)

            yield temp_dir

    def test_basic_search_consistency(self, temp_dir_with_content):
        """Test that both methods return consistent results for basic searches."""
        executor = GrepExecutor(temp_dir_with_content)
        action = GrepAction(pattern="hello")

        # Get results from both methods
        ripgrep_result = executor._execute_with_ripgrep(
            action, Path(temp_dir_with_content)
        )
        fallback_result = executor._execute_with_grep(
            action, Path(temp_dir_with_content)
        )

        # Both should succeed
        assert not ripgrep_result.is_error
        assert not fallback_result.is_error

        # Convert to sets of matching files for exact comparison
        ripgrep_matches = set(ripgrep_result.matches)
        fallback_matches = set(fallback_result.matches)

        # Both methods must return exactly the same files
        assert ripgrep_matches == fallback_matches, (
            f"Ripgrep found: {ripgrep_matches}\n"
            f"Fallback found: {fallback_matches}\n"
            f"Difference (ripgrep - fallback): {ripgrep_matches - fallback_matches}\n"
            f"Difference (fallback - ripgrep): {fallback_matches - ripgrep_matches}"
        )

    def test_case_insensitive_consistency(self, temp_dir_with_content):
        """Test that both methods handle case-insensitive searches consistently."""
        executor = GrepExecutor(temp_dir_with_content)
        action = GrepAction(pattern="HELLO")  # Uppercase pattern

        # Get results from both methods
        ripgrep_result = executor._execute_with_ripgrep(
            action, Path(temp_dir_with_content)
        )
        fallback_result = executor._execute_with_grep(
            action, Path(temp_dir_with_content)
        )

        # Both should succeed
        assert not ripgrep_result.is_error
        assert not fallback_result.is_error

        # Convert to sets for exact comparison
        ripgrep_matches = set(ripgrep_result.matches)
        fallback_matches = set(fallback_result.matches)

        # Both methods must return exactly the same files
        assert ripgrep_matches == fallback_matches, (
            f"Ripgrep found: {ripgrep_matches}\n"
            f"Fallback found: {fallback_matches}\n"
            f"Difference (ripgrep - fallback): {ripgrep_matches - fallback_matches}\n"
            f"Difference (fallback - ripgrep): {fallback_matches - ripgrep_matches}"
        )

    def test_include_pattern_consistency(self, temp_dir_with_content):
        """Test that both methods handle include patterns consistently."""
        executor = GrepExecutor(temp_dir_with_content)
        action = GrepAction(pattern="hello", include="*.py")

        # Get results from both methods
        ripgrep_result = executor._execute_with_ripgrep(
            action, Path(temp_dir_with_content)
        )
        fallback_result = executor._execute_with_grep(
            action, Path(temp_dir_with_content)
        )

        # Both should succeed
        assert not ripgrep_result.is_error
        assert not fallback_result.is_error

        # Convert to sets for exact comparison
        ripgrep_matches = set(ripgrep_result.matches)
        fallback_matches = set(fallback_result.matches)

        # Both methods must return exactly the same files
        assert ripgrep_matches == fallback_matches, (
            f"Ripgrep found: {ripgrep_matches}\n"
            f"Fallback found: {fallback_matches}\n"
            f"Difference (ripgrep - fallback): {ripgrep_matches - fallback_matches}\n"
            f"Difference (fallback - ripgrep): {fallback_matches - ripgrep_matches}"
        )

        # Verify all matches are Python files
        for match in ripgrep_matches:
            assert match.endswith(".py"), f"Non-Python file found: {match}"

    def test_no_matches_consistency(self, temp_dir_with_content):
        """Test that both methods handle no matches consistently."""
        executor = GrepExecutor(temp_dir_with_content)
        action = GrepAction(pattern="nonexistentpattern12345")

        # Get results from both methods
        ripgrep_result = executor._execute_with_ripgrep(
            action, Path(temp_dir_with_content)
        )
        fallback_result = executor._execute_with_grep(
            action, Path(temp_dir_with_content)
        )

        # Both should succeed with identical empty results
        assert not ripgrep_result.is_error
        assert not fallback_result.is_error

        # Convert to sets for exact comparison
        ripgrep_matches = set(ripgrep_result.matches)
        fallback_matches = set(fallback_result.matches)

        # Both must return exactly the same (empty) set
        assert ripgrep_matches == fallback_matches == set()

    def test_regex_pattern_consistency(self, temp_dir_with_content):
        """Test that both methods handle simple regex patterns consistently."""
        executor = GrepExecutor(temp_dir_with_content)
        action = GrepAction(pattern="def ")  # Simple pattern that should work in both

        # Get results from both methods
        ripgrep_result = executor._execute_with_ripgrep(
            action, Path(temp_dir_with_content)
        )
        fallback_result = executor._execute_with_grep(
            action, Path(temp_dir_with_content)
        )

        # Both should succeed
        assert not ripgrep_result.is_error
        assert not fallback_result.is_error

        # Convert to sets for exact comparison
        ripgrep_matches = set(ripgrep_result.matches)
        fallback_matches = set(fallback_result.matches)

        # Both methods must return exactly the same files
        assert ripgrep_matches == fallback_matches, (
            f"Ripgrep found: {ripgrep_matches}\n"
            f"Fallback found: {fallback_matches}\n"
            f"Difference (ripgrep - fallback): {ripgrep_matches - fallback_matches}\n"
            f"Difference (fallback - ripgrep): {fallback_matches - ripgrep_matches}"
        )

    def test_todo_comments_consistency(self, temp_dir_with_content):
        """Test that both methods find TODO comments consistently."""
        executor = GrepExecutor(temp_dir_with_content)
        action = GrepAction(pattern="TODO")

        # Get results from both methods
        ripgrep_result = executor._execute_with_ripgrep(
            action, Path(temp_dir_with_content)
        )
        fallback_result = executor._execute_with_grep(
            action, Path(temp_dir_with_content)
        )

        # Both should succeed
        assert not ripgrep_result.is_error
        assert not fallback_result.is_error

        # Convert to sets for exact comparison
        ripgrep_matches = set(ripgrep_result.matches)
        fallback_matches = set(fallback_result.matches)

        # Both methods must return exactly the same files
        assert ripgrep_matches == fallback_matches, (
            f"Ripgrep found: {ripgrep_matches}\n"
            f"Fallback found: {fallback_matches}\n"
            f"Difference (ripgrep - fallback): {ripgrep_matches - fallback_matches}\n"
            f"Difference (fallback - ripgrep): {fallback_matches - ripgrep_matches}"
        )

    def test_error_patterns_consistency(self, temp_dir_with_content):
        """Test that both methods find error patterns consistently."""
        executor = GrepExecutor(temp_dir_with_content)
        action = GrepAction(pattern="ERROR:")

        # Get results from both methods
        ripgrep_result = executor._execute_with_ripgrep(
            action, Path(temp_dir_with_content)
        )
        fallback_result = executor._execute_with_grep(
            action, Path(temp_dir_with_content)
        )

        # Both should succeed
        assert not ripgrep_result.is_error
        assert not fallback_result.is_error

        # Convert to sets for exact comparison
        ripgrep_matches = set(ripgrep_result.matches)
        fallback_matches = set(fallback_result.matches)

        # Both methods must return exactly the same files
        assert ripgrep_matches == fallback_matches, (
            f"Ripgrep found: {ripgrep_matches}\n"
            f"Fallback found: {fallback_matches}\n"
            f"Difference (ripgrep - fallback): {ripgrep_matches - fallback_matches}\n"
            f"Difference (fallback - ripgrep): {fallback_matches - ripgrep_matches}"
        )

    def test_import_statements_consistency(self, temp_dir_with_content):
        """Test that both methods find import statements consistently."""
        executor = GrepExecutor(temp_dir_with_content)
        action = GrepAction(pattern="import ", include="*.py")

        # Get results from both methods
        ripgrep_result = executor._execute_with_ripgrep(
            action, Path(temp_dir_with_content)
        )
        fallback_result = executor._execute_with_grep(
            action, Path(temp_dir_with_content)
        )

        # Both should succeed
        assert not ripgrep_result.is_error
        assert not fallback_result.is_error

        # Convert to sets for exact comparison
        ripgrep_matches = set(ripgrep_result.matches)
        fallback_matches = set(fallback_result.matches)

        # Both methods must return exactly the same files
        assert ripgrep_matches == fallback_matches, (
            f"Ripgrep found: {ripgrep_matches}\n"
            f"Fallback found: {fallback_matches}\n"
            f"Difference (ripgrep - fallback): {ripgrep_matches - fallback_matches}\n"
            f"Difference (fallback - ripgrep): {fallback_matches - ripgrep_matches}"
        )

    def test_class_definitions_consistency(self, temp_dir_with_content):
        """Test that both methods find class definitions consistently."""
        executor = GrepExecutor(temp_dir_with_content)
        action = GrepAction(pattern="class ")

        # Get results from both methods
        ripgrep_result = executor._execute_with_ripgrep(
            action, Path(temp_dir_with_content)
        )
        fallback_result = executor._execute_with_grep(
            action, Path(temp_dir_with_content)
        )

        # Both should succeed
        assert not ripgrep_result.is_error
        assert not fallback_result.is_error

        # Convert to sets for exact comparison
        ripgrep_matches = set(ripgrep_result.matches)
        fallback_matches = set(fallback_result.matches)

        # Both methods must return exactly the same files
        assert ripgrep_matches == fallback_matches, (
            f"Ripgrep found: {ripgrep_matches}\n"
            f"Fallback found: {fallback_matches}\n"
            f"Difference (ripgrep - fallback): {ripgrep_matches - fallback_matches}\n"
            f"Difference (fallback - ripgrep): {fallback_matches - ripgrep_matches}"
        )

    def test_deep_nested_search_consistency(self, temp_dir_with_content):
        """Test that both methods search deeply nested files consistently."""
        executor = GrepExecutor(temp_dir_with_content)
        action = GrepAction(pattern="deep")

        # Get results from both methods
        ripgrep_result = executor._execute_with_ripgrep(
            action, Path(temp_dir_with_content)
        )
        fallback_result = executor._execute_with_grep(
            action, Path(temp_dir_with_content)
        )

        # Both should succeed
        assert not ripgrep_result.is_error
        assert not fallback_result.is_error

        # Convert to sets for exact comparison
        ripgrep_matches = set(ripgrep_result.matches)
        fallback_matches = set(fallback_result.matches)

        # Both methods must return exactly the same files
        assert ripgrep_matches == fallback_matches, (
            f"Ripgrep found: {ripgrep_matches}\n"
            f"Fallback found: {fallback_matches}\n"
            f"Difference (ripgrep - fallback): {ripgrep_matches - fallback_matches}\n"
            f"Difference (fallback - ripgrep): {fallback_matches - ripgrep_matches}"
        )

    def test_config_file_search_consistency(self, temp_dir_with_content):
        """Test that both methods search various config file formats consistently."""
        executor = GrepExecutor(temp_dir_with_content)

        for pattern, file_type in [
            ("debug", "*.json"),
            ("localhost", "*.yaml"),
            ("secret", "*.env"),
        ]:
            action = GrepAction(pattern=pattern, include=file_type)

            # Get results from both methods
            ripgrep_result = executor._execute_with_ripgrep(
                action, Path(temp_dir_with_content)
            )
            fallback_result = executor._execute_with_grep(
                action, Path(temp_dir_with_content)
            )

            # Both should succeed
            assert not ripgrep_result.is_error
            assert not fallback_result.is_error

            # Convert to sets for exact comparison
            ripgrep_matches = set(ripgrep_result.matches)
            fallback_matches = set(fallback_result.matches)

            # Both methods must return exactly the same files
            assert ripgrep_matches == fallback_matches, (
                f"Pattern: {pattern}, File type: {file_type}\n"
                f"Ripgrep found: {ripgrep_matches}\n"
                f"Fallback found: {fallback_matches}\n"
                f"Difference (ripgrep - fallback): {ripgrep_matches - fallback_matches}\n"
                f"Difference (fallback - ripgrep): {fallback_matches - ripgrep_matches}"
            )

    def test_hidden_files_search_consistency(self, temp_dir_with_content):
        """Test that both methods search hidden files consistently."""
        executor = GrepExecutor(temp_dir_with_content)
        action = GrepAction(pattern="API_KEY")

        # Get results from both methods
        ripgrep_result = executor._execute_with_ripgrep(
            action, Path(temp_dir_with_content)
        )
        fallback_result = executor._execute_with_grep(
            action, Path(temp_dir_with_content)
        )

        # Both should succeed
        assert not ripgrep_result.is_error
        assert not fallback_result.is_error

        # Convert to sets for exact comparison
        ripgrep_matches = set(ripgrep_result.matches)
        fallback_matches = set(fallback_result.matches)

        # Both methods must return exactly the same files
        assert ripgrep_matches == fallback_matches, (
            f"Ripgrep found: {ripgrep_matches}\n"
            f"Fallback found: {fallback_matches}\n"
            f"Difference (ripgrep - fallback): {ripgrep_matches - fallback_matches}\n"
            f"Difference (fallback - ripgrep): {fallback_matches - ripgrep_matches}"
        )
