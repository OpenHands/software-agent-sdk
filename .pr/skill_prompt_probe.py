from __future__ import annotations

import json
import os
import re
from pathlib import Path
from xml.etree import ElementTree as ET

import frontmatter
from pydantic import SecretStr

from openhands.sdk import LLM, Agent, AgentContext, Conversation
from openhands.sdk.context.skills import Skill


def _find_top_skill_descriptions(
    extensions_repo: Path, count: int = 3
) -> list[dict[str, str | int]]:
    rows: list[dict[str, str | int]] = []
    for path in extensions_repo.rglob("SKILL.md"):
        post = frontmatter.load(path)
        description = post.metadata.get("description")
        if description is None:
            continue
        rows.append(
            {
                "name": str(post.metadata.get("name", path.parent.name)),
                "path": str(path),
                "description": str(description),
                "length": len(str(description)),
            }
        )

    rows.sort(key=lambda row: int(row["length"]), reverse=True)
    return rows[:count]


def _extract_system_text(completion_data: dict) -> str:
    if "instructions" in completion_data:
        return str(completion_data["instructions"])

    messages = completion_data.get("messages")
    if not isinstance(messages, list):
        raise RuntimeError("Completion log does not contain instructions or messages")

    for message in messages:
        if message.get("role") != "system":
            continue

        content = message.get("content")
        if isinstance(content, str):
            return content

        text_parts = []
        for item in content or []:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(str(item.get("text", "")))
            elif isinstance(item, dict) and "text" in item:
                text_parts.append(str(item["text"]))
            elif isinstance(item, str):
                text_parts.append(item)
        if text_parts:
            return "\n".join(text_parts)

    raise RuntimeError("Could not find system prompt content in completion log")


def _extract_available_skills_block(system_text: str) -> str:
    match = re.search(
        r"(<available_skills>.*?</available_skills>)",
        system_text,
        flags=re.DOTALL,
    )
    if match:
        return match.group(1)

    raise RuntimeError("Could not find <available_skills> block in system prompt")


def _parse_prompt_descriptions(available_skills_block: str) -> dict[str, str]:
    root = ET.fromstring(available_skills_block)
    descriptions: dict[str, str] = {}
    for skill_node in root.findall("skill"):
        name = skill_node.findtext("name")
        description = skill_node.findtext("description")
        if name is None or description is None:
            continue
        descriptions[name] = description
    return descriptions


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    artifacts_dir = repo_root / ".pr" / "skill_prompt_probe"
    completions_dir = artifacts_dir / "completions"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    completions_dir.mkdir(parents=True, exist_ok=True)

    extensions_repo = Path("/workspace/project/extensions")
    example_path = (
        repo_root
        / "examples"
        / "05_skills_and_plugins"
        / "01_loading_agentskills"
        / "main.py"
    )

    top_skills = _find_top_skill_descriptions(extensions_repo)
    (artifacts_dir / "top_skills.json").write_text(
        json.dumps(top_skills, indent=2), encoding="utf-8"
    )

    skills = [Skill.load(Path(skill_info["path"])) for skill_info in top_skills]

    api_key = os.environ["OPENAI_API_KEY"]
    llm = LLM(
        usage_id="skill-prompt-probe",
        model=os.getenv("LLM_MODEL", "openai/gpt-5-nano"),
        api_key=SecretStr(api_key),
        log_completions=True,
        log_completions_folder=str(completions_dir),
    )

    agent_context = AgentContext(skills=skills, load_public_skills=False)
    agent = Agent(llm=llm, agent_context=agent_context)
    conversation = Conversation(agent=agent, workspace=str(repo_root))

    answer = conversation.ask_agent("Reply with exactly OK.")
    (artifacts_dir / "response.txt").write_text(answer + "\n", encoding="utf-8")

    completion_logs = sorted(completions_dir.glob("*.json"))
    if not completion_logs:
        raise RuntimeError("No completion logs were written")

    completion_log = max(completion_logs, key=lambda path: path.stat().st_mtime)
    completion_data = json.loads(completion_log.read_text(encoding="utf-8"))
    system_text = _extract_system_text(completion_data)

    (artifacts_dir / "request_payload.json").write_text(
        json.dumps(completion_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (artifacts_dir / "system_prompt.txt").write_text(
        system_text + "\n", encoding="utf-8"
    )

    available_skills_block = _extract_available_skills_block(system_text)
    (artifacts_dir / "available_skills_prompt.xml").write_text(
        available_skills_block + "\n", encoding="utf-8"
    )

    prompt_descriptions = _parse_prompt_descriptions(available_skills_block)

    comparison_rows: list[dict[str, str | int | bool]] = []
    for skill_info in top_skills:
        name = str(skill_info["name"])
        full_description = str(skill_info["description"])
        prompt_description = prompt_descriptions[name]
        comparison_rows.append(
            {
                "name": name,
                "path": str(skill_info["path"]),
                "full_description_length": len(full_description),
                "prompt_description_length": len(prompt_description),
                "sent_in_full": prompt_description == full_description,
                "prompt_description": prompt_description,
            }
        )

    (artifacts_dir / "description_comparison.json").write_text(
        json.dumps(comparison_rows, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    lines = [
        f"Example basis: {example_path}",
        f"Extensions repo: {extensions_repo}",
        f"Completion log: {completion_log}",
        "",
        (
            "| skill | full description length | prompt description length | "
            "sent in full |"
        ),
        "| --- | ---: | ---: | --- |",
    ]
    for row in comparison_rows:
        sent = "yes" if row["sent_in_full"] else "no"
        lines.append(
            "| "
            f"{row['name']} | {row['full_description_length']} | "
            f"{row['prompt_description_length']} | {sent} |"
        )

    lines.extend(
        [
            "",
            "Prompt descriptions:",
            "",
        ]
    )
    for row in comparison_rows:
        lines.extend(
            [
                f"## {row['name']}",
                f"Source: `{row['path']}`",
                "",
                row["prompt_description"],
                "",
            ]
        )

    (artifacts_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps(comparison_rows, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
