"""Drive an `ACPAgent` the way a program swaps the built-in LLM loop for an
external ACP agent: `ACPAgent(acp_command=[...])` in an ordinary
`Conversation`. The ACP server is `fake_acp.py` (a real subprocess speaking
ACP over stdio), which journals every request so the result can show what the
SDK sent as well as what it made of the replies.

Usage: acp_conversation.py MODE
  turn       two turns (text + image, then a secret echo), ask_agent, model
             switch (valid, invalid, before start), close
  resume     reopen a persisted conversation; explicit and unknown resume ids
  errors     init failures: auth required, missing binary, startup timeout,
             custom tools; a failed init is retried on the same conversation
  timeouts   idle timeout with a steady and a silent server, retries
  cancel     cancel `arun()` mid-prompt, then run another turn
  codex      a server launched as a `codex-acp` binary: file secrets, data
             dir isolation, auth method choice, model + reasoning effort
  discovery  discover_acp_models() and ACPAgentSettings.create_agent()
"""

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

from sdk_verify import emit, run_paths

from openhands.sdk import Conversation, ImageContent, Message, TextContent
from openhands.sdk.agent import ACPAgent
from openhands.sdk.event import ActionEvent, SystemPromptEvent
from openhands.sdk.event.acp_tool_call import ACPToolCallEvent
from openhands.sdk.event.conversation_error import ConversationErrorEvent


HERE = Path(__file__).resolve().parent
FAKE = [sys.executable, str(HERE / "fake_acp.py")]
mode = sys.argv[1]
paths = run_paths()
work = paths["run"] / "acp" / mode
work.mkdir(parents=True, exist_ok=True)
JOURNAL = work / "journal.jsonl"
os.environ["FAKE_ACP_JOURNAL"] = str(JOURNAL)
os.environ["FAKE_ACP_SESSIONS"] = str(work / "sessions.txt")
workspace = paths["workspace"]
workspace.mkdir(parents=True, exist_ok=True)
result: dict = {"ok": True, "mode": mode}
warnings_seen: list[str] = []


class _Capture(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.WARNING:
            warnings_seen.append(record.getMessage()[:300])


logging.getLogger("openhands").addHandler(_Capture())
# A 1x1 PNG, sent inline so the run needs no network.
PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def journal(kind: str | None = None) -> list[dict]:
    if not JOURNAL.exists():
        return []
    rows = [
        json.loads(line) for line in JOURNAL.read_text().splitlines() if line.strip()
    ]
    return [r for r in rows if kind is None or r["kind"] == kind]


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0); read its state.
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return False


def tool_events(conversation) -> list[ACPToolCallEvent]:
    return [e for e in conversation.state.events if isinstance(e, ACPToolCallEvent)]


def finishes(conversation) -> list[ActionEvent]:
    return [
        e
        for e in conversation.state.events
        if isinstance(e, ActionEvent) and e.tool_name == "finish"
    ]


def errors_of(conversation) -> list[dict]:
    return [
        {"code": e.code, "detail": e.detail[:300]}
        for e in conversation.state.events
        if isinstance(e, ConversationErrorEvent)
    ]


def run_turn(conversation, message) -> str | None:
    """send + run; returns the exception class name if run() raised."""
    conversation.send_message(message)
    try:
        conversation.run()
    except Exception as exc:  # recorded, the checks judge it
        return f"{type(exc).__name__}: {str(exc)[:200]}"
    return None


if mode == "turn":
    os.environ["npm_config_verify_marker"] = "leaks-if-not-stripped"
    os.environ["CLAUDECODE"] = "1"
    from openhands.sdk import AgentContext
    from openhands.sdk.context import Skill

    agent = ACPAgent(
        acp_command=FAKE,
        acp_model="m2",
        mcp_config={
            "files": {
                "command": "uvx",
                "args": ["mcp-server-files"],
                "env": {"ROOT": "/tmp"},
            },
            "search": {
                "url": "https://mcp.example.test/mcp",
                "headers": {"X-Team": "verify"},
            },
        },
        agent_context=AgentContext(
            skills=[
                Skill(
                    name="deploy-notes",
                    content="How we deploy.",
                    description="Deployment notes",
                )
            ]
        ),
    )
    result["before_start"] = {
        "current_model_id": agent.current_model_id,
        "supports_switch": agent.supports_runtime_model_switch,
        "tools": len(agent.tools),
        "llm_model": agent.llm.model,
    }
    secret = "tok-s3cret-4417"
    tokens: list[str] = []
    conversation = Conversation(
        agent=agent,
        workspace=str(workspace),
        visualizer=None,
        secrets={"FAKE_TOKEN": secret, "EMPTY_SECRET": ""},
        token_callbacks=[lambda chunk: tokens.append(str(chunk))],
    )
    try:
        ACPAgent(acp_command=FAKE).ask_agent("no session yet?")
        result["agent_ask_before_start"] = "no error"
    except Exception as exc:
        result["agent_ask_before_start"] = type(exc).__name__
    first_error = run_turn(
        conversation,
        Message(
            role="user",
            content=[
                TextContent(text="List the files here."),
                ImageContent(image_urls=[PNG]),
            ],
        ),
    )
    live = conversation.agent
    after_first = {
        "status": conversation.state.execution_status.value,
        "error": first_error,
        "current_model_id": live.current_model_id,
        "available_models": [m.model_id for m in (live.available_models or [])],
        "supports_switch": live.supports_runtime_model_switch,
        "cost": live.llm.metrics.accumulated_cost,
        "tokens_streamed": "".join(tokens),
    }
    second_error = run_turn(conversation, "[secret] Again, and show the token.")
    cost_before_ask = conversation.agent.llm.metrics.accumulated_cost
    tokens_before_ask = (
        conversation.agent.llm.metrics.accumulated_token_usage.prompt_tokens
    )
    warnings_before_ask = len(warnings_seen)
    t0 = time.monotonic()
    answer = conversation.ask_agent("Which files did you list?")
    result["cost_after_turns"] = cost_before_ask
    result["prompt_tokens_after_turns"] = tokens_before_ask
    result["ask_seconds"] = round(time.monotonic() - t0, 2)
    result["ask_cost_delta"] = round(
        conversation.agent.llm.metrics.accumulated_cost - cost_before_ask, 6
    )
    result["ask_prompt_tokens_delta"] = (
        conversation.agent.llm.metrics.accumulated_token_usage.prompt_tokens
        - tokens_before_ask
    )
    result["ask_warnings"] = warnings_seen[warnings_before_ask:]
    result["fork_usage_updates_sent"] = len([p for p in journal("prompt") if p["fork"]])
    cost_after_ask = conversation.agent.llm.metrics.accumulated_cost
    conversation.switch_acp_model("m1")
    switched = conversation.agent
    try:
        conversation.switch_acp_model("no-such-model")
        bad_switch = "no error"
    except ValueError as exc:
        bad_switch = f"ValueError: {exc}"[:200]
    tools = tool_events(conversation)
    fins = finishes(conversation)
    sys_prompts = [
        e for e in conversation.state.events if isinstance(e, SystemPromptEvent)
    ]
    usage = conversation.agent.llm.metrics.accumulated_token_usage
    start = journal("start")[0]
    prompts = [p for p in journal("prompt") if not p["fork"]]
    events_dump = json.dumps(
        [e.model_dump(mode="json") for e in conversation.state.events]
    )
    pid = start["pid"]
    alive_before_close = pid_alive(pid)
    conversation.agent.close()
    time.sleep(0.5)
    alive_after_close = pid_alive(pid)
    try:
        conversation.agent.close()
        second_close = "ok"
    except Exception as exc:
        second_close = type(exc).__name__
    result.update(
        {
            "after_first": after_first,
            "status": conversation.state.execution_status.value,
            "second_error": second_error,
            "finish_messages": [e.action.message for e in fins],
            "finish_reasoning": [e.reasoning_content for e in fins],
            "tool_events": [f"{e.tool_call_id}:{e.status}" for e in tools],
            "tool_event_errors": [e.is_error for e in tools],
            "tool_titles": sorted({e.title for e in tools}),
            "tool_list_events_turn1": len(
                [e for e in tools if e.tool_call_id == "t1-list"]
            ),
            "tool_list_final_output": next(
                (e.raw_output for e in reversed(tools) if e.tool_call_id == "t1-list"),
                None,
            ),
            "tool_before_finish": bool(tools and fins)
            and (
                conversation.state.events.index(tools[0])
                < conversation.state.events.index(fins[0])
            ),
            "permission_answers": [p["answer"] for p in journal("permission_answer")],
            "system_prompt_events": len(sys_prompts),
            "system_prompt_names_secret": bool(sys_prompts)
            and "FAKE_TOKEN" in str(getattr(sys_prompts[0], "dynamic_context", "")),
            "system_prompt_has_skill": bool(sys_prompts)
            and "deploy-notes" in str(getattr(sys_prompts[0], "dynamic_context", "")),
            "prompt_count": len(prompts),
            "prompt1_types": [b["type"] for b in prompts[0]["blocks"]]
            if prompts
            else [],
            "prompt1_image_mime": next(
                (b["mimeType"] for b in prompts[0]["blocks"] if b["type"] == "image"),
                None,
            )
            if prompts
            else None,
            "prompt1_has_skill": bool(prompts)
            and any("deploy-notes" in b["text"] for b in prompts[0]["blocks"]),
            "prompt1_names_secret": bool(prompts)
            and any("FAKE_TOKEN" in b["text"] for b in prompts[0]["blocks"]),
            "prompt1_leaks_secret_value": bool(prompts)
            and any(secret in b["text"] for b in prompts[0]["blocks"]),
            "prompt2_blocks": len(prompts[1]["blocks"]) if len(prompts) > 1 else None,
            "prompt2_has_skill": len(prompts) > 1
            and any("deploy-notes" in b["text"] for b in prompts[1]["blocks"]),
            "env_seen": start["env"],
            "session_new": journal("session_new"),
            "config_calls": [
                f"{c['config_id']}={c['value']}" for c in journal("set_config_option")
            ],
            "set_mode_calls": len(journal("set_mode")),
            "forks": len(journal("session_fork")),
            "fork_parent_is_main": bool(prompts)
            and all(
                f["parent"] == prompts[0]["session"] for f in journal("session_fork")
            ),
            "main_prompts_after_ask": len(
                [p for p in journal("prompt") if not p["fork"]]
            ),
            "ask_answer": answer,
            "cost": conversation.agent.llm.metrics.accumulated_cost,
            "cost_after_ask": cost_after_ask,
            "combined_cost": (
                conversation.conversation_stats.get_combined_metrics().accumulated_cost
            ),
            "prompt_tokens": usage.prompt_tokens if usage else None,
            "completion_tokens": usage.completion_tokens if usage else None,
            "cache_read_tokens": usage.cache_read_tokens if usage else None,
            "context_window": usage.context_window if usage else None,
            "latencies": len(conversation.agent.llm.metrics.response_latencies),
            "switched_model": switched.acp_model,
            "switched_current": switched.current_model_id,
            "switched_llm_model": switched.llm.model,
            "bad_switch": bad_switch,
            "secret_in_events": secret in events_dump,
            "secret_masked_in_finish": bool(fins)
            and secret not in fins[-1].action.message
            and "<secret-hidden>" in fins[-1].action.message,
            "secret_in_tokens": secret in "".join(tokens),
            "alive_before_close": alive_before_close,
            "alive_after_close": alive_after_close,
            "second_close": second_close,
            "persisted_session_id": "acp_session_id" in conversation.state.agent_state,
        }
    )
    # A model switch before the session exists is recorded and applied at start.
    early = Conversation(
        agent=ACPAgent(acp_command=FAKE), workspace=str(workspace), visualizer=None
    )
    early.switch_acp_model("m2")
    deferred_model = early.agent.acp_model
    early_error = run_turn(early, "hello")
    result["deferred"] = {
        "acp_model": deferred_model,
        "error": early_error,
        "current_model_id": early.agent.current_model_id,
        "config_calls": [
            f"{c['config_id']}={c['value']}" for c in journal("set_config_option")
        ][-1:],
    }
    early.agent.close()
    early.close()
    conversation.close()

elif mode == "resume":
    store = paths["conversations"]
    first = Conversation(
        agent=ACPAgent(acp_command=FAKE, acp_model="m2"),
        workspace=str(workspace),
        persistence_dir=str(store),
        visualizer=None,
    )
    cid = first.id
    result["first_error"] = run_turn(first, "first turn")
    first_session = first.state.agent_state.get("acp_session_id")
    first_pid = journal("start")[-1]["pid"]
    first.agent.close()
    first.close()
    # The program restarts: same conversation id and persistence dir.
    second = Conversation(
        agent=ACPAgent(acp_command=FAKE, acp_model="m2"),
        workspace=str(workspace),
        persistence_dir=str(store),
        conversation_id=cid,
        visualizer=None,
    )
    result["second_error"] = run_turn(second, "second turn")
    second_pid = journal("start")[-1]["pid"]
    loads = journal("session_load")
    prompts = journal("prompt")
    result.update(
        {
            "first_session_present": bool(first_session),
            "new_process": second_pid != first_pid,
            "load_calls": len(loads),
            "load_used_persisted_id": bool(loads)
            and loads[0]["session"] == first_session,
            "load_known": bool(loads) and loads[0]["known"],
            "session_new_calls": len(journal("session_new")),
            "same_session_after_resume": second.state.agent_state.get("acp_session_id")
            == first_session,
            "model_reapplied_after_load": [
                f"{c['config_id']}={c['value']}"
                for c in journal("set_config_option")
                if c["t"] > loads[0]["t"]
            ]
            if loads
            else [],
            "second_status": second.state.execution_status.value,
            "second_prompt_blocks": len(prompts[-1]["blocks"]) if prompts else None,
            "both_turns_same_session": len({p["session"] for p in prompts}) == 1,
            "events_after_resume": len(finishes(second)),
        }
    )
    second.agent.close()
    second.close()
    # A fresh sandbox that lost base_state.json, with the id mirrored elsewhere.
    explicit = ACPAgent(acp_command=FAKE, acp_resume_session_id=first_session)
    result["resume_id_serialized"] = explicit.model_dump(mode="json")[
        "acp_resume_session_id"
    ]
    result["resume_id_plaintext_roundtrip"] = (
        ACPAgent.model_validate(
            explicit.model_dump(mode="json", context={"expose_secrets": "plaintext"})
        ).acp_resume_session_id
        == first_session
    )
    third = Conversation(agent=explicit, workspace=str(workspace), visualizer=None)
    loads_before = len(journal("session_load"))
    result["explicit_error"] = run_turn(third, "third turn")
    new_loads = journal("session_load")[loads_before:]
    result["explicit_loaded"] = [
        load["session"] == first_session and load["known"] for load in new_loads
    ]
    third.agent.close()
    third.close()
    # An id the server has forgotten: falls back to a new session.
    news_before = len(journal("session_new"))
    forgotten = Conversation(
        agent=ACPAgent(acp_command=FAKE, acp_resume_session_id="sess-forgotten"),
        workspace=str(workspace),
        visualizer=None,
    )
    result["forgotten_error"] = run_turn(forgotten, "fourth turn")
    result["forgotten_load_known"] = [
        load["known"]
        for load in journal("session_load")
        if load["session"] == "sess-forgotten"
    ]
    result["forgotten_new_sessions"] = len(journal("session_new")) - news_before
    result["forgotten_status"] = forgotten.state.execution_status.value
    result["forgotten_warning"] = any(
        "load_session" in w and "fresh session" in w for w in warnings_seen
    )
    result["session_id_in_warnings"] = any("sess-forgotten" in w for w in warnings_seen)
    forgotten.agent.close()
    forgotten.close()

elif mode == "errors":
    cases: dict = {}

    def init_case(name: str, agent, env: dict[str, str] | None = None) -> None:
        saved = {k: os.environ.get(k) for k in (env or {})}
        os.environ.update(env or {})
        conversation = Conversation(
            agent=agent, workspace=str(workspace), visualizer=None
        )
        t0 = time.monotonic()
        error = run_turn(conversation, "hello")
        cases[name] = {
            "raised": error,
            "errors": errors_of(conversation),
            "seconds": round(time.monotonic() - t0, 1),
            "status": conversation.state.execution_status.value,
        }
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if name == "auth_required":
            # The user fixes the credential; the same conversation retries.
            os.environ["FAKE_ACP_KEY"] = "now-set"
            os.environ["FAKE_ACP_REQUIRE_KEY"] = "1"
            cases[name]["retry_error"] = run_turn(conversation, "hello again")
            cases[name]["retry_status"] = conversation.state.execution_status.value
            cases[name]["retry_finishes"] = len(finishes(conversation))
            os.environ.pop("FAKE_ACP_KEY")
            os.environ.pop("FAKE_ACP_REQUIRE_KEY")
        agent_alive = [r["pid"] for r in journal("start")]
        conversation.agent.close()
        conversation.close()
        cases[name]["leftover_processes"] = sum(pid_alive(p) for p in agent_alive)

    init_case(
        "auth_required", ACPAgent(acp_command=FAKE), {"FAKE_ACP_REQUIRE_KEY": "1"}
    )
    init_case(
        "missing_binary", ACPAgent(acp_command=[str(work / "no-such-acp-binary")])
    )
    init_case(
        "startup_timeout",
        ACPAgent(acp_command=FAKE, acp_startup_timeout=2.0),
        {"FAKE_ACP_INIT_SLEEP": "20"},
    )
    from openhands.sdk import Tool

    try:
        init_case(
            "custom_tools", ACPAgent(acp_command=FAKE, tools=[Tool(name="terminal")])
        )
    except Exception as exc:
        cases["custom_tools"] = {
            "raised_at_construct": f"{type(exc).__name__}: {exc}"[:200]
        }
    result["cases"] = cases

elif mode == "timeouts":
    cases = {}

    def timed_turn(conversation, tag: str) -> dict:
        before = len(conversation.state.events)
        prompts_before = len(journal("prompt"))
        cancels_before = len(journal("cancel"))
        t0 = time.monotonic()
        error = run_turn(conversation, f"{tag} work")
        new_events = list(conversation.state.events)[before:]
        return {
            "raised": error,
            "seconds": round(time.monotonic() - t0, 1),
            "status": conversation.state.execution_status.value,
            "prompts_sent": len(journal("prompt")) - prompts_before,
            "cancel_sent": len(journal("cancel")) - cancels_before,
            "finished": any(
                isinstance(e, ActionEvent) and e.tool_name == "finish"
                for e in new_events
            ),
            "errors": [
                {"code": e.code, "detail": e.detail[:200]}
                for e in new_events
                if isinstance(e, ConversationErrorEvent)
            ],
            "agent_messages": [
                e.llm_message.content[0].text[:120]
                for e in new_events
                if type(e).__name__ == "MessageEvent" and e.source == "agent"
            ],
        }

    # The idle timeout is 2 s; the [slow] turn takes 4 s but is never silent.
    steady = Conversation(
        agent=ACPAgent(acp_command=FAKE, acp_prompt_timeout=2.0),
        workspace=str(workspace),
        visualizer=None,
    )
    for tag in ("[slow]", "[flaky]", "[fail]"):
        cases[tag.strip("[]")] = timed_turn(steady, tag)
    steady.agent.close()
    steady.close()
    # A server that goes silent: one prompt, cancelled after 2 s, run ends.
    silent = Conversation(
        agent=ACPAgent(acp_command=FAKE, acp_prompt_timeout=2.0),
        workspace=str(workspace),
        visualizer=None,
    )
    cases["hang"] = timed_turn(silent, "[hang]")
    silent.agent.close()
    silent.close()
    result["cases"] = cases

elif mode == "cancel":
    agent = ACPAgent(acp_command=FAKE)
    conversation = Conversation(agent=agent, workspace=str(workspace), visualizer=None)

    async def cancel_mid_prompt() -> str:
        conversation.send_message("[cancel] build everything")
        task = asyncio.create_task(conversation.arun())
        deadline = time.monotonic() + 30
        while not journal("cancel_waiting") and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return "CancelledError"
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"[:200]
        return "completed"

    outcome = asyncio.run(cancel_mid_prompt())
    tools = tool_events(conversation)
    result.update(
        {
            "outcome": outcome,
            "status_after_cancel": conversation.state.execution_status.value,
            "cancel_sent": len(journal("cancel")),
            "long_tool_statuses": [
                e.status for e in tools if e.tool_call_id.endswith("-long")
            ],
            "long_tool_failed": any(
                e.is_error for e in tools if e.tool_call_id.endswith("-long")
            ),
            "errors": errors_of(conversation),
            "event_kinds": sorted(
                {type(e).__name__ for e in conversation.state.events}
            ),
        }
    )
    result["next_error"] = run_turn(conversation, "and now a normal turn")
    result["next_status"] = conversation.state.execution_status.value
    result["next_finishes"] = len(finishes(conversation))
    result["sessions_used"] = len({p["session"] for p in journal("prompt")})
    conversation.agent.close()
    conversation.close()

elif mode == "codex":
    # The user installed the Codex adapter as a binary; launching it by path
    # is how a pinned install looks (the SDK recognises `codex-acp`).
    bin_dir = work / "bin"
    bin_dir.mkdir(exist_ok=True)
    launcher = bin_dir / "codex-acp"
    launcher.write_text(
        f'#!/bin/sh\nexec {sys.executable} {HERE / "fake_acp.py"} "$@"\n'
    )
    launcher.chmod(0o755)
    os.environ.update(
        {"FAKE_ACP_NAME": "codex-acp", "FAKE_ACP_AUTH_METHODS": "chat-gpt,api-key"}
    )
    os.environ["CODEX_HOME"] = str(work / "ambient-codex-home")
    chatgpt_auth = json.dumps(
        {
            "auth_mode": "chatgpt",
            "OPENAI_API_KEY": None,
            "tokens": {
                "id_token": "a.b.c",
                "access_token": "tok-access-1",
                "refresh_token": "tok-refresh-1",
                "account_id": "acct-1",
            },
            "last_refresh": "2026-10-01T00:00:00Z",
        }
    )
    runs = {}
    for name, secrets, isolate in (
        ("api_key", {"CODEX_API_KEY": "sk-codex-verify"}, False),
        (
            "chatgpt_file",
            {
                "CODEX_AUTH_JSON": chatgpt_auth,
                "CODEX_API_KEY": "sk-codex-verify",
                "GOOGLE_APPLICATION_CREDENTIALS_JSON": '{"type": "service_account"}',
            },
            True,
        ),
    ):
        starts_before = len(journal("start"))
        auth_before = len(journal("authenticate"))
        config_before = len(journal("set_config_option"))
        mode_before = len(journal("set_mode"))
        store = work / f"store-{name}"
        agent = ACPAgent(
            acp_command=[str(launcher)],
            acp_model="gpt-5.5/high",
            acp_isolate_data_dir=isolate,
        )
        conversation = Conversation(
            agent=agent,
            workspace=str(workspace),
            visualizer=None,
            persistence_dir=str(store),
            secrets=secrets,
        )
        error = run_turn(conversation, "hello codex")
        start = journal("start")[starts_before]
        conv_dir = Path(conversation.state.persistence_dir or "")
        runs[name] = {
            "error": error,
            "status": conversation.state.execution_status.value,
            "auth_methods_used": [
                a["params"].get("methodId")
                for a in journal("authenticate")[auth_before:]
            ],
            "config_calls": [
                f"{c['config_id']}={c['value']}"
                for c in journal("set_config_option")[config_before:]
            ],
            "session_modes": [m["mode"] for m in journal("set_mode")[mode_before:]],
            "current_model_id": conversation.agent.current_model_id,
            "supports_switch": conversation.agent.supports_runtime_model_switch,
            "codex_home": start["env"].get("CODEX_HOME"),
            "codex_home_is_ambient": start["env"].get("CODEX_HOME")
            == os.environ["CODEX_HOME"],
            "codex_home_under_conversation": bool(conv_dir.parts)
            and str(start["env"].get("CODEX_HOME", "")).startswith(str(conv_dir)),
            "codex_home_files": start["codex_home_files"],
            "env_has_codex_auth_blob": "CODEX_AUTH_JSON" in start["env"],
            "env_has_google_creds_path": "GOOGLE_APPLICATION_CREDENTIALS"
            in start["env"],
            "env_has_google_creds_blob": "GOOGLE_APPLICATION_CREDENTIALS_JSON"
            in start["env"],
            "env_has_codex_api_key": "CODEX_API_KEY" in start["env"],
            "acp_dir": sorted(p.name for p in (conv_dir / "acp").iterdir())
            if (conv_dir / "acp").is_dir()
            else None,
            "auth_file_mode": oct(
                (Path(start["env"]["CODEX_HOME"]) / "auth.json").stat().st_mode & 0o777
            )
            if start["env"].get("CODEX_HOME")
            and (Path(start["env"]["CODEX_HOME"]) / "auth.json").exists()
            else None,
        }
        conversation.agent.close()
        conversation.close()
    result["runs"] = runs

elif mode == "discovery":
    from openhands.sdk.agent.acp_model_discovery import discover_acp_models
    from openhands.sdk.settings import ACPAgentSettings

    def disc(env: dict[str, str], **kw) -> dict:
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            d = discover_acp_models(
                ACPAgentSettings(acp_server="custom", acp_command=FAKE),
                work_root=str(work),
                **kw,
            )
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        data = d.model_dump(mode="json")
        return {
            "agent_name": data.get("agent_name"),
            "agent_version": data.get("agent_version"),
            "current_model_id": data.get("current_model_id"),
            "models": [m.get("model_id") for m in data.get("available_models") or []],
            "error": data.get("error"),
        }

    result["config_options"] = disc({})
    result["legacy"] = disc({"FAKE_ACP_LEGACY_MODELS": "1"})
    result["needs_key"] = disc({"FAKE_ACP_REQUIRE_KEY": "1"})
    result["with_key_secret"] = disc(
        {"FAKE_ACP_REQUIRE_KEY": "1"}, secrets={"FAKE_ACP_KEY": "k-1"}
    )
    result["missing_binary"] = {k: v for k, v in disc({}).items()} if False else None
    d = discover_acp_models(
        ACPAgentSettings(acp_server="custom", acp_command=[str(work / "nope")]),
        work_root=str(work),
    )
    result["missing_binary"] = d.model_dump(mode="json").get("error")
    result["probe_dirs_left"] = sorted(p.name for p in work.iterdir() if p.is_dir())
    result["leftover_processes"] = sum(pid_alive(r["pid"]) for r in journal("start"))
    # Settings: a custom server needs its command; a known one gets a default.
    try:
        ACPAgentSettings(acp_server="custom").resolve_acp_command()
        result["custom_without_command"] = "no error"
    except ValueError as exc:
        result["custom_without_command"] = f"ValueError: {exc}"[:200]
    result["claude_default_command"] = ACPAgentSettings(
        acp_server="claude-code"
    ).resolve_acp_command()
    settings = ACPAgentSettings(
        acp_server="custom", acp_command=FAKE, acp_model="m2", acp_isolate_data_dir=True
    )
    agent = settings.create_agent()
    result["created"] = {
        "type": type(agent).__name__,
        "acp_model": agent.acp_model,
        "acp_server": agent.acp_server,
        "isolate": agent.acp_isolate_data_dir,
        "command_is_fake": list(agent.acp_command) == FAKE,
    }
    conversation = Conversation(agent=agent, workspace=str(workspace), visualizer=None)
    result["created_turn_error"] = run_turn(conversation, "hi from settings")
    result["created_status"] = conversation.state.execution_status.value
    result["created_current_model"] = conversation.agent.current_model_id
    conversation.agent.close()
    conversation.close()

emit(result)
