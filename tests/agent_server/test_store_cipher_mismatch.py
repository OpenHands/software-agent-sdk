"""Settings and secrets writes must not destroy values stored under another key.

Regression tests for #5622: after a cipher key change, a read-modify-write used
to load undecryptable values as ``None`` and save them back as ``null``.
"""

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.persistence import (
    FileSecretsStore,
    FileSettingsStore,
    PersistedSettings,
    reencrypt_stale_stores,
    reset_stores,
)
from openhands.agent_server.persistence.models import SettingsUpdatePayload
from openhands.sdk.llm import LLM
from openhands.sdk.settings import OpenHandsAgentSettings
from openhands.sdk.utils.cipher import Cipher


KEY_A = "qa-cipher-key-a"
KEY_B = "qa-cipher-key-b"
LLM_KEY = "sk-test-original"
SECRET_VALUE = "token-original"


@pytest.fixture
def persistence_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        reset_stores()
        old_val = os.environ.get("OH_PERSISTENCE_DIR")
        os.environ["OH_PERSISTENCE_DIR"] = tmpdir
        yield Path(tmpdir)
        reset_stores()
        if old_val is not None:
            os.environ["OH_PERSISTENCE_DIR"] = old_val
        else:
            os.environ.pop("OH_PERSISTENCE_DIR", None)


def _apply(
    settings: PersistedSettings, payload: SettingsUpdatePayload
) -> PersistedSettings:
    settings.update(payload)
    return settings


def _seed(persistence_dir: Path) -> tuple[bytes, bytes]:
    """Store an LLM key and a custom secret under KEY_A; return the raw files."""
    cipher = Cipher(KEY_A)
    settings_store = FileSettingsStore(persistence_dir, cipher=cipher)
    settings_store.update(
        lambda s: _apply(s, {"agent_settings_diff": {"llm": {"api_key": LLM_KEY}}})
    )
    secrets_store = FileSecretsStore(persistence_dir, cipher=cipher)
    secrets_store.set_secret("QA_TOKEN", SECRET_VALUE)
    return (
        (persistence_dir / "settings.json").read_bytes(),
        (persistence_dir / "secrets.json").read_bytes(),
    )


def _assert_original_key_reads_back(persistence_dir: Path) -> None:
    cipher = Cipher(KEY_A)
    settings = FileSettingsStore(persistence_dir, cipher=cipher).load()
    assert settings is not None
    assert isinstance(settings.agent_settings, OpenHandsAgentSettings)
    llm = settings.agent_settings.llm
    assert isinstance(llm, LLM)
    assert llm.api_key is not None
    assert isinstance(llm.api_key, SecretStr)
    assert llm.api_key.get_secret_value() == LLM_KEY
    secrets_store = FileSecretsStore(persistence_dir, cipher=cipher)
    assert secrets_store.get_secret("QA_TOKEN") == SECRET_VALUE


def test_settings_update_refuses_under_wrong_cipher(persistence_dir):
    settings_before, _ = _seed(persistence_dir)
    store = FileSettingsStore(persistence_dir, cipher=Cipher(KEY_B))

    with pytest.raises(RuntimeError, match="different key"):
        store.update(lambda s: _apply(s, {"misc_settings_diff": {"qa_note": True}}))

    assert (persistence_dir / "settings.json").read_bytes() == settings_before
    _assert_original_key_reads_back(persistence_dir)


@pytest.mark.parametrize(
    "write",
    [
        lambda store: store.set_secret("QA_OTHER", "x"),
        lambda store: store.delete_secret("QA_TOKEN"),
        lambda store: store.load_versioned_secret("QA_TOKEN"),
    ],
    ids=["set_secret", "delete_secret", "load_versioned_secret"],
)
def test_secrets_writes_refuse_under_wrong_cipher(persistence_dir, write):
    _, secrets_before = _seed(persistence_dir)
    store = FileSecretsStore(persistence_dir, cipher=Cipher(KEY_B))

    with pytest.raises(RuntimeError, match="different key"):
        write(store)

    assert (persistence_dir / "secrets.json").read_bytes() == secrets_before
    _assert_original_key_reads_back(persistence_dir)


def test_reads_stay_lenient_under_wrong_cipher(persistence_dir):
    _seed(persistence_dir)
    cipher = Cipher(KEY_B)

    settings = FileSettingsStore(persistence_dir, cipher=cipher).load()
    assert settings is not None
    assert isinstance(settings.agent_settings, OpenHandsAgentSettings)
    assert isinstance(settings.agent_settings.llm, LLM)
    assert settings.agent_settings.llm.api_key is None
    secrets_store = FileSecretsStore(persistence_dir, cipher=cipher)
    assert secrets_store.load() is not None
    assert secrets_store.get_secret("QA_TOKEN") is None


def test_api_writes_refuse_under_wrong_cipher(persistence_dir):
    settings_before, secrets_before = _seed(persistence_dir)
    config = Config(
        static_files_path=None,
        session_api_keys=[],
        secret_key=SecretStr(KEY_B),
    )
    client = TestClient(create_app(config))

    patch = client.patch(
        "/api/settings", json={"misc_settings_diff": {"qa_note": True}}
    )
    assert patch.status_code == 409
    put = client.put("/api/settings/secrets", json={"name": "QA_OTHER", "value": "x"})
    assert put.status_code == 500
    delete = client.delete("/api/settings/secrets/QA_TOKEN")
    assert delete.status_code == 500
    for response in (patch, put, delete):
        assert "gAAAAA" not in response.text

    # Reads under the wrong key still answer.
    assert client.get("/api/settings").status_code == 200
    assert client.get("/api/settings/secrets").status_code == 200

    assert (persistence_dir / "settings.json").read_bytes() == settings_before
    assert (persistence_dir / "secrets.json").read_bytes() == secrets_before
    _assert_original_key_reads_back(persistence_dir)


# ── Key rotation: the old key listed in previous_secret_keys ─────────────

KEY_C = "qa-cipher-key-c"


def _rotated() -> Cipher:
    return Cipher(KEY_B, previous_keys=[KEY_A])


def _assert_readable_with(persistence_dir: Path, cipher: Cipher) -> None:
    """Every stored value loads strictly (no ``None``) under ``cipher``."""
    settings = FileSettingsStore(persistence_dir, cipher=cipher).load(strict=True)
    assert settings is not None
    assert isinstance(settings.agent_settings, OpenHandsAgentSettings)
    llm = settings.agent_settings.llm
    assert isinstance(llm, LLM)
    assert isinstance(llm.api_key, SecretStr)
    assert llm.api_key.get_secret_value() == LLM_KEY
    secrets = FileSecretsStore(persistence_dir, cipher=cipher).load(strict=True)
    assert secrets is not None
    secret = secrets.custom_secrets["QA_TOKEN"].secret
    assert secret is not None
    assert secret.get_secret_value() == SECRET_VALUE


def test_rotation_keeps_values_readable_and_reencrypts_on_save(persistence_dir):
    _seed(persistence_dir)
    cipher = _rotated()
    settings_store = FileSettingsStore(persistence_dir, cipher=cipher)
    secrets_store = FileSecretsStore(persistence_dir, cipher=cipher)

    # Reads under the new key see the values saved under the old one.
    _assert_readable_with(persistence_dir, cipher)
    assert secrets_store.get_secret("QA_TOKEN") == SECRET_VALUE

    # Writes go through and re-encrypt everything under the new key.
    settings_store.update(
        lambda s: _apply(s, {"misc_settings_diff": {"qa_note": True}})
    )
    secrets_store.set_secret("QA_OTHER", "x")

    # The previous key is no longer needed ...
    _assert_readable_with(persistence_dir, Cipher(KEY_B))
    assert (
        FileSecretsStore(persistence_dir, cipher=Cipher(KEY_B)).get_secret("QA_OTHER")
        == "x"
    )
    # ... and the old key alone can no longer read anything.
    old = FileSettingsStore(persistence_dir, cipher=Cipher(KEY_A)).load()
    assert old is not None
    assert isinstance(old.agent_settings, OpenHandsAgentSettings)
    assert isinstance(old.agent_settings.llm, LLM)
    assert old.agent_settings.llm.api_key is None


def test_reencrypt_rewrites_only_stale_files(persistence_dir):
    settings_before, secrets_before = _seed(persistence_dir)

    # A wrong previous key: nothing can be decrypted, nothing is rewritten.
    wrong = Cipher(KEY_B, previous_keys=[KEY_C])
    assert FileSettingsStore(persistence_dir, cipher=wrong).reencrypt() is False
    assert FileSecretsStore(persistence_dir, cipher=wrong).reencrypt() is False
    assert (persistence_dir / "settings.json").read_bytes() == settings_before
    assert (persistence_dir / "secrets.json").read_bytes() == secrets_before

    cipher = _rotated()
    assert FileSettingsStore(persistence_dir, cipher=cipher).reencrypt() is True
    assert FileSecretsStore(persistence_dir, cipher=cipher).reencrypt() is True
    _assert_readable_with(persistence_dir, Cipher(KEY_B))

    # Everything is current now: a second pass leaves the files alone.
    settings_after = (persistence_dir / "settings.json").read_bytes()
    secrets_after = (persistence_dir / "secrets.json").read_bytes()
    assert FileSettingsStore(persistence_dir, cipher=cipher).reencrypt() is False
    assert FileSecretsStore(persistence_dir, cipher=cipher).reencrypt() is False
    assert (persistence_dir / "settings.json").read_bytes() == settings_after
    assert (persistence_dir / "secrets.json").read_bytes() == secrets_after


def test_reencrypt_stale_stores_at_startup(persistence_dir):
    _seed(persistence_dir)
    config = Config(
        static_files_path=None,
        session_api_keys=[],
        secret_key=SecretStr(KEY_B),
        previous_secret_keys=[SecretStr(KEY_A)],
    )
    reencrypt_stale_stores(config)
    _assert_readable_with(persistence_dir, Cipher(KEY_B))


def test_api_writes_succeed_after_rotation_with_previous_key(persistence_dir):
    _seed(persistence_dir)
    config = Config(
        static_files_path=None,
        session_api_keys=[],
        secret_key=SecretStr(KEY_B),
        previous_secret_keys=[SecretStr(KEY_A)],
    )
    client = TestClient(create_app(config))

    assert client.get("/api/settings").json()["llm_api_key_is_set"] is True
    assert client.get("/api/settings/secrets/QA_TOKEN").text == SECRET_VALUE
    patch = client.patch(
        "/api/settings", json={"misc_settings_diff": {"qa_note": True}}
    )
    assert patch.status_code == 200
    put = client.put("/api/settings/secrets", json={"name": "QA_OTHER", "value": "x"})
    assert put.status_code == 200
    delete = client.delete("/api/settings/secrets/QA_OTHER")
    assert delete.status_code == 200

    # The writes re-encrypted both files: the new key alone now reads them.
    reset_stores()
    client = TestClient(
        create_app(
            Config(
                static_files_path=None,
                session_api_keys=[],
                secret_key=SecretStr(KEY_B),
            )
        )
    )
    assert client.get("/api/settings").json()["llm_api_key_is_set"] is True
    assert client.get("/api/settings/secrets/QA_TOKEN").text == SECRET_VALUE
    assert (
        client.patch(
            "/api/settings", json={"misc_settings_diff": {"qa_note": False}}
        ).status_code
        == 200
    )


def test_api_refusal_names_previous_keys(persistence_dir):
    _seed(persistence_dir)
    config = Config(
        static_files_path=None,
        session_api_keys=[],
        secret_key=SecretStr(KEY_B),
        previous_secret_keys=[SecretStr(KEY_C)],
    )
    client = TestClient(create_app(config))
    patch = client.patch(
        "/api/settings", json={"misc_settings_diff": {"qa_note": True}}
    )
    assert patch.status_code == 409
    assert "OH_PREVIOUS_SECRET_KEYS" in patch.json()["detail"]
    put = client.put("/api/settings/secrets", json={"name": "QA_OTHER", "value": "x"})
    # The server masks 500 details; the guidance is in the server log.
    assert put.status_code == 500
