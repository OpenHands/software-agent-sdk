#!/usr/bin/env python3
"""Test PR #6276 via the agent-server API on port 18000.

Sets two scoped StaticSecrets in agent_context.secrets, then creates a
conversation asking the agent to name the org token.
"""
import json
import os
import sys
import time

import httpx

BASE = "http://localhost:20010"
APIKEY_FILE = os.path.expanduser(
    "~/workspace/.oh-pr6276/agent-canvas/.apikey"
)
with open(APIKEY_FILE) as f:
    _APIKEY = f.read().strip()
AUTH = {"X-Session-API-Key": _APIKEY}


def main() -> int:
    print("=== 1. Fetch current settings ===")
    resp = httpx.get(f"{BASE}/api/settings", headers=AUTH, timeout=30)
    resp.raise_for_status()
    settings = resp.json()
    print(f"active_profile: {settings.get('active_profile')}")
    print(f"model: {settings['agent_settings']['llm']['model']}")

    print("\n=== 2. Update agent_context.secrets with two scoped StaticSecrets ===")
    # PATCH expects agent_settings_diff (deep-merged), not the full agent_settings.
    diff = {
        "agent_settings_diff": {
            "agent_context": {
                "secrets": {
                    "PERSONAL_GITHUB_TOKEN": {
                        "kind": "StaticSecret",
                        "value": "ghp_PERSONAL_12345",
                        "description": "GitHub PAT",
                        "scope": "personal",
                        "scope_description": (
                            "The user's own credentials. Actions are attributed "
                            "to the user and count against their personal rate "
                            "limits."
                        ),
                    },
                    "ORG_GITHUB_TOKEN": {
                        "kind": "StaticSecret",
                        "value": "ghp_ORG_67890",
                        "description": "GitHub PAT",
                        "scope": "organization",
                        "scope_description": (
                            "Shared service-account credentials. Actions are "
                            "attributed to the shared account and should be used "
                            "for org-level work."
                        ),
                    },
                }
            }
        }
    }
    resp = httpx.patch(f"{BASE}/api/settings", headers=AUTH, json=diff, timeout=30)
    resp.raise_for_status()
    updated = resp.json()
    secrets = updated["agent_settings"]["agent_context"]["secrets"]
    if secrets is None:
        print("ERROR: secrets not persisted", file=sys.stderr)
        return 1
    print(f"secrets set: {list(secrets.keys())}")

    print("\n=== 2b. Verify rendered system prompt shows grouped scopes ===")
    resp = httpx.get(
        f"{BASE}/api/agent-schema", headers=AUTH, timeout=30
    )
    # The system prompt is rendered at conversation start; we'll verify via
    # the standalone SDK script output instead (see scripts/test_pr6276_scope_grouping.py).

    print("\n=== 3. Create a conversation ===")
    resp = httpx.post(
        f"{BASE}/api/conversations",
        headers=AUTH,
        json={
            "workspace": {"working_dir": os.path.expanduser("~/workspace/.oh-pr6276/test-ws")},
            "agent_settings": settings["agent_settings"],
        },
        timeout=30,
    )
    resp.raise_for_status()
    conv_id = resp.json()["id"]
    print(f"conversation_id: {conv_id}")

    print("Sending message (with run=true)...")
    msg = {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": (
                    "We need to do an org-level audit. Print ONLY the name of "
                    "the environment variable that holds the organization's "
                    "shared service-account GitHub token (do not print its "
                    "value). Do not use the personal token."
                ),
            }
        ],
        "run": True,
    }
    resp = httpx.post(
        f"{BASE}/api/conversations/{conv_id}/events",
        headers=AUTH,
        json=msg,
        timeout=300,
    )
    if resp.status_code not in (200, 201, 202):
        print(f"send response: {resp.status_code} {resp.text[:500]}", file=sys.stderr)
    resp.raise_for_status()

    print("\n=== 4. Agent's answer ===")
    resp = httpx.get(
        f"{BASE}/api/conversations/{conv_id}/events", headers=AUTH, timeout=30
    )
    resp.raise_for_status()
    events = resp.json()
    events = events if isinstance(events, list) else events.get("events", [])

    answer = ""
    for e in reversed(events):
        if e.get("source") == "agent" and e.get("llm_message"):
            content = e["llm_message"].get("content", [])
            for block in content:
                if block.get("type") == "text" and block.get("text", "").strip():
                    answer = block["text"]
                    break
            if answer:
                break

    print(answer or "(no assistant text found)")
    print()

    if "ORG_GITHUB_TOKEN" in answer.upper() and "PERSONAL_GITHUB_TOKEN" not in answer.upper():
        print("PASS: agent picked ORG_GITHUB_TOKEN (organization scope).")
        return 0
    print("RESULT: see answer above.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
