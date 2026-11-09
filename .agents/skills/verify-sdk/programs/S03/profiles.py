"""Save, load and manage named LLM configurations, as a program does with
`LLMProfileStore`, `ProviderConnectionStore` and `MetaProfileStore`.

Usage: profiles.py MODE
  crud         save/load/list/summaries/rename/delete and the errors
  secrets      masking, include_secrets, cipher at rest
  concurrency  threads saving and reading, max_profiles
  migration    a legacy OpenHands-proxy profile, a newer schema version
  connections  a profile that names a provider connection
  meta         MetaProfileStore save/list/load/delete, validation, limits
  default-dir  the default store lives under OH_PERSISTENCE_DIR
"""

import json
import sys
import threading
import time

from pydantic import SecretStr
from sdk_verify import emit, run_paths

from openhands.sdk import LLM
from openhands.sdk.llm.llm_profile_store import LLMProfileStore


mode = sys.argv[1]
base = run_paths()["run"] / f"profiles-{mode}"
out: dict = {"ok": True, "mode": mode}


def attempt(fn):
    try:
        return fn(), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"[:300]


def llm(model: str = "openai/gpt-4o-mini", **kw) -> LLM:
    return LLM(model=model, api_key=SecretStr("sk-profile-secret"), usage_id="p", **kw)


if mode == "crud":
    store = LLMProfileStore(base)
    store.save("fast", llm(temperature=0.1))
    store.save("creative", llm("anthropic/claude-sonnet-4-5-20250929", temperature=0.9))
    on_disk = json.loads((base / "fast.json").read_text())
    loaded = store.load("fast")
    out |= {
        "files": sorted(p.name for p in base.glob("*.json")),
        "tmp_leftovers": len(list(base.glob("*.tmp"))),
        "schema_version": on_disk.get("schema_version"),
        "list": store.list(),
        "summaries": store.list_summaries(),
        "loaded_model": loaded.model,
        "loaded_temperature": loaded.temperature,
    }
    store.rename("creative", "bold")
    out["after_rename"] = store.list()
    store.delete("bold")
    out["after_delete"] = store.list()
    out["invalid_name"] = attempt(lambda: store.save("../escape", llm()))[1]
    out["missing"] = attempt(lambda: store.load("nope"))[1]
    (base / "broken.json").write_text("{not json")
    out["corrupted"] = attempt(lambda: store.load("broken"))[1]

elif mode == "secrets":
    from openhands.sdk.utils.cipher import Cipher

    store = LLMProfileStore(base)
    store.save("masked", llm())
    store.save("plain", llm(), include_secrets=True)
    cipher = Cipher("a-secret-key-for-the-verify-program")
    store.save("encrypted", llm(), include_secrets=True, cipher=cipher)
    text = {
        n: (base / f"{n}.json").read_text() for n in ("masked", "plain", "encrypted")
    }

    def key_of(loaded: LLM) -> str | None:
        return (
            loaded.api_key.get_secret_value()
            if isinstance(loaded.api_key, SecretStr)
            else None
        )

    out |= {
        "masked_has_secret": "sk-profile-secret" in text["masked"],
        "masked_loaded_key": key_of(store.load("masked")),
        "plain_has_secret": "sk-profile-secret" in text["plain"],
        "plain_loaded_key": key_of(store.load("plain")),
        "encrypted_has_secret": "sk-profile-secret" in text["encrypted"],
        "encrypted_loaded_key": key_of(store.load("encrypted", cipher=cipher)),
        "summaries": {s["name"]: s.get("api_key_set") for s in store.list_summaries()},
        "summaries_expose_secret": "sk-profile-secret"
        in json.dumps(store.list_summaries()),
    }

elif mode == "concurrency":
    from openhands.sdk.llm.llm_profile_store import ProfileLimitExceeded

    store = LLMProfileStore(base)
    errors: list[str] = []
    loaded_ok: list[bool] = []

    def writer(n: int) -> None:
        for i in range(10):
            try:
                store.save(
                    "shared",
                    llm(temperature=round((n * 10 + i) / 100, 2)),
                    include_secrets=True,
                )
                store.save(f"w{n}", llm(), include_secrets=True)
            except Exception as exc:  # report, do not judge
                errors.append(type(exc).__name__)

    def reader() -> None:
        for _ in range(20):
            try:
                loaded_ok.append(store.load("shared").model == "openai/gpt-4o-mini")
            except FileNotFoundError:
                time.sleep(0.01)
            except Exception as exc:
                errors.append(type(exc).__name__)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    threads += [threading.Thread(target=reader) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    out |= {
        "errors": errors,
        "reads_ok": all(loaded_ok) and len(loaded_ok) > 0,
        "profiles": sorted(store.list()),
        "tmp_leftovers": len(list(base.glob("*.tmp"))),
    }
    limited = LLMProfileStore(base / "limited")
    limited.save("one", llm(), max_profiles=2)
    limited.save("two", llm(), max_profiles=2)
    _, over = attempt(lambda: limited.save("three", llm(), max_profiles=2))
    _, overwrite = attempt(
        lambda: limited.save("two", llm(temperature=0.5), max_profiles=2)
    )
    out |= {
        "over_limit_error": over,
        "over_limit_is_profile_limit": over is not None
        and over.startswith(ProfileLimitExceeded.__name__),
        "overwrite_error": overwrite,
        "limited_profiles": sorted(limited.list()),
    }

elif mode == "migration":
    from openhands.sdk.llm import LLM_PROFILE_SCHEMA_VERSION

    store = LLMProfileStore(base)
    (base / "legacy.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model": "litellm_proxy/claude-opus-5",
                "base_url": "https://llm-proxy.app.all-hands.dev/",
            }
        )
    )
    (base / "thirdparty.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model": "litellm_proxy/claude-opus-5",
                "base_url": "https://litellm.example.com/",
            }
        )
    )
    (base / "future.json").write_text(
        json.dumps(
            {
                "schema_version": LLM_PROFILE_SCHEMA_VERSION + 1,
                "model": "openai/gpt-4o-mini",
            }
        )
    )
    legacy = store.load("legacy")
    third = store.load("thirdparty")
    out |= {
        "legacy_model": legacy.model,
        "legacy_base_url": legacy.base_url,
        "thirdparty_model": third.model,
        "thirdparty_base_url": third.base_url,
        "future_error": attempt(lambda: store.load("future"))[1],
    }

elif mode == "connections":
    from openhands.sdk.llm.provider_connection_store import (
        ProviderConnection,
        ProviderConnectionStore,
    )

    store = LLMProfileStore(base / "profiles")
    connections = ProviderConnectionStore(base_dir=base / "provider-connections")
    now = int(time.time())
    connections.create(
        ProviderConnection(
            id="conn1",
            display_name="Shared Anthropic",
            provider="anthropic",
            api_key="sk-shared-connection",
            base_url="https://anthropic.example.com",
            created_at=now,
            updated_at=now,
        )
    )
    store.save(
        "linked",
        LLM(
            model="anthropic/claude-sonnet-4-5-20250929",
            usage_id="l",
            provider_connection_id="conn1",
            api_key=SecretStr("sk-inline-ignored"),
        ),
        include_secrets=True,
    )
    on_disk = json.loads((base / "profiles" / "linked.json").read_text())
    loaded = store.load("linked")
    connections.update(
        ProviderConnection(
            id="conn1",
            display_name="Shared Anthropic",
            provider="anthropic",
            api_key="sk-rotated",
            base_url="https://anthropic.example.com",
            created_at=now,
            updated_at=now + 1,
        )
    )
    rotated = store.load("linked")
    connections.delete("conn1")
    out |= {
        "stored_api_key": on_disk.get("api_key"),
        "stored_connection_id": on_disk.get("provider_connection_id"),
        "loaded_key": loaded.api_key.get_secret_value() if loaded.api_key else None,
        "loaded_base_url": loaded.base_url,
        "rotated_key": rotated.api_key.get_secret_value() if rotated.api_key else None,
        "dangling_error": attempt(lambda: store.load("linked"))[1],
        "sibling_dir_default": (
            LLMProfileStore(base / "profiles")._provider_store.base_dir
            == base / "provider-connections"
        ),
    }

elif mode == "meta":
    from openhands.sdk.llm.meta_profile_store import (
        MetaProfile,
        MetaProfileClass,
        MetaProfileStore,
    )

    store = MetaProfileStore(base_dir=base)
    structured = MetaProfile(
        classifier_model="classifier",
        classes=[
            MetaProfileClass(description="small edits", model="cheap"),
            MetaProfileClass(description="hard debugging", model="strong"),
        ],
    )
    direct = MetaProfile(
        classifier_model="classifier",
        prompt_template="Pick a model.\n{{ model_table }}\nTask:\n{{ instance_text }}",
        model_table="- cheap\n- strong",
    )
    store.save("structured", structured)
    store.save("direct", direct)
    out |= {
        "list": sorted(store.list()),
        "loaded_classes": [c.model for c in store.load("structured").classes],
        "loaded_template": "{{ instance_text }}"
        in (store.load("direct").prompt_template or ""),
        "summaries": len(store.list_summaries()),
    }
    store.delete("direct")
    out["after_delete"] = store.list()
    out["template_without_instance_text"] = attempt(
        lambda: MetaProfile(
            classifier_model="c", prompt_template="no placeholder", model_table="- a"
        )
    )[1]
    out["template_and_classes"] = attempt(
        lambda: MetaProfile(
            classifier_model="c",
            prompt_template="{{ instance_text }}",
            classes=[MetaProfileClass(description="x", model="a")],
        )
    )[1]
    limited = MetaProfileStore(base_dir=base / "limited")
    limited.save("a", structured, max_profiles=1)
    out["over_limit"] = attempt(lambda: limited.save("b", structured, max_profiles=1))[
        1
    ]

elif mode == "default-dir":
    import os

    from openhands.sdk.llm.meta_profile_store import default_meta_profile_dir

    store = LLMProfileStore()
    out |= {
        "oh_persistence_dir": os.environ.get("OH_PERSISTENCE_DIR"),
        "profile_dir": str(store.base_dir),
        "meta_profile_dir": str(default_meta_profile_dir()),
    }

else:
    raise SystemExit(f"unknown mode {mode}")

emit(out)
