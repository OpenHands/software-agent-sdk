import json

import pytest
from pydantic import SecretStr, ValidationError

from openhands.sdk.llm.llm import LLM
from openhands.sdk.llm.llm_registry import LLMRegistry
from openhands.sdk.persistence import INLINE_CONTEXT_KEY


def test_list_profiles_returns_sorted_names(tmp_path):
    registry = LLMRegistry(profile_dir=tmp_path)
    (tmp_path / "b.json").write_text("{}", encoding="utf-8")
    (tmp_path / "a.json").write_text("{}", encoding="utf-8")

    assert registry.list_profiles() == ["a", "b"]


def test_save_profile_excludes_secret_fields(tmp_path):
    registry = LLMRegistry(profile_dir=tmp_path)
    llm = LLM(
        model="gpt-4o-mini",
        usage_id="service",
        api_key=SecretStr("secret"),
        aws_access_key_id=SecretStr("id"),
        aws_secret_access_key=SecretStr("value"),
    )

    path = registry.save_profile("sample", llm)
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["profile_id"] == "sample"
    assert data["usage_id"] == "service"
    assert "api_key" not in data
    assert "aws_access_key_id" not in data
    assert "aws_secret_access_key" not in data


def test_save_profile_can_include_secret_fields(tmp_path):
    registry = LLMRegistry(profile_dir=tmp_path)
    llm = LLM(
        model="gpt-4o-mini",
        usage_id="service",
        api_key=SecretStr("secret"),
        aws_access_key_id=SecretStr("id"),
        aws_secret_access_key=SecretStr("value"),
    )

    path = registry.save_profile("sample", llm, include_secrets=True)
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["api_key"] == "secret"
    assert data["aws_access_key_id"] == "id"
    assert data["aws_secret_access_key"] == "value"


def test_load_profile_assigns_profile_id_when_missing(tmp_path):
    registry = LLMRegistry(profile_dir=tmp_path)
    profile_path = tmp_path / "foo.json"
    profile_path.write_text(
        json.dumps({"model": "gpt-4o-mini", "usage_id": "svc"}),
        encoding="utf-8",
    )

    llm = registry.load_profile("foo")

    assert llm.profile_id == "foo"
    assert llm.usage_id == "svc"


def test_load_profile_rejects_unknown_fields(tmp_path):
    registry = LLMRegistry(profile_dir=tmp_path)
    profile_path = tmp_path / "legacy.json"
    profile_path.write_text(
        json.dumps(
            {
                "model": "gpt-4o-mini",
                "usage_id": "svc",
                "metadata": {"profile_description": "Legacy profile payload"},
                "unknown_field": 123,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        registry.load_profile("legacy")


def test_llm_serializer_respects_inline_context():
    llm = LLM(model="gpt-4o-mini", usage_id="service", profile_id="sample")

    inline_payload = llm.model_dump(mode="json")
    assert inline_payload["model"] == "gpt-4o-mini"

    referenced = llm.model_dump(mode="json", context={INLINE_CONTEXT_KEY: False})
    assert referenced == {"profile_id": "sample"}


def test_llm_validator_loads_profile_reference(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENHANDS_INLINE_CONVERSATIONS", "false")
    registry = LLMRegistry(profile_dir=tmp_path)
    source_llm = LLM(model="gpt-4o-mini", usage_id="service")
    registry.save_profile("profile-tests", source_llm)

    parsed = LLM.model_validate(
        {"profile_id": "profile-tests"},
        context={INLINE_CONTEXT_KEY: False, "llm_registry": registry},
    )

    assert parsed.model == source_llm.model
    assert parsed.profile_id == "profile-tests"
    assert parsed.usage_id == source_llm.usage_id


def test_validate_profile_reports_errors(tmp_path):
    registry = LLMRegistry(profile_dir=tmp_path)

    ok, errors = registry.validate_profile({"model": "gpt-4o-mini", "usage_id": "svc"})
    assert ok
    assert errors == []

    ok, errors = registry.validate_profile({"usage_id": "svc"})
    assert not ok
    assert any("model" in message for message in errors)


def test_get_profile_path_rejects_traversal(tmp_path):
    registry = LLMRegistry(profile_dir=tmp_path)
    with pytest.raises(ValueError):
        registry.get_profile_path("../secret")


def test_load_profile_syncs_mismatched_profile_id(tmp_path):
    """Test that load_profile syncs profile_id when file name differs from stored id."""
    registry = LLMRegistry(profile_dir=tmp_path)
    profile_path = tmp_path / "correct-name.json"
    profile_path.write_text(
        json.dumps(
            {
                "model": "gpt-4o-mini",
                "usage_id": "svc",
                "profile_id": "wrong-name",  # Mismatched with filename
            }
        ),
        encoding="utf-8",
    )

    llm = registry.load_profile("correct-name")

    # Should use filename as authoritative profile_id
    assert llm.profile_id == "correct-name"
    assert llm.usage_id == "svc"


def test_profile_id_validation_rejects_invalid_characters(tmp_path):
    """Test that profile IDs with invalid characters are rejected."""
    registry = LLMRegistry(profile_dir=tmp_path)
    llm = LLM(model="gpt-4o-mini", usage_id="svc")

    # Test various invalid profile IDs
    invalid_ids = [
        "",  # Empty string
        ".",  # Single dot
        "..",  # Double dot
        "profile/with/slashes",  # Path separators
        "profile\\with\\backslashes",  # Windows path separators
        "profile with spaces",  # Spaces (valid per pattern but let's test)
        "profile@special!",  # Special characters
    ]

    for invalid_id in invalid_ids:
        with pytest.raises(ValueError):
            registry.save_profile(invalid_id, llm)


def test_profile_id_validation_accepts_valid_characters(tmp_path):
    """Test that profile IDs with valid characters are accepted."""
    registry = LLMRegistry(profile_dir=tmp_path)
    llm = LLM(model="gpt-4o-mini", usage_id="svc")

    # Test various valid profile IDs
    valid_ids = [
        "simple",
        "with-dashes",
        "with_underscores",
        "with.dots",
        "Mixed123Case",
        "all-valid_chars.123",
    ]

    for valid_id in valid_ids:
        path = registry.save_profile(valid_id, llm)
        assert path.exists()
        assert path.stem == valid_id


def test_llm_model_copy_updates_profile_id():
    """Test that LLM.model_copy can update profile_id."""
    original = LLM(model="gpt-4o-mini", usage_id="svc", profile_id="original")

    updated = original.model_copy(update={"profile_id": "updated"})

    assert original.profile_id == "original"
    assert updated.profile_id == "updated"
    assert updated.model == original.model
    assert updated.usage_id == original.usage_id


def test_load_profile_without_registry_context_requires_inline_mode(tmp_path):
    """Profile stubs need a registry when inline is disabled."""

    registry = LLMRegistry(profile_dir=tmp_path)
    llm = LLM(model="gpt-4o-mini", usage_id="svc")
    registry.save_profile("test-profile", llm)

    # Without registry in context and with inline=False, should fail
    with pytest.raises(ValueError, match="LLM registry required"):
        LLM.model_validate(
            {"profile_id": "test-profile"}, context={INLINE_CONTEXT_KEY: False}
        )


def test_profile_directory_created_on_save_profile(tmp_path):
    """Profile directory is created when saving profiles (not on init)."""

    profile_dir = tmp_path / "new" / "nested" / "dir"
    assert not profile_dir.exists()

    registry = LLMRegistry(profile_dir=profile_dir)
    assert registry.profile_dir == profile_dir
    assert registry.list_profiles() == []
    assert not profile_dir.exists()

    llm = LLM(model="gpt-4o-mini", usage_id="svc")
    registry.save_profile("dir-create-test", llm)

    assert profile_dir.exists()
    assert profile_dir.is_dir()


def test_profile_id_preserved_through_serialization_roundtrip():
    """Test that profile_id is preserved through save/load cycle."""
    llm = LLM(model="gpt-4o-mini", usage_id="svc", profile_id="test-profile")

    # Serialize with inline mode
    inline_data = llm.model_dump(mode="json", context={INLINE_CONTEXT_KEY: True})
    assert inline_data["profile_id"] == "test-profile"
    assert inline_data["model"] == "gpt-4o-mini"

    # Deserialize
    restored = LLM.model_validate(inline_data)
    assert restored.profile_id == "test-profile"
    assert restored.model == "gpt-4o-mini"


def test_registry_list_usage_ids_after_multiple_adds(tmp_path):
    """Test that list_usage_ids correctly tracks multiple LLM instances."""
    registry = LLMRegistry(profile_dir=tmp_path)

    llm1 = LLM(model="gpt-4o-mini", usage_id="service-1")
    llm2 = LLM(model="gpt-4o", usage_id="service-2")
    llm3 = LLM(model="claude-3-sonnet", usage_id="service-3")

    registry.add(llm1)
    registry.add(llm2)
    registry.add(llm3)

    usage_ids = registry.list_usage_ids()
    assert len(usage_ids) == 3
    assert "service-1" in usage_ids
    assert "service-2" in usage_ids
    assert "service-3" in usage_ids


def test_save_profile_overwrites_existing_file(tmp_path):
    """Test that saving a profile overwrites existing file with same name."""
    registry = LLMRegistry(profile_dir=tmp_path)

    # Save initial profile
    llm1 = LLM(model="gpt-4o-mini", usage_id="original")
    registry.save_profile("test", llm1)

    # Save updated profile with same name
    llm2 = LLM(model="gpt-4o", usage_id="updated")
    registry.save_profile("test", llm2)

    # Load and verify it's the updated version
    loaded = registry.load_profile("test")
    assert loaded.model == "gpt-4o"
    assert loaded.usage_id == "updated"


def test_load_profile_not_found_raises_file_not_found_error(tmp_path):
    """Test that loading non-existent profile raises FileNotFoundError."""
    registry = LLMRegistry(profile_dir=tmp_path)

    with pytest.raises(FileNotFoundError, match="Profile not found"):
        registry.load_profile("nonexistent")


def test_registry_subscriber_notification_on_add(tmp_path):
    """Test that registry notifies subscriber when LLM is added."""
    registry = LLMRegistry(profile_dir=tmp_path)
    notifications = []

    def subscriber(event):
        notifications.append(event)

    registry.subscribe(subscriber)

    llm = LLM(model="gpt-4o-mini", usage_id="test")
    registry.add(llm)

    assert len(notifications) == 1
    assert notifications[0].llm.model == "gpt-4o-mini"
    assert notifications[0].llm.usage_id == "test"


def test_profile_serialization_mode_reference_only(tmp_path):
    """Test that non-inline mode returns only profile_id reference."""
    llm = LLM(model="gpt-4o-mini", usage_id="svc", profile_id="ref-test")

    ref_data = llm.model_dump(mode="json", context={INLINE_CONTEXT_KEY: False})

    # Should only contain profile_id
    assert ref_data == {"profile_id": "ref-test"}
    assert "model" not in ref_data
    assert "usage_id" not in ref_data
