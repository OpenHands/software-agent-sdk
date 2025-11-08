"""
Helper: Compare assistant content between telemetry logs and persisted events.

- Runs a short conversation using the configured LLM (LLM_MODEL/LLM_API_KEY)
- Ensures telemetry logging is enabled and a persistence_dir is used
- After the run, scans:
  - Telemetry logs under LOG_DIR/completions for entries written during this run
  - Conversation events under <persistence_dir>/<conv_id_hex>/events
- For each case where assistant content is empty either in telemetry or in events,
  appends a section into a Markdown report with the JSON for:
  - The telemetry LLM response (response object)
  - The matching (or nearest) assistant MessageEvent (if any)

Usage:
  LLM_MODEL=gemini/gemini-2.5-pro LLM_API_KEY=... uv run python \
    examples/01_standalone_sdk/27_compare_content_vs_telemetry.py

Optional env:
  LOG_DIR defaults to ./logs; the report is stored under LOG_DIR/comparisons
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.tool import Tool
from openhands.tools.terminal import TerminalTool


def ensure_dir(p: str) -> None:
    Path(p).mkdir(parents=True, exist_ok=True)


def read_json(p: Path) -> Any:
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def content_text_from_message_content(payload: Any) -> str:
    """Heuristic extraction of text from message.content payloads.

    Supports several shapes:
    - None -> ""
    - str -> itself
    - list[dict or str] -> concatenate any 'text' fields from dict items or str items
    """
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list):
        texts: list[str] = []
        for item in payload:
            if isinstance(item, str):
                texts.append(item)
            elif isinstance(item, dict):
                # Common shapes: {"type": "text", "text": "..."}
                t = item.get("text")
                if isinstance(t, str):
                    texts.append(t)
        return "\n".join(texts)
    return ""


def content_text_from_event_llm_message_content(payload: Any) -> str:
    """Extract text from persisted event llm_message.content list.

    Events typically store a list of TextContent dicts with 'text' keys.
    """
    if not payload:
        return ""
    if isinstance(payload, list):
        texts: list[str] = []
        for item in payload:
            if isinstance(item, dict):
                t = item.get("text")
                if isinstance(t, str):
                    texts.append(t)
        return "\n".join(texts)
    return ""


def main() -> None:
    api_key = os.getenv("LLM_API_KEY")
    assert api_key, "LLM_API_KEY is required"
    model = os.getenv("LLM_MODEL", "gemini/gemini-2.5-pro")

    log_dir = os.getenv("LOG_DIR", "logs")
    completions_dir = os.path.join(log_dir, "completions")
    comparisons_dir = os.path.join(log_dir, "comparisons")
    ensure_dir(completions_dir)
    ensure_dir(comparisons_dir)

    # Build LLM with telemetry logging enabled
    llm = LLM(
        model=model,
        api_key=SecretStr(api_key),
        log_completions=True,
        log_completions_folder=completions_dir,
        usage_id="agent",
    )

    agent = Agent(llm=llm, tools=[Tool(name=TerminalTool.name)])

    # Use .conversations as base; conversation id folder appended automatically
    persistence_base = ".conversations"
    ensure_dir(persistence_base)

    # Mark start time to filter telemetry written during this run
    t0 = time.time() - 1.0  # small slack

    conversation = Conversation(
        agent=agent,
        workspace=os.getcwd(),
        persistence_dir=persistence_base,
    )

    # Generate both a tool-call turn and a pure text turn
    conversation.send_message("Please echo 'HELLO' then say Done.")
    conversation.run()
    conversation.send_message("Reply with only OK")
    conversation.run()

    # Locate events dir
    # persistence_dir is non-None since we passed a base dir
    assert conversation.state.persistence_dir is not None
    conv_dir = Path(conversation.state.persistence_dir)
    events_dir = conv_dir / "events"

    # Collect telemetry records written after t0 and map by response id
    telemetry_files = sorted(Path(completions_dir).glob("*.json"))
    telemetry_by_id: dict[str, dict[str, Any]] = {}
    for tf in telemetry_files:
        try:
            data = read_json(tf)
        except Exception:
            continue
        ts = float(data.get("timestamp", 0.0))
        if ts < t0:
            continue
        resp = data.get("response", {})
        rid = resp.get("id")
        if isinstance(rid, str) and rid:
            telemetry_by_id[rid] = {"path": str(tf), "data": data}

    # Collect LLM-convertible events grouped by llm_response_id
    events_by_id: dict[str, dict[str, list[dict[str, Any]]]] = {}
    if events_dir.is_dir():
        for ef in sorted(events_dir.glob("*.json")):
            try:
                ev = read_json(ef)
            except Exception:
                continue
            kind = ev.get("kind")
            # MessageEvent (assistant) – use llm_response_id when present
            if kind == "MessageEvent":
                if ev.get("source") == "agent":
                    rid = ev.get("llm_response_id")
                    if isinstance(rid, str) and rid:
                        events_by_id.setdefault(rid, {"message": [], "action": []})
                        events_by_id[rid]["message"].append(
                            {"path": str(ef), "data": ev}
                        )
            # ActionEvent – always has llm_response_id
            elif kind == "ActionEvent":
                rid = ev.get("llm_response_id")
                if isinstance(rid, str) and rid:
                    events_by_id.setdefault(rid, {"message": [], "action": []})
                    events_by_id[rid]["action"].append({"path": str(ef), "data": ev})

    # Prepare markdown report
    out_path = Path(comparisons_dir) / (f"content_vs_telemetry_{int(time.time())}.md")

    def dump_json(obj: Any) -> str:
        try:
            return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True)
        except Exception:
            return str(obj)

    lines: list[str] = []
    lines.append("# Content vs Telemetry Comparison\n")
    lines.append(f"- Model: **{model}**\n")
    lines.append(f"- Conversation dir: `{conv_dir}`\n")
    lines.append(f"- Events dir: `{events_dir}`\n")
    lines.append(f"- Telemetry scan start: `{t0}`\n")
    lines.append("")

    # Helper to check emptiness for telemetry message content
    def telemetry_content_text(rec: dict[str, Any]) -> str:
        resp = rec["data"].get("response", {})
        # Chat completions shape
        choices = resp.get("choices") or []
        if choices:
            msg = (choices[0] or {}).get("message", {})
            content = msg.get("content")
            return content_text_from_message_content(content).strip()
        # Responses API shape
        out_text = resp.get("output_text")
        if isinstance(out_text, str):
            return out_text.strip()
        return ""

    # Helpers to extract event-side content
    def message_event_text(ev: dict[str, Any]) -> str:
        lm = ev.get("llm_message", {})
        return content_text_from_event_llm_message_content(lm.get("content")).strip()

    def action_event_text(ev: dict[str, Any]) -> str:
        # ActionEvent stores assistant "content" as 'thought' (list of TextContent)
        thought = ev.get("thought") or []
        if isinstance(thought, list):
            texts: list[str] = []
            for item in thought:
                if isinstance(item, dict):
                    t = item.get("text")
                    if isinstance(t, str):
                        texts.append(t)
            return "\n".join(texts).strip()
        return ""

    lines.append("## Cases with empty content in telemetry or events\n")

    # Iterate telemetry entries and compare with events by response id
    for rid, tele in telemetry_by_id.items():
        tele_txt = telemetry_content_text(tele)
        tele_empty = len(tele_txt) == 0

        ev_bucket = events_by_id.get(rid, {"message": [], "action": []})
        msg_texts = [
            message_event_text(e["data"]) for e in ev_bucket.get("message", [])
        ]
        act_texts = [action_event_text(e["data"]) for e in ev_bucket.get("action", [])]

        # Event-side emptiness: True if relevant events exist and all are empty
        has_ev = bool(ev_bucket.get("message") or ev_bucket.get("action"))
        ev_empty = has_ev and all(len(t) == 0 for t in (msg_texts + act_texts))

        if not (tele_empty or ev_empty):
            continue

        lines.append(f"### Response `{rid}`\n")
        # Telemetry block
        lines.append(f"- Telemetry file: `{tele['path']}`\n")
        lines.append(f"- Telemetry content empty: **{tele_empty}**\n")
        lines.append("- Telemetry response JSON:\n")
        lines.append("```json")
        lines.append(dump_json(tele["data"].get("response", {})))
        lines.append("```")

        # Event blocks
        if has_ev:
            for e in ev_bucket.get("message", []):
                etxt = message_event_text(e["data"]) or ""
                lines.append(f"- Event (MessageEvent) file: `{e['path']}`\n")
                lines.append(f"  - Event content empty: **{len(etxt) == 0}**\n")
                lines.append("  - Event JSON:\n")
                lines.append("```json")
                lines.append(dump_json(e["data"]))
                lines.append("```")
            for e in ev_bucket.get("action", []):
                atxt = action_event_text(e["data"]) or ""
                lines.append(f"- Event (ActionEvent) file: `{e['path']}`\n")
                lines.append(f"  - Event thought empty: **{len(atxt) == 0}**\n")
                lines.append("  - Event JSON:\n")
                lines.append("```json")
                lines.append(dump_json(e["data"]))
                lines.append("```")
        else:
            lines.append("- Event: none\n")

        lines.append("")

    with out_path.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"Wrote report to: {out_path}")


if __name__ == "__main__":
    main()
