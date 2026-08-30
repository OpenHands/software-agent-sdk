"""Tests for skills service."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from openhands.agent_server.skills_service import (
    SANDBOX_WORKER_URL_PREFIX,
    ExposedUrlData,
    SkillLoadResult,
    create_sandbox_skill,
    discover_profile_skills,
    load_all_skills,
    load_org_skills_from_url,
    load_registered_marketplace_skills,
    merge_skills,
    sync_public_skills,
)
from openhands.sdk.marketplace.registration import MarketplaceRegistration
from openhands.sdk.skills import Skill


def _create_test_plugin(plugin_dir: Path, name: str, skill_name: str) -> Path:
    manifest_dir = plugin_dir / ".plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "plugin.json").write_text(
        json.dumps({"name": name, "version": "1.0.0"})
    )
    skills_dir = plugin_dir / "skills"
    skills_dir.mkdir()
    (skills_dir / f"{skill_name}.md").write_text(
        f"---\nname: {skill_name}\n---\n{skill_name} content"
    )
    return plugin_dir


def _create_test_marketplace(marketplace_dir: Path) -> Path:
    _create_test_plugin(marketplace_dir / "plugins" / "auto", "auto", "auto-skill")
    _create_test_plugin(
        marketplace_dir / "plugins" / "manual", "manual", "manual-skill"
    )
    manifest_dir = marketplace_dir / ".plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "marketplace.json").write_text(
        json.dumps(
            {
                "name": "test-marketplace",
                "owner": {"name": "Test Team"},
                "plugins": [
                    {"name": "auto", "source": "./plugins/auto"},
                    {"name": "manual", "source": "./plugins/manual"},
                ],
            }
        )
    )
    return marketplace_dir


def _create_standalone_skill(skill_dir: Path, name: str) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} description\n---\n{name} content"
    )


def _create_skills_only_marketplace(marketplace_dir: Path) -> Path:
    """Marketplace declaring only standalone skills (no plugins)."""
    _create_standalone_skill(marketplace_dir / "skills" / "greet", "greet")
    _create_standalone_skill(marketplace_dir / "skills" / "commit", "commit")
    manifest_dir = marketplace_dir / ".plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "marketplace.json").write_text(
        json.dumps(
            {
                "name": "skills-marketplace",
                "owner": {"name": "Test Team"},
                "plugins": [],
                "skills": [
                    {"name": "greet", "source": "./skills/greet"},
                    {"name": "commit", "source": "./skills/commit"},
                ],
            }
        )
    )
    return marketplace_dir


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

    _PATCH_TARGET = "openhands.agent_server.skills_service.load_available_skills"

    def test_load_all_skills_registered_marketplaces_keep_legacy_public(
        self, tmp_path: Path
    ):
        marketplace_dir = _create_test_marketplace(tmp_path / "marketplace")
        public_skill = Skill(name="public-skill", content="public", trigger=None)
        user_skill = Skill(name="user-skill", content="user", trigger=None)

        with patch(
            self._PATCH_TARGET,
            side_effect=[
                {"public-skill": public_skill, "user-skill": user_skill},
                {},
            ],
        ) as mock_avail:
            result = load_all_skills(
                load_public=True,
                load_user=True,
                load_project=False,
                load_org=False,
                registered_marketplaces=[
                    MarketplaceRegistration(
                        name="test",
                        source=str(marketplace_dir),
                        auto_load=True,
                    )
                ],
            )

        skill_names = {skill.name for skill in result.skills}
        assert "auto-skill" in skill_names
        assert "manual-skill" in skill_names
        assert "public-skill" in skill_names
        assert "user-skill" in skill_names
        assert result.sources["registered_marketplaces"] == 2
        assert result.sources["sdk_base"] == 2
        assert mock_avail.call_args_list[0].kwargs["include_public"] is True

    def test_load_all_skills_non_auto_registered_marketplaces_keep_legacy_public(
        self, tmp_path: Path
    ):
        marketplace_dir = _create_test_marketplace(tmp_path / "marketplace")
        public_skill = Skill(name="public-skill", content="public", trigger=None)

        with patch(
            self._PATCH_TARGET, side_effect=[{"public-skill": public_skill}, {}]
        ) as mock_avail:
            result = load_all_skills(
                load_public=True,
                load_user=False,
                load_project=False,
                load_org=False,
                registered_marketplaces=[
                    MarketplaceRegistration(name="manual", source=str(marketplace_dir))
                ],
            )

        assert [skill.name for skill in result.skills] == ["public-skill"]
        assert result.sources["registered_marketplaces"] == 0
        assert mock_avail.call_args_list[0].kwargs["include_public"] is True

    def test_load_registered_marketplace_skills_uses_auto_load_registrations(
        self, tmp_path: Path
    ):
        """Only registrations marked auto_load=True contribute plugin skills."""
        marketplace_dir = _create_test_marketplace(tmp_path / "marketplace")

        skills = load_registered_marketplace_skills(
            [
                MarketplaceRegistration(
                    name="test",
                    source=str(marketplace_dir),
                    auto_load=True,
                ),
                MarketplaceRegistration(name="manual", source=str(marketplace_dir)),
            ]
        )

        assert {skill.name for skill in skills} == {"auto-skill", "manual-skill"}

    def test_load_registered_marketplace_skills_selects_listed_plugins(
        self, tmp_path: Path
    ):
        marketplace_dir = _create_test_marketplace(tmp_path / "marketplace")

        skills = load_registered_marketplace_skills(
            [
                MarketplaceRegistration(
                    name="test",
                    source=str(marketplace_dir),
                    auto_load=["manual"],
                )
            ]
        )

        assert [skill.name for skill in skills] == ["manual-skill"]

    def test_load_registered_marketplace_skills_loads_standalone_skills(
        self, tmp_path: Path
    ):
        """auto_load=True loads standalone skills, not just plugins."""
        marketplace_dir = _create_skills_only_marketplace(tmp_path / "marketplace")

        skills = load_registered_marketplace_skills(
            [
                MarketplaceRegistration(
                    name="skills-only",
                    source=str(marketplace_dir),
                    auto_load=True,
                )
            ]
        )

        assert {skill.name for skill in skills} == {"greet", "commit"}

    def test_load_registered_marketplace_skills_selects_listed_standalone_skills(
        self, tmp_path: Path
    ):
        """A name list selects standalone skills the same way it selects plugins."""
        marketplace_dir = _create_skills_only_marketplace(tmp_path / "marketplace")

        skills = load_registered_marketplace_skills(
            [
                MarketplaceRegistration(
                    name="skills-only",
                    source=str(marketplace_dir),
                    auto_load=["greet"],
                )
            ]
        )

        assert [skill.name for skill in skills] == ["greet"]

    def test_load_registered_marketplace_skills_skips_standalone_when_not_auto_load(
        self, tmp_path: Path
    ):
        """A skills-only marketplace without auto_load contributes nothing."""
        marketplace_dir = _create_skills_only_marketplace(tmp_path / "marketplace")

        skills = load_registered_marketplace_skills(
            [MarketplaceRegistration(name="skills-only", source=str(marketplace_dir))]
        )

        assert skills == []

    def test_load_registered_marketplace_skills_loads_plugins_and_standalone(
        self, tmp_path: Path
    ):
        """A marketplace with both plugins and standalone skills loads both."""
        marketplace_dir = tmp_path / "marketplace"
        _create_test_plugin(marketplace_dir / "plugins" / "auto", "auto", "auto-skill")
        _create_standalone_skill(marketplace_dir / "skills" / "greet", "greet")
        manifest_dir = marketplace_dir / ".plugin"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "marketplace.json").write_text(
            json.dumps(
                {
                    "name": "mixed-marketplace",
                    "owner": {"name": "Test Team"},
                    "plugins": [{"name": "auto", "source": "./plugins/auto"}],
                    "skills": [{"name": "greet", "source": "./skills/greet"}],
                }
            )
        )

        skills = load_registered_marketplace_skills(
            [
                MarketplaceRegistration(
                    name="mixed", source=str(marketplace_dir), auto_load=True
                )
            ]
        )

        assert {skill.name for skill in skills} == {"auto-skill", "greet"}

    def test_plugin_wins_over_standalone_on_name_collision(self, tmp_path: Path):
        """A plugin skill overrides a same-named standalone skill (catalog rule)."""
        marketplace_dir = tmp_path / "marketplace"
        # Plugin 'p' bundles a skill named 'shared'.
        plugin_dir = marketplace_dir / "plugins" / "p"
        (plugin_dir / ".plugin").mkdir(parents=True)
        (plugin_dir / ".plugin" / "plugin.json").write_text(
            json.dumps({"name": "p", "version": "1.0.0"})
        )
        (plugin_dir / "skills").mkdir()
        (plugin_dir / "skills" / "shared.md").write_text(
            "---\nname: shared\n---\nFROM_PLUGIN"
        )
        # Standalone skill also named 'shared'.
        standalone = marketplace_dir / "skills" / "shared"
        standalone.mkdir(parents=True)
        (standalone / "SKILL.md").write_text(
            "---\nname: shared\ndescription: d\n---\nFROM_STANDALONE"
        )
        manifest_dir = marketplace_dir / ".plugin"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "marketplace.json").write_text(
            json.dumps(
                {
                    "name": "collision",
                    "owner": {"name": "Test Team"},
                    "plugins": [{"name": "p", "source": "./plugins/p"}],
                    "skills": [{"name": "shared", "source": "./skills/shared"}],
                }
            )
        )

        # Marketplace auto-load is gated on load_public; the patch keeps the
        # public tier empty so only the marketplace contributes 'shared'.
        with patch(self._PATCH_TARGET, return_value={}):
            result = load_all_skills(
                load_public=True,
                load_user=False,
                load_project=False,
                load_org=False,
                registered_marketplaces=[
                    MarketplaceRegistration(
                        name="collision", source=str(marketplace_dir), auto_load=True
                    )
                ],
            )

        shared = [s for s in result.skills if s.name == "shared"]
        assert len(shared) == 1
        assert "FROM_PLUGIN" in shared[0].content
        assert "FROM_STANDALONE" not in shared[0].content

    def test_load_registered_marketplace_skills_uses_registration_fetch_fields(
        self, tmp_path: Path
    ):
        """Marketplace source, ref, and repo_path drive registry fetching."""
        marketplace_dir = _create_test_marketplace(tmp_path / "marketplace")

        with patch(
            "openhands.sdk.marketplace.registry.fetch_plugin_with_resolution",
            return_value=(marketplace_dir, "abc123"),
        ) as mock_fetch:
            skills = load_registered_marketplace_skills(
                [
                    MarketplaceRegistration(
                        name="custom",
                        source="github:example/marketplaces",
                        ref="feature-branch",
                        repo_path="catalogs/public",
                        auto_load=True,
                    )
                ]
            )

        assert {skill.name for skill in skills} == {"auto-skill", "manual-skill"}
        mock_fetch.assert_called_once_with(
            source="github:example/marketplaces",
            ref="feature-branch",
            repo_path="catalogs/public",
        )

    def test_load_all_skills_returns_skill_load_result(self):
        """Test that load_all_skills returns a SkillLoadResult."""
        with patch(self._PATCH_TARGET, return_value={}):
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

        # First call returns sdk_base (public+user), second returns project
        with patch(
            self._PATCH_TARGET,
            side_effect=[
                {"public1": skill1, "user1": skill2},  # sdk_base
                {},  # project
            ],
        ):
            result = load_all_skills(
                load_public=True,
                load_user=True,
                load_project=False,
                load_org=False,
            )

            assert result.sources["sdk_base"] == 2
            assert result.sources["sandbox"] == 0
            assert result.sources["org"] == 0
            assert result.sources["project"] == 0
            assert result.sources["registered_marketplaces"] == 0

    def test_load_all_skills_passes_marketplace_path_to_sdk_base(self):
        """Test that marketplace_path is forwarded to SDK public skill loading."""
        with patch(self._PATCH_TARGET, side_effect=[{}, {}]) as mock_avail:
            load_all_skills(
                load_public=True,
                load_user=True,
                load_project=False,
                load_org=False,
                marketplace_path="marketplaces/custom.json",
            )

        sdk_base_call = mock_avail.call_args_list[0]
        assert sdk_base_call.kwargs["include_public"] is True
        assert sdk_base_call.kwargs["marketplace_path"] == "marketplaces/custom.json"

        project_call = mock_avail.call_args_list[1]
        assert project_call.kwargs["include_public"] is False

    def test_load_all_skills_disabled_sources(self):
        """Test that disabled sources are not loaded."""
        with patch(self._PATCH_TARGET, return_value={}) as mock_avail:
            result = load_all_skills(
                load_public=False,
                load_user=False,
                load_project=False,
                load_org=False,
            )

            # Called twice (sdk_base + project), both with disabled flags
            assert mock_avail.call_count == 2
            assert result.sources["sdk_base"] == 0
            assert result.sources["project"] == 0

    def test_load_all_skills_with_sandbox_urls(self):
        """Test loading skills with sandbox URLs."""
        sandbox_urls = [
            ExposedUrlData(name="WORKER_8080", url="http://localhost:8080", port=8080),
        ]

        with patch(self._PATCH_TARGET, return_value={}):
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
        user_skill = Skill(name="user1", content="content", trigger=None)

        # load_available_skills handles exceptions internally and returns
        # whatever it can. Simulate: first call returns user skill only
        # (public failed internally), second call returns empty project.
        with patch(
            self._PATCH_TARGET,
            side_effect=[
                {"user1": user_skill},  # sdk_base (public error handled inside)
                {},  # project
            ],
        ):
            result = load_all_skills(
                load_public=True,
                load_user=True,
                load_project=False,
                load_org=False,
            )

            assert result.sources["sdk_base"] == 1

    def test_load_all_skills_merge_precedence(self):
        """Test that skills are merged with correct precedence."""
        base_skill = Skill(name="shared", content="user", trigger=None)
        project_skill = Skill(name="shared", content="project", trigger=None)

        # sdk_base returns user version, project returns project version
        with patch(
            self._PATCH_TARGET,
            side_effect=[
                {"shared": base_skill},  # sdk_base
                {"shared": project_skill},  # project
            ],
        ):
            result = load_all_skills(
                load_public=True,
                load_user=True,
                load_project=True,
                load_org=False,
                project_dir="/workspace",
            )

            # Project should override user/public
            shared_skills = [s for s in result.skills if s.name == "shared"]
            assert len(shared_skills) == 1
            assert shared_skills[0].content == "project"

    def test_load_all_skills_loops_and_merges_multiple_org_repos(self):
        """Every org repo is loaded (in order) and merged into the org source."""
        with patch(self._PATCH_TARGET, return_value={}):
            with patch(
                "openhands.agent_server.skills_service.load_org_skills_from_url",
                side_effect=[
                    [Skill(name="org_a", content="a", trigger=None)],
                    [Skill(name="org_b", content="b", trigger=None)],
                ],
            ) as mock_org:
                result = load_all_skills(
                    load_public=False,
                    load_user=False,
                    load_project=False,
                    load_org=True,
                    org_repos=[
                        ("https://git/hieptl/.openhands", "hieptl"),
                        ("https://git/hieptl/.agents", "hieptl"),
                    ],
                )

        assert result.sources["org"] == 2
        assert [c.kwargs["org_repo_url"] for c in mock_org.call_args_list] == [
            "https://git/hieptl/.openhands",
            "https://git/hieptl/.agents",
        ]

    def test_load_all_skills_org_merge_precedence(self):
        """A skill in multiple org repos resolves to the later repo's version."""
        with patch(self._PATCH_TARGET, return_value={}):
            with patch(
                "openhands.agent_server.skills_service.load_org_skills_from_url",
                side_effect=[
                    [Skill(name="shared", content="openhands", trigger=None)],
                    [Skill(name="shared", content="agents", trigger=None)],
                ],
            ):
                result = load_all_skills(
                    load_public=False,
                    load_user=False,
                    load_project=False,
                    load_org=True,
                    org_repos=[
                        ("https://git/hieptl/.openhands", "hieptl"),
                        ("https://git/hieptl/.agents", "hieptl"),
                    ],
                )

        shared = [s for s in result.skills if s.name == "shared"]
        assert len(shared) == 1
        assert shared[0].content == "agents"


class TestDiscoverProfileSkills:
    """Tests for discover_profile_skills (the OpenHands profile launch catalog)."""

    _LOAD_ALL = "openhands.agent_server.skills_service.load_all_skills"

    def test_returns_merged_user_and_public_skills(self):
        skills = [Skill(name="a", content="x"), Skill(name="b", content="y")]
        with patch(self._LOAD_ALL) as mock_load:
            mock_load.return_value = SkillLoadResult(skills=skills, sources={})
            result = discover_profile_skills()

        assert result == skills
        # Only the deterministic, no-extra-context sources are discovered.
        assert mock_load.call_args.kwargs == {
            "load_public": True,
            "load_user": True,
            "load_org": False,
            "load_project": False,
        }

    def test_propagates_unexpected_failure(self):
        # load_all_skills absorbs benign per-source failures internally; an
        # unexpected failure propagates rather than silently resolving to a
        # zero-skill agent (the caller surfaces it loudly).
        with patch(self._LOAD_ALL, side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                discover_profile_skills()


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

    def test_sync_public_skills_invalidates_in_memory_cache(self):
        """Successful sync must drop the in-memory cache so the next call
        re-parses immediately instead of waiting for the TTL."""
        with (
            patch(
                "openhands.agent_server.skills_service.get_skills_cache_dir"
            ) as mock_cache,
            patch(
                "openhands.agent_server.skills_service.update_skills_repository"
            ) as mock_update,
            patch(
                "openhands.agent_server.skills_service._invalidate_public_skills_cache"
            ) as mock_invalidate,
        ):
            mock_cache.return_value = Path("/tmp/cache")
            mock_update.return_value = Path("/tmp/cache/public-skills")

            success, _ = sync_public_skills()

            assert success is True
            mock_invalidate.assert_called_once()

    def test_sync_public_skills_failure_does_not_invalidate_cache(self):
        """A failed sync must not clobber the cache so the previous skills
        stay available until the next successful refresh."""
        with (
            patch(
                "openhands.agent_server.skills_service.get_skills_cache_dir"
            ) as mock_cache,
            patch(
                "openhands.agent_server.skills_service.update_skills_repository"
            ) as mock_update,
            patch(
                "openhands.agent_server.skills_service._invalidate_public_skills_cache"
            ) as mock_invalidate,
        ):
            mock_cache.return_value = Path("/tmp/cache")
            mock_update.return_value = None

            success, _ = sync_public_skills()

            assert success is False
            mock_invalidate.assert_not_called()


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


class TestMarketplaceCatalogCache:
    """Tests for TTL caching in service_get_marketplace_catalog."""

    def setup_method(self):
        """Reset the module-level cache before each test."""
        import openhands.agent_server.skills_service as svc

        svc._catalog_cache = None

    def test_cache_miss_calls_fetch(self):
        """First call (cold cache) fetches from the repository."""
        entries = [("github", "GitHub skill", "github:org/repo")]
        with (
            patch(
                "openhands.agent_server.skills_service._fetch_catalog_entries",
                return_value=entries,
            ) as mock_fetch,
            patch(
                "openhands.agent_server.skills_service.service_list_installed_skills",
                return_value=[],
            ),
        ):
            from openhands.agent_server.skills_service import (
                service_get_marketplace_catalog,
            )

            result = service_get_marketplace_catalog()

        mock_fetch.assert_called_once()
        assert len(result) == 1
        assert result[0].name == "github"
        assert result[0].installed is False

    def test_cache_hit_skips_fetch(self):
        """Second call within TTL reuses cached entries without another fetch."""
        entries = [("github", "GitHub skill", "github:org/repo")]
        with (
            patch(
                "openhands.agent_server.skills_service._fetch_catalog_entries",
                return_value=entries,
            ) as mock_fetch,
            patch(
                "openhands.agent_server.skills_service.service_list_installed_skills",
                return_value=[],
            ),
        ):
            from openhands.agent_server.skills_service import (
                service_get_marketplace_catalog,
            )

            service_get_marketplace_catalog()
            service_get_marketplace_catalog()

        mock_fetch.assert_called_once()  # only one fetch despite two calls

    def test_installed_status_always_fresh(self):
        """installed flag is derived fresh on every call, not from the cache."""
        from unittest.mock import MagicMock

        from openhands.agent_server.skills_service import (
            InstalledSkillInfo,
            service_get_marketplace_catalog,
        )

        entries = [("github", "GitHub skill", "github:org/repo")]
        installed_skill = MagicMock(spec=InstalledSkillInfo)
        installed_skill.name = "github"

        with (
            patch(
                "openhands.agent_server.skills_service._fetch_catalog_entries",
                return_value=entries,
            ),
            patch(
                "openhands.agent_server.skills_service.service_list_installed_skills",
            ) as mock_installed,
        ):
            # First call: skill not installed
            mock_installed.return_value = []
            result1 = service_get_marketplace_catalog()
            assert result1[0].installed is False

            # Second call (cache hit): skill now installed
            mock_installed.return_value = [installed_skill]
            result2 = service_get_marketplace_catalog()
            assert result2[0].installed is True

        # service_list_installed_skills called twice (once per request)
        assert mock_installed.call_count == 2

    def test_cache_expires_after_ttl(self):
        """After TTL expires, the next call fetches from the repository again."""
        import openhands.agent_server.skills_service as svc

        entries = [("github", "GitHub skill", "github:org/repo")]
        with (
            patch(
                "openhands.agent_server.skills_service._fetch_catalog_entries",
                return_value=entries,
            ) as mock_fetch,
            patch(
                "openhands.agent_server.skills_service.service_list_installed_skills",
                return_value=[],
            ),
        ):
            from openhands.agent_server.skills_service import (
                service_get_marketplace_catalog,
            )

            service_get_marketplace_catalog()
            # Artificially expire the cache
            assert svc._catalog_cache is not None
            svc._catalog_cache = (
                svc._catalog_cache[0] - svc._CATALOG_TTL_SECONDS - 1,
                entries,
            )
            service_get_marketplace_catalog()

        assert mock_fetch.call_count == 2  # fetched again after expiry
