"""Run real browser conversations against a recording stub LLM.

Reports, from the HTTP request the LLM actually receives, where the browser
rules land: the system message or the tool schemas. Needs a local Chrome or
Chromium; no LLM key.

    uv run python .pr/e2e_browser_guidance.py <report.md>
"""

import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.tools.browser_use import BrowserToolSet


RULES = (
    "Try curl/wget/fetch first",
    "Max 10 browser actions per sub-task",
    "On 403/CAPTCHA/login wall",
    "ALWAYS call browser_get_state before EVERY browser_click",
    "Do NOT submit forms or create accounts",
)
PAGE_MARKER = "e2e-page-marker-7f3a"
requests: list[dict] = []
report: list[str] = []


class PageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = f"<html><body><h1>{PAGE_MARKER}</h1></body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _tool_call(name: str, args: dict) -> dict:
    return {
        "id": f"call_{len(requests)}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def make_llm_handler(page_url: str, use_browser: bool):
    class LLMHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            done = sum(1 for m in body["messages"] if m["role"] == "tool")
            script = (
                [
                    ("browser_navigate", {"url": page_url}),
                    ("browser_get_content", {}),
                ]
                if use_browser
                else []
            )
            if done < len(script):
                name, args = script[done]
            else:
                name, args = "finish", {"message": "done"}
            reply = {
                "id": "stub",
                "object": "chat.completion",
                "created": 0,
                "model": body.get("model", "gpt-4o"),
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [_tool_call(name, args)],
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }
            data = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    return LLMHandler


def serve(handler) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def system_text(request: dict) -> str:
    content = request["messages"][0]["content"]
    if isinstance(content, str):
        return content
    return "\n".join(block.get("text", "") for block in content)


def run(label: str, *, browser: bool, system_prompt: str | None) -> None:
    requests.clear()
    page = serve(PageHandler)
    page_url = f"http://127.0.0.1:{page.server_port}/"
    llm_server = serve(make_llm_handler(page_url, use_browser=browser))
    llm = LLM(
        model="openai/gpt-4o",
        base_url=f"http://127.0.0.1:{llm_server.server_port}/v1",
        api_key=SecretStr("stub"),
        usage_id="e2e",
    )
    tools = [Tool(name=BrowserToolSet.name)] if browser else []
    agent = Agent(llm=llm, tools=tools, system_prompt=system_prompt)
    with tempfile.TemporaryDirectory() as workspace:
        conversation = Conversation(agent=agent, workspace=workspace)
        conversation.send_message("Read the page and finish.")
        conversation.run()
        conversation.close()
    page.shutdown()
    llm_server.shutdown()

    first = requests[0]
    system = system_text(first)
    tool_schemas = json.dumps(first.get("tools", []), ensure_ascii=False)
    navigate = next(
        (
            t["function"]["description"]
            for t in first.get("tools", [])
            if t["function"]["name"] == "browser_navigate"
        ),
        "",
    )
    page_seen = any(
        m["role"] == "tool" and PAGE_MARKER in json.dumps(m["content"])
        for m in requests[-1]["messages"]
    )
    out = report.append
    out(f"\n## {label}")
    sent = "browser_navigate" in tool_schemas
    out(f"LLM requests: {len(requests)}; browser tools sent: {sent}")
    out(f"page content reached the LLM via the real browser: {page_seen}")
    out(f"<BROWSER_TOOLS> in system message: {'<BROWSER_TOOLS>' in system}")
    out("| rule | in system message | count in tool schemas |")
    out("|---|---|---|")
    for rule in RULES:
        out(f"| {rule} | {rule in system} | {tool_schemas.count(rule)} |")
    if navigate:
        out(
            "\nbrowser_navigate description as sent:\n```\n"
            + navigate.strip()
            + "\n```"
        )


if __name__ == "__main__":
    run("default system prompt + browser", browser=True, system_prompt=None)
    run(
        "custom system_prompt + browser",
        browser=True,
        system_prompt="You are a terse test agent.",
    )
    run("default system prompt, no browser", browser=False, system_prompt=None)
    with open(sys.argv[1], "w", encoding="utf-8") as f:
        f.write("\n".join(report) + "\n")
