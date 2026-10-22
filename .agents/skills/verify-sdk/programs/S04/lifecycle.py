"""Create, persist, reload, observe and close local conversations the way a
program does, one scenario per mode. The model is the SDK's scripted `TestLLM`,
or a real `LLM` against the local endpoint in `S02/fake_provider.py` where the
request the model receives matters (memory after a resume).

Usage: lifecycle.py MODE
  factory      what Conversation(...) returns, ids, persistence_dir rules
  persist      the on-disk layout, written event by event
  corrupt      reloading a conversation whose event files were damaged
  resume       a new Conversation with the same id continues with memory
  compat       which agent changes a resume accepts
  file-store   an injected FileStore instead of persistence_dir
  callbacks    order, delivery after persistence, a failed write
  run-error    an exception inside run()
  close        terminal sessions, idempotence, atexit, delete_on_close, __del__
"""

import gc
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import weakref
from pathlib import Path

from pydantic import SecretStr
from S02.fake_provider import FakeProvider
from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import LLM, Agent, Conversation, LocalWorkspace, Tool
from openhands.sdk.event import MessageEvent
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.tools.terminal import TerminalTool


mode = sys.argv[1]
paths = run_paths()
WORKSPACE = str(paths["workspace"])
BASE = paths["run"] / f"lifecycle-{mode}"
BASE.mkdir(parents=True, exist_ok=True)
out: dict = {"ok": True, "mode": mode}


def attempt(fn):
    try:
        return fn(), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"[:400]


def terminal_agent(script, **kw) -> Agent:
    return Agent(llm=make_llm(script), tools=[Tool(name=TerminalTool.name)], **kw)


ECHO_FINISH = [
    {"tool": "terminal", "args": {"command": "echo persisted-step"}},
    {"finish": "all written"},
]


def tmux_sessions() -> int:
    """Live tmux servers in this run. Each terminal gets its own socket under
    the run's private TMUX_TMPDIR; a socket file can outlive its server, so ask
    each one whether a server still answers."""
    folder = Path(os.environ["TMUX_TMPDIR"]) / f"tmux-{os.getuid()}"
    sockets = [p for p in folder.glob("*") if p.is_socket()] if folder.is_dir() else []
    return sum(
        subprocess.run(["tmux", "-S", str(p), "ls"], capture_output=True).returncode
        == 0
        for p in sockets
    )


if mode == "factory":
    from openhands.sdk.workspace import RemoteWorkspace

    a = Conversation(
        agent=Agent(llm=make_llm([]), tools=[]), workspace=WORKSPACE, visualizer=None
    )
    b = Conversation(
        agent=Agent(llm=make_llm([]), tools=[]),
        workspace=LocalWorkspace(working_dir=WORKSPACE),
        visualizer=None,
    )
    chosen = uuid.uuid4()
    c = Conversation(
        agent=Agent(llm=make_llm([]), tools=[]),
        workspace=WORKSPACE,
        persistence_dir=str(BASE),
        conversation_id=chosen,
        visualizer=None,
    )
    remote_ws = RemoteWorkspace(host="http://127.0.0.1:9", working_dir="/workspace")
    _, remote_with_dir = attempt(
        lambda: Conversation(
            agent=Agent(llm=make_llm([]), tools=[]),
            workspace=remote_ws,
            persistence_dir=str(BASE),
        )
    )
    saved = json.loads((BASE / chosen.hex / "base_state.json").read_text())
    out |= {
        "path_type": type(a).__name__,
        "local_workspace_type": type(b).__name__,
        "ids_differ": a.id != b.id,
        "id_is_uuid4": a.id.version == 4,
        "chosen_id_kept": c.id == chosen,
        "persisted_id": saved.get("id"),
        "chosen_id": str(chosen),
        "remote_with_persistence_dir": remote_with_dir,
    }
    for conv in (a, b, c):
        conv.close()

elif mode == "persist":
    seen: list[dict] = []
    holder: dict = {}

    def on_event(event) -> None:
        conv = holder.get("c")
        if conv is None:
            return
        directory = BASE / conv.id.hex / "events"
        seen.append(
            {
                "kind": type(event).__name__,
                "file_exists": any(directory.glob(f"event-*-{event.id}.json")),
                "files_now": len(list(directory.glob("event-*.json"))),
            }
        )

    conversation = Conversation(
        agent=terminal_agent(ECHO_FINISH),
        workspace=WORKSPACE,
        persistence_dir=str(BASE),
        callbacks=[on_event],
        visualizer=None,
    )
    holder["c"] = conversation
    conversation.send_message("Echo something, then finish.")
    conversation.run()
    folder = BASE / conversation.id.hex
    files = sorted(p.name for p in (folder / "events").glob("*.json"))
    pattern = re.compile(r"^event-\d{5}-[0-9a-f-]+\.json$")
    ids_in_order = [e.id for e in conversation.state.events]
    out |= {
        "folder_is_id_hex": folder.is_dir(),
        "base_state": (folder / "base_state.json").is_file(),
        "event_files": len(files),
        "events_in_state": len(ids_in_order),
        "names_match": all(pattern.match(f) for f in files),
        "numbered_in_order": [int(f.split("-")[1]) for f in files]
        == list(range(len(files))),
        "files_follow_event_ids": [f.split("-", 2)[2][:-5] for f in files]
        == ids_in_order,
        "callback_saw_file_every_time": all(s["file_exists"] for s in seen),
        "files_grew_one_by_one": [s["files_now"] for s in seen]
        == sorted(s["files_now"] for s in seen),
        "callback_events": len(seen),
        "status": conversation.state.execution_status.value,
    }
    conversation.close()

elif mode == "corrupt":
    conversation = Conversation(
        agent=terminal_agent(ECHO_FINISH),
        workspace=WORKSPACE,
        persistence_dir=str(BASE),
        visualizer=None,
    )
    conversation.send_message("Echo something, then finish.")
    conversation.run()
    cid, count = conversation.id, len(conversation.state.events)
    conversation.close()
    events_dir = BASE / cid.hex / "events"
    files = sorted(events_dir.glob("event-*.json"))

    def reopen():
        conv = Conversation(
            agent=terminal_agent([]),
            workspace=WORKSPACE,
            persistence_dir=str(BASE),
            conversation_id=cid,
            visualizer=None,
        )
        try:
            return len(list(conv.state.events))
        finally:
            conv.close()

    import traceback

    backup = BASE / "backup"
    shutil.copytree(str(events_dir), str(backup))

    def damaged(change) -> dict:
        shutil.rmtree(str(events_dir))
        shutil.copytree(str(backup), str(events_dir))
        change(sorted(events_dir.glob("event-*.json")))
        try:
            return {"events": reopen(), "error": None}
        except Exception as exc:
            frame = traceback.extract_tb(exc.__traceback__)[-1]
            return {
                "events": None,
                "error": f"{type(exc).__name__}: {exc}"[:300],
                "raised_in": f"{Path(frame.filename).name}:{frame.name}",
            }

    out["events_before"] = count
    out["intact"] = damaged(lambda _files: None)
    # An empty file is what an interrupted write leaves behind.
    out["empty_middle"] = damaged(lambda files: files[2].write_text(""))
    out["empty_last"] = damaged(lambda files: files[-1].write_text(""))
    out["invalid_json_middle"] = damaged(
        lambda files: files[2].write_text('{"invalid": json}')
    )
    out["missing_middle"] = damaged(lambda files: files[2].unlink())
    out["missing_last"] = damaged(lambda files: files[-1].unlink())

elif mode == "resume":
    cid = uuid.uuid4()
    with FakeProvider(
        [{"text": "Noted."}, {"text": "The code word was PLUM."}]
    ) as server:

        def llm() -> LLM:
            return LLM(
                model="openai/gpt-4o-mini",
                base_url=server.url,
                api_key=SecretStr("sk-test"),
                usage_id="agent",
                num_retries=1,
            )

        first = Conversation(
            agent=Agent(llm=llm(), tools=[Tool(name=TerminalTool.name)]),
            workspace=WORKSPACE,
            persistence_dir=str(BASE),
            conversation_id=cid,
            visualizer=None,
        )
        first.send_message("Remember the code word PLUM.")
        first.run()
        before = len(first.state.events)
        first.close()
        del first
        gc.collect()

        second = Conversation(
            agent=Agent(llm=llm(), tools=[Tool(name=TerminalTool.name)]),
            workspace=WORKSPACE,
            persistence_dir=str(BASE),
            conversation_id=cid,
            visualizer=None,
        )
        reloaded = len(second.state.events)
        status_after_load = second.state.execution_status.value
        second.send_message("What was the code word?")
        second.run()
        last_body = json.dumps(server.requests[-1]["body"]["messages"])
        replies = [
            "".join(c.text for c in e.llm_message.content)
            for e in second.state.events
            if isinstance(e, MessageEvent) and e.source == "agent"
        ]
        out |= {
            "events_before": before,
            "events_reloaded": reloaded,
            "status_after_load": status_after_load,
            "second_request_has_first_turn": "Remember the code word PLUM."
            in last_body,
            "second_request_has_first_reply": "Noted." in last_body,
            "system_prompts_sent": last_body.count('"role": "system"'),
            "replies": replies,
            "status": second.state.execution_status.value,
            "events_after": len(second.state.events),
        }
        second.close()

elif mode == "compat":
    from openhands.tools.file_editor import FileEditorTool

    class OtherAgent(Agent):
        pass

    def make(cid, agent):
        return Conversation(
            agent=agent,
            workspace=WORKSPACE,
            persistence_dir=str(BASE),
            conversation_id=cid,
            visualizer=None,
        )

    def seeded(tools, **kw) -> uuid.UUID:
        cid = uuid.uuid4()
        conv = make(cid, Agent(llm=make_llm([{"finish": "seeded"}]), tools=tools, **kw))
        conv.send_message("Seed the conversation.")
        conv.run()
        conv.close()
        return cid

    def resume(cid, agent) -> str | None:
        def go():
            conv = make(cid, agent)
            conv.send_message("Continue.")
            conv.run()
            status = conv.state.execution_status.value
            conv.close()
            return status

        status, err = attempt(go)
        return err or status

    both = [Tool(name=TerminalTool.name), Tool(name=FileEditorTool.name)]
    term = [Tool(name=TerminalTool.name)]
    out["remove_tool"] = resume(
        seeded(both), Agent(llm=make_llm([{"finish": "x"}]), tools=term)
    )
    out["add_tool"] = resume(
        seeded(term), Agent(llm=make_llm([{"finish": "x"}]), tools=both)
    )
    out["change_llm"] = resume(
        seeded(term),
        Agent(
            llm=make_llm(
                [{"finish": "x"}], model="openai/gpt-4.1-mini", usage_id="agent"
            ),
            tools=term,
        ),
    )
    out["change_agent_class"] = resume(
        seeded(term), OtherAgent(llm=make_llm([{"finish": "x"}]), tools=term)
    )
    out["remove_default_tool"] = resume(
        seeded(term),
        Agent(
            llm=make_llm([{"finish": "x"}]),
            tools=term,
            include_default_tools=["FinishTool"],
        ),
    )

elif mode == "file-store":
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation
    from openhands.sdk.io import InMemoryFileStore

    store = InMemoryFileStore()
    cid = uuid.uuid4()
    conversation = LocalConversation(
        agent=terminal_agent(ECHO_FINISH),
        workspace=WORKSPACE,
        persistence_dir=str(BASE),
        conversation_id=cid,
        file_store=store,
        visualizer=None,
    )
    conversation.send_message("Echo something, then finish.")
    conversation.run()
    count = len(conversation.state.events)
    conversation.close()
    again = LocalConversation(
        agent=terminal_agent([]),
        workspace=WORKSPACE,
        conversation_id=cid,
        file_store=store,
        visualizer=None,
    )
    out |= {
        "reported_persistence_dir": conversation.state.persistence_dir,
        "expected_persistence_dir": str(BASE / cid.hex),
        "store_has_base_state": store.exists("base_state.json"),
        "store_event_files": len(
            [p for p in store.list("events") if p.endswith(".json")]
        ),
        "events": count,
        "nothing_on_disk": not (BASE / cid.hex / "base_state.json").exists()
        and not (BASE / cid.hex / "events").exists(),
        "reloaded_from_store": len(again.state.events),
    }
    again.close()

elif mode == "callbacks":
    from openhands.sdk.conversation.visualizer import ConversationVisualizerBase

    order: list[str] = []
    persisted_at_callback: list[bool] = []
    persisted_at_visualizer: list[bool] = []
    holder: dict = {}

    class Recording(ConversationVisualizerBase):
        def on_event(self, event) -> None:
            order.append("visualizer")
            conv = holder.get("c")
            if conv is not None:
                persisted_at_visualizer.append(event.id in conv.state.events)

    def first(event) -> None:
        order.append("first")
        conv = holder.get("c")
        if conv is not None:
            persisted_at_callback.append(event.id in conv.state.events)

    def second(_event) -> None:
        order.append("second")

    conversation = Conversation(
        agent=terminal_agent(ECHO_FINISH),
        workspace=WORKSPACE,
        persistence_dir=str(BASE),
        callbacks=[first, second],
        visualizer=Recording(),
    )
    holder["c"] = conversation
    conversation.send_message("Echo something, then finish.")
    conversation.run()
    out |= {
        "order_first_event": order[:3],
        "every_event_delivered": order.count("first") == len(conversation.state.events),
        "consistent_order": order
        == ["visualizer", "first", "second"] * (len(order) // 3),
        "persisted_before_user_callback": bool(persisted_at_callback)
        and all(persisted_at_callback),
        "persisted_before_visualizer": bool(persisted_at_visualizer)
        and all(persisted_at_visualizer),
    }
    conversation.close()

    # A write that fails: the event never reaches the callbacks.
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation
    from openhands.sdk.io import InMemoryFileStore

    class FailingStore(InMemoryFileStore):
        fail = False

        def write(self, path, contents):
            if self.fail and path.startswith("events/"):
                raise OSError("disk full (simulated)")
            return super().write(path, contents)

    store = FailingStore()
    delivered: list[str] = []
    failing = LocalConversation(
        agent=terminal_agent([{"finish": "never"}]),
        workspace=WORKSPACE,
        file_store=store,
        callbacks=[lambda e: delivered.append(type(e).__name__)],
        visualizer=None,
    )
    failing.send_message("First message is stored.")
    before = list(delivered)
    store.fail = True
    _, out["failed_write_error"] = attempt(
        lambda: failing.send_message("Second message cannot be stored.")
    )
    out["delivered_before_failure"] = before
    out["delivered_after_failure"] = delivered[len(before) :]
    store.fail = False
    failing.close()

elif mode == "run-error":
    from openhands.sdk.conversation.exceptions import ConversationRunError
    from openhands.sdk.llm.exceptions import LLMAuthenticationError
    from openhands.sdk.testing import TestLLM

    def failing_run(exc: Exception, persist: bool) -> dict:
        conversation = Conversation(
            agent=Agent(llm=TestLLM.from_messages([exc], usage_id="agent"), tools=[]),
            workspace=WORKSPACE,
            persistence_dir=str(BASE) if persist else None,
            visualizer=None,
        )
        conversation.send_message("Do the task.")
        row: dict = {}
        try:
            conversation.run()
            row["raised"] = None
        except ConversationRunError as err:
            text = str(err)
            row |= {
                "raised": type(err).__name__,
                "names_id": str(conversation.id) in text,
                "names_persistence_dir": err.persistence_dir is not None
                and err.persistence_dir in text,
                "persistence_dir": err.persistence_dir,
                "has_issue_url": "github.com/OpenHands/software-agent-sdk/issues"
                in text,
                "original": type(err.original_exception).__name__,
            }
        except Exception as err:  # report, do not judge
            row["raised"] = f"{type(err).__name__}: {err}"[:300]
        errors = [
            e
            for e in conversation.state.events
            if isinstance(e, ConversationErrorEvent)
        ]
        row |= {
            "error_events": len(errors),
            "codes": [e.code for e in errors],
            "sources": [e.source for e in errors],
            "status": conversation.state.execution_status.value,
        }
        conversation.close()
        return row

    out["plain_exception"] = failing_run(
        RuntimeError("boom in the model call"), persist=True
    )
    out["llm_error"] = failing_run(LLMAuthenticationError("bad key"), persist=False)

elif mode == "close":

    def build(**kw):
        conv = Conversation(
            agent=terminal_agent(
                [
                    {"tool": "terminal", "args": {"command": "echo open"}},
                    {"finish": "ok"},
                ]
            ),
            workspace=WORKSPACE,
            persistence_dir=str(BASE),
            visualizer=None,
            **kw,
        )
        conv.send_message("Use the terminal.")
        conv.run()
        return conv

    baseline = tmux_sessions()
    conv = build()
    open_sessions = tmux_sessions()
    conv.close()
    after_close = tmux_sessions()
    _, second_close_error = attempt(conv.close)
    ref = weakref.ref(conv)
    folder = BASE / conv.id.hex
    del conv
    gc.collect()
    out |= {
        "sessions_while_open": open_sessions - baseline,
        "sessions_after_close": after_close - baseline,
        "second_close_error": second_close_error,
        "collected_after_close": ref() is None,
        "persisted_files_kept": (folder / "base_state.json").is_file(),
    }
    keep = build(delete_on_close=False)
    keep_open = tmux_sessions()
    keep.close()
    out["keep_sessions_while_open"] = keep_open - baseline
    out["keep_sessions_after_close"] = tmux_sessions() - baseline
    out["keep_files_kept"] = (BASE / keep.id.hex / "base_state.json").is_file()
    dropped = build()
    dropped_open = tmux_sessions()
    dropped.__del__()
    out["del_sessions_while_open"] = dropped_open - baseline
    out["del_sessions_after"] = tmux_sessions() - baseline

else:
    raise SystemExit(f"unknown mode {mode}")

emit(out)
