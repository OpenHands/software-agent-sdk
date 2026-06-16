import json
from pathlib import Path

import pytest

from openhands.sdk.llm.meta_profile_store import (
    MetaProfile,
    MetaProfileClass,
    MetaProfileLimitExceeded,
    MetaProfileStore,
)


def _write(base: Path, name: str, data: dict) -> None:
    (base / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")


VALID = {
    "classifier_model": "minimax",
    "default_model": "gpt",
    "classes": [
        {"description": "UI / images", "model": "deepseek"},
        {"description": "research", "model": "gemini"},
    ],
}


def test_load_valid_meta_profile(tmp_path: Path) -> None:
    _write(tmp_path, "balanced", VALID)
    store = MetaProfileStore(base_dir=tmp_path)

    meta = store.load("balanced")

    assert isinstance(meta, MetaProfile)
    assert meta.classifier_model == "minimax"
    assert meta.default_model == "gpt"
    assert [c.model for c in meta.classes] == ["deepseek", "gemini"]


def test_load_accepts_name_with_json_suffix(tmp_path: Path) -> None:
    _write(tmp_path, "balanced", VALID)
    store = MetaProfileStore(base_dir=tmp_path)

    assert store.load("balanced.json").classifier_model == "minimax"


def test_list_returns_sorted_valid_names(tmp_path: Path) -> None:
    _write(tmp_path, "b", VALID)
    _write(tmp_path, "a", VALID)
    # A file with an invalid stem must be ignored by list().
    (tmp_path / ".hidden.json").write_text("{}", encoding="utf-8")
    store = MetaProfileStore(base_dir=tmp_path)

    assert store.list() == ["a", "b"]


def test_load_missing_raises_file_not_found(tmp_path: Path) -> None:
    store = MetaProfileStore(base_dir=tmp_path)

    with pytest.raises(FileNotFoundError):
        store.load("nope")


def test_load_invalid_name_raises_value_error(tmp_path: Path) -> None:
    store = MetaProfileStore(base_dir=tmp_path)

    with pytest.raises(ValueError):
        store.load("../escape")


def test_load_corrupted_json_raises_value_error(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    store = MetaProfileStore(base_dir=tmp_path)

    with pytest.raises(ValueError):
        store.load("broken")


def test_load_schema_violation_raises_value_error(tmp_path: Path) -> None:
    _write(tmp_path, "bad", {"default_model": "gpt"})  # missing classifier_model
    store = MetaProfileStore(base_dir=tmp_path)

    with pytest.raises(ValueError):
        store.load("bad")


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    store = MetaProfileStore(base_dir=tmp_path)
    meta = MetaProfile(
        classifier_model="minimax",
        default_model="gpt",
        classes=[MetaProfileClass(description="UI / images", model="deepseek")],
    )

    store.save("balanced", meta)

    loaded = store.load("balanced")
    assert loaded.classifier_model == "minimax"
    assert loaded.default_model == "gpt"
    assert [c.model for c in loaded.classes] == ["deepseek"]


def test_save_overwrites_existing(tmp_path: Path) -> None:
    store = MetaProfileStore(base_dir=tmp_path)
    store.save("p", MetaProfile(classifier_model="a", default_model="b"))
    store.save("p", MetaProfile(classifier_model="c", default_model="d"))

    assert store.load("p").classifier_model == "c"
    assert store.list() == ["p"]


def test_save_invalid_name_raises_value_error(tmp_path: Path) -> None:
    store = MetaProfileStore(base_dir=tmp_path)

    with pytest.raises(ValueError):
        store.save("../escape", MetaProfile(classifier_model="a", default_model="b"))


def test_save_respects_max_profiles(tmp_path: Path) -> None:
    store = MetaProfileStore(base_dir=tmp_path)
    store.save("a", MetaProfile(classifier_model="a", default_model="b"))

    with pytest.raises(MetaProfileLimitExceeded):
        store.save(
            "b", MetaProfile(classifier_model="a", default_model="b"), max_profiles=1
        )

    # Overwriting an existing one is still allowed at the limit.
    store.save(
        "a", MetaProfile(classifier_model="x", default_model="y"), max_profiles=1
    )
    assert store.load("a").classifier_model == "x"


def test_delete_is_idempotent(tmp_path: Path) -> None:
    store = MetaProfileStore(base_dir=tmp_path)
    store.save("p", MetaProfile(classifier_model="a", default_model="b"))

    store.delete("p")
    assert store.list() == []
    # Deleting a missing meta-profile is a no-op.
    store.delete("p")


def test_list_summaries_skips_corrupted(tmp_path: Path) -> None:
    _write(tmp_path, "good", VALID)
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    store = MetaProfileStore(base_dir=tmp_path)

    summaries = store.list_summaries()

    assert summaries == [
        {
            "name": "good",
            "classifier_model": "minimax",
            "default_model": "gpt",
            "num_classes": 2,
        }
    ]
