"""update_secrets must hold the state lock like every other state mutator.

Without the lock, an update_secrets call from a user/HTTP thread mutates
``state.secret_registry.secret_sources`` in place while another thread's
state-field assignment is autosaving (``_save_base_state`` ->
``model_dump_json``), which iterates that same dict during pydantic
serialization. pydantic-core surfaces the mutation as
``pyo3_runtime.PanicException`` ("dictionary changed size during iteration") —
a BaseException that escapes the run loop's ``except Exception`` handlers, so
the conversation thread dies without transitioning to ERROR.

The test freezes the autosave mid-iteration with a blocking secret serializer,
then calls the real ``conversation.update_secrets()`` from another thread, and
asserts the autosave completes without crashing.
"""

import threading

from pydantic import SecretStr

import openhands.sdk.secret.secrets as secrets_mod
from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.llm import LLM


AUTOSAVE_THREAD_NAME = "autosave-thread"


def test_update_secrets_does_not_race_autosave_serialization(tmp_path, monkeypatch):
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    conversation = Conversation(
        agent=agent,
        workspace=str(tmp_path / "workspace"),
        persistence_dir=str(tmp_path / "persist"),
    )
    # Seed secrets so the autosave serialization iterates secret_sources.
    conversation.update_secrets({"seed_a": "value-a", "seed_b": "value-b"})

    in_serializer = threading.Event()
    proceed = threading.Event()
    original_serialize = secrets_mod.serialize_secret

    def blocking_serialize(v, info):
        # Freeze the autosave thread on the first secret it serializes,
        # holding pydantic-core mid-iteration of secret_sources.
        if (
            threading.current_thread().name == AUTOSAVE_THREAD_NAME
            and not in_serializer.is_set()
        ):
            in_serializer.set()
            assert proceed.wait(timeout=10), "test orchestration timeout"
        return original_serialize(v, info)

    monkeypatch.setattr(secrets_mod, "serialize_secret", blocking_serialize)

    state = conversation.state
    autosave_exc: list[BaseException] = []

    def autosave_thread():
        try:
            # What the run loop does on every step under `with self._state:` —
            # any field assignment triggers _save_base_state -> model_dump_json.
            with state:
                state.execution_status = ConversationExecutionStatus.RUNNING
        except BaseException as e:  # noqa: BLE001 - PanicException is not Exception
            autosave_exc.append(e)

    t_autosave = threading.Thread(target=autosave_thread, name=AUTOSAVE_THREAD_NAME)
    t_autosave.start()
    assert in_serializer.wait(timeout=10), "autosave never reached the serializer"

    # A user/HTTP thread updates secrets while the autosave is mid-iteration.
    t_update = threading.Thread(
        target=lambda: conversation.update_secrets({"injected": "value-c"})
    )
    t_update.start()
    # Fixed: update_secrets blocks on the state lock held by the autosave
    # thread. Unfixed: it mutates secret_sources immediately.
    t_update.join(timeout=1.0)

    proceed.set()
    t_autosave.join(timeout=10)
    t_update.join(timeout=10)
    assert not t_autosave.is_alive()
    assert not t_update.is_alive()

    assert not autosave_exc, (
        f"autosave crashed while update_secrets ran concurrently: {autosave_exc!r}"
    )
    assert "injected" in state.secret_registry.secret_sources
