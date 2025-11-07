import json

import pytest
from pydantic import SecretStr

from openhands.sdk.llm.llm import LLM
from openhands.sdk.llm.llm_registry import LLMRegistry
from openhands.sdk.persistence.settings import INLINE_CONTEXT_KEY


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


def test_register_profiles_skips_invalid_and_duplicate_profiles(tmp_path):
    registry = LLMRegistry(profile_dir=tmp_path)

    llm = LLM(model="gpt-4o-mini", usage_id="shared")
    registry.save_profile("alpha", llm)

    duplicate_data = llm.model_dump(exclude_none=True)
    duplicate_data["profile_id"] = "beta"
    (tmp_path / "beta.json").write_text(
        json.dumps(duplicate_data),
        encoding="utf-8",
    )

    (tmp_path / "gamma.json").write_text("{", encoding="utf-8")

    registry.register_profiles()

    assert registry.list_usage_ids() == ["shared"]


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


def test_switch_profile_replaces_active_llm(tmp_path):
    registry = LLMRegistry(profile_dir=tmp_path)
    base_llm = LLM(model="gpt-4o-mini", usage_id="primary")
    registry.add(base_llm)
    registry.save_profile("alternate", LLM(model="gpt-4o", usage_id="alternate"))

    events: list = []
    registry.subscribe(events.append)

    switched = registry.switch_profile("primary", "alternate")

    assert switched.profile_id == "alternate"
    assert switched.usage_id == "primary"
    assert registry.get("primary") is switched
    assert switched.model == "gpt-4o"
    assert len(events) == 1
    assert events[0].llm is switched

    # switching to the same profile should be a no-op
    again = registry.switch_profile("primary", "alternate")
    assert again is switched
    assert len(events) == 1


def test_switch_profile_unknown_usage(tmp_path):
    registry = LLMRegistry(profile_dir=tmp_path)
    with pytest.raises(KeyError):
        registry.switch_profile("missing", "profile")


def test_switch_profile_missing_profile(tmp_path):
    registry = LLMRegistry(profile_dir=tmp_path)
    registry.add(LLM(model="gpt-4o-mini", usage_id="primary"))

    with pytest.raises(FileNotFoundError):
        registry.switch_profile("primary", "does-not-exist")
