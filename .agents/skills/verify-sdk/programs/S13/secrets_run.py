"""Usage: secrets_run.py [sync|async]

Secrets as a program hands them to a conversation, and the masking that
keeps their values out of everything the conversation shows or stores.

A local HTTP server stands in for a secrets service (`/secret/*` answers a
value and records the Authorization header; `/broken` answers 500).

  sources    StaticSecret / LookupSecret dumps, relative URLs, local resolvers.
  registry   the conversation's SecretRegistry.
  run        secrets from Conversation(secrets=...), AgentContext.secrets and
             update_secrets() reach the terminal; tool observations, agent
             messages and persisted events show <secret-hidden>.
  models     mask_secrets_in_model on nested values and image data.
  async      arun() with a LookupSecret served by the running event loop
             itself, which registers another secret while answering.
"""

import asyncio
import json
import os
import sys
import threading
import time
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import NamedTuple

from pydantic import BaseModel, SecretStr
from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import Agent, AgentContext, Conversation, Tool
from openhands.sdk.event import MessageEvent, ObservationEvent, SystemPromptEvent
from openhands.sdk.llm import ImageContent, TextContent
from openhands.sdk.secret import (
    LookupSecret,
    SecretSource,
    StaticSecret,
    register_local_secret_resolver,
    unregister_local_secret_resolver,
)
from openhands.sdk.utils.cipher import Cipher
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.glob import GlobTool
from openhands.tools.grep import GrepTool
from openhands.tools.terminal import TerminalTool


MODE = sys.argv[1] if len(sys.argv) > 1 else "sync"
paths = run_paths()
ws = paths["workspace"]
HIDDEN = "<secret-hidden>"
hits: list[dict] = []


class Secrets(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        hits.append({"path": self.path, "auth": self.headers.get("Authorization")})
        if self.path.startswith("/broken"):
            self.send_response(500)
            self.end_headers()
            return
        body = ("val-" + self.path.rsplit("/", 1)[-1]).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Secrets)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_port}"


def obs_texts(events, tool: str) -> list[str]:
    return [
        "".join(getattr(c, "text", "") for c in e.observation.to_llm_content)
        for e in events
        if isinstance(e, ObservationEvent) and e.tool_name == tool
    ]


result: dict = {"ok": True}


class Counter(SecretSource):
    """A program's own source: counts lookups."""

    calls: int = 0

    def get_value(self) -> str | None:
        self.calls += 1
        return "counter-val"


class Color(str, Enum):
    SECRET = "AAAAs13key"


class Pair(NamedTuple):
    left: str
    right: int


class Inner(BaseModel):
    note: str
    tags: set[str]


class Report(BaseModel):
    title: str
    inner: Inner
    items: list[str]
    mapping: dict[str, str]
    pair: Pair
    count: int
    key: SecretStr
    color: Color
    image: ImageContent
    text: TextContent


def sync_part() -> None:
    # --- sources -----------------------------------------------------------------
    static = StaticSecret(value=SecretStr("static-val"), description="a static one")
    lookup = LookupSecret(
        url=f"{base}/secret/alpha",
        headers={"Authorization": "Bearer svc-key", "X-Trace": "t1"},
    )
    cipher = Cipher(secret_key="s13-cipher")
    lookup_ciphered = lookup.model_dump(mode="json", context={"cipher": cipher})
    os.environ["OH_INTERNAL_SERVER_URL"] = base + "/"
    relative = LookupSecret(url="secret/relative")
    before = len(hits)

    def resolver(url: str) -> str | None:
        return "local-val" if url.endswith("/secret/local") else None

    def broken_resolver(url: str) -> str | None:  # noqa: ARG001
        raise RuntimeError("resolver down")

    register_local_secret_resolver(broken_resolver)
    register_local_secret_resolver(resolver)
    local_value = LookupSecret(url=f"{base}/secret/local").get_value()
    local_requests = len(hits) - before
    fallthrough_value = LookupSecret(url=f"{base}/secret/other").get_value()
    unregister_local_secret_resolver(resolver)
    unregister_local_secret_resolver(broken_resolver)
    result["sources"] = {
        "static_value": static.get_value(),
        "static_dump": static.model_dump(mode="json")["value"],
        "static_exposed": static.model_dump(
            mode="json", context={"expose_secrets": True}
        )["value"],
        "lookup_value": lookup.get_value(),
        "lookup_auth_sent": hits[-1]["auth"],
        "lookup_dump_headers": lookup.model_dump(mode="json")["headers"],
        "lookup_cipher_hides": "svc-key" not in json.dumps(lookup_ciphered),
        "lookup_cipher_restores": LookupSecret.model_validate(
            lookup_ciphered, context={"cipher": cipher}
        ).headers.get("Authorization"),
        "relative_url": relative.url == f"{base}/secret/relative",
        "relative_value": relative.get_value(),
        "local_resolver_value": local_value,
        "local_resolver_http_requests": local_requests,
        "declined_falls_back": fallthrough_value,
        "polymorphic": type(
            SecretSource.model_validate({"kind": "LookupSecret", "url": f"{base}/x"})
        ).__name__,
    }

    # --- run: injection and masking in a real conversation -----------------------
    (ws / "creds.txt").write_text("db password is tok-abc-s13\n")
    (ws / "tok-abc-s13.cfg").write_text("x\n")
    counter = Counter()
    broken = LookupSecret(url=f"{base}/broken/z")
    script = [
        {
            "tool": "terminal",
            "args": {"command": 'echo "$API_TOKEN" > token.txt; echo token=$API_TOKEN'},
        },
        {
            "tool": "terminal",
            "args": {
                "command": "echo $LOOKED_UP $CTX_ONLY $LATE_TOKEN > others.txt; "
                "cat others.txt"
            },
        },
        {"tool": "terminal", "args": {"command": "env | grep -c abc-s13 || true"}},
        {
            "tool": "file_editor",
            "args": {"command": "view", "path": str(ws / "creds.txt")},
        },
        {"tool": "grep", "args": {"pattern": "tok-abc-s13", "path": str(ws)}},
        {"tool": "glob", "args": {"pattern": "*.cfg", "path": str(ws)}},
        {"text": "I found tok-abc-s13 and val-beta."},
    ]
    c = Conversation(
        agent=Agent(
            llm=make_llm(script),
            tools=[
                Tool(name=TerminalTool.name),
                Tool(name=FileEditorTool.name),
                Tool(name=GrepTool.name),
                Tool(name=GlobTool.name),
            ],
            agent_context=AgentContext(
                secrets={"API_TOKEN": "ctx-loses", "CTX_ONLY": "ctx-only-val"}
            ),
        ),
        workspace=str(ws),
        persistence_dir=str(paths["conversations"]),
        visualizer=None,
        secrets={
            "API_TOKEN": "tok-abc-s13",
            "LOOKED_UP": LookupSecret(url=f"{base}/secret/beta"),
            "COUNTER": counter,
        },
    )
    c.update_secrets({"LATE_TOKEN": "late-val", "BROKEN": broken})
    registry = c.state.secret_registry
    reg = {
        "find_case_insensitive": sorted(
            registry.find_secrets_in_text("echo $api_token and $Late_Token")
        ),
        "env_for_command": registry.get_secrets_as_env_vars("echo $LATE_TOKEN $BROKEN"),
        "env_without_reference": registry.get_secrets_as_env_vars("echo hello"),
        "counter_calls_before_masking": counter.calls,
    }
    c.send_message("Use the secrets.")
    c.run()
    events = list(c.state.events)
    system = next(e for e in events if isinstance(e, SystemPromptEvent))
    dynamic = system.dynamic_context.text if system.dynamic_context else ""
    agent_text = [
        "".join(getattr(x, "text", "") for x in e.llm_message.content)
        for e in events
        if isinstance(e, MessageEvent) and e.source == "agent"
    ]
    # Action events keep the arguments the model wrote; everything else is checked.
    persisted = "".join(
        p.read_text()
        for p in Path(c.state.persistence_dir).rglob("*.json")
        if '"kind":"ActionEvent"' not in p.read_text()
    )
    action_args = "".join(
        p.read_text()
        for p in Path(c.state.persistence_dir).rglob("*.json")
        if '"kind":"ActionEvent"' in p.read_text()
    )
    terminal = obs_texts(events, "terminal")
    broken_hits = sum(1 for h in hits if h["path"].startswith("/broken"))
    registry.mask_secrets_in_output("again tok-abc-s13")  # within the back-off window
    reg["broken_hits_after_remask"] = (
        sum(1 for h in hits if h["path"].startswith("/broken")) - broken_hits
    )
    registry.update_secrets({"LATE_TOKEN": "late-val-2"})
    reg["overwrite"] = registry.get_secret_value("LATE_TOKEN")
    reg["counter_calls"] = counter.calls
    stream = registry.compile_stream_mask()
    streamed = stream.feed("start tok-ab") + stream.feed("c-s13 end") + stream.flush()
    reg["stream_split_value"] = streamed
    roundtrip = type(registry).model_validate(
        registry.model_dump(mode="json", context={"expose_secrets": True})
    )
    reg["roundtrip_masks"] = roundtrip.mask_secrets_in_output(
        "x tok-abc-s13 late-val-2 y"
    )
    result["registry"] = reg
    result["run"] = {
        "status": c.state.execution_status.value,
        "token_file": (ws / "token.txt").read_text().strip(),
        "others_file": (ws / "others.txt").read_text().strip(),
        "terminal_0": terminal[0],
        "terminal_1": terminal[1],
        "env_after_export": terminal[2].strip().splitlines()[0]
        if terminal[2].strip()
        else "",
        "file_view_masked": HIDDEN in obs_texts(events, "file_editor")[0],
        "file_view_leaks": "tok-abc-s13" in obs_texts(events, "file_editor")[0],
        "grep_masked": HIDDEN in obs_texts(events, "grep")[0],
        "grep_leaks": "tok-abc-s13" in obs_texts(events, "grep")[0],
        "glob_masked": HIDDEN in obs_texts(events, "glob")[0],
        "glob_leaks": "tok-abc-s13" in obs_texts(events, "glob")[0],
        "agent_message": agent_text[-1] if agent_text else None,
        "prompt_lists": [
            n
            for n in ("API_TOKEN", "LOOKED_UP", "CTX_ONLY", "LATE_TOKEN")
            if n in dynamic
        ],
        "prompt_leaks": any(
            v in dynamic for v in ("tok-abc-s13", "late-val", "ctx-only-val")
        ),
        "persisted_leaks": any(
            v in persisted for v in ("tok-abc-s13", "val-beta", "ctx-only-val")
        ),
        "persisted_masked": HIDDEN in persisted,
        "action_args_keep_value": "tok-abc-s13" in action_args,
    }
    c.close()

    # --- models: mask_secrets_in_model -------------------------------------------
    c = Conversation(
        agent=Agent(llm=make_llm([]), tools=[]),
        workspace=str(ws),
        visualizer=None,
        secrets={"B64ISH": "AAAAs13key"},
    )
    masked = c.state.secret_registry.mask_secrets_in_model(
        Report(
            title="t AAAAs13key",
            inner=Inner(note="n AAAAs13key", tags={"AAAAs13key"}),
            items=["i AAAAs13key"],
            mapping={"k": "v AAAAs13key"},
            pair=Pair("p AAAAs13key", 1),
            count=7,
            key=SecretStr("AAAAs13key"),
            color=Color.SECRET,
            image=ImageContent(
                image_urls=[
                    "data:image/png;base64,iVBORAAAAs13keyQmCC",
                    "https://img.example/?k=AAAAs13key",
                ]
            ),
            text=TextContent(text="AAAAs13key"),
        )
    )
    c.close()
    result["models"] = {
        "title": masked.title,
        "inner_note": masked.inner.note,
        "inner_tags": sorted(masked.inner.tags),
        "items": masked.items,
        "mapping": masked.mapping,
        "pair": list(masked.pair),
        "pair_is_namedtuple": isinstance(masked.pair, Pair),
        "count": masked.count,
        "key_untouched": masked.key.get_secret_value(),
        "color_untouched": masked.color is Color.SECRET,
        "data_url_intact": masked.image.image_urls[0]
        == "data:image/png;base64,iVBORAAAAs13keyQmCC",
        "https_url": masked.image.image_urls[1],
        "text": masked.text.text,
    }


# --- async: arun() with a lookup served by the running loop -------------------
async def async_case(first_step: dict) -> dict:
    """The loop that runs arun() also serves the LookupSecret, and while
    answering registers NESTED on the conversation."""
    holder: dict = {}

    async def serve(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        await asyncio.to_thread(holder["c"].update_secrets, {"NESTED": "nested-val"})
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Length: 8\r\nConnection: close\r\n\r\nloop-val"
        )
        await writer.drain()
        writer.close()

    srv = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    conversation = Conversation(
        agent=Agent(
            llm=make_llm(
                [first_step, {"text": "the values are loop-val and nested-val"}]
            ),
            tools=[Tool(name=TerminalTool.name)],
        ),
        workspace=str(ws),
        visualizer=None,
        secrets={"LOOP": LookupSecret(url=f"http://127.0.0.1:{port}/s")},
    )
    holder["c"] = conversation
    conversation.send_message("go")
    started = time.monotonic()
    await asyncio.wait_for(conversation.arun(), timeout=90)
    seconds = round(time.monotonic() - started, 1)
    if first_step.get("text") and "tool" not in first_step:
        conversation.send_message("again")
        await asyncio.wait_for(conversation.arun(), timeout=90)
    evs = list(conversation.state.events)
    messages = [
        "".join(getattr(x, "text", "") for x in e.llm_message.content)
        for e in evs
        if isinstance(e, MessageEvent) and e.source == "agent"
    ]
    out = {
        "status": conversation.state.execution_status.value,
        "messages": messages,
        "seconds": seconds,
    }
    conversation.close()
    srv.close()
    return out


if MODE == "sync":
    sync_part()
else:
    result["async"] = {
        # the lookup first happens while masking an agent message
        "message_step": asyncio.run(async_case({"text": "first loop-val"})),
        # the lookup first happens while masking a terminal observation
        "tool_step": asyncio.run(
            async_case(
                {
                    "tool": "terminal",
                    "args": {"command": "echo hi"},
                    "text": "running it",
                }
            )
        ),
    }
server.shutdown()
emit(result)
