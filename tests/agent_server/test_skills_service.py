"""Tests for skills service."""

import tempfile
from pathlib import Path
from unittest.mock import patch

from openhands.agent_server.skills_service import (
    SANDBOX_WORKER_URL_PREFIX,
    ExposedUrlData,
    SkillLoadResult,
    create_sandbox_skill,
    load_all_skills,
    load_org_skills_from_url,
    merge_skills,
    sync_public_skills,
)
from openhands.sdk.context.skills import Skill


class TestExposedUrlData:
    """Tests for ExposedUrlData dataclass."""

    def test_create_exposed_url_data(self):
        """Test creating ExposedUrlData instance."""
        url_data = ExposedUrlData(
            name="WORKER_8080",
            url="http://localhost:8080",
            port=8080,
        )
        assert url_data.name == "WORKER_8080"
        assert url_data.url == "http://localhost:8080"
        assert url_data.port == 8080


class TestCreateSandboxSkill:
    """Tests for create_sandbox_skill function."""

    def test_create_sandbox_skill_with_worker_urls(self):
        """Test creating sandbox skill with WORKER_ prefixed URLs."""
        exposed_urls = [
            ExposedUrlData(name="WORKER_8080", url="http://localhost:8080", port=8080),
            ExposedUrlData(name="WORKER_3000", url="http://localhost:3000", port=3000),
        ]

        skill = create_sandbox_skill(exposed_urls)

        assert skill is not None
        assert skill.name == "work_hosts"
        assert "http://localhost:8080" in skill.content
        assert "http://localhost:3000" in skill.content
        assert "port 8080" in skill.content
        assert "port 3000" in skill.content
        assert skill.trigger is None
        assert skill.source is None

    def test_create_sandbox_skill_no_worker_urls(self):
        """Test that non-WORKER_ URLs are filtered out."""
        exposed_urls = [
            ExposedUrlData(name="DATABASE", url="http://localhost:5432", port=5432),
            ExposedUrlData(name="REDIS", url="http://localhost:6379", port=6379),
        ]

        skill = create_sandbox_skill(exposed_urls)

        assert skill is None

    def test_create_sandbox_skill_mixed_urls(self):
        """Test with mix of WORKER_ and non-WORKER_ URLs."""
        exposed_urls = [
            ExposedUrlData(name="WORKER_8080", url="http://localhost:8080", port=8080),
            ExposedUrlData(name="DATABASE", url="http://localhost:5432", port=5432),
            ExposedUrlData(name="WORKER_3000", url="http://localhost:3000", port=3000),
        ]

        skill = create_sandbox_skill(exposed_urls)

        assert skill is not None
        assert "http://localhost:8080" in skill.content
        assert "http://localhost:3000" in skill.content
        assert "http://localhost:5432" not in skill.content

    def test_create_sandbox_skill_empty_list(self):
        """Test with empty URL list."""
        skill = create_sandbox_skill([])
        assert skill is None

    def test_sandbox_worker_url_prefix_constant(self):
        """Test that SANDBOX_WORKER_URL_PREFIX is correctly defined."""
        assert SANDBOX_WORKER_URL_PREFIX == "WORKER_"


class TestMergeSkills:
    """Tests for merge_skills function."""

    def test_merge_empty_lists(self):
        """Test merging empty skill lists."""
        result = merge_skills([[], [], []])
        assert result == []

    def test_merge_single_list(self):
        """Test merging a single skill list."""
        skills = [
            Skill(name="skill1", content="content1", trigger=None),
            Skill(name="skill2", content="content2", trigger=None),
        ]

        result = merge_skills([skills])

        assert len(result) == 2
        assert {s.name for s in result} == {"skill1", "skill2"}

    def test_merge_multiple_lists_no_duplicates(self):
        """Test merging multiple lists without duplicates."""
        list1 = [Skill(name="skill1", content="content1", trigger=None)]
        list2 = [Skill(name="skill2", content="content2", trigger=None)]
        list3 = [Skill(name="skill3", content="content3", trigger=None)]

        result = merge_skills([list1, list2, list3])

        assert len(result) == 3
        assert {s.name for s in result} == {"skill1", "skill2", "skill3"}

    def test_merge_with_duplicates_later_wins(self):
        """Test that later lists override earlier lists for duplicate names."""
        list1 = [Skill(name="skill1", content="original", trigger=None)]
        list2 = [Skill(name="skill1", content="override", trigger=None)]

        result = merge_skills([list1, list2])

        assert len(result) == 1
        assert result[0].name == "skill1"
        assert result[0].content == "override"

    def test_merge_preserves_precedence_order(self):
        """Test that precedence order is maintained (later overrides earlier)."""
        list1 = [Skill(name="shared", content="first", trigger=None)]
        list2 = [Skill(name="shared", content="second", trigger=None)]
        list3 = [Skill(name="shared", content="third", trigger=None)]

        result = merge_skills([list1, list2, list3])

        assert len(result) == 1
        assert result[0].content == "third"


class TestLoadOrgSkillsFromUrl:
    """Tests for load_org_skills_from_url function."""

    def test_load_org_skills_git_clone_failure(self):
        """Test handling of git clone failure."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = Exception("Git not found")

            result = load_org_skills_from_url(
                org_repo_url="https://github.com/org/.openhands",
                org_name="test-org",
            )

            assert result == []

    def test_load_org_skills_repo_not_found(self):
        """Test handling of repository not found."""
        import subprocess

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=128,
                cmd=["git", "clone"],
            )

            result = load_org_skills_from_url(
                org_repo_url="https://github.com/org/.openhands",
                org_name="test-org",
            )

            assert result == []

    def test_load_org_skills_timeout(self):
        """Test handling of git clone timeout."""
        import subprocess

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd=["git", "clone"],
                timeout=120,
            )

            result = load_org_skills_from_url(
                org_repo_url="https://github.com/org/.openhands",
                org_name="test-org",
            )

            assert result == []

    def test_load_org_skills_custom_working_dir(self):
        """Test using custom working directory."""
        import subprocess

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("subprocess.run") as mock_run:
                mock_run.side_effect = subprocess.CalledProcessError(
                    returncode=128,
                    cmd=["git", "clone"],
                )

                result = load_org_skills_from_url(
                    org_repo_url="https://github.com/org/.openhands",
                    org_name="test-org",
                    working_dir=tmpdir,
                )

                assert result == []


class TestLoadAllSkills:
    """Tests for load_all_skills function."""

    def test_load_all_skills_returns_skill_load_result(self):
        """Test that load_all_skills returns a SkillLoadResult."""
        with (
            patch(
                "openhands.agent_server.skills_service.load_public_skills"
            ) as mock_public,
            patch(
                "openhands.agent_server.skills_service.load_user_skills"
            ) as mock_user,
            patch(
                "openhands.agent_server.skills_service.load_project_skills"
            ) as mock_project,
        ):
            mock_public.return_value = []
            mock_user.return_value = []
            mock_project.return_value = []

            result = load_all_skills(
                load_public=True,
                load_user=True,
                load_project=False,
                load_org=False,
            )

            assert isinstance(result, SkillLoadResult)
            assert isinstance(result.skills, list)
            assert isinstance(result.sources, dict)

    def test_load_all_skills_sources_tracking(self):
        """Test that source counts are tracked correctly."""
        skill1 = Skill(name="public1", content="c1", trigger=None)
        skill2 = Skill(name="user1", content="c2", trigger=None)

        with (
            patch(
                "openhands.agent_server.skills_service.load_public_skills"
            ) as mock_public,
            patch(
                "openhands.agent_server.skills_service.load_user_skills"
            ) as mock_user,
            patch(
                "openhands.agent_server.skills_service.load_project_skills"
            ) as mock_project,
        ):
            mock_public.return_value = [skill1]
            mock_user.return_value = [skill2]
            mock_project.return_value = []

            result = load_all_skills(
                load_public=True,
                load_user=True,
                load_project=False,
                load_org=False,
            )

            assert result.sources["public"] == 1
            assert result.sources["user"] == 1
            assert result.sources["sandbox"] == 0
            assert result.sources["org"] == 0
            assert result.sources["project"] == 0

    def test_load_all_skills_disabled_sources(self):
        """Test that disabled sources are not loaded."""
        with (
            patch(
                "openhands.agent_server.skills_service.load_public_skills"
            ) as mock_public,
            patch(
                "openhands.agent_server.skills_service.load_user_skills"
            ) as mock_user,
        ):
            mock_public.return_value = []
            mock_user.return_value = []

            result = load_all_skills(
                load_public=False,
                load_user=False,
                load_project=False,
                load_org=False,
            )

            mock_public.assert_not_called()
            mock_user.assert_not_called()
            assert result.sources["public"] == 0
            assert result.sources["user"] == 0

    def test_load_all_skills_with_sandbox_urls(self):
        """Test loading skills with sandbox URLs."""
        sandbox_urls = [
            ExposedUrlData(name="WORKER_8080", url="http://localhost:8080", port=8080),
        ]

        with (
            patch(
                "openhands.agent_server.skills_service.load_public_skills"
            ) as mock_public,
            patch(
                "openhands.agent_server.skills_service.load_user_skills"
            ) as mock_user,
        ):
            mock_public.return_value = []
            mock_user.return_value = []

            result = load_all_skills(
                load_public=False,
                load_user=False,
                load_project=False,
                load_org=False,
                sandbox_exposed_urls=sandbox_urls,
            )

            assert result.sources["sandbox"] == 1
            assert len(result.skills) == 1
            assert result.skills[0].name == "work_hosts"

    def test_load_all_skills_handles_exceptions(self):
        """Test that exceptions from skill loaders are handled gracefully."""
        with (
            patch(
                "openhands.agent_server.skills_service.load_public_skills"
            ) as mock_public,
            patch(
                "openhands.agent_server.skills_service.load_user_skills"
            ) as mock_user,
        ):
            mock_public.side_effect = Exception("Network error")
            mock_user.return_value = [
                Skill(name="user1", content="content", trigger=None)
            ]

            result = load_all_skills(
                load_public=True,
                load_user=True,
                load_project=False,
                load_org=False,
            )

            # Should still return results from successful loaders
            assert result.sources["public"] == 0
            assert result.sources["user"] == 1

    def test_load_all_skills_merge_precedence(self):
        """Test that skills are merged with correct precedence."""
        public_skill = Skill(name="shared", content="public", trigger=None)
        user_skill = Skill(name="shared", content="user", trigger=None)
        project_skill = Skill(name="shared", content="project", trigger=None)

        with (
            patch(
                "openhands.agent_server.skills_service.load_public_skills"
            ) as mock_public,
            patch(
                "openhands.agent_server.skills_service.load_user_skills"
            ) as mock_user,
            patch(
                "openhands.agent_server.skills_service.load_project_skills"
            ) as mock_project,
        ):
            mock_public.return_value = [public_skill]
            mock_user.return_value = [user_skill]
            mock_project.return_value = [project_skill]

            result = load_all_skills(
                load_public=True,
                load_user=True,
                load_project=True,
                load_org=False,
                project_dir="/workspace",
            )

            # Project should override user which overrides public
            shared_skills = [s for s in result.skills if s.name == "shared"]
            assert len(shared_skills) == 1
            assert shared_skills[0].content == "project"


class TestSyncPublicSkills:
    """Tests for sync_public_skills function."""

    def test_sync_public_skills_success(self):
        """Test successful skill sync."""
        with (
            patch(
                "openhands.agent_server.skills_service.get_skills_cache_dir"
            ) as mock_cache,
            patch(
                "openhands.agent_server.skills_service.update_skills_repository"
            ) as mock_update,
        ):
            mock_cache.return_value = Path("/tmp/cache")
            mock_update.return_value = Path("/tmp/cache/public-skills")

            success, message = sync_public_skills()

            assert success is True
            assert "success" in message.lower()

    def test_sync_public_skills_failure(self):
        """Test failed skill sync."""
        with (
            patch(
                "openhands.agent_server.skills_service.get_skills_cache_dir"
            ) as mock_cache,
            patch(
                "openhands.agent_server.skills_service.update_skills_repository"
            ) as mock_update,
        ):
            mock_cache.return_value = Path("/tmp/cache")
            mock_update.return_value = None

            success, message = sync_public_skills()

            assert success is False
            assert "failed" in message.lower()

    def test_sync_public_skills_exception(self):
        """Test skill sync with exception."""
        with patch(
            "openhands.agent_server.skills_service.get_skills_cache_dir"
        ) as mock_cache:
            mock_cache.side_effect = Exception("Permission denied")

            success, message = sync_public_skills()

            assert success is False
            assert "failed" in message.lower() or "error" in message.lower()


class TestSkillLoadResult:
    """Tests for SkillLoadResult dataclass."""

    def test_skill_load_result_creation(self):
        """Test creating SkillLoadResult instance."""
        skills = [Skill(name="test", content="content", trigger=None)]
        sources = {"public": 1, "user": 0}

        result = SkillLoadResult(skills=skills, sources=sources)

        assert result.skills == skills
        assert result.sources == sources

    def test_skill_load_result_empty(self):
        """Test creating empty SkillLoadResult."""
        result = SkillLoadResult(skills=[], sources={})

        assert result.skills == []
        assert result.sources == {}
