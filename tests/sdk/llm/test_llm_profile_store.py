import concurrent.futures
import json
import threading
from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.sdk.llm import LLM
from openhands.sdk.llm.llm_profile_store import LLMProfileStore


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
    ["", ".json", ".", "..", "my/profile", "my//profile"],
)
def test_save_with_invalid_profile_name(
    name: str, profile_store: LLMProfileStore, sample_llm: LLM
) -> None:
    with pytest.raises(ValueError, match=f"Invalid profile name: {name!r}. "):
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
