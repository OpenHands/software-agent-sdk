"""Tests to verify consistency between ripgrep and fallback implementations."""

import tempfile
from pathlib import Path

import pytest

from openhands.tools.glob.definition import GlobAction
from openhands.tools.glob.impl import GlobExecutor
from openhands.tools.utils import _check_ripgrep_available


@pytest.mark.skipif(
    not _check_ripgrep_available(),
    reason="ripgrep not available - consistency tests require ripgrep",
)
class TestGlobConsistency:
    """Test that ripgrep and fallback methods produce consistent results."""

    @pytest.fixture
    def temp_dir_with_files(self):
        """Create a temporary directory with test files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create test files with more complex structure
            test_files = {
                # Root level files
                "app.py": "print('hello world')",
                "main.py": "def main(): pass",
                "test.py": "import unittest",
                "config.json": '{"name": "test"}',
                "config.yaml": "name: test",
                "readme.md": "# Test Project",
                "README.MD": "# Alternate README",
                ".gitignore": "*.pyc\n__pycache__/",
                "setup.py": "from setuptools import setup",
                # Source directory
                "src/utils.py": "def helper(): pass",
                "src/models.py": "class User: pass",
                "src/api.py": "def api_handler(): pass",
                "src/__init__.py": "",
                "src/core/engine.py": "class Engine: pass",
                "src/core/parser.py": "def parse(): pass",
                "src/core/__init__.py": "",
                "src/plugins/auth.py": "def authenticate(): pass",
                "src/plugins/db.py": "class Database: pass",
                "src/plugins/__init__.py": "",
                # Tests directory
                "tests/test_utils.py": "def test_helper(): pass",
                "tests/test_models.py": "def test_user(): pass",
                "tests/integration/test_api.py": "def test_api(): pass",
                "tests/integration/__init__.py": "",
                "tests/unit/test_engine.py": "def test_engine(): pass",
                "tests/unit/test_parser.py": "def test_parser(): pass",
                "tests/unit/__init__.py": "",
                # Documentation
                "docs/guide.md": "# Guide",
                "docs/api.md": "# API Reference",
                "docs/tutorial.rst": "Tutorial",
                "docs/images/diagram.png": b"\x89PNG",  # Minimal PNG header
                "docs/examples/example1.py": "# Example 1",
                "docs/examples/example2.py": "# Example 2",
                # Configuration files in various formats
                "config/settings.json": '{"debug": true}',
                "config/database.yaml": "host: localhost",
                "config/logging.ini": "[loggers]",
                "config/secrets.env": "API_KEY=secret",
                # Scripts
                "scripts/deploy.sh": "#!/bin/bash\necho 'deploying'",
                "scripts/build.py": "import subprocess",
                "scripts/test.py": "import pytest",
                # Build artifacts (should be matched by patterns)
                "build/output.js": "console.log('built')",
                "build/styles.css": "body { margin: 0; }",
                "dist/bundle.js": "// bundled code",
                # Hidden directory
                ".github/workflows/ci.yml": "name: CI",
                ".github/workflows/deploy.yml": "name: Deploy",
                # Deep nesting
                "deep/level1/level2/level3/file.py": "# Deep file",
                "deep/level1/level2/level3/data.json": "{}",
                # Multiple extensions
                "data.tar.gz": "archive",
                "backup.2024.tar.gz": "backup",
                "script.test.py": "# Test script",
                # Special characters in names
                "file-with-dashes.py": "# Dashes",
                "file_with_underscores.py": "# Underscores",
                "file.backup.py": "# Backup",
                # Empty directories (add marker files)
                "empty_dir/.keep": "",
                "another_empty/.gitkeep": "",
            }

            for file_path, content in test_files.items():
                full_path = Path(temp_dir) / file_path
                full_path.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(content, bytes):
                    full_path.write_bytes(content)
                else:
                    full_path.write_text(content)

            yield temp_dir

    def test_basic_pattern_consistency(self, temp_dir_with_files):
        """Test that both methods return consistent results for basic patterns."""
        executor = GlobExecutor(temp_dir_with_files)
        action = GlobAction(pattern="*.py")

        # Get results from both methods
        ripgrep_files, _ = executor._execute_with_ripgrep(
            action.pattern, Path(temp_dir_with_files)
        )
        fallback_files, _ = executor._execute_with_glob(
            action.pattern, Path(temp_dir_with_files)
        )

        # Convert to sets for exact comparison
        ripgrep_files = set(ripgrep_files)
        fallback_files = set(fallback_files)

        # Both methods must return exactly the same files
        assert ripgrep_files == fallback_files, (
            f"Ripgrep found: {ripgrep_files}\n"
            f"Fallback found: {fallback_files}\n"
            f"Difference (ripgrep - fallback): {ripgrep_files - fallback_files}\n"
            f"Difference (fallback - ripgrep): {fallback_files - ripgrep_files}"
        )

    def test_recursive_pattern_consistency(self, temp_dir_with_files):
        """Test that both methods handle recursive patterns consistently."""
        executor = GlobExecutor(temp_dir_with_files)
        action = GlobAction(pattern="**/*.py")

        # Get results from both methods
        ripgrep_files, _ = executor._execute_with_ripgrep(
            action.pattern, Path(temp_dir_with_files)
        )
        fallback_files, _ = executor._execute_with_glob(
            action.pattern, Path(temp_dir_with_files)
        )

        # Convert to sets for exact comparison
        ripgrep_files = set(ripgrep_files)
        fallback_files = set(fallback_files)

        # Both methods must return exactly the same files
        assert ripgrep_files == fallback_files, (
            f"Ripgrep found: {ripgrep_files}\n"
            f"Fallback found: {fallback_files}\n"
            f"Difference (ripgrep - fallback): {ripgrep_files - fallback_files}\n"
            f"Difference (fallback - ripgrep): {fallback_files - ripgrep_files}"
        )

    def test_no_matches_consistency(self, temp_dir_with_files):
        """Test that both methods handle no matches consistently."""
        executor = GlobExecutor(temp_dir_with_files)
        action = GlobAction(pattern="*.nonexistent")

        # Get results from both methods
        ripgrep_files, _ = executor._execute_with_ripgrep(
            action.pattern, Path(temp_dir_with_files)
        )
        fallback_files, _ = executor._execute_with_glob(
            action.pattern, Path(temp_dir_with_files)
        )

        # Convert to sets for exact comparison
        ripgrep_files = set(ripgrep_files)
        fallback_files = set(fallback_files)

        # Both must return exactly the same (empty) set
        assert ripgrep_files == fallback_files == set()

    def test_hidden_files_consistency(self, temp_dir_with_files):
        """Test that both methods handle hidden files consistently."""
        executor = GlobExecutor(temp_dir_with_files)
        action = GlobAction(pattern=".*")

        # Get results from both methods
        ripgrep_files, _ = executor._execute_with_ripgrep(
            action.pattern, Path(temp_dir_with_files)
        )
        fallback_files, _ = executor._execute_with_glob(
            action.pattern, Path(temp_dir_with_files)
        )

        # Convert to sets for exact comparison
        ripgrep_files = set(ripgrep_files)
        fallback_files = set(fallback_files)

        # Both methods must return exactly the same files
        assert ripgrep_files == fallback_files, (
            f"Ripgrep found: {ripgrep_files}\n"
            f"Fallback found: {fallback_files}\n"
            f"Difference (ripgrep - fallback): {ripgrep_files - fallback_files}\n"
            f"Difference (fallback - ripgrep): {fallback_files - ripgrep_files}"
        )

    def test_multiple_extensions_consistency(self, temp_dir_with_files):
        """Test that both methods handle multiple extensions consistently."""
        executor = GlobExecutor(temp_dir_with_files)
        action = GlobAction(pattern="*.tar.gz")

        # Get results from both methods
        ripgrep_files, _ = executor._execute_with_ripgrep(
            action.pattern, Path(temp_dir_with_files)
        )
        fallback_files, _ = executor._execute_with_glob(
            action.pattern, Path(temp_dir_with_files)
        )

        # Convert to sets for exact comparison
        ripgrep_files = set(ripgrep_files)
        fallback_files = set(fallback_files)

        # Both methods must return exactly the same files
        assert ripgrep_files == fallback_files, (
            f"Ripgrep found: {ripgrep_files}\n"
            f"Fallback found: {fallback_files}\n"
            f"Difference (ripgrep - fallback): {ripgrep_files - fallback_files}\n"
            f"Difference (fallback - ripgrep): {fallback_files - ripgrep_files}"
        )

    def test_deep_nesting_consistency(self, temp_dir_with_files):
        """Test that both methods handle deeply nested files consistently."""
        executor = GlobExecutor(temp_dir_with_files)
        action = GlobAction(pattern="**/level3/*.py")

        # Get results from both methods
        ripgrep_files, _ = executor._execute_with_ripgrep(
            action.pattern, Path(temp_dir_with_files)
        )
        fallback_files, _ = executor._execute_with_glob(
            action.pattern, Path(temp_dir_with_files)
        )

        # Convert to sets for exact comparison
        ripgrep_files = set(ripgrep_files)
        fallback_files = set(fallback_files)

        # Both methods must return exactly the same files
        assert ripgrep_files == fallback_files, (
            f"Ripgrep found: {ripgrep_files}\n"
            f"Fallback found: {fallback_files}\n"
            f"Difference (ripgrep - fallback): {ripgrep_files - fallback_files}\n"
            f"Difference (fallback - ripgrep): {fallback_files - ripgrep_files}"
        )

    def test_wildcard_directory_consistency(self, temp_dir_with_files):
        """Test that both methods handle wildcard directories consistently."""
        executor = GlobExecutor(temp_dir_with_files)
        action = GlobAction(pattern="**/test*.py")

        # Get results from both methods
        ripgrep_files, _ = executor._execute_with_ripgrep(
            action.pattern, Path(temp_dir_with_files)
        )
        fallback_files, _ = executor._execute_with_glob(
            action.pattern, Path(temp_dir_with_files)
        )

        # Convert to sets for exact comparison
        ripgrep_files = set(ripgrep_files)
        fallback_files = set(fallback_files)

        # Both methods must return exactly the same files
        assert ripgrep_files == fallback_files, (
            f"Ripgrep found: {ripgrep_files}\n"
            f"Fallback found: {fallback_files}\n"
            f"Difference (ripgrep - fallback): {ripgrep_files - fallback_files}\n"
            f"Difference (fallback - ripgrep): {fallback_files - ripgrep_files}"
        )

    def test_config_files_consistency(self, temp_dir_with_files):
        """Test that both methods find various config file formats consistently."""
        executor = GlobExecutor(temp_dir_with_files)

        for pattern in ["*.json", "*.yaml", "*.yml", "*.ini", "*.env"]:
            action = GlobAction(pattern=pattern)

            # Get results from both methods
            ripgrep_files, _ = executor._execute_with_ripgrep(
                action.pattern, Path(temp_dir_with_files)
            )
            fallback_files, _ = executor._execute_with_glob(
                action.pattern, Path(temp_dir_with_files)
            )

            # Convert to sets for exact comparison
            ripgrep_files = set(ripgrep_files)
            fallback_files = set(fallback_files)

            # Both methods must return exactly the same files
            assert ripgrep_files == fallback_files, (
                f"Pattern: {pattern}\n"
                f"Ripgrep found: {ripgrep_files}\n"
                f"Fallback found: {fallback_files}\n"
                f"Difference (ripgrep - fallback): {ripgrep_files - fallback_files}\n"
                f"Difference (fallback - ripgrep): {fallback_files - ripgrep_files}"
            )

    def test_special_characters_consistency(self, temp_dir_with_files):
        """
        Test that both methods handle special characters in filenames consistently.
        """
        executor = GlobExecutor(temp_dir_with_files)
        action = GlobAction(pattern="*-with-*.py")

        # Get results from both methods
        ripgrep_files, _ = executor._execute_with_ripgrep(
            action.pattern, Path(temp_dir_with_files)
        )
        fallback_files, _ = executor._execute_with_glob(
            action.pattern, Path(temp_dir_with_files)
        )

        # Convert to sets for exact comparison
        ripgrep_files = set(ripgrep_files)
        fallback_files = set(fallback_files)

        # Both methods must return exactly the same files
        assert ripgrep_files == fallback_files, (
            f"Ripgrep found: {ripgrep_files}\n"
            f"Fallback found: {fallback_files}\n"
            f"Difference (ripgrep - fallback): {ripgrep_files - fallback_files}\n"
            f"Difference (fallback - ripgrep): {fallback_files - ripgrep_files}"
        )

    def test_case_sensitivity_consistency(self, temp_dir_with_files):
        """Test that both methods handle case sensitivity consistently."""
        executor = GlobExecutor(temp_dir_with_files)
        action = GlobAction(pattern="*.md")

        # Get results from both methods
        ripgrep_files, _ = executor._execute_with_ripgrep(
            action.pattern, Path(temp_dir_with_files)
        )
        fallback_files, _ = executor._execute_with_glob(
            action.pattern, Path(temp_dir_with_files)
        )

        # Convert to sets for exact comparison
        ripgrep_files = set(ripgrep_files)
        fallback_files = set(fallback_files)

        # Both methods must return exactly the same files
        assert ripgrep_files == fallback_files, (
            f"Ripgrep found: {ripgrep_files}\n"
            f"Fallback found: {fallback_files}\n"
            f"Difference (ripgrep - fallback): {ripgrep_files - fallback_files}\n"
            f"Difference (fallback - ripgrep): {fallback_files - ripgrep_files}"
        )
