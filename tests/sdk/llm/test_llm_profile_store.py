import concurrent.futures
import json
import re
import threading
from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.sdk.llm import LLM
from openhands.sdk.llm.llm_profile_store import (
    LLMProfileStore,
    ProfileLimitExceeded,
)


@pytest.fixture
def profile_store(tmp_path: Path) -> LLMProfileStore:
    """Create a profile store with a temporary directory."""
    return LLMProfileStore(base_dir=tmp_path)


@pytest.fixture
def sample_llm() -> LLM:
    """Create a sample LLM instance for testing."""
    return LLM(
        usage_id="test-llm",
        model="gpt-4-turbo",
        temperature=0.7,
        max_output_tokens=2000,
    )


@pytest.fixture
def sample_llm_with_secrets() -> LLM:
    """Create a sample LLM instance with secrets for testing."""
    return LLM(
        usage_id="test-llm-secrets",
        model="gpt-4-turbo",
        temperature=0.5,
        api_key=SecretStr("secret-api-key-12345"),
    )


def test_init_creates_directory(tmp_path: Path) -> None:
    """Test that initialization creates the base directory."""
    profile_dir = tmp_path / "profiles"
    assert not profile_dir.exists()

    LLMProfileStore(base_dir=profile_dir)

    assert profile_dir.exists()
    assert profile_dir.is_dir()


def test_init_with_string_path(tmp_path: Path) -> None:
    """Test initialization with a string path."""
    profile_dir = str(tmp_path / "profiles")
    store = LLMProfileStore(base_dir=profile_dir)

    assert store.base_dir == Path(profile_dir)
    assert store.base_dir.exists()


def test_init_with_path_object(tmp_path: Path) -> None:
    """Test initialization with a Path object."""
    profile_dir = tmp_path / "profiles"
    store = LLMProfileStore(base_dir=profile_dir)

    assert store.base_dir == profile_dir
    assert store.base_dir.exists()


def test_init_with_existing_directory(tmp_path: Path) -> None:
    """Test initialization with an existing directory."""
    profile_dir = tmp_path / "profiles"
    profile_dir.mkdir()

    store = LLMProfileStore(base_dir=profile_dir)

    assert store.base_dir == profile_dir


def test_list_empty_store(profile_store: LLMProfileStore) -> None:
    """Test listing profiles in an empty store."""
    profiles = profile_store.list()
    assert profiles == []


def test_list_with_profiles(profile_store: LLMProfileStore, sample_llm: LLM) -> None:
    """Test listing profiles after saving some."""
    profile_store.save("profile1", sample_llm)
    profile_store.save("profile2", sample_llm)

    profiles = profile_store.list()

    assert len(profiles) == 2
    assert "profile1.json" in profiles
    assert "profile2.json" in profiles


def test_list_excludes_non_json_files(
    profile_store: LLMProfileStore, sample_llm: LLM
) -> None:
    """Test that list() only returns .json files."""
    profile_store.save("valid", sample_llm)

    # Create a non-json file
    (profile_store.base_dir / "not_a_profile.txt").write_text("hello")

    profiles = profile_store.list()

    assert profiles == ["valid.json"]


def test_save_creates_file(profile_store: LLMProfileStore, sample_llm: LLM) -> None:
    """Test that save creates a profile file."""
    profile_store.save("my_profile", sample_llm)

    profile_path = profile_store.base_dir / "my_profile.json"
    assert profile_path.exists()


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".json",
        ".",
        "..",
        "my/profile",
        "my//profile",
        ".leading-dot",
        "-leading-dash",
        "_leading_under",
        "name with space",
        "name@symbol",
        "name$dollar",
        "a" * 65,
    ],
)
def test_save_with_invalid_profile_name(
    name: str, profile_store: LLMProfileStore, sample_llm: LLM
) -> None:
    with pytest.raises(ValueError, match=re.escape(f"Invalid profile name: {name!r}.")):
        profile_store.save(name, sample_llm)


def test_save_writes_valid_json(
    profile_store: LLMProfileStore, sample_llm: LLM
) -> None:
    """Test that saved file contains valid JSON."""
    profile_store.save("my_profile", sample_llm)

    profile_path = profile_store.base_dir / "my_profile.json"
    content = profile_path.read_text()
    data = json.loads(content)

    assert data["model"] == "gpt-4-turbo"
    assert data["temperature"] == 0.7


def test_save_with_json_extension(
    profile_store: LLMProfileStore, sample_llm: LLM
) -> None:
    """Test saving with .json extension in name."""
    profile_store.save("my_profile.json", sample_llm)

    # Should not create my_profile.json.json
    assert (profile_store.base_dir / "my_profile.json").exists()
    assert not (profile_store.base_dir / "my_profile.json.json").exists()


def test_save_overwrites_existing(
    profile_store: LLMProfileStore, sample_llm: LLM
) -> None:
    """Test that save overwrites an existing profile."""
    profile_store.save("my_profile", sample_llm)

    # Modify and save again
    modified_llm = LLM(
        usage_id="modified",
        model="gpt-3.5-turbo-16k",
        temperature=0.3,
    )
    profile_store.save("my_profile", modified_llm)

    # Load and verify
    loaded = profile_store.load("my_profile")
    assert loaded.model == "gpt-3.5-turbo-16k"
    assert loaded.temperature == 0.3


def test_save_without_secrets(
    profile_store: LLMProfileStore, sample_llm_with_secrets: LLM
) -> None:
    """Test that secrets are not saved by default."""
    profile_store.save("with_secrets", sample_llm_with_secrets)

    profile_path = profile_store.base_dir / "with_secrets.json"
    content = profile_path.read_text()

    # Secret should be masked
    assert "secret-api-key-12345" not in content


def test_save_with_secrets(
    profile_store: LLMProfileStore, sample_llm_with_secrets: LLM
) -> None:
    """Test that secrets are saved when include_secrets=True."""
    profile_store.save("with_secrets", sample_llm_with_secrets, include_secrets=True)

    profile_path = profile_store.base_dir / "with_secrets.json"
    content = profile_path.read_text()

    # Secret should be present
    assert "secret-api-key-12345" in content


@pytest.mark.parametrize("name", ["my_profile", "my_profile.json"])
def test_load_existing_profile(
    name: str, profile_store: LLMProfileStore, sample_llm: LLM
) -> None:
    """Test loading an existing profile."""
    profile_store.save(name, sample_llm)

    loaded = profile_store.load(name)

    assert loaded.usage_id == sample_llm.usage_id
    assert loaded.model == sample_llm.model
    assert loaded.temperature == sample_llm.temperature
    assert loaded.max_output_tokens == sample_llm.max_output_tokens


def test_load_nonexistent_profile(profile_store: LLMProfileStore) -> None:
    """Test loading a profile that doesn't exist."""
    with pytest.raises(FileNotFoundError) as exc_info:
        profile_store.load("nonexistent")

    assert "nonexistent" in str(exc_info.value)
    assert "not found" in str(exc_info.value)


def test_load_nonexistent_shows_available(
    profile_store: LLMProfileStore, sample_llm: LLM
) -> None:
    """Test that error message shows available profiles."""
    profile_store.save("available1", sample_llm)
    profile_store.save("available2", sample_llm)

    with pytest.raises(FileNotFoundError) as exc_info:
        profile_store.load("nonexistent")

    error_msg = str(exc_info.value)
    assert "available1.json" in error_msg
    assert "available2.json" in error_msg


def test_load_corrupted_profile(profile_store: LLMProfileStore) -> None:
    """Test loading a corrupted profile raises ValueError."""
    # Create a corrupted profile file
    profile_path = profile_store.base_dir / "corrupted.json"
    profile_path.write_text("{ invalid json }")

    with pytest.raises(ValueError) as exc_info:
        profile_store.load("corrupted")

    assert "Failed to load profile" in str(exc_info.value)
    assert "corrupted" in str(exc_info.value)


@pytest.mark.parametrize("name", ["to_delete", "to_delete.json"])
def test_delete_existing_profile(
    name: str, profile_store: LLMProfileStore, sample_llm: LLM
) -> None:
    """Test deleting an existing profile."""
    profile_store.save(name, sample_llm)
    profile_filename = f"{name}.json" if not name.endswith(".json") else name
    assert profile_filename in profile_store.list()

    profile_store.delete(name)
    assert profile_filename not in profile_store.list()


def test_delete_nonexistent_profile(profile_store: LLMProfileStore) -> None:
    """Test that deleting a nonexistent profile doesn't raise an error."""
    profile_store.delete("nonexistent")


def test_concurrent_saves(tmp_path: Path) -> None:
    """Test that concurrent saves don't corrupt data."""
    store = LLMProfileStore(base_dir=tmp_path)
    num_threads = 10
    results: list[int] = []
    errors: list[tuple[int, Exception]] = []

    def save_profile(index: int) -> None:
        try:
            llm = LLM(
                usage_id=f"test-{index}",
                model=f"model-{index}",
                temperature=0.1 * index,
            )
            store.save(f"profile_{index}", llm)
            results.append(index)
        except Exception as e:
            errors.append((index, e))

    threads = [
        threading.Thread(target=save_profile, args=(i,)) for i in range(num_threads)
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Errors occurred: {errors}"
    assert len(results) == num_threads

    # Verify all profiles were saved correctly
    profiles = store.list()
    assert len(profiles) == num_threads


def test_concurrent_reads_and_writes(tmp_path: Path) -> None:
    """Test concurrent reads and writes don't cause issues."""
    store = LLMProfileStore(base_dir=tmp_path)

    # Pre-create some profiles
    for i in range(5):
        llm = LLM(usage_id=f"test-{i}", model=f"model-{i}")
        store.save(f"profile_{i}", llm)

    errors: list[tuple[str, str | int, Exception]] = []
    read_results: list[str] = []
    write_results: list[int] = []

    def read_profile(name: str) -> None:
        try:
            loaded = store.load(name)
            read_results.append(loaded.model)
        except Exception as e:
            errors.append(("read", name, e))

    def write_profile(index: int) -> None:
        try:
            llm = LLM(usage_id=f"new-{index}", model=f"new-model-{index}")
            store.save(f"new_profile_{index}", llm)
            write_results.append(index)
        except Exception as e:
            errors.append(("write", index, e))

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = []
        # Submit read tasks
        for i in range(5):
            futures.append(executor.submit(read_profile, f"profile_{i}"))
        # Submit write tasks
        for i in range(5):
            futures.append(executor.submit(write_profile, i))

        concurrent.futures.wait(futures)

    assert len(errors) == 0, f"Errors occurred: {errors}"
    assert len(read_results) == 5
    assert len(write_results) == 5


def test_full_workflow(profile_store: LLMProfileStore) -> None:
    """Test a complete save-list-load-delete workflow."""
    llm = LLM(
        usage_id="workflow-test",
        model="claude-3-opus",
        temperature=0.8,
        max_output_tokens=4096,
    )

    # Save
    profile_store.save("workflow_profile", llm)

    # List
    profiles = profile_store.list()
    assert "workflow_profile.json" in profiles

    # Load
    loaded = profile_store.load("workflow_profile")
    assert loaded.usage_id == llm.usage_id
    assert loaded.model == llm.model
    assert loaded.temperature == llm.temperature
    assert loaded.max_output_tokens == llm.max_output_tokens

    # Delete
    profile_store.delete("workflow_profile")
    assert "workflow_profile.json" not in profile_store.list()


# ── Rename ────────────────────────────────────────────────────────────────


def test_rename_moves_file(profile_store: LLMProfileStore, sample_llm: LLM) -> None:
    profile_store.save("old", sample_llm)
    profile_store.rename("old", "new")

    assert (profile_store.base_dir / "new.json").exists()
    assert not (profile_store.base_dir / "old.json").exists()
    assert profile_store.load("new").model == sample_llm.model


def test_rename_preserves_secrets(
    profile_store: LLMProfileStore, sample_llm_with_secrets: LLM
) -> None:
    profile_store.save("old", sample_llm_with_secrets, include_secrets=True)
    profile_store.rename("old", "new")

    loaded = profile_store.load("new")
    assert isinstance(loaded.api_key, SecretStr)
    assert loaded.api_key.get_secret_value() == "secret-api-key-12345"


def test_rename_source_missing_raises(profile_store: LLMProfileStore) -> None:
    with pytest.raises(FileNotFoundError, match="missing"):
        profile_store.rename("missing", "anywhere")


def test_rename_target_exists_raises(
    profile_store: LLMProfileStore, sample_llm: LLM
) -> None:
    profile_store.save("old", sample_llm)
    profile_store.save("taken", sample_llm)

    with pytest.raises(FileExistsError, match="taken"):
        profile_store.rename("old", "taken")

    # Both files still present (no partial state)
    assert (profile_store.base_dir / "old.json").exists()
    assert (profile_store.base_dir / "taken.json").exists()


def test_rename_same_name_is_noop(
    profile_store: LLMProfileStore, sample_llm: LLM
) -> None:
    profile_store.save("same", sample_llm)
    profile_store.rename("same", "same")
    assert profile_store.list() == ["same.json"]


def test_rename_same_name_missing_raises(profile_store: LLMProfileStore) -> None:
    """Same-name rename still verifies the profile exists."""
    with pytest.raises(FileNotFoundError, match="ghost"):
        profile_store.rename("ghost", "ghost")


def test_rename_invalid_name_raises(
    profile_store: LLMProfileStore, sample_llm: LLM
) -> None:
    profile_store.save("ok", sample_llm)
    with pytest.raises(ValueError, match="Invalid profile name"):
        profile_store.rename("ok", "../escape")
    with pytest.raises(ValueError, match="Invalid profile name"):
        profile_store.rename(".hidden", "ok2")


# ── list_summaries ────────────────────────────────────────────────────────


def test_list_summaries_empty(profile_store: LLMProfileStore) -> None:
    assert profile_store.list_summaries() == []


def test_list_summaries_returns_metadata(
    profile_store: LLMProfileStore, sample_llm: LLM
) -> None:
    profile_store.save("a", sample_llm)
    profile_store.save("b", sample_llm)

    summaries = profile_store.list_summaries()
    assert len(summaries) == 2
    by_name = {s["name"]: s for s in summaries}
    assert by_name["a"]["model"] == sample_llm.model
    assert by_name["a"]["base_url"] == sample_llm.base_url
    assert by_name["a"]["api_key_set"] is False


def test_list_summaries_api_key_set_with_secrets(
    profile_store: LLMProfileStore, sample_llm_with_secrets: LLM
) -> None:
    profile_store.save("with_key", sample_llm_with_secrets, include_secrets=True)

    [summary] = profile_store.list_summaries()
    assert summary["api_key_set"] is True


def test_list_summaries_api_key_redacted_means_not_set(
    profile_store: LLMProfileStore, sample_llm_with_secrets: LLM
) -> None:
    """A profile saved without secrets stores '**********' on disk; not 'set'."""
    profile_store.save("no_key", sample_llm_with_secrets, include_secrets=False)

    [summary] = profile_store.list_summaries()
    assert summary["api_key_set"] is False


def test_list_summaries_skips_corrupted(
    profile_store: LLMProfileStore, sample_llm: LLM
) -> None:
    profile_store.save("good", sample_llm)
    (profile_store.base_dir / "bad.json").write_text("{ not valid json")

    summaries = profile_store.list_summaries()
    assert [s["name"] for s in summaries] == ["good"]


def test_list_summaries_skips_non_dict(
    profile_store: LLMProfileStore, sample_llm: LLM
) -> None:
    """A JSON file whose top-level value isn't an object is skipped, not raised."""
    profile_store.save("good", sample_llm)
    (profile_store.base_dir / "list.json").write_text("[1, 2, 3]")
    (profile_store.base_dir / "string.json").write_text('"plain"')

    summaries = profile_store.list_summaries()
    assert [s["name"] for s in summaries] == ["good"]


def test_list_summaries_skips_invalid_filename(
    profile_store: LLMProfileStore, sample_llm: LLM
) -> None:
    """Files with names not matching PROFILE_NAME_REGEX are skipped."""
    profile_store.save("good", sample_llm)
    (profile_store.base_dir / ".hidden.json").write_text('{"model": "x"}')
    (profile_store.base_dir / "bad@name.json").write_text('{"model": "x"}')

    summaries = profile_store.list_summaries()
    assert [s["name"] for s in summaries] == ["good"]


# ── Save with max_profiles ─────────────────────────────────────────────────


def test_save_with_max_profiles_blocks_over_limit(
    profile_store: LLMProfileStore, sample_llm: LLM
) -> None:
    profile_store.save("a", sample_llm)
    profile_store.save("b", sample_llm)

    with pytest.raises(ProfileLimitExceeded, match="2"):
        profile_store.save("c", sample_llm, max_profiles=2)


def test_save_with_max_profiles_allows_overwrite(
    profile_store: LLMProfileStore, sample_llm: LLM
) -> None:
    """Overwriting an existing profile is allowed even when at the limit."""
    profile_store.save("a", sample_llm)
    profile_store.save("b", sample_llm)

    profile_store.save("a", sample_llm, max_profiles=2)
    assert len(profile_store.list()) == 2


def test_save_with_max_profiles_allows_under_limit(
    profile_store: LLMProfileStore, sample_llm: LLM
) -> None:
    profile_store.save("a", sample_llm, max_profiles=5)
    profile_store.save("b", sample_llm, max_profiles=5)
    assert len(profile_store.list()) == 2


def test_save_cleans_up_tmp_on_replace_failure(
    profile_store: LLMProfileStore,
    sample_llm: LLM,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Path.replace fails, no .tmp file should be left behind."""

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", boom)

    with pytest.raises(OSError, match="disk full"):
        profile_store.save("doomed", sample_llm)

    leftovers = list(profile_store.base_dir.glob("*.tmp"))
    assert leftovers == []


def test_save_with_max_profiles_ignores_invalid_filenames(
    profile_store: LLMProfileStore, sample_llm: LLM
) -> None:
    """Stray .json files with invalid names must not consume limit slots."""
    profile_store.save("real", sample_llm)
    (profile_store.base_dir / ".hidden.json").write_text('{"model": "x"}')
    (profile_store.base_dir / "bad@name.json").write_text('{"model": "x"}')

    # Only 'real' counts, so saving up to the limit of 2 should succeed.
    profile_store.save("another", sample_llm, max_profiles=2)
    assert "another.json" in profile_store.list()


def test_list_summaries_does_not_mutate_env(
    profile_store: LLMProfileStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Listing summaries must not run LLM validators (which set env vars)."""
    llm = LLM(
        usage_id="t",
        model="bedrock/test",
        aws_access_key_id="from-profile",
    )
    profile_store.save("aws", llm, include_secrets=True)

    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    profile_store.list_summaries()

    import os

    assert os.environ.get("AWS_ACCESS_KEY_ID") is None


# ── Misc ──────────────────────────────────────────────────────────────────


def test_multiple_profiles(profile_store: LLMProfileStore) -> None:
    """Test managing multiple profiles."""
    profiles_data = [
        ("gpt4", "gpt-4-turbo", 0.7),
        ("gpt35", "gpt-3.5-turbo-16k", 0.5),
        ("claude", "claude-3-opus", 0.9),
    ]

    # Save all
    for name, model, temp in profiles_data:
        llm = LLM(usage_id=name, model=model, temperature=temp)
        profile_store.save(name, llm)

    # Verify all exist
    stored = profile_store.list()
    assert len(stored) == 3

    # Load and verify each
    for name, expected_model, expected_temp in profiles_data:
        loaded = profile_store.load(name)
        assert loaded.model == expected_model
        assert loaded.temperature == expected_temp

    # Delete one
    profile_store.delete("gpt4")
    assert len(profile_store.list()) == 2
    assert "gpt4.json" not in profile_store.list()
